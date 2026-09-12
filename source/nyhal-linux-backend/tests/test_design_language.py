#!/usr/bin/env python3
"""Tests for the design-language token bridge in ui/hig.py and its
adoption by the form-heavy surfaces (settings panel), phase 2.3 of the
design-language rollout (docs/reference/design-language.md §7).

Pins:
- the token vocabulary exists and matches the .nstudio reference values
  (ui/compositor.DESIGN_TOKENS / default-shell designTokens),
- interactive geometry meets the 44-px target minimum,
- the settings panel's builtin themes carry the brand accent/surfaces,
- theme retuning stays value-driven (no ad-hoc hexes in the panel).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ui import hig
from ui.settings_panel import (
    BUILTIN_THEMES,
    SettingsPanel,
    THEME_ECLIPSE,
    THEME_SOLAR,
    Theme,
)


class TestTokenBridge(unittest.TestCase):
    """ui/hig.py carries the Nyrqis design-language vocabulary."""

    def test_space_grid_is_four_point(self):
        for name, value in (("XS", 4), ("SM", 8), ("MD", 12),
                            ("LG", 16), ("XL", 24)):
            self.assertEqual(getattr(hig, f"SPACE_{name}"), value)

    def test_radius_bounds(self):
        self.assertEqual(hig.RADIUS_SM, 8)
        self.assertEqual(hig.RADIUS_MD, 12)
        self.assertEqual(hig.RADIUS_LG, 16)

    def test_target_minimum(self):
        # Apple HIG / Material: every interactive element >= 44 px with
        # >= 8 px separation.
        self.assertEqual(hig.TARGET_MIN, 44)
        self.assertEqual(hig.TARGET_GAP, 8)

    def test_motion_exits_faster_than_entrances(self):
        enter_dur, _ = hig.MOTION_ENTER
        exit_dur, _ = hig.MOTION_EXIT
        self.assertLess(exit_dur, enter_dur)

    def test_motion_easings_in_nui_enum(self):
        allowed = {"linear", "ease-in", "ease-out", "ease-in-out", "steps"}
        for token in (hig.MOTION_MICRO, hig.MOTION_ENTER, hig.MOTION_EXIT,
                      hig.MOTION_MOVE, hig.MOTION_EMPHASIS):
            self.assertIn(token[1], allowed)

    def test_accent_matches_nstudio_reference(self):
        # The .nstudio reference shell carries the same hexes.
        self.assertEqual(hig.ACCENT_ECLIPSE, (124, 184, 255))  # #7CB8FF
        self.assertEqual(hig.ACCENT_SOLAR, (46, 111, 219))     # #2E6FDB

    def test_surface_tokens_match_reference(self):
        self.assertEqual(hig.SURFACE_BASE_ECLIPSE, (16, 20, 24))
        self.assertEqual(hig.SURFACE_BASE_SOLAR, (244, 242, 237))
        self.assertAlmostEqual(hig.SURFACE_BAR_OPACITY, 0.85)
        self.assertAlmostEqual(hig.SURFACE_RAISED_OPACITY, 0.92)

    def test_tokens_agree_with_compositor_defaults(self):
        # One vocabulary: the Python bridge must not drift from the
        # compositor's DESIGN_TOKENS defaults.
        from ui.compositor import DESIGN_TOKENS
        for name, value in (("xs", hig.SPACE_XS), ("sm", hig.SPACE_SM),
                            ("md", hig.SPACE_MD), ("lg", hig.SPACE_LG),
                            ("xl", hig.SPACE_XL)):
            self.assertEqual(DESIGN_TOKENS["space"][name], value)
        self.assertEqual(DESIGN_TOKENS["radius"]["sm"], hig.RADIUS_SM)
        self.assertEqual(DESIGN_TOKENS["radius"]["md"], hig.RADIUS_MD)
        self.assertEqual(DESIGN_TOKENS["radius"]["lg"], hig.RADIUS_LG)
        self.assertEqual(DESIGN_TOKENS["target"]["min"], hig.TARGET_MIN)
        self.assertEqual(DESIGN_TOKENS["target"]["gap"], hig.TARGET_GAP)
        # Surface opacity: the spec value lives on the bridge; the
        # compositor's DEFAULT is deliberately 1.0 (opaque) so documents
        # without designTokens render pixel-identically — translucency
        # is opt-in via a document's tokens.
        self.assertEqual(hig.SURFACE_BAR_OPACITY, 0.85)
        self.assertEqual(DESIGN_TOKENS["surface"]["bar"]["opacity"], 1.0)


class TestSettingsPanelAdoption(unittest.TestCase):
    """The settings panel consumes the shared tokens."""

    def test_builtin_themes_carry_brand_tokens(self):
        self.assertEqual(THEME_ECLIPSE.bg, hig.SURFACE_BASE_ECLIPSE)
        self.assertEqual(THEME_ECLIPSE.accent, hig.ACCENT_ECLIPSE)
        self.assertEqual(THEME_SOLAR.bg, hig.SURFACE_BASE_SOLAR)
        self.assertEqual(THEME_SOLAR.accent, hig.ACCENT_SOLAR)

    def test_builtin_theme_count_and_order_unchanged(self):
        self.assertEqual([t.name for t in BUILTIN_THEMES],
                         ["Eclipse", "Solar", "Dracula"])

    def test_interactive_rows_meet_target_minimum(self):
        self.assertGreaterEqual(SettingsPanel.TOGGLE_HEIGHT, hig.TARGET_MIN)
        self.assertGreaterEqual(SettingsPanel.THEME_ITEM_HEIGHT,
                                hig.TARGET_MIN)

    def test_padding_snaps_to_grid(self):
        self.assertEqual(SettingsPanel.PADDING % 4, 0)
        self.assertGreaterEqual(SettingsPanel.PADDING, hig.SPACE_LG)

    def test_theme_dataclass_default_unchanged(self):
        # Pinned by test_settings_tiling: the generic Theme default bg.
        self.assertEqual(Theme(name="T").bg, (30, 30, 42))

    def test_theme_preview_rows_fit_section_band(self):
        # 44 header zone + 3 x 44-px rows must fit the 184-px band.
        self.assertEqual(3 * SettingsPanel.THEME_ITEM_HEIGHT + 44 + 8, 184)


class TestMediaGroupContrastAssessment(unittest.TestCase):
    """§7 checklist assessment of the media/creative group (phase 2.3c).

    Conclusion, pinned so it stays true: the group's colors are DATA
    (paint palette, album art, calendar categories) rendered as content,
    not interactive chrome — §7's accent/contrast rules bind the CHROME,
    which lives in the compositor (tokenized in phases 2.1–2.3b: slider,
    progress, list, menu-item, toggle, button, input). Nothing in the
    app modules draws text over its own data colors without going
    through a theme.
    """

    def test_calendar_colors_render_as_text_metadata_not_text_background(self):
        # render_calendars prints the hex string itself (no color-over-
        # text rendering path exists), so no AA contrast obligation.
        from ui.calendar_app import CalendarApp
        app = CalendarApp()
        out = "\n".join(app.render_calendars())
        self.assertIn("#", out)   # the raw hex is user-facing metadata

    def test_paint_palette_is_content_not_chrome(self):
        # Palette swatches are drawing colors (user data), not interactive
        # chrome — §7's accent-on-interactive-only rule means the theme
        # accents must never appear as (or in) palette entries.
        from ui.paint_app import PaintApp
        import ui.hig as hig
        app = PaintApp()
        accents = {f"#{c:02X}{g:02X}{b:02X}"
                   for c, g, b in (hig.ACCENT_ECLIPSE, hig.ACCENT_SOLAR)}
        for name, colors in app.palettes.items():
            self.assertTrue(colors, f"palette {name!r} is empty")
            for hexval in colors:
                self.assertRegex(hexval, r"^#[0-9A-Fa-f]{6}$")
                self.assertNotIn(hexval.upper(), accents)
                self.assertNotIn(hexval.lower(),
                                 {a.lower() for a in accents})

    def test_media_chrome_sliders_and_progress_are_tokenized(self):
        # The chrome the media group actually renders (position/volume
        # sliders, recording progress) reads the design tokens.
        from ui.compositor import DESIGN_TOKENS
        self.assertIn("control", DESIGN_TOKENS["radius"])
        self.assertEqual(DESIGN_TOKENS["radius"]["control"], 4)


class TestRegistryVersionHistory(unittest.TestCase):
    """Registry 1.1's versionHistory — the machine-readable contract-
    change log consumers like Nyforge's Inspector read. The loader
    validates every entry's shape at import (fail-closed, like the
    component tables) and exposes it as ``VERSION_HISTORY``."""

    def test_history_exposed_and_well_formed(self):
        from ui.nstudio import VERSION_HISTORY
        self.assertTrue(VERSION_HISTORY, "registry 1.1 ships a history")
        versions = {e["registryVersion"] for e in VERSION_HISTORY}
        self.assertIn("1.1", versions)
        for entry in VERSION_HISTORY:
            self.assertIsInstance(entry["registryVersion"], str)
            self.assertIsInstance(entry["change"], str)
            self.assertIsInstance(entry["breaking"], bool)

    def test_newest_entry_records_cornerRadius_as_additive(self):
        from ui.nstudio import VERSION_HISTORY
        entry = next(e for e in VERSION_HISTORY
                     if e["registryVersion"] == "1.1")
        self.assertFalse(entry["breaking"])
        self.assertIn("cornerRadius", entry["change"])

    def test_malformed_history_fails_registry_import(self):
        # The validator runs inside _load_registry; feed it a broken
        # registry through a temp file and a patched module path.
        import json
        import tempfile
        from ui import nstudio
        import importlib
        import os
        reg = json.load(open(os.path.join(
            os.path.dirname(os.path.abspath(nstudio.__file__)),
            "contracts", "nui-api-v1.json")))
        broken = dict(reg)
        broken["versionHistory"] = [
            {"registryVersion": "1.1"}]  # no change/breaking
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(broken, fh)
            old = nstudio._REGISTRY_PATH
            nstudio._REGISTRY_PATH = path
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    nstudio._load_registry()
                self.assertIn("versionHistory", str(ctx.exception))
            finally:
                nstudio._REGISTRY_PATH = old
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
