#!/usr/bin/env python3
"""NyrqisRuntime — the real Nyrqis UI runtime (ADR-0025 §9).

Manages runtime state, dispatches events to behaviors, evaluates
conditions (including AND/OR logic groups), executes action chains,
and applies bindings. This is the Nyrqis-side counterpart of
Nyforge's ``ForgePreviewRuntime``; both implement the same semantics
defined by the NUI schema (NUI-SCHEMA §7.3, §8.4).

The runtime wraps a loaded ``NstudioDocument`` and exposes:

- ``RuntimeState`` — the live key/value store that expressions and
  bindings resolve against (flat states + scoped states merged).
- ``fire_event(component_id, event_name)`` — find the behavior
  attached to the component's event, evaluate its condition, and
  execute its action chain.
- ``apply_binding(binding)`` — sync one state value to a component
  property.
- ``apply_all_bindings()`` — re-apply every binding from the document.
- ``set_state(key, value)`` — mutate a flat state value.
- ``resolve_state(key)`` — resolve a state reference (flat or scoped).

References:
- NFS-001 §7: behaviors (WHEN/IF/DO, chains, AND/OR groups)
- NFS-001 §8: bindings (component property ← state)
- NFS-001 §8.4: state scopes (global, session, persistent)
- NUI-SCHEMA §7.3: condition tree and action chain model
- ADR-0025 §9: runtime consumption decision
"""

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from . import nexpr
from .nstudio import (
    NstudioAnimation,
    NstudioBehavior,
    NstudioBinding,
    NstudioComponent,
    NstudioDocument,
    NstudioValidationError,
)

if TYPE_CHECKING:
    from .animation import AnimationTimeline

logger = logging.getLogger(__name__)

# Type alias for the event-log callback (message string).
LogCallback = Callable[[str], None]


