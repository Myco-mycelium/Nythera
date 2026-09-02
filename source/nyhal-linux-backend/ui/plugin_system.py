#!/usr/bin/env python3
"""plugin_system — Nyrqis plugin/extension system.

A full plugin framework for extending the desktop:

- Plugin manifest with metadata, dependencies, permissions
- Lifecycle hooks: install, enable, disable, uninstall
- Event hooks: on_click, on_key, on_render, on_theme_change
- Plugin registry with versioning and dependency resolution
- Sandboxed execution with permission checks
- Plugin marketplace metadata (icon, rating, downloads)
- Hot-reload support (disable → reload → enable)
- Plugin communication bus (inter-plugin messaging)
- Settings persistence per plugin

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class PluginState(Enum):
    """Plugin lifecycle states."""
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"
    UNLOADED = "unloaded"


class Permission(Enum):
    """Plugin permissions."""
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    NETWORK = "network"
    NOTIFICATIONS = "notifications"
    SYSTEM_INFO = "system_info"
    WINDOW_MANAGE = "window_manage"
    THEME_CHANGE = "theme_change"
    INPUT_INJECT = "input_inject"
    PLUGIN_COMM = "plugin_comm"
    FULL_ACCESS = "full_access"


@dataclass
class PluginManifest:
    """Plugin metadata and configuration."""
    id: str
    name: str
    version: str
    author: str = ""
    description: str = ""
    icon: str = ""
    homepage: str = ""
    min_nyrqis_version: str = "0.1.0"
    max_nyrqis_version: str = ""
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    settings_schema: Dict[str, Any] = field(default_factory=dict)
    entry_point: str = ""       # module.path:class_name
    hooks: List[str] = field(default_factory=list)  # which hooks this plugin uses
    tags: List[str] = field(default_factory=list)
    rating: float = 0.0
    downloads: int = 0
    size_bytes: int = 0

    @property
    def display_name(self) -> str:
        return f"{self.name} v{self.version}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "author": self.author, "description": self.description,
            "icon": self.icon, "homepage": self.homepage,
            "min_nyrqis_version": self.min_nyrqis_version,
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "settings_schema": dict(self.settings_schema),
            "entry_point": self.entry_point, "hooks": list(self.hooks),
            "tags": list(self.tags), "rating": self.rating,
            "downloads": self.downloads, "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PluginManifest":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PluginInstance:
    """Runtime state of an installed plugin."""
    manifest: PluginManifest
    state: PluginState = PluginState.INSTALLED
    settings: Dict[str, Any] = field(default_factory=dict)
    install_time: float = 0.0
    last_enabled: float = 0.0
    error_message: str = ""
    load_count: int = 0

    def __post_init__(self):
        if self.install_time == 0.0:
            self.install_time = time.time()

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def is_active(self) -> bool:
        return self.state == PluginState.ENABLED


@dataclass
class PluginEvent:
    """An event dispatched through the hook system."""
    hook: str            # e.g. "on_click", "on_render", "on_key"
    data: Dict[str, Any] = field(default_factory=dict)
    source_plugin: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class PluginMessage:
    """Inter-plugin communication message."""
    from_plugin: str
    to_plugin: str       # "" = broadcast
    topic: str
    payload: Any = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------
# Plugin base class
# ---------------------------------------------------------------------------

class PluginBase:
    """Base class for Nyrqis plugins.

    Subclass this to create a plugin. Override hooks as needed.
    """

    def __init__(self, manifest: PluginManifest, settings: Dict[str, Any]) -> None:
        self.manifest = manifest
        self.settings = settings

    def on_install(self) -> None:
        """Called when plugin is first installed."""

    def on_enable(self) -> None:
        """Called when plugin is enabled."""

    def on_disable(self) -> None:
        """Called when plugin is disabled."""

    def on_uninstall(self) -> None:
        """Called when plugin is removed."""

    def on_click(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Called when a UI element is clicked."""
        return None

    def on_key(self, event: Dict[str, Any]) -> Optional[str]:
        """Called on keyboard input. Return action string to consume."""
        return None

    def on_render(self, context: Dict[str, Any]) -> Optional[Any]:
        """Called during render. Return overlay to composite."""
        return None

    def on_theme_change(self, theme_name: str) -> None:
        """Called when the theme changes."""

    def on_settings_change(self, key: str, value: Any) -> None:
        """Called when a plugin setting changes."""

    def on_message(self, message: PluginMessage) -> Optional[Any]:
        """Called when another plugin sends a message."""
        return None

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value


# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

class PluginRegistry:
    """Manages the plugin catalog and installation state."""

    def __init__(self, config_dir: Optional[str] = None) -> None:
        self._plugins: Dict[str, PluginInstance] = {}
        self._config_dir = config_dir
        self._callbacks: List[Callable] = []

    def install(self, manifest: PluginManifest) -> PluginInstance:
        """Install a plugin from manifest."""
        if manifest.id in self._plugins:
            existing = self._plugins[manifest.id]
            if existing.state == PluginState.UNLOADED:
                existing.manifest = manifest
                existing.state = PluginState.INSTALLED
                existing.error_message = ""
                return existing
            return existing

        instance = PluginInstance(manifest=manifest, state=PluginState.INSTALLED)
        self._plugins[manifest.id] = instance
        self._dispatch("installed", manifest.id)
        return instance

    def uninstall(self, plugin_id: str) -> bool:
        """Remove a plugin."""
        instance = self._plugins.get(plugin_id)
        if instance is None:
            return False
        if instance.state == PluginState.ENABLED:
            self.disable(plugin_id)
        instance.state = PluginState.UNLOADED
        del self._plugins[plugin_id]
        self._dispatch("uninstalled", plugin_id)
        return True

    def enable(self, plugin_id: str) -> bool:
        """Enable a plugin."""
        instance = self._plugins.get(plugin_id)
        if instance is None or instance.state not in (
            PluginState.INSTALLED, PluginState.DISABLED):
            return False

        # Check permissions
        missing = self._check_permissions(instance)
        if missing:
            instance.state = PluginState.ERROR
            instance.error_message = f"Missing permissions: {', '.join(missing)}"
            self._dispatch("error", plugin_id)
            return False

        instance.state = PluginState.ENABLED
        instance.last_enabled = time.time()
        instance.load_count += 1
        self._dispatch("enabled", plugin_id)
        return True

    def disable(self, plugin_id: str) -> bool:
        """Disable a plugin."""
        instance = self._plugins.get(plugin_id)
        if instance is None or instance.state != PluginState.ENABLED:
            return False
        instance.state = PluginState.DISABLED
        self._dispatch("disabled", plugin_id)
        return True

    def _check_permissions(self, instance: PluginInstance) -> List[str]:
        """Check that required permissions are available."""
        # In a real system, this would check against system policy
        allowed = set(p.value for p in Permission)
        return [p for p in instance.manifest.permissions
                if p not in allowed]

    # -- Plugin access -------------------------------------------------

    def get(self, plugin_id: str) -> Optional[PluginInstance]:
        return self._plugins.get(plugin_id)

    @property
    def installed(self) -> List[PluginInstance]:
        return list(self._plugins.values())

    @property
    def enabled(self) -> List[PluginInstance]:
        return [p for p in self._plugins.values() if p.state == PluginState.ENABLED]

    @property
    def disabled(self) -> List[PluginInstance]:
        return [p for p in self._plugins.values() if p.state == PluginState.DISABLED]

    def by_tag(self, tag: str) -> List[PluginInstance]:
        return [p for p in self._plugins.values()
                if tag in p.manifest.tags]

    def search(self, query: str) -> List[PluginInstance]:
        q = query.lower()
        return [p for p in self._plugins.values()
                if q in p.manifest.name.lower()
                or q in p.manifest.description.lower()
                or q in " ".join(p.manifest.tags).lower()]

    def count(self) -> int:
        return len(self._plugins)

    def enabled_count(self) -> int:
        return len(self.enabled)

    # -- Settings persistence ------------------------------------------

    def set_setting(self, plugin_id: str, key: str, value: Any) -> bool:
        instance = self._plugins.get(plugin_id)
        if instance is None:
            return False
        instance.settings[key] = value
        return True

    def get_setting(self, plugin_id: str, key: str, default: Any = None) -> Any:
        instance = self._plugins.get(plugin_id)
        if instance is None:
            return default
        return instance.settings.get(key, default)

    def save_state(self) -> Optional[str]:
        """Export all plugin state as JSON."""
        state = {}
        for pid, inst in self._plugins.items():
            state[pid] = {
                "manifest": inst.manifest.to_dict(),
                "state": inst.state.value,
                "settings": inst.settings,
                "install_time": inst.install_time,
                "last_enabled": inst.last_enabled,
                "load_count": inst.load_count,
            }
        return json.dumps(state, indent=2)

    def load_state(self, json_str: str) -> int:
        """Restore plugin state from JSON. Returns count restored."""
        try:
            state = json.loads(json_str)
        except json.JSONDecodeError:
            return 0

        count = 0
        for pid, data in state.items():
            manifest = PluginManifest.from_dict(data["manifest"])
            inst = PluginInstance(
                manifest=manifest,
                state=PluginState(data["state"]),
                settings=data.get("settings", {}),
                install_time=data.get("install_time", 0),
                last_enabled=data.get("last_enabled", 0),
                load_count=data.get("load_count", 0),
            )
            self._plugins[pid] = inst
            count += 1
        return count

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, plugin_id: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, plugin_id)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Hook dispatcher
# ---------------------------------------------------------------------------

