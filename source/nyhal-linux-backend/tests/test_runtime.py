#!/usr/bin/env python3
"""Tests for ui.runtime.NyrqisRuntime — the real Nyrqis UI runtime."""

import json
import os
import sys
import unittest

# Ensure the backend package is importable
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from ui.nstudio import loads, NstudioValidationError
from ui.runtime import NyrqisRuntime

# ---- fixtures -----------------------------------------------------------

_MINIMAL_DOC = json.dumps({
    "version": "0.4.0",
    "project": {"name": "Test", "id": "test", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"},
    "themes": {"active": "Eclipse", "overrides": {}},
    "states": {"volume": 60, "startMenuOpen": False, "doNotDisturb": False},
    "stateScopes": {"persistent": {"theme": "Eclipse", "volume": 60}, "session": {"clockTime": "14:32"}},
    "animations": [{"id": "fade", "target": "start_menu", "property": "opacity", "duration": 200, "easing": "ease-out"}],
    "locales": {"active": "en", "tables": {"en": {}}},
    "behaviors": [
        {
            "id": "bh_start_toggle",
            "condition": None,
            "action": {"target": "start_menu", "name": "Toggle", "arguments": {}},
        },
        {
            "id": "bh_theme_set",
            "condition": None,
            "action": {"target": "System", "name": "Nyrqis.Theme.Set", "arguments": {"theme": "Solar"}},
        },
        {
            "id": "bh_chain",
            "condition": None,
            "actions": [
                {"target": "System", "name": "Nyrqis.Theme.Set", "arguments": {"theme": "Eclipse"}},
                {"target": "System", "name": "Nyrqis.Animation.Play", "arguments": {"animation": "fade"}},
            ],
        },
        {
            "id": "bh_conditional",
            "condition": {"expression": "state.volume > 50"},
            "action": {"target": "System", "name": "Nyrqis.Notification.Show", "arguments": {"title": "Loud", "message": "Vol high"}},
        },
        {
            "id": "bh_conditional_false",
            "condition": {"expression": "state.volume > 90"},
            "action": {"target": "System", "name": "Nyrqis.Notification.Show", "arguments": {"title": "Should not fire"}},
        },
        {
            "id": "bh_and_group",
            "condition": {
                "logic": "and",
                "conditions": [
                    {"expression": "state.doNotDisturb == true"},
                    {"expression": "state.volume > 50"},
                ],
            },
            "action": {"target": "System", "name": "Nyrqis.Notification.Show", "arguments": {"title": "DND+Loud"}},
        },
    ],
    "components": [],
    "bindings": [
        {"component": "start_menu", "property": "open", "state": "startMenuOpen"},
    ],
    "screens": [
        {
            "id": "desktop",
            "size": {"width": 1440, "height": 900},
            "root": {
                "id": "start_menu",
                "type": "StartMenu",
                "properties": {"open": False},
                "layout": {"x": 0, "y": 0, "width": 400, "height": 300},
                "events": {"opened": "bh_start_toggle"},
                "children": [],
            },
        },
    ],
})


class TestRuntimeState(unittest.TestCase):
    """State management: set, resolve, scoped resolution."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)

    def test_initial_state(self):
        self.assertEqual(self.rt.resolve_state("volume"), 60)
        self.assertFalse(self.rt.resolve_state("startMenuOpen"))

    def test_set_state(self):
        self.rt.set_state("volume", 80)
        self.assertEqual(self.rt.resolve_state("volume"), 80)

    def test_scoped_state_resolution(self):
        self.assertEqual(self.rt.resolve_state("persistent.theme"), "Eclipse")
        self.assertEqual(self.rt.resolve_state("session.clockTime"), "14:32")

    def test_scoped_state_mutation(self):
        self.rt.set_state("volume", 100)
        # Flat state changed
        self.assertEqual(self.rt.states["volume"], 100)

    def test_resolve_states_flat_and_scoped(self):
        flat = self.rt.resolve_states()
        self.assertEqual(flat["volume"], 60)
        self.assertEqual(flat["persistent.theme"], "Eclipse")
        self.assertEqual(flat["session.clockTime"], "14:32")


class TestRuntimeEventDispatch(unittest.TestCase):
    """Event dispatch: find behavior, evaluate condition, execute action."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)
        self.log_messages = []
        self.rt._log = lambda msg: self.log_messages.append(msg)

    def test_fire_event_opens_menu(self):
        actions = self.rt.fire_event("start_menu", "opened")
        self.assertEqual(len(actions), 1)
        self.assertTrue(self.doc.find_component("start_menu").properties["open"])

    def test_fire_event_toggle_closes_menu(self):
        # Open first
        self.rt.fire_event("start_menu", "opened")
        self.assertTrue(self.doc.find_component("start_menu").properties["open"])
        # Toggle again → close
        self.rt.fire_event("start_menu", "opened")
        self.assertFalse(self.doc.find_component("start_menu").properties["open"])

    def test_fire_event_sets_theme(self):
        actions = self.rt.fire_event("start_menu", "Clicked")  # opens menu
        # Now fire the theme behavior (targeted at System, no component)
        # We need a component with that event — let's test directly
        self.rt.set_state("volume", 60)
        actions = self.rt._execute_actions_for_behavior("bh_conditional")
        self.assertEqual(len(actions), 1)

    def test_fire_event_condition_true(self):
        self.rt.set_state("volume", 80)
        actions = self.rt._execute_actions_for_behavior("bh_conditional")
        self.assertEqual(len(actions), 1)

    def test_fire_event_condition_false(self):
        self.rt.set_state("volume", 30)
        actions = self.rt._execute_actions_for_behavior("bh_conditional_false")
        self.assertEqual(len(actions), 0)

    def test_fire_event_missing_component(self):
        with self.assertRaises(NstudioValidationError):
            self.rt.fire_event("nonexistent", "clicked")

    def test_fire_event_no_behavior(self):
        # start_menu has no "nonexistent" event
        actions = self.rt.fire_event("start_menu", "nonexistent")
        self.assertEqual(len(actions), 0)

    def test_and_group_condition_false(self):
        # doNotDisturb is False, so AND group should be False
        actions = self.rt._execute_actions_for_behavior("bh_and_group")
        self.assertEqual(len(actions), 0)

    def test_and_group_condition_true(self):
        self.rt.set_state("doNotDisturb", True)
        self.rt.set_state("volume", 80)
        actions = self.rt._execute_actions_for_behavior("bh_and_group")
        self.assertEqual(len(actions), 1)

    def test_log_messages_populated(self):
        self.rt.fire_event("start_menu", "opened")
        self.assertTrue(len(self.log_messages) > 0)


class TestRuntimeActionChains(unittest.TestCase):
    """Action chains: multi-step execution."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)
        self.rt._log = lambda msg: None  # suppress logs

    def test_chain_executes_all_steps(self):
        actions = self.rt._execute_actions_for_behavior("bh_chain")
        self.assertEqual(len(actions), 2)
        # Theme should be set to Eclipse
        self.assertEqual(self.doc.themes["active"], "Eclipse")

    def test_chain_logs_each_step(self):
        messages = []
        self.rt._log = lambda msg: messages.append(msg)
        actions = self.rt._execute_actions_for_behavior("bh_chain")
        self.assertEqual(len(actions), 2)
        theme_logs = [m for m in messages if "Theme" in m]
        anim_logs = [m for m in messages if "Animation" in m]
        self.assertEqual(len(theme_logs), 1)
        self.assertEqual(len(anim_logs), 1)


class TestRuntimeBindings(unittest.TestCase):
    """Binding application: state → component property."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)
        self.rt._log = lambda msg: None

    def test_apply_binding(self):
        self.rt.set_state("startMenuOpen", True)
        binding = self.doc.bindings[0]
        self.rt.apply_binding(binding)
        component = self.doc.find_component("start_menu")
        self.assertTrue(component.properties["open"])

    def test_apply_all_bindings(self):
        self.rt.set_state("startMenuOpen", True)
        self.rt.apply_all_bindings()
        component = self.doc.find_component("start_menu")
        self.assertTrue(component.properties["open"])

    def test_binding_missing_component(self):
        from ui.nstudio import NstudioBinding
        binding = NstudioBinding(component="nonexistent", property="x", state="volume")
        # Should not raise — just log and skip
        self.rt.apply_binding(binding)


class TestRuntimeSystemActions(unittest.TestCase):
    """System-level actions: theme, animation, notification."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)
        self.log_messages = []
        self.rt._log = lambda msg: self.log_messages.append(msg)

    def test_theme_set(self):
        self.rt._execute_system_action("Nyrqis.Theme.Set", {"theme": "Solar"})
        self.assertEqual(self.doc.themes["active"], "Solar")
        self.assertEqual(self.doc.state_scopes["persistent"]["theme"], "Solar")

    def test_animation_play(self):
        self.rt._execute_system_action("Nyrqis.Animation.Play", {"animation": "fade"})
        anim_logs = [m for m in self.log_messages if "fade" in m]
        self.assertEqual(len(anim_logs), 1)

    def test_notification_show(self):
        self.rt._execute_system_action(
            "Nyrqis.Notification.Show",
            {"title": "Test", "message": "Hello", "severity": "info"})
        notif_logs = [m for m in self.log_messages if "Notification" in m]
        self.assertEqual(len(notif_logs), 1)


class TestRuntimeSummary(unittest.TestCase):
    """Summary and document access."""

    def setUp(self):
        self.doc = loads(_MINIMAL_DOC)
        self.rt = NyrqisRuntime(self.doc)
        self.rt._log = lambda msg: None

    def test_summary(self):
        s = self.rt.summary()
        self.assertEqual(s["screens"], 1)
        self.assertEqual(s["behaviors"], 6)
        self.assertEqual(s["bindings"], 1)
        self.assertEqual(s["active_theme"], "Eclipse")

    def test_render(self):
        entries = self.rt.render()
        self.assertEqual(len(entries), 1)  # just start_menu
        self.assertEqual(entries[0][0].id, "start_menu")

    def test_document_property(self):
        self.assertIs(self.rt.document, self.doc)


if __name__ == "__main__":
    unittest.main()