class NyrqisRuntime:
    """The real Nyrqis UI runtime.

    Wraps a loaded ``NstudioDocument`` and provides the runtime
    operations that a shell compositor (or the floor test harness)
    uses to manage state, dispatch events, and apply bindings.

    The runtime does NOT own the document — it reads from it. State
    mutations happen through ``set_state()`` which modifies the
    document's ``states`` dict in place (the document is the source
    of truth for state, just as the NUI schema specifies).

    Parameters
    ----------
    document : NstudioDocument
        The loaded .nstudio document to run.
    log : callable, optional
        Callback for runtime log messages (event dispatches, state
        changes, action executions).
    """

    def __init__(
        self,
        document: NstudioDocument,
        log: Optional[LogCallback] = None,
        timeline: Optional["AnimationTimeline"] = None,
    ) -> None:
        self._doc = document
        self._log = log or (lambda msg: None)
        self._timeline = timeline

    # ---- state management ------------------------------------------------

    @property
    def states(self) -> Dict[str, Any]:
        """The document's flat state dict (mutable)."""
        return self._doc.states

    def set_state(self, key: str, value: Any) -> None:
        """Mutate a flat state value and log the change."""
        old = self._doc.states.get(key)
        self._doc.states[key] = value
        self._log(f"State '{key}' = {value}")

    def resolve_state(self, key: str, default: Any = None) -> Any:
        """Resolve a state reference (flat or scoped) — delegates to
        ``NstudioDocument.resolve_state``."""
        return self._doc.resolve_state(key, default)

    def resolve_states(self) -> Dict[str, Any]:
        """The flattened state view (flat + scoped entries under dotted
        names) used by the expression evaluator."""
        return self._doc.resolve_states()

    # ---- event dispatch --------------------------------------------------

    def fire_event(
        self,
        component_id: str,
        event_name: str,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Dispatch an event: find the behavior attached to the
        component's event, evaluate its condition, and execute its
        action chain.

        Returns the list of ``(target, name, arguments)`` tuples
        that were executed (empty if the condition was false or no
        behavior was found).

        Raises
        ------
        NstudioValidationError
            If the component or behavior doesn't exist, or the action
            target/name is invalid.
        """
        component = self._doc.find_component(component_id)
        if component is None:
            raise NstudioValidationError(
                f"component '{component_id}' does not exist")

        behavior_id = component.events.get(event_name)
        if behavior_id is None:
            self._log(
                f"Event '{event_name}' on '{component_id}' has no "
                f"bound behavior — ignoring")
            return []

        behavior = self._doc.behavior_by_id(behavior_id)
        if behavior is None:
            self._log(
                f"Behavior '{behavior_id}' not found — the events map "
                f"points at something that doesn't exist in Behaviors[]")
            return []

        # Evaluate condition
        condition_result = self._doc.resolve_condition(behavior_id)
        if condition_result is False:
            self._log(
                f"Event '{event_name}' on '{component_id}' → "
                f"behavior '{behavior_id}' condition false — skipping")
            return []

        # Resolve actions
        actions = self._doc.resolve_actions(behavior_id)
        self._log(
            f"Event '{event_name}' on '{component_id}' → "
            f"behavior '{behavior_id}' → {len(actions)} action(s)")

        # Execute each action
        executed: List[Tuple[str, str, Dict[str, Any]]] = []
        for target, name, arguments in actions:
            self._execute_action(target, name, arguments)
            executed.append((target, name, arguments))

        return executed

    # ---- binding application ---------------------------------------------

    def apply_binding(self, binding: NstudioBinding) -> None:
        """Apply a single binding: resolve the state value and set it
        on the target component's property."""
        component = self._doc.find_component(binding.component)
        if component is None:
            self._log(
                f"Binding target '{binding.component}' not found — "
                f"skipping")
            return

        value = self.resolve_state(binding.state)
        if value is None:
            self._log(
                f"Binding state '{binding.state}' is None — "
                f"skipping")
            return

        # Set the property on the component's properties dict
        old = component.properties.get(binding.property)
        component.properties[binding.property] = value
        self._log(
            f"Binding: {binding.component}.{binding.property} "
            f"← state '{binding.state}' = {value}")

    def apply_all_bindings(self) -> None:
        """Re-apply every binding in the document."""
        for binding in self._doc.bindings:
            self.apply_binding(binding)

    # ---- action execution ------------------------------------------------

    def _execute_action(
        self,
        target: str,
        name: str,
        arguments: Dict[str, Any],
    ) -> None:
        """Execute a resolved action.

        Handles the built-in system actions:
        - ``Nyrqis.Theme.Set`` — mutate the active theme
        - ``Nyrqis.Animation.Play`` — start a declared animation on
          the connected ``AnimationTimeline``
        - ``Nyrqis.Notification.Show`` — log the notification
        - ``Nyrqis.State.Set`` — mutate a flat state value
        - ``Nyrqis.State.Toggle`` — toggle a boolean state value
        - ``Open`` / ``Close`` / ``Toggle`` — mutate component properties

        Custom actions (targeting specific components) mutate the
        target component's properties directly.

        In a real OS runtime, system actions would invoke the
        compositor's theme engine, notification daemon, etc. This
        floor implementation handles the common cases for testing.
        """
        if target == "System":
            self._execute_system_action(name, arguments)
        else:
            self._execute_component_action(target, name, arguments)

    def _execute_system_action(
        self,
        name: str,
        arguments: Dict[str, Any],
    ) -> None:
        """Execute a system-level action (theme, animation, notification)."""
        if name == "Nyrqis.Theme.Set":
            theme = arguments.get("theme", "Eclipse")
            self._doc.themes["active"] = theme
            # Also update the persistent scope if it exists
            persistent = self._doc.state_scopes.get("persistent")
            if isinstance(persistent, dict):
                persistent["theme"] = theme
            self._log(f"Theme → {theme}")

        elif name == "Nyrqis.Animation.Play":
            anim_id = arguments.get("animation", "")
            self._play_animation(anim_id)

        elif name == "Nyrqis.Notification.Show":
            title = arguments.get("title", "")
            message = arguments.get("message", "")
            severity = arguments.get("severity", "info")
            self._log(
                f"Notification [{severity}]: {title} — {message}")

        elif name == "Nyrqis.State.Set":
            key = arguments.get("key", "")
            value = arguments.get("value")
            if key:
                self.set_state(key, value)
            else:
                self._log("Nyrqis.State.Set: missing 'key'")

        elif name == "Nyrqis.State.Toggle":
            key = arguments.get("key", "")
            if key:
                current = self.resolve_state(key, False)
                self.set_state(key, not current)
            else:
                self._log("Nyrqis.State.Toggle: missing 'key'")

        else:
            self._log(f"System action '{name}' (no-op on floor)")

    def _execute_component_action(
        self,
        target: str,
        name: str,
        arguments: Dict[str, Any],
    ) -> None:
        """Execute an action targeting a specific component."""
        component = self._doc.find_component(target)
        if component is None:
            self._log(
                f"Action target '{target}' not found — skipping")
            return

        if name == "Open":
            component.properties["open"] = True
            self._log(f"Component '{target}' opened")
        elif name == "Close":
            component.properties["open"] = False
            self._log(f"Component '{target}' closed")
        elif name == "Toggle":
            current = component.properties.get("open", False)
            component.properties["open"] = not current
            self._log(
                f"Component '{target}' toggled → "
                f"{component.properties['open']}")
        else:
            # Generic action: set any arguments as properties
            for key, value in arguments.items():
                component.properties[key] = value
            self._log(
                f"Action '{name}' on '{target}' with "
                f"{len(arguments)} arg(s)")

    # ---- internal helpers ------------------------------------------------

    def _execute_actions_for_behavior(
        self,
        behavior_id: str,
    ) -> List[Tuple[str, str, Dict[str, Any]]]:
        """Execute a behavior's actions directly (by ID), evaluating
        its condition first. Used by tests and internal dispatch."""
        behavior = self._doc.behavior_by_id(behavior_id)
        if behavior is None:
            raise NstudioValidationError(
                f"behavior '{behavior_id}' does not exist")

        condition_result = self._doc.resolve_condition(behavior_id)
        if condition_result is False:
            return []

        actions = self._doc.resolve_actions(behavior_id)
        executed: List[Tuple[str, str, Dict[str, Any]]] = []
        for target, name, arguments in actions:
            self._execute_action(target, name, arguments)
            executed.append((target, name, arguments))
        return executed

    # ---- animation helpers -----------------------------------------------

    def _play_animation(self, animation_id: str) -> None:
        """Look up a declared animation by id and start it on the
        connected timeline (if any)."""
        anim = next(
            (a for a in self._doc.animations if a.id == animation_id),
            None,
        )
        if anim is None:
            self._log(
                f"Animation '{animation_id}' not found in document")
            return
        if self._timeline is not None:
            self._timeline.play_from_nstudio(
                anim, state=self._doc.states)
            self._log(
                f"Animation '{animation_id}' started on timeline")
        else:
            self._log(
                f"Animation '{animation_id}' triggered (no timeline)")

    # ---- document access -------------------------------------------------

    @property
    def document(self) -> NstudioDocument:
        """The underlying document (read-only access for compositor)."""
        return self._doc

    def render(
        self, screen_id: Optional[str] = None
    ) -> List[Tuple[NstudioComponent, int]]:
        """Render the component tree (delegates to document)."""
        return self._doc.render(screen_id)

    def text_preview(
        self, screen_id: Optional[str] = None
    ) -> str:
        """Text preview of the screen (delegates to document)."""
        return self._doc.text_preview(screen_id)

    def summary(self) -> Dict[str, Any]:
        """Runtime summary for diagnostics."""
        return {
            "screens": len(self._doc.screens),
            "components": len(self._doc.component_ids()),
            "behaviors": len(self._doc.behaviors),
            "bindings": len(self._doc.bindings),
            "animations": len(self._doc.animations),
            "states": len(self._doc.states),
            "state_scopes": list(self._doc.state_scopes.keys()),
            "active_theme": self._doc.themes.get("active", "Eclipse"),
        }