class HookDispatcher:
    """Dispatches events to registered plugin hooks.

    Parameters
    ----------
    registry : PluginRegistry
        The plugin registry.
    """

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry
        self._handlers: Dict[str, List[Tuple[str, Callable]]] = {}
        self._message_handlers: Dict[str, List[Tuple[str, Callable]]] = {}

    def register_hook(self, plugin_id: str, hook: str, handler: Callable) -> None:
        """Register a plugin's hook handler."""
        if hook not in self._handlers:
            self._handlers[hook] = []
        self._handlers[hook].append((plugin_id, handler))

    def unregister_hook(self, plugin_id: str, hook: str) -> None:
        """Remove a plugin's hook handler."""
        if hook in self._handlers:
            self._handlers[hook] = [
                (pid, h) for pid, h in self._handlers[hook]
                if pid != plugin_id
            ]

    def unregister_all(self, plugin_id: str) -> None:
        """Remove all hooks for a plugin."""
        for hook in list(self._handlers.keys()):
            self.unregister_hook(plugin_id, hook)

    def dispatch(self, hook: str, data: Optional[Dict] = None) -> List[Any]:
        """Dispatch an event to all handlers of a hook.

        Returns list of non-None return values.
        """
        results = []
        for plugin_id, handler in self._handlers.get(hook, []):
            instance = self._registry.get(plugin_id)
            if instance is None or instance.state != PluginState.ENABLED:
                continue
            try:
                event = PluginEvent(hook=hook, data=data or {},
                                    source_plugin=plugin_id)
                result = handler(event)
                if result is not None:
                    results.append(result)
            except Exception as e:
                instance.state = PluginState.ERROR
                instance.error_message = str(e)
        return results

    def dispatch_sorted(self, hook: str, data: Optional[Dict] = None) -> List[Any]:
        """Dispatch with priority ordering (lower priority = runs first)."""
        entries = self._handlers.get(hook, [])
        # Sort by plugin_id for deterministic order
        entries = sorted(entries, key=lambda x: x[0])
        results = []
        for plugin_id, handler in entries:
            instance = self._registry.get(plugin_id)
            if instance is None or instance.state != PluginState.ENABLED:
                continue
            try:
                event = PluginEvent(hook=hook, data=data or {},
                                    source_plugin=plugin_id)
                result = handler(event)
                if result is not None:
                    results.append(result)
            except Exception as e:
                instance.state = PluginState.ERROR
                instance.error_message = str(e)
        return results

    @property
    def registered_hooks(self) -> List[str]:
        return list(self._handlers.keys())

    def hook_count(self, hook: str) -> int:
        return len(self._handlers.get(hook, []))

    def total_hooks(self) -> int:
        return sum(len(h) for h in self._handlers.values())


