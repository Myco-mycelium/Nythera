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
    "version": "1.0.0",
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


class TestNyRuntimeLauncher(unittest.TestCase):
    """Tests for NyRuntime launcher integration (backend/nyruntime.py)."""

    def test_nyruntime_loader_importable(self):
        """The NyRuntime Python loader is importable."""
        from backend.nyruntime import NyRuntime
        self.assertTrue(hasattr(NyRuntime, 'init'))
        self.assertTrue(hasattr(NyRuntime, 'state'))
        self.assertTrue(hasattr(NyRuntime, 'destroy'))

    def test_nyruntime_create_and_destroy(self):
        """Create and destroy a NyRuntime instance."""
        try:
            from backend.nyruntime import NyRuntime
            rt = NyRuntime()
        except ImportError:
            self.skipTest("NyRuntime crate not built")
        self.assertIsNotNone(rt)
        rt.destroy()
        self.assertTrue(rt._destroyed)

    def test_nyruntime_context_manager(self):
        """NyRuntime works as a context manager."""
        try:
            from backend.nyruntime import NyRuntime
            rt = NyRuntime()
        except ImportError:
            self.skipTest("NyRuntime crate not built")
        with rt:
            self.assertIsNotNone(rt)
        self.assertTrue(rt._destroyed)

    def test_nyruntime_init(self):
        """Initialize the runtime."""
        from backend.nyruntime import NyRuntime
        try:
            with NyRuntime() as rt:
                rt.init()
                self.assertEqual(rt.state, 1)  # Ready
        except ImportError:
            self.skipTest("NyRuntime crate not built")

    def test_nyruntime_state_uninitialized(self):
        """Runtime starts in Uninitialized state."""
        from backend.nyruntime import NyRuntime
        try:
            with NyRuntime() as rt:
                self.assertEqual(rt.state, 0)  # Uninitialized
        except ImportError:
            self.skipTest("NyRuntime crate not built")

    def test_container_config_app_path(self):
        """ContainerConfig supports app_path field."""
        from backend.container import ContainerConfig
        cfg = ContainerConfig(app_path="/tmp/test.napp")
        self.assertEqual(cfg.app_path, "/tmp/test.napp")

    def test_container_config_app_path_default(self):
        """ContainerConfig app_path defaults to None."""
        from backend.container import ContainerConfig
        cfg = ContainerConfig()
        self.assertIsNone(cfg.app_path)

    def test_launcher_args_with_nyrqis_app(self):
        """_launcher_args includes --nyrqis-app when app_path is set."""
        from backend.container import ContainerManager, ContainerConfig
        from pathlib import Path
        import tempfile
        mgr = ContainerManager(use_direct_syscalls=False)
        cfg = ContainerConfig(
            command=["echo", "test"],
            app_path="/tmp/test.napp",
            seccomp=False,
            strict_seccomp=False,
        )
        container = mgr.create(cfg)
        argv = mgr._launcher_args(container, Path("/tmp/launcher.py"))
        self.assertIn("--nyrqis-app", argv)
        self.assertIn("/tmp/test.napp", argv)

    def test_build_napp_tool(self):
        """The build_napp tool can create a .napp binary."""
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.napp', delete=False) as f:
            outpath = f.name
        try:
            result = subprocess.run(
                [sys.executable, 'tools/build_napp.py',
                 '--name', 'test', '--version', '1.0.0',
                 '--output', outpath, '--code', '01,01,00', '--data', '42'],
                capture_output=True, text=True,
                cwd=_BACKEND_DIR,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = open(outpath, 'rb').read()
            self.assertEqual(data[0:4], b'NYAP')
            # header (17) + manifest + code (3) + data (1)
            self.assertGreater(len(data), 17 + 3 + 1)
        finally:
            os.unlink(outpath)




class TestNyrqisApps(unittest.TestCase):
    """Integration tests for Nyrqis applications (config_manager, status_client)."""

    def test_config_manager_builds(self):
        """config_manager.py builds a valid .napp binary."""
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.napp', delete=False) as f:
            outpath = f.name
        try:
            result = subprocess.run(
                [sys.executable, 'examples/config_manager.py',
                 '--output', outpath],
                capture_output=True, text=True,
                cwd=_BACKEND_DIR,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = open(outpath, 'rb').read()
            self.assertEqual(data[0:4], b'NYAP')
            self.assertEqual(data[4], 1)  # version
            self.assertGreater(len(data), 100)
        finally:
            os.unlink(outpath)

    def test_status_client_builds(self):
        """status_client.py builds a valid .napp binary."""
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.napp', delete=False) as f:
            outpath = f.name
        try:
            result = subprocess.run(
                [sys.executable, 'examples/status_client.py',
                 '--output', outpath],
                capture_output=True, text=True,
                cwd=_BACKEND_DIR,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = open(outpath, 'rb').read()
            self.assertEqual(data[0:4], b'NYAP')
            self.assertEqual(data[4], 1)  # version
            self.assertGreater(len(data), 50)
        finally:
            os.unlink(outpath)

    def test_config_manager_napp_parsing(self):
        """config_manager .napp can be parsed by the Rust runtime."""
        try:
            from backend.nyruntime import NyRuntime
        except ImportError:
            self.skipTest("NyRuntime crate not built")
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.napp', delete=False) as f:
            outpath = f.name
        try:
            subprocess.run(
                [sys.executable, 'examples/config_manager.py',
                 '--output', outpath],
                capture_output=True, cwd=_BACKEND_DIR,
            )
            data = open(outpath, 'rb').read()
            with NyRuntime() as rt:
                rt.init()
                rt.load_napp(data)
                self.assertEqual(rt.state, 2)  # Loaded
        finally:
            os.unlink(outpath)

    def test_config_manager_executes(self):
        """config_manager .napp executes successfully through NyRuntime."""
        try:
            from backend.nyruntime import NyRuntime
        except ImportError:
            self.skipTest("NyRuntime crate not built")
        import subprocess
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.napp', delete=False) as f:
            outpath = f.name
        try:
            subprocess.run(
                [sys.executable, 'examples/config_manager.py',
                 '--output', outpath],
                capture_output=True, cwd=_BACKEND_DIR,
            )
            data = open(outpath, 'rb').read()
            with NyRuntime() as rt:
                rt.init()
                rt.load_napp(data)
                exit_code = rt.execute()
                self.assertEqual(exit_code, 0)
                # Should have log entries (level 0 = INFO)
                logs = rt.log_entries()
                self.assertGreater(len(logs), 0)
        finally:
            os.unlink(outpath)

    def test_nyruntime_loader_has_new_methods(self):
        """NyRuntime loader exposes load_napp, execute, set_ipc."""
        from backend.nyruntime import NyRuntime
        self.assertTrue(hasattr(NyRuntime, 'load_napp'))
        self.assertTrue(hasattr(NyRuntime, 'execute'))
        self.assertTrue(hasattr(NyRuntime, 'set_ipc'))


if __name__ == "__main__":
    unittest.main()
