#!/usr/bin/env python3
"""Tests for theme engine, accessibility system, and updated demo."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


# ===================================================================
# Theme Engine Tests
# ===================================================================

class TestThemeEngine(unittest.TestCase):
    """Tests for ThemeEngine."""

    def setUp(self):
        from ui.theme_engine import ThemeEngine
        self.engine = ThemeEngine("Eclipse")

    def test_initial_state(self):
        self.assertEqual(self.engine.current_theme_name, "Eclipse")
        self.assertIsNotNone(self.engine.current_theme)

    def test_available_themes(self):
        themes = self.engine.available_themes
        self.assertIn("Eclipse", themes)
        self.assertIn("Solar", themes)
        self.assertIn("Dracula", themes)
        self.assertIn("Nord", themes)

    def test_set_theme(self):
        result = self.engine.set_theme("Solar")
        self.assertTrue(result)
        self.assertEqual(self.engine.current_theme_name, "Solar")

    def test_set_nonexistent(self):
        result = self.engine.set_theme("No Such Theme")
        self.assertFalse(result)

    def test_next_theme(self):
        initial = self.engine.current_theme_name
        new = self.engine.next_theme()
        self.assertNotEqual(new, initial)

    def test_previous_theme(self):
        initial = self.engine.current_theme_name
        new = self.engine.previous_theme()
        self.assertNotEqual(new, initial)

    def test_resolve_color(self):
        color = self.engine.color("accent")
        self.assertTrue(color.startswith("#"))
        self.assertEqual(len(color), 7)

    def test_resolve_metric(self):
        radius = self.engine.metric("border_radius")
        self.assertEqual(radius, 8)

    def test_rgb_tuple(self):
        r, g, b = self.engine.rgb("accent")
        self.assertGreater(r, 0)
        self.assertGreater(g, 0)
        self.assertGreater(b, 0)

    def test_rgba_tuple(self):
        r, g, b, a = self.engine.rgba("accent", 0.5)
        self.assertEqual(a, 127)

    def test_register_custom_theme(self):
        from ui.theme_engine import ThemeDefinition, ThemeMode
        custom = ThemeDefinition(
            name="Custom", mode=ThemeMode.DARK,
            colors={"accent": "#ff0000"},
        )
        result = self.engine.register_theme(custom)
        self.assertTrue(result)
        self.assertIn("Custom", self.engine.available_themes)

    def test_unregister_custom(self):
        from ui.theme_engine import ThemeDefinition, ThemeMode
        custom = ThemeDefinition(name="Removable", mode=ThemeMode.DARK)
        self.engine.register_theme(custom)
        result = self.engine.unregister_theme("Removable")
        self.assertTrue(result)
        self.assertNotIn("Removable", self.engine.available_themes)

    def test_cannot_unregister_builtin(self):
        result = self.engine.unregister_theme("Eclipse")
        self.assertFalse(result)

    def test_style_resolution(self):
        style = self.engine.style("Button")
        self.assertIn("bg", style)
        self.assertIn("fg", style)
        self.assertIn("accent", style)

    def test_export_import(self):
        data = self.engine.export_theme("Eclipse")
        self.assertIsNotNone(data)
        self.assertEqual(data["name"], "Eclipse")
        json_str = self.engine.export_json("Eclipse")
        self.assertIsNotNone(json_str)

    def test_import_theme(self):
        data = {
            "name": "Imported",
            "mode": "dark",
            "colors": {"accent": "#aabbcc"},
            "metrics": {"border_radius": 12},
        }
        theme = self.engine.import_theme(data)
        self.assertIsNotNone(theme)
        self.assertEqual(theme.name, "Imported")

    def test_import_json(self):
        data = {
            "name": "FromJSON",
            "mode": "light",
            "colors": {"bg_primary": "#ffffff"},
        }
        theme = self.engine.import_json(json.dumps(data))
        self.assertIsNotNone(theme)

    def test_wcag_check(self):
        results = self.engine.check_all_contrast()
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIn("ratio", r)
            self.assertGreater(r["ratio"], 0)

    def test_mode(self):
        from ui.theme_engine import ThemeMode
        self.assertEqual(self.engine.mode, ThemeMode.DARK)
        self.engine.set_theme("Solar")
        self.assertEqual(self.engine.mode, ThemeMode.LIGHT)

    def test_history(self):
        self.engine.set_theme("Solar")
        self.engine.set_theme("Dracula")
        history = self.engine.history
        self.assertIn("Eclipse", history)
        self.assertIn("Solar", history)
        self.assertIn("Dracula", history)

    def test_callback(self):
        events = []
        self.engine.on_event(lambda t, d: events.append((t, d)))
        self.engine.set_theme("Solar")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "theme_changed")

    def test_repr(self):
        r = repr(self.engine)
        self.assertIn("ThemeEngine", r)

    def test_builtin_theme_count(self):
        self.assertGreaterEqual(len(self.engine.available_themes), 4)


class TestColorUtils(unittest.TestCase):
    """Tests for color utility functions."""

    def test_hex_to_rgb(self):
        from ui.theme_engine import hex_to_rgb
        r, g, b = hex_to_rgb("#ff8800")
        self.assertEqual(r, 255)
        self.assertEqual(g, 136)
        self.assertEqual(b, 0)

    def test_hex_short(self):
        from ui.theme_engine import hex_to_rgb
        r, g, b = hex_to_rgb("#fff")
        self.assertEqual(r, 255)
        self.assertEqual(g, 255)
        self.assertEqual(b, 255)

    def test_rgb_to_hex(self):
        from ui.theme_engine import rgb_to_hex
        h = rgb_to_hex(255, 128, 0)
        self.assertEqual(h, "#ff8000")

    def test_contrast_ratio(self):
        from ui.theme_engine import contrast_ratio
        ratio = contrast_ratio("#ffffff", "#000000")
        self.assertAlmostEqual(ratio, 21.0, delta=0.1)

    def test_contrast_ratio_same(self):
        from ui.theme_engine import contrast_ratio
        ratio = contrast_ratio("#ffffff", "#ffffff")
        self.assertAlmostEqual(ratio, 1.0, delta=0.1)

    def test_lighten(self):
        from ui.theme_engine import lighten
        result = lighten("#000000", 0.5)
        self.assertTrue(result.startswith("#"))

    def test_darken(self):
        from ui.theme_engine import darken
        result = darken("#ffffff", 0.5)
        self.assertTrue(result.startswith("#"))

    def test_blend(self):
        from ui.theme_engine import blend_color
        mid = blend_color("#000000", "#ffffff", 0.5)
        r, g, b = int(mid[1:3], 16), int(mid[3:5], 16), int(mid[5:7], 16)
        self.assertAlmostEqual(r, 127, delta=2)


# ===================================================================
# Accessibility Tests
# ===================================================================

class TestScreenReader(unittest.TestCase):
    """Tests for ScreenReader."""

    def setUp(self):
        from ui.accessibility import ScreenReader
        self.sr = ScreenReader()

    def test_announce(self):
        self.sr.announce("Hello world")
        self.assertEqual(len(self.sr.queue), 1)
        self.assertEqual(self.sr.queue[0].text, "Hello world")

    def test_assertive_clears_queue(self):
        from ui.accessibility import AnnouncementPriority
        self.sr.announce("First")
        self.sr.announce("Urgent", priority=AnnouncementPriority.ASSERTIVE)
        self.assertEqual(len(self.sr.queue), 1)
        self.assertEqual(self.sr.queue[0].text, "Urgent")

    def test_say_shorthand(self):
        self.sr.say("Testing")
        self.assertEqual(len(self.sr.queue), 1)

    def test_muted(self):
        self.sr.muted = True
        self.sr.announce("Should not appear")
        self.assertEqual(len(self.sr.queue), 0)

    def test_disabled(self):
        self.sr.enabled = False
        self.sr.announce("Disabled")
        self.assertEqual(len(self.sr.queue), 0)

    def test_consume_next(self):
        self.sr.announce("Test")
        ann = self.sr.consume_next()
        self.assertIsNotNone(ann)
        self.assertEqual(ann.text, "Test")
        self.assertEqual(len(self.sr.queue), 0)

    def test_history(self):
        self.sr.announce("A")
        self.sr.announce("B")
        self.assertEqual(len(self.sr.history), 2)

    def test_read_element(self):
        from ui.accessibility import FocusableElement
        elem = FocusableElement(id="btn", role="button", label="OK")
        self.sr.read_element(elem)
        self.assertIn("OK", self.sr.queue[0].text)
        self.assertIn("button", self.sr.queue[0].text)

    def test_read_focus_change(self):
        from ui.accessibility import FocusableElement
        old = FocusableElement(id="a", role="button", label="A")
        new = FocusableElement(id="b", role="button", label="B")
        self.sr.read_focus_change(old, new)
        self.assertGreater(len(self.sr.queue), 0)

    def test_callback(self):
        events = []
        self.sr.on_event(lambda ann: events.append(ann.text))
        self.sr.announce("Event test")
        self.assertEqual(events, ["Event test"])


class TestFocusManager(unittest.TestCase):
    """Tests for FocusManager."""

    def setUp(self):
        from ui.accessibility import FocusManager, FocusableElement
        self.fm = FocusManager()
        for i in range(5):
            self.fm.register(FocusableElement(
                id=f"elem-{i}", role="button",
                label=f"Element {i}", tab_index=i,
                rect=(i * 100, 0, 80, 30),
            ))

    def test_register(self):
        self.assertEqual(len(self.fm.elements), 5)

    def test_unregister(self):
        result = self.fm.unregister("elem-0")
        self.assertTrue(result)
        self.assertEqual(len(self.fm.elements), 4)

    def test_focus(self):
        result = self.fm.focus("elem-2")
        self.assertTrue(result)
        self.assertEqual(self.fm.focused_id, "elem-2")

    def test_focus_nonexistent(self):
        result = self.fm.focus("no-such")
        self.assertFalse(result)

    def test_focus_first(self):
        self.fm.focus_first()
        self.assertIsNotNone(self.fm.focused)

    def test_focus_last(self):
        self.fm.focus_last()
        self.assertIsNotNone(self.fm.focused)

    def test_focus_next(self):
        self.fm.focus("elem-0")
        self.fm.focus_next()
        self.assertEqual(self.fm.focused_id, "elem-1")

    def test_focus_previous(self):
        self.fm.focus("elem-2")
        self.fm.focus_previous()
        self.assertEqual(self.fm.focused_id, "elem-1")

    def test_focus_wraps(self):
        self.fm.focus("elem-4")
        self.fm.focus_next()  # Should wrap to elem-0
        self.assertEqual(self.fm.focused_id, "elem-0")

    def test_focus_by_direction(self):
        from ui.accessibility import FocusDirection
        self.fm.focus("elem-0")
        result = self.fm.focus_by_direction(FocusDirection.RIGHT)
        self.assertTrue(result)

    def test_focusable_elements(self):
        focusable = self.fm.focusable_elements
        self.assertEqual(len(focusable), 5)

    def test_disabled_not_focusable(self):
        from ui.accessibility import FocusableElement
        self.fm.register(FocusableElement(
            id="disabled", role="button", enabled=False, tab_index=0))
        focusable = self.fm.focusable_elements
        ids = [e.id for e in focusable]
        self.assertNotIn("disabled", ids)

    def test_history(self):
        self.fm.focus("elem-0")
        self.fm.focus("elem-1")
        self.assertEqual(len(self.fm.history), 2)

    def test_clear(self):
        self.fm.focus("elem-0")
        self.fm.clear()
        self.assertIsNone(self.fm.focused)


class TestKeyboardManager(unittest.TestCase):
    """Tests for KeyboardManager."""

    def setUp(self):
        from ui.accessibility import KeyboardManager
        self.km = KeyboardManager.with_defaults()

    def test_defaults_registered(self):
        self.assertGreater(len(self.km.shortcuts), 10)

    def test_handle_shortcut(self):
        action = self.km.handle_key("Ctrl+T")
        self.assertEqual(action, "open_terminal")

    def test_handle_unknown(self):
        action = self.km.handle_key("Ctrl+Z")
        self.assertIsNone(action)

    def test_register_custom(self):
        kb = self.km.register("Ctrl+Shift+X", "custom_action", "Custom")
        self.assertIsNotNone(kb)
        action = self.km.handle_key("Ctrl+Shift+X")
        self.assertEqual(action, "custom_action")

    def test_unregister(self):
        kb = self.km.register("Ctrl+X", "test")
        result = self.km.unregister(kb.id)
        self.assertTrue(result)
        action = self.km.handle_key("Ctrl+X")
        self.assertIsNone(action)

    def test_disabled(self):
        self.km.enabled = False
        action = self.km.handle_key("Ctrl+T")
        self.assertIsNone(action)

    def test_shortcuts_by_category(self):
        apps = self.km.shortcuts_by_category("apps")
        self.assertGreater(len(apps), 0)

    def test_history(self):
        self.km.handle_key("Ctrl+T")
        self.km.handle_key("Ctrl+E")
        self.assertEqual(len(self.km.history), 2)

    def test_callback(self):
        events = []
        self.km.on_event(lambda t, d: events.append(t))
        self.km.handle_key("Ctrl+T")
        self.assertIn("shortcut_activated", events)

    def test_find_shortcut(self):
        kb = self.km.get_shortcut("Ctrl+T")
        self.assertIsNotNone(kb)
        self.assertEqual(kb.action, "open_terminal")


class TestAccessibilitySystem(unittest.TestCase):
    """Tests for AccessibilitySystem."""

    def setUp(self):
        from ui.accessibility import AccessibilitySystem
        self.a11y = AccessibilitySystem()

    def test_initial_state(self):
        self.assertFalse(self.a11y.high_contrast)
        self.assertFalse(self.a11y.reduce_motion)
        self.assertFalse(self.a11y.large_text)
        self.assertEqual(self.a11y.magnifier_zoom, 1.0)

    def test_high_contrast(self):
        self.a11y.set_high_contrast(True)
        self.assertTrue(self.a11y.high_contrast)
        self.assertEqual(self.a11y.focus.ring.width, 4)

    def test_reduce_motion(self):
        self.a11y.set_reduce_motion(True)
        self.assertTrue(self.a11y.reduce_motion)

    def test_large_text(self):
        self.a11y.set_large_text(True)
        self.assertTrue(self.a11y.large_text)
        self.assertAlmostEqual(self.a11y.text_scale(), 1.25)

    def test_magnifier_zoom(self):
        self.a11y.set_magnifier_zoom(2.0)
        self.assertEqual(self.a11y.magnifier_zoom, 2.0)
        self.assertAlmostEqual(self.a11y.text_scale(), 2.0)

    def test_zoom_in(self):
        zoom = self.a11y.zoom_in()
        self.assertGreater(zoom, 1.0)

    def test_zoom_out(self):
        self.a11y.set_magnifier_zoom(2.0)
        zoom = self.a11y.zoom_out()
        self.assertLess(zoom, 2.0)

    def test_zoom_clamp(self):
        self.a11y.set_magnifier_zoom(0.5)  # Below minimum
        self.assertEqual(self.a11y.magnifier_zoom, 1.0)

    def test_zoom_reset(self):
        self.a11y.set_magnifier_zoom(3.0)
        self.a11y.zoom_reset()
        self.assertEqual(self.a11y.magnifier_zoom, 1.0)

    def test_register_focusable(self):
        from ui.accessibility import FocusableElement
        elem = FocusableElement(id="btn", role="button", label="OK")
        self.a11y.register_focusable(elem)
        self.assertIsNotNone(self.a11y.focus.get("btn"))

    def test_focus_element(self):
        from ui.accessibility import FocusableElement
        self.a11y.register_focusable(FocusableElement(
            id="a", role="button", label="A"))
        self.a11y.register_focusable(FocusableElement(
            id="b", role="button", label="B"))
        self.a11y.focus_element("b")
        self.assertEqual(self.a11y.focused.id, "b")

    def test_announce(self):
        self.a11y.announce("Test announcement")
        self.assertEqual(len(self.a11y.screen_reader.queue), 1)

    def test_register_shortcut(self):
        kb = self.a11y.register_shortcut("Ctrl+X", "test_action")
        self.assertIsNotNone(kb)

    def test_handle_shortcut(self):
        action = self.a11y.handle_shortcut("Ctrl+T")
        self.assertEqual(action, "open_terminal")

    def test_audit(self):
        from ui.accessibility import FocusableElement
        self.a11y.register_focusable(FocusableElement(
            id="no-label", role="button"))
        issues = self.a11y.audit_focusable()
        self.assertGreater(len(issues), 0)

    def test_summary(self):
        s = self.a11y.summary()
        self.assertIn("high_contrast", s)
        self.assertIn("magnifier_zoom", s)
        self.assertIn("shortcut_count", s)

    def test_reading_mode(self):
        from ui.accessibility import ReadingMode
        self.a11y.set_reading_mode(ReadingMode.SCREEN_READER)
        self.assertEqual(self.a11y.reading_mode, ReadingMode.SCREEN_READER)

    def test_screen_reader_mode(self):
        self.a11y.set_screen_reader_mode(True)
        self.assertTrue(self.a11y.screen_reader.enabled)

    def test_callback(self):
        events = []
        self.a11y.on_event(lambda t, d: events.append(t))
        self.a11y.set_high_contrast(True)
        self.assertIn("mode_changed", events)

    def test_repr(self):
        r = repr(self.a11y)
        self.assertIn("AccessibilitySystem", r)


if __name__ == "__main__":
    unittest.main()