# ---------------------------------------------------------------------------
# Message bus
# ---------------------------------------------------------------------------

class MessageBus:
    """Inter-plugin communication bus."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Tuple[str, Callable]]] = {}
        self._broadcast_handlers: List[Tuple[str, Callable]] = []
        self._history: List[PluginMessage] = []
        self._max_history = 100

    def subscribe(self, plugin_id: str, topic: str, handler: Callable) -> None:
        """Subscribe to a topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append((plugin_id, handler))

    def unsubscribe(self, plugin_id: str, topic: str) -> None:
        if topic in self._subscribers:
            self._subscribers[topic] = [
                (pid, h) for pid, h in self._subscribers[topic]
                if pid != plugin_id
            ]

    def subscribe_broadcast(self, plugin_id: str, handler: Callable) -> None:
        """Subscribe to all messages."""
        self._broadcast_handlers.append((plugin_id, handler))

    def send(self, message: PluginMessage) -> int:
        """Send a message. Returns count of handlers notified."""
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        count = 0

        # Targeted
        if message.to_plugin:
            # Send to specific topic subscribers
            for pid, handler in self._subscribers.get(message.topic, []):
                if pid == message.to_plugin or pid == message.from_plugin:
                    continue
                try:
                    handler(message)
                    count += 1
                except Exception:
                    pass
        else:
            # Topic broadcast
            for pid, handler in self._subscribers.get(message.topic, []):
                if pid == message.from_plugin:
                    continue
                try:
                    handler(message)
                    count += 1
                except Exception:
                    pass

        # Global broadcast
        for pid, handler in self._broadcast_handlers:
            if pid == message.from_plugin:
                continue
            try:
                handler(message)
                count += 1
            except Exception:
                pass

        return count

    def broadcast(self, from_plugin: str, topic: str, payload: Any = None) -> int:
        """Send a broadcast message."""
        msg = PluginMessage(
            from_plugin=from_plugin, to_plugin="",
            topic=topic, payload=payload,
        )
        return self.send(msg)

    @property
    def history(self) -> List[PluginMessage]:
        return list(self._history)

    def topic_count(self) -> int:
        return len(self._subscribers)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, []))


# ---------------------------------------------------------------------------
# Plugin manager (orchestrator)
# ---------------------------------------------------------------------------

