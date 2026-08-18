#!/usr/bin/env python3
"""Tests for ui.shell.NyrqisShell — the Nyrqis Desktop Shell runner."""

import json
import os
import sys
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from ui.shell import NyrqisShell
from ui.nstudio import loads

# Fixture path: the Nyforge repo is a sibling of the Nyrqis repo
_NYFORGE_ROOT = os.path.normpath(os.path.join(_BACKEND_DIR, "..", "..", "..", "Nyforge", "Nyforge"))
_DESKTOP_FIXTURE = os.path.join(
    _NYFORGE_ROOT, "examples", "nyrqis-shell", "desktop.nstudio")

_MINIMAL_SHELL = json.dumps({
    "version": "0.4.0",
    "project": {"name": "Shell", "id": "shell", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"},
    "themes": {"active": "Eclipse", "overrides": {}},
    "states": {"volume": 60, "startMenuOpen": False, "doNotDisturb": False},
    "stateScopes": {"persistent": {"theme": "Eclipse"}, "session": {"clockTime": "14:32"}},
    "animations": [{"id": "fade", "target": "start_menu", "property": "opacity", "duration": 200, "easing": "ease-out"}],
    "locales": {"active": "en", "tables": {"en": {}}},
    "behaviors": [
        {"id": "bh_toggle", "condition": None,
         "action": {"target": "start_menu", "name": "Toggle", "arguments": {}}},
        {"id": "bh_theme_solar", "condition": None,
         "action": {"target": "System", "name": "Nyrqis.Theme.Set", "arguments": {"theme": "Solar"}}},
        {"id": "bh_chain", "condition": None,
         "actions": [
             {"target": "System", "name": "Nyrqis.Theme.Set", "arguments": {"theme": "Eclipse"}},
             {"target": "System", "name": "Nyrqis.Animation.Play", "arguments": {"animation": "fade"}},
         ]},
        {"id": "bh_conditional", "condition": {"expression": "state.volume > 50"},
         "action": {"target": "System", "name": "Nyrqis.Notification.Show",
                    "arguments": {"title": "Loud", "message": "Vol high"}}},
        {"id": "bh_and_group", "condition": {
            "logic": "and",
            "conditions": [
                {"expression": "state.doNotDisturb == true"},
                {"expression": "state.volume > 50"},
            ]},
         "action": {"target": "System", "name": "Nyrqis.Notification.Show",
                    "arguments": {"title": "DND+Loud"}}},
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
                "events": {"opened": "bh_toggle"},
                "children": [],
            },
        },
    ],
})


class TestNyrqisShellBasic(unittest.TestCase):
    """Basic shell operations: load, summary, text preview."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_from_json(self):
        self.assertIsNotNone(self.shell)
        self.assertIsNotNone(self.shell.runtime)

    def test_summary(self):
        s = self.shell.runtime.summary()
        self.assertEqual(s["screens"], 1)
        self.assertEqual(s["behaviors"], 5)
        self.assertEqual(s["bindings"], 1)
        self.assertEqual(s["active_theme"], "Eclipse")

    def test_text_preview(self):
        preview = self.shell.runtime.text_preview()
        self.assertIn("start_menu", preview)


class TestNyrqisShellRun(unittest.TestCase):
    """Run the shell and verify state + binding application."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_run_returns_ok(self):
        result = self.shell.run()
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["screens"], 1)

    def test_run_has_bindings_applied(self):
        result = self.shell.run()
        self.assertEqual(result["bindings_applied"], 1)

    def test_run_has_log(self):
        result = self.shell.run()
        self.assertIsInstance(result["log"], list)

    def test_run_has_final_states(self):
        result = self.shell.run()
        self.assertIn("volume", result["final_states"])
        self.assertEqual(result["final_states"]["volume"], 60)

    def test_run_has_text_preview(self):
        result = self.shell.run()
        self.assertIn("start_menu", result["text_preview"])


class TestNyrqisShellInteractive(unittest.TestCase):
    """Interactive event dispatch: fire events and verify state changes."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_fire_event_opens_menu(self):
        result = self.shell.run_interactive("start_menu", "opened")
        self.assertTrue(result["ok"])
        self.assertEqual(result["actions_executed"], 1)
        comp = self.shell.runtime.document.find_component("start_menu")
        self.assertTrue(comp.properties["open"])

    def test_fire_event_toggle_closes_menu(self):
        self.shell.run_interactive("start_menu", "opened")
        result = self.shell.run_interactive("start_menu", "opened")
        self.assertTrue(result["ok"])
        comp = self.shell.runtime.document.find_component("start_menu")
        self.assertFalse(comp.properties["open"])

    def test_fire_event_missing_component(self):
        result = self.shell.run_interactive("nonexistent", "clicked")
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["error"])


class TestNyrqisShellConditionalBehavior(unittest.TestCase):
    """Conditional behaviors: AND groups and expression conditions."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_conditional_true(self):
        self.shell.runtime.set_state("volume", 80)
        actions = self.shell.runtime._execute_actions_for_behavior("bh_conditional")
        self.assertEqual(len(actions), 1)

    def test_conditional_false(self):
        self.shell.runtime.set_state("volume", 30)
        actions = self.shell.runtime._execute_actions_for_behavior("bh_conditional")
        self.assertEqual(len(actions), 0)

    def test_and_group_false(self):
        actions = self.shell.runtime._execute_actions_for_behavior("bh_and_group")
        self.assertEqual(len(actions), 0)

    def test_and_group_true(self):
        self.shell.runtime.set_state("doNotDisturb", True)
        self.shell.runtime.set_state("volume", 80)
        actions = self.shell.runtime._execute_actions_for_behavior("bh_and_group")
        self.assertEqual(len(actions), 1)


class TestNyrqisShellActionChain(unittest.TestCase):
    """Action chains: multi-step execution."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_chain_executes_all_steps(self):
        actions = self.shell.runtime._execute_actions_for_behavior("bh_chain")
        self.assertEqual(len(actions), 2)
        self.assertEqual(
            self.shell.runtime.document.themes["active"], "Eclipse")


class TestNyrqisShellBindings(unittest.TestCase):
    """Binding application: state → component property."""

    def setUp(self):
        self.shell = NyrqisShell.from_json(_MINIMAL_SHELL)

    def test_binding_applied_on_run(self):
        self.shell.runtime.set_state("startMenuOpen", True)
        self.shell.run()
        comp = self.shell.runtime.document.find_component("start_menu")
        self.assertTrue(comp.properties["open"])

    def test_binding_applied_false(self):
        self.shell.run()
        comp = self.shell.runtime.document.find_component("start_menu")
        self.assertFalse(comp.properties["open"])


class TestNyrqisShellDesignFixture(unittest.TestCase):
    """Validate the real desktop.nstudio fixture through the shell runner."""

    def test_desktop_fixture_runs(self):
        if not os.path.exists(_DESKTOP_FIXTURE):
            self.skipTest("desktop.nstudio fixture not found")

        shell = NyrqisShell.from_file(_DESKTOP_FIXTURE)
        result = shell.run()

        self.assertTrue(result["ok"])
        self.assertGreater(result["summary"]["components"], 0)
        self.assertGreater(result["summary"]["behaviors"], 0)
        self.assertIn("text_preview", result)


if __name__ == "__main__":
    unittest.main()
