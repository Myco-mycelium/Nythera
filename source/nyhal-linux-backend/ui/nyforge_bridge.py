#!/usr/bin/env python3
"""nyforge_bridge — Nyforge → Nyrqis DesktopSession integration.

Bridges the Nyforge visual editor to the running Nyrqis desktop session.
Loads .nstudio documents exported from Nyforge, maps NUI components to
real desktop session windows, and supports hot-reload so changes in
Nyforge appear live on the desktop.

Architecture::

    Nyforge Editor
         │
         │  export .nstudio
         ▼
    NyforgeBridge
         │
         │  parse → map → inject
         ▼
    DesktopSession
         │
         │  window management, compositor, input
         ▼
    Nyrqis Desktop

The bridge handles:
- Component mapping (NUI Window → DesktopSession window)
- Property injection (NUI properties → window geometry, theme, title)
- Behavior wiring (NUI WHEN/IF/DO → session event handlers)
- Hot-reload (file watcher → debounce → re-inject)
- State synchronization (NUI state ↔ session state)

References:
    - NFS-001 §3: NUI layout system
    - NFS-001 §7: behaviors (WHEN/IF/DO)
    - NFS-001 §8: bindings (component property ← state)
    - NUI-SCHEMA: component vocabulary
    - doc #14: Nyrqis Desktop Shell
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ui.desktop_session import Window

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component mapping
# ---------------------------------------------------------------------------

# Maps NUI component type names to Nyrqis desktop window roles.
# When Nyforge exports a Window with a known type hint, the bridge
# can route it to the corresponding built-in shell component.
COMPONENT_ROLE_MAP: Dict[str, str] = {
    "Window": "generic",
    "Taskbar": "taskbar",
    "StartMenu": "start_menu",
    "Settings": "settings",
    "FileManager": "file_manager",
    "Terminal": "terminal",
    "Calculator": "calculator",
    "TextEditor": "text_editor",
    "NotificationCenter": "notification_center",
    "LockScreen": "lock_screen",
    "PowerMenu": "power_menu",
    "SystemTray": "system_tray",
    "ContextMenu": "context_menu",
    "DesktopSurface": "desktop",
}


@dataclass
class MappedWindow:
    """A window in the DesktopSession that was created from an NUI component."""

    component_id: str
    window_id: str
    role: str
    title: str
    x: int = 0
    y: int = 0
    width: int = 800
    height: int = 600
    visible: bool = True
    theme_name: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    state_bindings: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NyforgeBridge
# ---------------------------------------------------------------------------

class NyforgeBridge:
    """Bridges Nyforge .nstudio documents to a running Nyrqis DesktopSession.

    Parameters
    ----------
    session : DesktopSession
        The running desktop session to inject windows into.
    """

    def __init__(self, session: Any) -> None:
        self._session = session
        self._mapped: Dict[str, MappedWindow] = {}
        self._doc_hash: Optional[str] = None
        self._doc_path: Optional[str] = None
        self._callbacks: List[Callable] = []
        self._hot_reload_enabled = False
        self._watch_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._watch_interval: float = 1.0  # seconds

    # -- Properties --------------------------------------------------------

    @property
    def mapped_windows(self) -> Dict[str, MappedWindow]:
        return dict(self._mapped)

    @property
    def doc_path(self) -> Optional[str]:
        return self._doc_path

    @property
    def doc_hash(self) -> Optional[str]:
        return self._doc_hash

    @property
    def is_hot_reload_active(self) -> bool:
        return self._hot_reload_enabled and self._watch_thread is not None

    # -- Core: load and inject ---------------------------------------------

    def load_document(self, path: str) -> Dict[str, Any]:
        """Load a .nstudio document and inject it into the desktop session.

        Returns
        -------
        dict
            Summary: windows created, components mapped, errors.
        """
        from ui.nstudio import load

        try:
            doc = load(path)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "windows_created": 0,
            }

        self._doc_path = os.path.abspath(path)

        # Compute document hash for change detection
        with open(path, "rb") as f:
            self._doc_hash = hashlib.sha256(f.read()).hexdigest()[:16]

        return self._inject_document(doc)

    def load_json(self, text: str) -> Dict[str, Any]:
        """Load a .nstudio document from a JSON string."""
        from ui.nstudio import loads

        try:
            doc = loads(text)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "windows_created": 0,
            }

        self._doc_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        self._doc_path = None

        return self._inject_document(doc)

    def _inject_document(self, doc: Any) -> Dict[str, Any]:
        """Map NUI components to DesktopSession windows."""
        created = []
        errors = []

        # Process each screen's top-level windows
        for screen in doc.screens:
            root = screen.root
            # The root itself may be a Window
            if root.type == "Window":
                try:
                    mapped = self._map_window(root, doc)
                    created.append(mapped)
                except Exception as exc:
                    errors.append({
                        "component": root.id,
                        "error": str(exc),
                    })
            # Also process root's direct children
            for component in root.children:
                if component.type == "Window":
                    try:
                        mapped = self._map_window(component, doc)
                        created.append(mapped)
                    except Exception as exc:
                        errors.append({
                            "component": component.id,
                            "error": str(exc),
                        })

        # Wire behaviors
        behaviors_wired = self._wire_behaviors(doc)

        # Apply bindings
        bindings_applied = self._apply_bindings(doc)

        return {
            "ok": len(errors) == 0,
            "windows_created": len(created),
            "behaviors_wired": behaviors_wired,
            "bindings_applied": bindings_applied,
            "errors": errors,
            "doc_hash": self._doc_hash,
        }

    def _map_window(self, component: Any, doc: Any) -> MappedWindow:
        """Map an NUI Window component to a DesktopSession window."""
        props = component.properties or {}
        layout = component.layout or {}

        # Extract geometry — check layout first, then props
        x = int(layout.get("x", props.get("x", props.get("left", 100))))
        y = int(layout.get("y", props.get("y", props.get("top", 100))))
        width = int(layout.get("width", props.get("width", 800)))
        height = int(layout.get("height", props.get("height", 600)))
        title = str(props.get("title", props.get("text", "Untitled")))
        visible = bool(props.get("visible", True))
        theme = props.get("theme", None)

        # Determine the role from component type or properties
        role = COMPONENT_ROLE_MAP.get(component.type, "generic")
        if role == "generic":
            # Try to infer from title or ID
            title_lower = title.lower()
            for nui_type, mapped_role in COMPONENT_ROLE_MAP.items():
                if nui_type.lower() in title_lower:
                    role = mapped_role
                    break

        # Create the window in the desktop session
        window_id = f"wnd-{component.id}-{uuid.uuid4().hex[:8]}"
        win = Window(
            id=window_id,
            component_id=component.id,
            title=title,
            x=x,
            y=y,
            width=width,
            height=height,
        )
        self._session.add_window(win)

        # Build state bindings from NUI bindings
        state_bindings = {}
        for binding in doc.bindings:
            if binding.component == component.id:
                state_bindings[binding.property] = binding.state

        mapped = MappedWindow(
            component_id=component.id,
            window_id=window_id,
            role=role,
            title=title,
            x=x,
            y=y,
            width=width,
            height=height,
            visible=visible,
            theme_name=theme,
            properties=dict(props),
            state_bindings=state_bindings,
        )

        if not visible:
            self._session.minimize_window(window_id)
            mapped.visible = False

        self._mapped[component.id] = mapped
        logger.info(
            "Mapped NUI component %s → window %s (role=%s)",
            component.id, window_id, role,
        )
        return mapped

    def _wire_behaviors(self, doc: Any) -> int:
        """Wire NUI behaviors to DesktopSession event handlers.

        NstudioBehavior has: id, condition, action (single dict), actions
        (chain list). Each action dict has: target, name, arguments.
        We try to match the action's target to a mapped component.
        """
        wired = 0
        for behavior in doc.behaviors:
            actions = behavior.actions or [behavior.action]

            for action in actions:
                if not isinstance(action, dict):
                    continue
                action_target = action.get("target", "")
                action_name = action.get("name", "")
                action_args = action.get("arguments", {})

                # Find the mapped window for this target
                if action_target not in self._mapped:
                    continue
                mapped = self._mapped[action_target]

                handler = self._resolve_action(
                    mapped, action_name, action_target, action_args
                )
                if handler:
                    wired += 1

        return wired

    def _resolve_action(
        self,
        mapped: MappedWindow,
        action_name: str,
        action_target: str,
        action_args: Dict[str, Any],
    ) -> Optional[Callable]:
        """Resolve an NUI action to a callable handler."""
        # Map common NUI actions to session operations.
        # NUI uses PascalCase names; we match both PascalCase and snake_case.
        action_map = {
            "navigate": lambda: self._action_navigate(mapped, action_args),
            "set_state": lambda: self._action_set_state(mapped, action_args),
            "open_window": lambda: self._action_open_window(action_args),
            "close_window": lambda: self._session.close_window(mapped.window_id),
            "Close": lambda: self._session.close_window(mapped.window_id),
            "minimize": lambda: self._session.minimize_window(mapped.window_id),
            "Minimize": lambda: self._session.minimize_window(mapped.window_id),
            "maximize": lambda: self._session.maximize_window(mapped.window_id),
            "Maximize": lambda: self._session.maximize_window(mapped.window_id),
        }

        handler_factory = action_map.get(action_name)
        if handler_factory:
            try:
                return handler_factory()
            except Exception as exc:
                logger.warning(
                    "Failed to wire action %s for %s: %s",
                    action_name, mapped.component_id, exc,
                )
        return None

    def _action_navigate(
        self, mapped: MappedWindow, args: Dict[str, Any]
    ) -> Optional[Callable]:
        """Create a navigate action handler."""
        target = args.get("target", "")
        if not target:
            return None

        def handler():
            logger.info("Navigate: %s → %s", mapped.title, target)
            self._notify("navigate", {
                "from": mapped.component_id,
                "target": target,
            })

        return handler

    def _action_set_state(
        self, mapped: MappedWindow, args: Dict[str, Any]
    ) -> Optional[Callable]:
        """Create a set_state action handler."""
        key = args.get("key", "")
        value = args.get("value")
        if not key:
            return None

        def handler():
            self._notify("state_change", {
                "component": mapped.component_id,
                "key": key,
                "value": value,
            })

        return handler

    def _action_open_window(self, args: Dict[str, Any]) -> Optional[Callable]:
        """Create an open_window action handler."""
        title = args.get("title", "New Window")

        def handler():
            wid = f"wnd-dyn-{uuid.uuid4().hex[:8]}"
            win = Window(id=wid, component_id=wid, title=title)
            self._session.add_window(win)
            self._notify("open_window", {"title": title})

        return handler

    def _apply_bindings(self, doc: Any) -> int:
        """Apply NUI state bindings to mapped windows.

        NstudioBinding has: component, property, state.
        """
        applied = 0
        states = doc.resolve_states()

        for binding in doc.bindings:
            if binding.component not in self._mapped:
                continue
            mapped = self._mapped[binding.component]
            if binding.state in states:
                value = states[binding.state]
                if binding.property == "title":
                    for w in self._session.windows:
                        if w.id == mapped.window_id:
                            w.title = str(value)
                            applied += 1
                            break
                elif binding.property == "visible":
                    if bool(value) and not mapped.visible:
                        self._session.minimize_window(mapped.window_id)
                        mapped.visible = True
                        applied += 1
                    elif not bool(value) and mapped.visible:
                        self._session.minimize_window(mapped.window_id)
                        mapped.visible = False
                        applied += 1

        return applied

    # -- Refresh (hot-reload) ----------------------------------------------

    def refresh(self) -> Dict[str, Any]:
        """Re-load the current document and re-inject.

        Returns the same structure as ``load_document``.
        """
        if self._doc_path is None:
            return {"ok": False, "error": "No document loaded"}

        # Check if the file has actually changed
        with open(self._doc_path, "rb") as f:
            new_hash = hashlib.sha256(f.read()).hexdigest()[:16]

        if new_hash == self._doc_hash:
            return {
                "ok": True,
                "unchanged": True,
                "doc_hash": self._doc_hash,
            }

        # Remove old mapped windows
        self._clear_mapped()

        # Re-load
        result = self.load_document(self._doc_path)
        if result["ok"]:
            self._notify("reload", result)
        return result

    def _clear_mapped(self) -> None:
        """Remove all mapped windows from the session."""
        for mapped in self._mapped.values():
            try:
                self._session.close_window(mapped.window_id)
            except Exception:
                pass
        self._mapped.clear()

    # -- Hot-reload --------------------------------------------------------

    def enable_hot_reload(
        self, interval: float = 1.0, callback: Optional[Callable] = None
    ) -> None:
        """Enable hot-reload: watch the document file for changes.

        Parameters
        ----------
        interval : float
            Poll interval in seconds.
        callback : callable, optional
            Called after each successful reload with the result dict.
        """
        if self._doc_path is None:
            raise ValueError("No document loaded — cannot watch")

        if callback:
            self._callbacks.append(callback)

        self._hot_reload_enabled = True
        self._watch_interval = interval
        self._stop_event.clear()

        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="nyforge-hot-reload",
        )
        self._watch_thread.start()
        logger.info(
            "Hot-reload enabled for %s (interval=%.1fs)",
            self._doc_path, interval,
        )

    def disable_hot_reload(self) -> None:
        """Disable hot-reload and stop the watch thread."""
        self._hot_reload_enabled = False
        self._stop_event.set()
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=3.0)
        self._watch_thread = None
        logger.info("Hot-reload disabled")

    def _watch_loop(self) -> None:
        """Background thread that polls the file for changes."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._watch_interval)
            if self._stop_event.is_set():
                break
            try:
                result = self.refresh()
                if not result.get("unchanged", False):
                    logger.info("Hot-reload triggered: %s", result)
            except Exception as exc:
                logger.warning("Hot-reload error: %s", exc)

    # -- Event system ------------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        """Register a callback for bridge events."""
        self._callbacks.append(callback)

    def _notify(self, event: str, data: Any) -> None:
        """Notify all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb(event, data)
            except Exception as exc:
                logger.warning("Callback error: %s", exc)

    # -- Utility -----------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Return a summary of the current bridge state."""
        return {
            "doc_path": self._doc_path,
            "doc_hash": self._doc_hash,
            "mapped_windows": len(self._mapped),
            "hot_reload_active": self.is_hot_reload_active,
            "windows": {
                cid: {
                    "window_id": m.window_id,
                    "role": m.role,
                    "title": m.title,
                    "size": f"{m.width}x{m.height}",
                    "visible": m.visible,
                }
                for cid, m in self._mapped.items()
            },
        }

    def unmap_all(self) -> None:
        """Remove all mappings and close associated windows."""
        self._clear_mapped()
        self._notify("unmap_all", {})