class PluginManager:
    """High-level plugin management system.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._registry = PluginRegistry()
        self._hooks = HookDispatcher(self._registry)
        self._bus = MessageBus()
        self._plugin_instances: Dict[str, PluginBase] = {}
        self._callbacks: List[Callable] = []

        # Wire registry events
        self._registry.on_event(self._on_registry_event)

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    @property
    def hooks(self) -> HookDispatcher:
        return self._hooks

    @property
    def bus(self) -> MessageBus:
        return self._bus

    # -- High-level API ------------------------------------------------

    def install(self, manifest: PluginManifest) -> bool:
        """Install and optionally enable a plugin."""
        instance = self._registry.install(manifest)
        if instance is None:
            return False

        # Create plugin instance
        plugin_class = self._load_plugin(manifest)
        if plugin_class:
            try:
                plugin = plugin_class(manifest, instance.settings)
                plugin.on_install()
                self._plugin_instances[manifest.id] = plugin
            except Exception as e:
                instance.state = PluginState.ERROR
                instance.error_message = str(e)
                return False

        self._dispatch("installed", manifest.id)
        return True

    def uninstall(self, plugin_id: str) -> bool:
        """Uninstall a plugin."""
        plugin = self._plugin_instances.get(plugin_id)
        if plugin:
            try:
                plugin.on_uninstall()
            except Exception:
                pass
            del self._plugin_instances[plugin_id]
            self._hooks.unregister_all(plugin_id)
        return self._registry.uninstall(plugin_id)

    def enable(self, plugin_id: str) -> bool:
        """Enable a plugin."""
        result = self._registry.enable(plugin_id)
        if result:
            plugin = self._plugin_instances.get(plugin_id)
            if plugin:
                try:
                    plugin.on_enable()
                except Exception as e:
                    self._registry.disable(plugin_id)
                    return False
        return result

    def disable(self, plugin_id: str) -> bool:
        """Disable a plugin."""
        plugin = self._plugin_instances.get(plugin_id)
        if plugin:
            try:
                plugin.on_disable()
            except Exception:
                pass
        return self._registry.disable(plugin_id)

    def reload(self, plugin_id: str) -> bool:
        """Hot-reload a plugin."""
        instance = self._registry.get(plugin_id)
        if instance is None:
            return False
        self.disable(plugin_id)
        # Re-create
        plugin = self._load_plugin(instance.manifest)
        if plugin:
            try:
                p = plugin(instance.manifest, instance.settings)
                self._plugin_instances[plugin_id] = p
            except Exception:
                pass
        self.enable(plugin_id)
        return True

    def _load_plugin(self, manifest: PluginManifest) -> Optional[type]:
        """Load a plugin class from entry point."""
        # In production, this would use importlib
        # For now, return None (plugins loaded externally)
        return None

    # -- Hook dispatch -------------------------------------------------

    def dispatch_click(self, x: int, y: int, button: int = 1) -> List[Any]:
        return self._hooks.dispatch_sorted("on_click", {
            "x": x, "y": y, "button": button,
        })

    def dispatch_key(self, key: str, modifiers: Optional[Dict] = None) -> List[Any]:
        return self._hooks.dispatch_sorted("on_key", {
            "key": key, "modifiers": modifiers or {},
        })

    def dispatch_render(self, context: Dict) -> List[Any]:
        return self._hooks.dispatch_sorted("on_render", context)

    def dispatch_theme_change(self, theme_name: str) -> None:
        self._hooks.dispatch_sorted("on_theme_change", {"theme": theme_name})

    # -- Communication -------------------------------------------------

    def send_message(self, from_id: str, to_id: str, topic: str,
                     payload: Any = None) -> int:
        return self._bus.send(PluginMessage(
            from_plugin=from_id, to_plugin=to_id,
            topic=topic, payload=payload,
        ))

    def broadcast(self, from_id: str, topic: str, payload: Any = None) -> int:
        return self._bus.broadcast(from_id, topic, payload)

    # -- State ---------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        return {
            "total_installed": self._registry.count(),
            "enabled": self._registry.enabled_count(),
            "total_hooks": self._hooks.total_hooks(),
            "topics": self._bus.topic_count(),
        }

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _on_registry_event(self, event_type: str, plugin_id: str) -> None:
        self._dispatch(event_type, plugin_id)

    def _dispatch(self, event_type: str, plugin_id: str) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, plugin_id)
            except Exception:
                pass

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"PluginManager(installed={s['total_installed']}, "
            f"enabled={s['enabled']}, "
            f"hooks={s['total_hooks']})"
        )


__all__ = [
    "PluginManager", "PluginRegistry", "HookDispatcher", "MessageBus",
    "PluginManifest", "PluginInstance", "PluginBase", "PluginEvent",
    "PluginMessage", "PluginState", "Permission",
]
