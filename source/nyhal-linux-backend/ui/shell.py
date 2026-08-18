#!/usr/bin/env python3
"""NyrqisShell — a simple runner for the Nyrqis Desktop Shell design.

Loads a persisted .nstudio document, creates a NyrqisRuntime,
and provides operations to exercise the design.

This is the floor-side "run on Nyrqis" operation — the Nyrqis
counterpart of Nyforge's Preview window. In a real OS this would
invoke the compositor; on the floor it exercises the runtime
semantics and produces a deterministic text output.

References:
- NFS-001 §7: behaviors (WHEN/IF/DO)
- NFS-001 §8: bindings (component property ← state)
- ADR-0025 §9: runtime consumption decision
- doc #14: Nyrqis Desktop Shell as a running product
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from .nstudio import (
    NstudioDocument,
    NstudioValidationError,
    load,
)
from .runtime import NyrqisRuntime

logger = logging.getLogger(__name__)


class NyrqisShell:
    """A simple runner for the Nyrqis Desktop Shell.

    Loads a persisted .nstudio document, creates a NyrqisRuntime,
    and provides operations to exercise the design.

    Parameters
    ----------
    document : NstudioDocument
        The loaded shell design.
    """

    def __init__(self, document: NstudioDocument) -> None:
        self._doc = document
        self._runtime = NyrqisRuntime(document)
        self._log_messages: List[str] = []
        self._runtime._log = lambda msg: self._log_messages.append(msg)

    @classmethod
    def from_file(cls, path: os.PathLike | str) -> "NyrqisShell":
        """Load a shell design from a .nstudio file."""
        doc = load(path)
        return cls(doc)

    @classmethod
    def from_json(cls, text: str) -> "NyrqisShell":
        """Load a shell design from a JSON string."""
        from .nstudio import loads
        doc = loads(text)
        return cls(doc)

    @property
    def runtime(self) -> NyrqisRuntime:
        """The underlying NyrqisRuntime."""
        return self._runtime

    @property
    def log_messages(self) -> List[str]:
        """Log messages from the last run."""
        return list(self._log_messages)

    def run(
        self,
        screen_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the shell design: apply bindings, render the component
        tree, and return the runtime state.

        Behaviors are NOT executed here — they are only triggered by
        events via ``run_interactive``. This method sets up the
        initial state by applying bindings from state to component
        properties.

        Returns
        -------
        dict
            A summary of the shell: screens, components, bindings,
            final states, text preview, and log messages.
        """
        self._log_messages.clear()

        # 1. Render the component tree
        entries = self._runtime.render(screen_id)
        component_count = len(entries)

        # 2. Apply all bindings (state → component properties)
        self._runtime.apply_all_bindings()

        # 3. Collect final state
        final_states = self._doc.resolve_states()

        # 4. Text preview
        text_preview = self._runtime.text_preview(screen_id)

        # 5. Summary
        summary = self._runtime.summary()

        return {
            "ok": True,
            "summary": summary,
            "component_count": component_count,
            "bindings_applied": len(self._doc.bindings),
            "final_states": final_states,
            "text_preview": text_preview,
            "log": self._log_messages,
        }

    def run_interactive(
        self,
        component_id: str,
        event_name: str,
    ) -> Dict[str, Any]:
        """Fire a single event and return the result.

        Returns
        -------
        dict
            The event dispatch result: actions executed, log messages,
            and the updated runtime state summary.
        """
        self._log_messages.clear()

        try:
            actions = self._runtime.fire_event(component_id, event_name)
            # Note: bindings are NOT re-applied here because event
            # dispatch already mutates component properties via actions.
            # Re-applying bindings would overwrite the action's effect
            # (the binding is one-directional: state → component).
            return {
                "ok": True,
                "actions_executed": len(actions),
                "actions": [
                    {"target": t, "name": n, "arguments": a}
                    for t, n, a in actions
                ],
                "log": self._log_messages,
                "summary": self._runtime.summary(),
            }
        except NstudioValidationError as e:
            return {
                "ok": False,
                "error": str(e),
                "log": self._log_messages,
            }
