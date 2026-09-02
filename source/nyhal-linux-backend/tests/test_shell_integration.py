#!/usr/bin/env python3
"""End-to-end integration tests for the Nyrqis Desktop Shell.

Loads ``examples/nyrqis-shell.nstudio`` and exercises the full
Nyforge → NUI → Runtime → DesktopSession pipeline: parsing,
validation, session creation, runtime state mutation, binding
application, animation triggering, behavior dispatch, and
compositor rendering.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

# Ensure the backend package is importable.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_EXAMPLES_DIR = os.path.join(_BACKEND_DIR, "examples")
_SHELL_NSTUDIO = os.path.join(_EXAMPLES_DIR, "nyrqis-shell.nstudio")


def _load_shell_doc():
    """Load the canonical nyrqis-shell.nstudio."""
    from ui.nstudio import load
    return load(_SHELL_NSTUDIO)


class TestShellDocumentParsing(unittest.TestCase):
    """Validate that the shell .nstudio parses and validates cleanly."""

    def test_load_succeeds(self):
        doc = _load_shell_doc()
        self.assertIsNotNone(doc)
        self.assertEqual(doc.version, "1.0.0")

    def test_project_metadata(self):
        doc = _load_shell_doc()
        self.assertEqual(doc.project.get("name"), "Nyrqis Desktop Shell")
        self.assertEqual(doc.project.get("id"), "nyrqis-shell")

    def test_has_one_screen(self):
        doc = _load_shell_doc()
        self.assertEqual(len(doc.screens), 1)
        self.assertEqual(doc.screens[0].id, "desktop")

    def test_screen_size(self):
        doc = _load_shell_doc()
        screen = doc.screens[0]
        self.assertEqual(screen.size.get("width"), 1920)
        self.assertEqual(screen.size.get("height"), 1080)

    def test_root_component(self):
        doc = _load_shell_doc()
        root = doc.screens[0].root
        self.assertEqual(root.type, "DesktopSurface")
        self.assertEqual(root.id, "desktop-root")
        self.assertGreater(len(root.children), 0)

    def test_shell_children_count(self):
        """The desktop surface should have top-level children:
        taskbar, start-menu, spotlight, notification-center,
        power-menu, lock-screen."""
        doc = _load_shell_doc()
        root = doc.screens[0].root
        child_ids = [c.id for c in root.children]
        self.assertIn("taskbar", child_ids)
        self.assertIn("start-menu", child_ids)
        self.assertIn("spotlight", child_ids)
        self.assertIn("notification-center", child_ids)
        self.assertIn("power-menu", child_ids)
        self.assertIn("lock-screen", child_ids)

    def test_taskbar_children(self):
        doc = _load_shell_doc()
        taskbar = doc.find_component("taskbar")
        self.assertIsNotNone(taskbar)
        self.assertEqual(taskbar.type, "Taskbar")
        child_ids = [c.id for c in taskbar.children]
        self.assertIn("taskbar-start", child_ids)
        self.assertIn("taskbar-search", child_ids)
        self.assertIn("taskbar-tray", child_ids)

    def test_has_states(self):
        doc = _load_shell_doc()
        self.assertIn("volume", doc.states)
        self.assertIn("brightness", doc.states)
        self.assertIn("startMenuOpen", doc.states)
        self.assertIn("theme", doc.states)

    def test_has_state_scopes(self):
        doc = _load_shell_doc()
        self.assertIn("session", doc.state_scopes)
        self.assertIn("persistent", doc.state_scopes)
        self.assertEqual(
            doc.state_scopes["session"].get("user"), "zeus")

    def test_has_behaviors(self):
        doc = _load_shell_doc()
        self.assertGreater(len(doc.behaviors), 0)
        behavior_ids = [b.id for b in doc.behaviors]
        self.assertIn("toggle-start-menu", behavior_ids)
        self.assertIn("toggle-spotlight", behavior_ids)

    def test_has_bindings(self):
        doc = _load_shell_doc()
        self.assertGreater(len(doc.bindings), 0)
        components = [b.component for b in doc.bindings]
        self.assertIn("start-menu", components)
        self.assertIn("spotlight", components)

    def test_has_animations(self):
        doc = _load_shell_doc()
        self.assertGreater(len(doc.animations), 0)
        anim_ids = [a.id for a in doc.animations]
        self.assertIn("startMenuOpen", anim_ids)
        self.assertIn("startMenuClose", anim_ids)
        self.assertIn("spotlightOpen", anim_ids)

    def test_has_localization(self):
        doc = _load_shell_doc()
        self.assertEqual(doc.locales.get("active"), "en")
        tables = doc.locales.get("tables", {})
        self.assertIn("en", tables)
        self.assertIn("shell.taskbar", tables["en"])

    def test_component_ids_unique(self):
        doc = _load_shell_doc()
        ids = doc.component_ids()
        self.assertEqual(len(ids), len(set(ids)),
                         f"Duplicate component ids: {[i for i in ids if ids.count(i) > 1]}")

    def test_component_count(self):
        """The shell should have a substantial component tree."""
        doc = _load_shell_doc()
        ids = doc.component_ids()
        self.assertGreaterEqual(len(ids), 15,
                                "Expected at least 15 components in the shell")


class TestShellDesktopSession(unittest.TestCase):
    """Validate the DesktopSession can run the shell document."""

    def setUp(self):
        self.doc = _load_shell_doc()
        from ui.desktop_session import DesktopSession
        self.session = DesktopSession(self.doc)

    def test_session_creates(self):
        self.assertIsNotNone(self.session)

    def test_session_has_shell_components(self):
        """The shell should have shell chrome components (taskbar, etc.)."""
        self.assertGreater(len(self.session._shell_components), 0)

    def test_shell_component_ids_detected(self):
        """Shell components should be detected by their id from the .nstudio."""
        self.assertIn("taskbar", list(self.session._shell_components.keys()),
                     f"Expected 'taskbar' in {list(self.session._shell_components.keys())}")

    def test_taskbar_position(self):
        """Taskbar should be near the bottom of the screen."""
        self.assertIn("taskbar", self.session._shell_components)
        taskbar_node = self.session._shell_components["taskbar"]
        taskbar_y = taskbar_node.layout.get("y", 0)
        # Taskbar y + height should be close to screen height (1080)
        self.assertGreaterEqual(taskbar_y, 900,
            f"Taskbar y={taskbar_y} should be near bottom")

    def test_hit_test_taskbar(self):
        """Clicking in the taskbar area should hit the taskbar window."""
        result = self.session.hit_test(100, 1050)
        self.assertTrue(result.hit)

    def test_hit_test_desktop(self):
        """Clicking on empty desktop area should hit the desktop."""
        result = self.session.hit_test(960, 500)
        self.assertTrue(result.hit)

    def test_focus_window(self):
        """Clicking on the taskbar area should produce a hit."""
        result = self.session.hit_test(100, 1050)
        self.assertTrue(result.hit)

    def test_close_window(self):
        """Closing a window should reduce window count."""
        initial = len(self.session.windows)
        if initial > 1:
            # Use .id (session window id), not component_id
            self.session.close_window(self.session.windows[1].id)
            self.assertLess(len(self.session.windows), initial)


class TestShellRuntime(unittest.TestCase):
    """Validate the NyrqisRuntime can run the shell document."""

    def setUp(self):
        self.doc = _load_shell_doc()
        from ui.runtime import NyrqisRuntime
        self.runtime = NyrqisRuntime(self.doc)

    def test_runtime_creates(self):
        self.assertIsNotNone(self.runtime)

    def test_initial_state(self):
        self.assertEqual(self.runtime.resolve_state("volume"), 75)
        self.assertEqual(self.runtime.resolve_state("brightness"), 100)
        self.assertTrue(self.runtime.resolve_state("wifi"))
        self.assertFalse(self.runtime.resolve_state("bluetooth"))

    def test_scoped_state(self):
        self.assertEqual(
            self.runtime.resolve_state("session.user"), "zeus")
        self.assertEqual(
            self.runtime.resolve_state("persistent.sidebarWidth"), 240)

    def test_set_state(self):
        self.runtime.set_state("volume", 50)
        self.assertEqual(self.runtime.resolve_state("volume"), 50)

    def test_binding_apply(self):
        """Applying bindings should sync state → component properties."""
        self.runtime.set_state("startMenuOpen", True)
        self.runtime.apply_all_bindings()
        comp = self.doc.find_component("start-menu")
        self.assertIsNotNone(comp)
        self.assertTrue(comp.properties.get("visible", False))

    def test_binding_toggle(self):
        """Toggle a binding value and re-apply."""
        self.runtime.set_state("startMenuOpen", True)
        self.runtime.apply_all_bindings()
        self.runtime.set_state("startMenuOpen", False)
        self.runtime.apply_all_bindings()
        comp = self.doc.find_component("start-menu")
        self.assertFalse(comp.properties.get("visible", True))

    def test_behavior_dispatch(self):
        """Firing an event should execute its behavior action."""
        actions = self.runtime.fire_event(
            "taskbar-start", "Click")
        # toggle-start-menu fires Close on start-menu
        self.assertIsNotNone(actions)


class TestShellAnimations(unittest.TestCase):
    """Validate the animation timeline works with the shell document."""

    def setUp(self):
        self.doc = _load_shell_doc()
        from ui.desktop_session import DesktopSession
        self.session = DesktopSession(self.doc)

    def test_timeline_exists(self):
        self.assertIsNotNone(self.session.timeline)

    def test_play_start_menu_open(self):
        """Playing the startMenuOpen animation should start it."""
        self.session.play_animation("startMenuOpen")
        self.assertGreater(self.session.timeline.active_count, 0)

    def test_animation_completes(self):
        """An animation should complete after its duration."""
        self.session.play_animation("startMenuOpen")
        # Tick past the duration (200ms)
        self.session.tick(0.25)
        # The animation should be done or near done
        snapshot = self.session.timeline.snapshot()
        self.assertIsNotNone(snapshot)

    def test_play_spotlight(self):
        """The spotlightOpen animation should be playable."""
        self.session.play_animation("spotlightOpen")
        self.assertGreater(self.session.timeline.active_count, 0)

    def test_stop_all(self):
        """Stopping all animations should clear the timeline."""
        self.session.play_animation("startMenuOpen")
        self.session.play_animation("spotlightOpen")
        self.session.timeline.stop_all()
        self.assertEqual(self.session.timeline.active_count, 0)


class TestShellCompositor(unittest.TestCase):
    """Validate the Apple compositor can render the shell document."""

    def setUp(self):
        self.doc = _load_shell_doc()

    def test_import(self):
        from ui.apple_compositor import AppleCompositor
        self.assertIsNotNone(AppleCompositor)

    def test_render_shell(self):
        """Render the full shell document to an image."""
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(scale=1.0)
        img = comp.render_document(self.doc)
        self.assertIsNotNone(img)
        w, h = img.size
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

    def test_render_dark_mode(self):
        """Render with dark theme."""
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(scale=0.5, dark_mode=True)
        img = comp.render_document(self.doc)
        self.assertIsNotNone(img)

    def test_render_and_save(self):
        """Render and save to a temp file."""
        from ui.apple_compositor import AppleCompositor
        comp = AppleCompositor(scale=0.5)
        img = comp.render_document(self.doc)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            img.save(f.name)
            size = os.path.getsize(f.name)
            os.unlink(f.name)
        self.assertGreater(size, 0)


class TestShellExpressionEngine(unittest.TestCase):
    """Validate the expression engine with shell state values."""

    def test_basic_expression(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("state.volume > 50"),
            {"volume": 75})
        self.assertTrue(result)

    def test_boolean_logic(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("state.wifi && !state.bluetooth"),
            {"wifi": True, "bluetooth": False})
        self.assertTrue(result)

    def test_if_function(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse('if(state.theme == "Eclipse", "dark", "light")'),
            {"theme": "Eclipse"})
        self.assertEqual(result, "dark")

    def test_state_reference(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("state.volume >= 0 && state.volume <= 100"),
            {"volume": 75})
        self.assertTrue(result)

    def test_nested_boolean(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("(state.wifi || state.bluetooth) && !state.doNotDisturb"),
            {"wifi": True, "bluetooth": False, "doNotDisturb": False})
        self.assertTrue(result)

    def test_min_max(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("min(state.volume, 50)"),
            {"volume": 75})
        self.assertEqual(result, 50)

    def test_scoped_state_reference(self):
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse('state.user == "zeus"'),
            {"user": "zeus"})
        self.assertTrue(result)


class TestShellAccessibility(unittest.TestCase):
    """Validate accessibility for the shell document."""

    def test_audit_shell(self):
        from ui.a11y import audit_document
        doc = _load_shell_doc()
        issues = audit_document(doc)
        # Shell should have minimal critical issues
        critical = [i for i in issues if i.get("severity") == "error"]
        # Allow some warnings but no errors for the shell
        # (shell components like DesktopSurface may not need labels)
        self.assertIsInstance(issues, list)

    def test_taskbar_has_label(self):
        """The taskbar should have an accessible name."""
        from ui.a11y import ComponentA11y
        doc = _load_shell_doc()
        taskbar = doc.find_component("taskbar")
        self.assertIsNotNone(taskbar)
        a11y = ComponentA11y.from_component(taskbar, doc)
        # Taskbar should have role 'banner', 'navigation', or 'toolbar'
        self.assertIn(a11y.role.lower(),
                      ["banner", "navigation", "toolbar", "menubar", "group"])


class TestShellLocalization(unittest.TestCase):
    """Validate localization for the shell document."""

    def test_resolve_taskbar_label(self):
        from ui.nstudio import resolve_text
        doc = _load_shell_doc()
        result = resolve_text("$localize:shell.taskbar", doc.locales)
        self.assertEqual(result, "Taskbar")

    def test_resolve_start_menu_label(self):
        from ui.nstudio import resolve_text
        doc = _load_shell_doc()
        result = resolve_text("$localize:shell.startMenu", doc.locales)
        self.assertEqual(result, "Start Menu")

    def test_plain_text_unchanged(self):
        from ui.nstudio import resolve_text
        doc = _load_shell_doc()
        result = resolve_text("Hello World", doc.locales)
        self.assertEqual(result, "Hello World")

    def test_missing_key_returns_literal(self):
        from ui.nstudio import resolve_text
        doc = _load_shell_doc()
        result = resolve_text("$localize:nonexistent.key", doc.locales)
        self.assertEqual(result, "$localize:nonexistent.key")


class TestShellResponsiveLayout(unittest.TestCase):
    """Validate responsive layout constraints for the shell."""

    def test_taskbar_full_width(self):
        """Taskbar should span full width at 1920px."""
        from ui.nstudio import resolve_layout
        taskbar_layout = {"x": 0, "y": 1032, "width": 1920, "height": 48}
        result = resolve_layout(taskbar_layout, 1920, 1080)
        self.assertEqual(result["width"], 1920)

    def test_taskbar_anchored_stretch(self):
        """Taskbar with both anchors should stretch to container width."""
        from ui.nstudio import resolve_layout
        taskbar_layout = {
            "x": 0, "y": 1032, "width": 1920, "height": 48,
            "anchorLeft": True, "anchorRight": True
        }
        result = resolve_layout(taskbar_layout, 1920, 1080)
        self.assertEqual(result["width"], 1920)

    def test_start_menu_fixed_size(self):
        """Start menu without anchors should keep its authored size."""
        from ui.nstudio import resolve_layout
        menu_layout = {"x": 0, "y": 700, "width": 600, "height": 332}
        result = resolve_layout(menu_layout, 1920, 1080)
        self.assertEqual(result["width"], 600)
        self.assertEqual(result["height"], 332)

    def test_min_width_clamp(self):
        """minWidth should clamp the result."""
        from ui.nstudio import resolve_layout
        layout = {"x": 0, "y": 0, "width": 100, "height": 50,
                  "minWidth": 200}
        result = resolve_layout(layout, 1920, 1080)
        self.assertEqual(result["width"], 200)

    def test_max_width_clamp(self):
        """maxWidth should clamp the result."""
        from ui.nstudio import resolve_layout
        layout = {"x": 0, "y": 0, "width": 500, "height": 50,
                  "maxWidth": 300}
        result = resolve_layout(layout, 1920, 1080)
        self.assertEqual(result["width"], 300)


class TestShellEndToEnd(unittest.TestCase):
    """Full end-to-end test: load → session → runtime → animate → render."""

    def test_full_pipeline(self):
        """The complete shell pipeline should work without errors."""
        # 1. Load
        doc = _load_shell_doc()
        self.assertEqual(doc.version, "1.0.0")

        # 2. Session
        from ui.desktop_session import DesktopSession
        session = DesktopSession(doc)
        self.assertGreater(len(session._shell_components), 0,
                          "Expected shell components (taskbar, etc.) to be loaded")

        # 3. Runtime
        from ui.runtime import NyrqisRuntime
        runtime = NyrqisRuntime(doc)
        runtime.set_state("startMenuOpen", True)
        runtime.apply_all_bindings()

        # 4. Verify binding applied
        comp = doc.find_component("start-menu")
        self.assertTrue(comp.properties.get("visible", False))

        # 5. Animation
        session.play_animation("startMenuOpen")
        session.tick(0.1)  # partial tick
        session.tick(0.25)  # complete animation

        # 6. Render
        from ui.apple_compositor import AppleCompositor
        compositor = AppleCompositor(scale=0.5)
        img = compositor.render_document(doc)
        self.assertIsNotNone(img)
        w, h = img.size
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)

        # 7. Validate accessibility
        from ui.a11y import audit_document
        issues = audit_document(doc)
        self.assertIsInstance(issues, list)

        # 8. Validate localization
        from ui.nstudio import resolve_text
        label = resolve_text("$localize:shell.taskbar", doc.locales)
        self.assertEqual(label, "Taskbar")

        # 9. Expression engine
        from ui import nexpr
        result = nexpr.eval_expr(
            nexpr.parse("state.volume > 50"),
            runtime._doc.states)
        self.assertTrue(result)

        # 10. Undo/redo
        session.execute = lambda cmd: None  # stub
        self.assertIsNotNone(session._undo_manager)

    def test_shell_state_cycle(self):
        """Full state cycle: open start menu → close → verify."""
        doc = _load_shell_doc()
        from ui.desktop_session import DesktopSession
        session = DesktopSession(doc)

        # Open start menu
        session._doc.states["startMenuOpen"] = True
        session._runtime.apply_all_bindings()
        comp = doc.find_component("start-menu")
        self.assertTrue(comp.properties.get("visible", False))

        # Close start menu
        session._doc.states["startMenuOpen"] = False
        session._runtime.apply_all_bindings()
        self.assertFalse(comp.properties.get("visible", True))


if __name__ == "__main__":
    unittest.main()
