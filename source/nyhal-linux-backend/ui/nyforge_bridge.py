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
import json
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

def _registry_version_key(version: Any) -> Optional[Tuple[int, ...]]:
    """Parse a dotted registry version ("1.1", "1.10") into a tuple of
    ints for numeric comparison; ``None`` when it is not dotted-numeric.
    """
    if not isinstance(version, str) or not version:
        return None
    parts = version.split(".")
    if not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


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
        self._rejected_hash: Optional[str] = None
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

    def inspect_version(self, path: Optional[str] = None,
                        text: Optional[str] = None) -> Dict[str, Any]:
        """Inspector preflight: report a document's contract situation
        WITHOUT injecting it.

        Answers the Inspector's version-picker question — "can I open
        this document, and what will it do to my project?" — in one
        call, against the registry's own ``versionHistory`` log.

        Parameters
        ----------
        path : str, optional
            Path to a .nstudio document (use this or ``text``).
        text : str, optional
            A .nstudio document as a JSON string.

        Returns
        -------
        dict
            ``documentSchemaVersion`` — the document's ``version`` (as
            written, ``None`` if absent),
            ``schemaSupported`` — whether the import gate would accept it,
            ``docHeaderVersions`` — every ``requiresRegistry`` entry the
            document declares,
            ``unknownDocRequirements`` — doc requirements that are
            neither a known registry version nor cleanly newer than the
            shipped one (a typo like ``1,1``, or a skipped version —
            the Inspector flags these for review),
            ``notYetInRegistry`` — the named "will lose these" set:
            requirements newer than the shipped registry, whether from
            this registry's own history or cleanly newer still (a
            document authored on a newer build),
            ``changesSinceOldestRequirement`` — the versionHistory
            entries this build knows that the document's requirements
            cover, oldest first, for the Inspector to render (empty
            when the document declares nothing),
            ``anyDropped`` — whether opening the document here would
            drop anything (any not-yet-shipped or unresolvable
            requirement).
        """
        raw: Dict[str, Any]
        if text is not None:
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"malformed JSON: {exc}"}
        elif path is not None:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                return {"ok": False, "error": str(exc)}
        else:
            return {"ok": False, "error": "provide 'path' or 'text'"}
        if not isinstance(raw, dict):
            return {"ok": False, "error": "document root must be a JSON object"}

        from ui.nstudio import NSTUDIO_SCHEMA_VERSION, VERSION_HISTORY

        # History order is authoritative for known versions — registry
        # versions are not necessarily single digits ("1.10" follows
        # "1.9" in history order, not lexicographic order). For a
        # requirement this build has never heard of, dotted-numeric
        # comparison against the shipped registry decides whether it is
        # a cleanly newer document (the cross-build case) or junk.
        order = {str(e["registryVersion"]): i
                 for i, e in enumerate(VERSION_HISTORY)}
        shipped_idx = len(VERSION_HISTORY) - 1
        shipped_key = _registry_version_key(
            VERSION_HISTORY[-1]["registryVersion"])

        def classify(req: str) -> str:
            if req in order:
                return ("honored" if order[req] <= shipped_idx
                        else "not_yet")
            key = _registry_version_key(req)
            if key is not None and shipped_key is not None \
                    and key > shipped_key:
                return "not_yet"
            return "unknown"

        doc_version = raw.get("version")
        requirements = [str(r) for r in raw.get("requiresRegistry") or []]
        verdicts = [classify(r) for r in requirements]

        unknown = sorted({r for r, v in zip(requirements, verdicts)
                          if v == "unknown"})
        # Total order: history position first (unknown requirements sort
        # after known ones, numerically among themselves) — never set
        # iteration order.
        not_yet = sorted(
            {r for r, v in zip(requirements, verdicts)
             if v == "not_yet"},
            key=lambda r: (order.get(r, -1),
                           _registry_version_key(r) or ()))
        # "Would I lose anything opening this here?" is true for BOTH a
        # newer-than-shipped requirement (a nameable loss) and an
        # unresolvable one (cannot be honored — flag for review).
        any_dropped = bool(not_yet or unknown)

        # Renderable context: what this build's log knows that the
        # document's requirements cover. When the document reaches
        # beyond this build, show the FULL log — the user deciding
        # whether to open it sees the whole contract lineage their
        # newer build would extend, not a truncated tail.
        known_idxs = [order[r] for r in requirements if r in order]
        if not_yet:
            span = list(VERSION_HISTORY)
        elif known_idxs:
            span = list(VERSION_HISTORY[:max(known_idxs) + 1])
        else:
            span = []

        return {
            "ok": True,
            "documentSchemaVersion": doc_version,
            "schemaSupported": doc_version == NSTUDIO_SCHEMA_VERSION,
            "docHeaderVersions": requirements,
            "unknownDocRequirements": unknown,
            "notYetInRegistry": not_yet,
            "changesSinceOldestRequirement": span,
            "anyDropped": any_dropped,
        }

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

    def swap_document(self, new_path: str) -> Dict[str, Any]:
        """Hot-swap the session to a different document — safely.

        The variant-switch operation behind "try the pill shell without
        restarting": the target is preflighted (``inspect_version``)
        and passed through the import gate BEFORE any window is
        removed, exactly like ``refresh``. On success the watch path
        follows the new document; on failure the current session stays
        untouched and the failure report names both documents.

        Parameters
        ----------
        new_path : str
            Path to the replacement document.

        Returns
        -------
        dict
            ``load_document``'s structure plus ``inspector`` (the
            target's preflight verdict), ``previous_path``, and, on
            rejection, ``previous_path_kept``.
        """
        if not new_path or not os.path.exists(new_path):
            return {
                "ok": False,
                "error": "target document not found: %r" % (new_path,),
                "windows_created": 0,
            }
        if self._doc_path is None:
            # Nothing live: a plain initial load (still preflighted).
            result = self.load_document(new_path)
            result["inspector"] = self.inspect_version(path=new_path)
            return result

        previous = self._doc_path
        inspector = self.inspect_version(path=new_path)

        # Validate the target through the gate before touching the
        # live session.
        from ui.nstudio import load as _load
        try:
            _load(new_path)
        except Exception as exc:
            return {
                "ok": False,
                "error": "swap rejected — session kept: %s" % exc,
                "windows_created": 0,
                "previous_path": previous,
                "previous_path_kept": True,
                "target_path": os.path.abspath(new_path),
                "inspector": inspector,
            }

        self._clear_mapped()
        self._doc_path = None  # load_document re-resolves the path
        result = self.load_document(new_path)
        result["inspector"] = inspector
        result["previous_path"] = previous
        if result["ok"]:
            self._notify("swap", result)
        else:
            # The target validated at gate time but injection failed
            # (session-side problem). The old document is no longer
            # running; report it honestly instead of pretending a swap
            # happened.
            result["previous_path_kept"] = False
            self._notify("swap_failed", result)
        return result

    def refresh(self) -> Dict[str, Any]:
        """Re-load the current document and re-inject — safely.

        A broken edit must never tear down the live session: the new
        document is preflighted (``inspect_version``) and passed through
        the import gate BEFORE any window is removed. Only a document
        that validates both ways replaces the running one; a failure
        leaves the current session untouched and reports the verdict.
        Returns the same structure as ``load_document`` plus an
        ``inspector`` key (the preflight report) on real reloads.
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

        # Inspector preflight FIRST — the verdict accompanies the
        # result whichever way the reload goes.
        inspector = self.inspect_version(path=self._doc_path)

        # Validate the new text through the gate before touching the
        # live session (loads, but does not inject).
        from ui.nstudio import load as _load
        try:
            _load(self._doc_path)
        except Exception as exc:
            result = {
                "ok": False,
                "error": "rejected — session kept: %s" % exc,
                "windows_created": 0,
                "doc_hash": self._doc_hash,
                "inspector": inspector,
            }
            # Transition-driven notifications: a file that STAYS broken
            # is rejected once per edit, not once per poll — re-polling
            # the same bytes returns the cached verdict without firing
            # the callbacks again.
            if new_hash == self._rejected_hash:
                cached = dict(result)
                cached["unchanged"] = True
                return cached
            self._rejected_hash = new_hash
            self._notify("reload_rejected", result)
            return result

        self._rejected_hash = None

        # Remove old mapped windows
        self._clear_mapped()

        # Re-load
        result = self.load_document(self._doc_path)
        result["inspector"] = inspector
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
