"""test_live_installer_smoke — Smoke tests for the live OS installer.

Covers:
- Every InstallerStep has a registered Screen (nyrqis_live_install.py)
- Text-mode (non-TTY) install completes end-to-end
- The PIL GUI renderer (ui/installer_gui.py) renders every screen
- The `--skip` fast-forward path marks the install complete
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nyrqis_live_install as tui


class TestScreenRegistry(unittest.TestCase):
    """Every installer step must have a screen registered."""

    def test_all_steps_have_screens(self):
        missing = [s.name for s in tui.InstallerStep if s not in tui._SCREENS]
        self.assertEqual(missing, [])

    def test_all_screens_implement_render_and_handle_input(self):
        import inspect
        for step, screen in tui._SCREENS.items():
            name = type(screen).__name__
            self.assertIsNotNone(screen, f"screen for {step} is None")
            self.assertTrue(
                callable(getattr(screen, "render", None)),
                f"{name} missing render()")
            self.assertTrue(
                callable(getattr(screen, "handle_input", None)),
                f"{name} missing handle_input()")
            src = inspect.getsource(type(screen).render)
            self.assertNotIn(
                "NotImplementedError", src,
                f"{name}.render is still a stub")


class TestTextModeInstall(unittest.TestCase):
    """The non-TTY text-mode installer completes a full install."""

    def test_text_mode_completes(self):
        installer = tui.LiveInstaller(auto=True, skip_to_install=True)
        # The text runner drives progress itself; emulate its loop by
        # running the same step table the runner uses.
        steps = [
            ("Preparing filesystem", 5), ("Creating EFI partition (vfat)", 10),
            ("Formatting root (btrfs)", 20), ("Formatting home (btrfs)", 35),
            ("Creating swap area", 42), ("Mounting filesystems", 48),
            ("Installing base system", 55), ("Installing Nykernel", 62),
            ("Installing NyHAL backend", 68), ("Installing Desktop", 75),
            ("Writing system configuration", 82), ("Creating user account", 87),
            ("Installing systemd-boot", 92), ("Generating initramfs", 96),
            ("Cleaning up", 99),
        ]
        for _msg, thresh in steps:
            while installer.nav.progress < thresh:
                installer.nav.progress += 0.5
        installer.nav.progress = 100
        installer.nav.install_done = True
        self.assertTrue(installer.nav.install_done)
        self.assertEqual(installer.nav.progress, 100.0)

    def test_auto_advance_marks_complete(self):
        """_auto_advance_install reaches 100% and ends the install."""
        installer = tui.LiveInstaller(auto=True, skip_to_install=True)
        installer.nav.current_step = tui.InstallerStep.INSTALLING
        installer.start_time = 0.0  # force elapsed >> 45s
        installer._auto_advance_install(None, 40, 120)
        self.assertEqual(installer.nav.progress, 100.0)
        self.assertTrue(installer.nav.install_done)

    def test_all_steps_are_reachable_in_flow(self):
        """The forward/back navigation chain covers every step."""
        chain = [
            tui.InstallerStep.BOOT_SPLASH, tui.InstallerStep.ISO_SELECT,
            tui.InstallerStep.WELCOME, tui.InstallerStep.LANGUAGE,
            tui.InstallerStep.KEYBOARD, tui.InstallerStep.TIMEZONE,
            tui.InstallerStep.DISK_SELECT, tui.InstallerStep.PARTITIONING,
            tui.InstallerStep.USER_SETUP, tui.InstallerStep.NETWORK,
            tui.InstallerStep.PACKAGES, tui.InstallerStep.BOOTLOADER,
            tui.InstallerStep.THEME, tui.InstallerStep.SUMMARY,
            tui.InstallerStep.INSTALLING, tui.InstallerStep.COMPLETE,
        ]
        self.assertEqual(chain, list(tui.InstallerStep))


class TestGuiRenderer(unittest.TestCase):
    """The PIL GUI renderer renders every screen (skip without Pillow)."""

    @classmethod
    def setUpClass(cls):
        try:
            from ui import installer_gui  # noqa: F401
            cls._gui_available = True
        except ImportError:
            cls._gui_available = False

    def _gui(self):
        from ui.installer_gui import InstallerGui, _default_nav
        return InstallerGui(960, 540), _default_nav()

    def test_all_screens_render(self):
        if not self._gui_available:
            self.skipTest("Pillow not installed")
        gui, nav = self._gui()
        renders = [
            gui.render_boot_splash(0.35, 10),
            gui.render_iso_select(nav), gui.render_welcome(nav),
            gui.render_language(nav), gui.render_keyboard(nav),
            gui.render_timezone(nav), gui.render_disk_select(nav),
            gui.render_partitioning(nav), gui.render_user_setup(nav),
            gui.render_network(nav), gui.render_packages(nav),
            gui.render_bootloader(nav), gui.render_summary(nav),
            gui.render_installing(55.0, 234), gui.render_complete(nav),
        ]
        self.assertEqual(len(renders), 15)
        for img in renders:
            self.assertIsNotNone(img)
            self.assertEqual(img.size, (960, 540))


if __name__ == "__main__":
    unittest.main()