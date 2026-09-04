"""
Tests for Printer Manager, Certificate Manager, and Color Calibration.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.printer_manager import (
    PrinterManager, Printer, PrintJob, InkLevel, PaperTray, DriverInfo,
    PrinterState, JobState, PrinterType, PaperSize, DuplexMode,
    PrintColorMode, MediaType,
)
from ui.certificate_manager import (
    CertificateManager, Certificate, CSRRequest, CertChain, CertEvent,
    CertType, CertStatus, KeyAlgorithm, SignatureAlgorithm, TrustStore,
)
from ui.color_calibration import (
    ColorCalibrationManager, MonitorCalibration, ICCProfile,
    NightLightSchedule, CalibrationStep, RGBColor,
    MonitorType, ColorSpace, CalibrationPreset, HDRMode, DitherMode, GammaType,
)


# ─── Printer Manager Tests ────────────────────────────────────────────────


class TestInkLevel(unittest.TestCase):
    def test_create(self):
        ink = InkLevel("Black", 850, 1000)
        self.assertEqual(ink.percent, 85.0)

    def test_bar(self):
        ink = InkLevel("Black", 50, 100)
        bar = ink.bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_status_icon_full(self):
        ink = InkLevel("Black", 80, 100)
        self.assertEqual(ink.status_icon, "🟢")

    def test_status_icon_low(self):
        ink = InkLevel("Black", 10, 100)
        self.assertEqual(ink.status_icon, "🟠")

    def test_status_icon_empty(self):
        ink = InkLevel("Black", 0, 100)
        self.assertEqual(ink.status_icon, "🔴")


class TestPaperTray(unittest.TestCase):
    def test_create(self):
        tray = PaperTray("Main", PaperSize.A4, 250, 500)
        self.assertEqual(tray.percent, 50.0)

    def test_bar(self):
        tray = PaperTray("Main", PaperSize.A4, 100, 200)
        bar = tray.bar
        self.assertIn("█", bar)

    def test_sheets_str(self):
        tray = PaperTray("Main", PaperSize.A4, 250, 500)
        self.assertEqual(tray.sheets_str, "250/500")


class TestPrintJob(unittest.TestCase):
    def test_create(self):
        job = PrintJob(id=1, name="test.pdf", printer="HP", user="admin")
        self.assertEqual(job.name, "test.pdf")

    def test_state_icon(self):
        job = PrintJob(state=JobState.COMPLETED)
        self.assertEqual(job.state_icon, "✅")

    def test_details(self):
        job = PrintJob(pages=5, copies=2, color_mode=PrintColorMode.COLOR,
                       paper_size=PaperSize.A4)
        d = job.details
        self.assertIn("2× 5p", d)
        self.assertIn("Color", d)

    def test_file_size_str(self):
        job = PrintJob(file_size=2048)
        self.assertIn("KB", job.file_size_str)


class TestPrinter(unittest.TestCase):
    def test_create(self):
        p = Printer("HP", "LaserJet Pro", PrinterType.NETWORK)
        self.assertEqual(p.name, "HP")

    def test_state_icon(self):
        p = Printer(state=PrinterState.IDLE)
        self.assertEqual(p.state_icon, "🟢")

    def test_ink_summary(self):
        p = Printer(ink_levels=[InkLevel("Black", 80, 100)])
        self.assertIn("🟢", p.ink_summary)

    def test_paper_summary(self):
        p = Printer(trays=[PaperTray("Main", PaperSize.A4, 250, 500)])
        self.assertEqual(p.paper_summary, "250 sheets")

    def test_is_available(self):
        p = Printer(state=PrinterState.IDLE)
        self.assertTrue(p.is_available)
        p2 = Printer(state=PrinterState.ERROR)
        self.assertFalse(p2.is_available)


class TestPrinterManager(unittest.TestCase):
    def setUp(self):
        self.mgr = PrinterManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.printers), 0)
        self.assertGreater(len(self.mgr.jobs), 0)

    def test_selected_printer(self):
        p = self.mgr.selected_printer
        self.assertIsNotNone(p)

    def test_select_printer(self):
        self.mgr.select_printer(2)
        self.assertEqual(self.mgr._selected_printer, 2)

    def test_print_test_page(self):
        count = len(self.mgr.jobs)
        job = self.mgr.print_test_page(0)
        self.assertIsNotNone(job)
        self.assertEqual(len(self.mgr.jobs), count + 1)
        self.assertEqual(job.name, "Test Page")

    def test_cancel_job(self):
        result = self.mgr.cancel_job(6)  # pending job
        self.assertTrue(result)
        self.assertEqual(self.mgr.jobs[6].state, JobState.CANCELLED)

    def test_retry_job(self):
        result = self.mgr.retry_job(5)  # failed job
        self.assertIsNotNone(result)
        self.assertEqual(result.state, JobState.PENDING)

    def test_clear_completed(self):
        count = self.mgr.clear_completed()
        self.assertGreater(count, 0)

    def test_stop_printer(self):
        result = self.mgr.stop_printer(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.printers[0].state, PrinterState.STOPPED)

    def test_start_printer(self):
        self.mgr.stop_printer(0)
        result = self.mgr.start_printer(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.printers[0].state, PrinterState.IDLE)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_printer, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_printer, 0)

    def test_search_printers(self):
        results = self.mgr.search_printers("canon")
        self.assertGreater(len(results), 0)

    def test_search_jobs(self):
        results = self.mgr.search_jobs("report")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_printers", stats)
        self.assertIn("total_pages_printed", stats)


# ─── Certificate Manager Tests ────────────────────────────────────────────


class TestCertificate(unittest.TestCase):
    def test_create(self):
        cert = Certificate(common_name="test.dev", organization="Test")
        self.assertEqual(cert.common_name, "test.dev")

    def test_type_icon(self):
        cert = Certificate(cert_type=CertType.LEAF)
        self.assertEqual(cert.type_icon, "📜")

    def test_status_icon(self):
        cert = Certificate(status=CertStatus.VALID)
        self.assertEqual(cert.status_icon, "🟢")

    def test_subject_full(self):
        cert = Certificate(common_name="test.dev", organization="Test Org",
                           country="US", locality="NYC")
        sf = cert.subject_full
        self.assertIn("CN=test.dev", sf)
        self.assertIn("O=Test Org", sf)

    def test_issuer_display(self):
        cert = Certificate(issuer_cn="Root CA", issuer_org="Nyrqis")
        self.assertIn("Root CA", cert.issuer_display)
        self.assertIn("Nyrqis", cert.issuer_display)


class TestCertificateManager(unittest.TestCase):
    def setUp(self):
        self.mgr = CertificateManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.certificates), 0)
        self.assertGreater(len(self.mgr.chains), 0)

    def test_selected_cert(self):
        cert = self.mgr.selected_cert
        self.assertIsNotNone(cert)

    def test_generate_self_signed(self):
        count = len(self.mgr.certificates)
        cert = self.mgr.generate_self_signed("test.nyrqis.dev", "Nyrqis OS")
        self.assertIsNotNone(cert)
        self.assertEqual(len(self.mgr.certificates), count + 1)
        self.assertEqual(cert.common_name, "test.nyrqis.dev")
        self.assertEqual(cert.cert_type, CertType.SELF_SIGNED)

    def test_create_csr(self):
        csr = self.mgr.create_csr("new.nyrqis.dev", "Nyrqis OS", "US")
        self.assertIsNotNone(csr)
        self.assertIn("CN=new.nyrqis.dev", csr.subject)

    def test_renew_cert(self):
        count = len(self.mgr.certificates)
        cert = self.mgr.renew_cert(0)
        self.assertIsNotNone(cert)
        self.assertEqual(len(self.mgr.certificates), count + 1)

    def test_revoke_cert(self):
        result = self.mgr.revoke_cert(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.certificates[0].status, CertStatus.REVOKED)

    def test_delete_cert(self):
        count = len(self.mgr.certificates)
        result = self.mgr.delete_cert(5)  # expired cert
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.certificates), count - 1)

    def test_toggle_trust(self):
        result = self.mgr.toggle_trust(0)
        self.assertTrue(result)
        self.assertFalse(self.mgr.certificates[0].trusted)

    def test_verify_chain(self):
        result = self.mgr.verify_chain(0)
        self.assertTrue(result)

    def test_export_cert(self):
        pem = self.mgr.export_cert(0)
        self.assertIn("BEGIN CERTIFICATE", pem)

    def test_import_cert(self):
        count = len(self.mgr.certificates)
        cert = self.mgr.import_cert("imported.dev")
        self.assertEqual(len(self.mgr.certificates), count + 1)

    def test_get_expiring(self):
        expiring = self.mgr.get_expiring_soon(30)
        self.assertGreater(len(expiring), 0)

    def test_get_expired(self):
        expired = self.mgr.get_expired()
        self.assertGreater(len(expired), 0)

    def test_search(self):
        results = self.mgr.search("nyrqis.dev")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total", stats)
        self.assertIn("valid", stats)
        self.assertIn("expired", stats)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_cert, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_cert, 0)


# ─── Color Calibration Tests ──────────────────────────────────────────────


class TestRGBColor(unittest.TestCase):
    def test_create(self):
        c = RGBColor(1.0, 0.5, 0.0)
        self.assertEqual(c.r, 1.0)

    def test_hex(self):
        c = RGBColor(1.0, 0.0, 0.0)
        self.assertEqual(c.hex, "#ff0000")


class TestICCProfile(unittest.TestCase):
    def test_create(self):
        p = ICCProfile("sRGB", "Standard RGB", is_builtin=True)
        self.assertEqual(p.name, "sRGB")

    def test_icon_builtin(self):
        p = ICCProfile(is_builtin=True)
        self.assertEqual(p.icon, "🏗️")

    def test_icon_user(self):
        p = ICCProfile(is_builtin=False)
        self.assertEqual(p.icon, "📄")


class TestNightLightSchedule(unittest.TestCase):
    def test_create(self):
        nl = NightLightSchedule(enabled=True, temperature_k=2700)
        self.assertTrue(nl.enabled)

    def test_start_str(self):
        nl = NightLightSchedule(start_hour=20, start_minute=30)
        self.assertEqual(nl.start_str, "20:30")

    def test_temp_label(self):
        nl = NightLightSchedule(temperature_k=2700)
        self.assertEqual(nl.temp_label, "Warm")


class TestMonitorCalibration(unittest.TestCase):
    def test_create(self):
        m = MonitorCalibration("Primary", "ASUS PA278QV")
        self.assertEqual(m.name, "Primary")

    def test_brightness_bar(self):
        m = MonitorCalibration(brightness=50)
        bar = m.brightness_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_hdr_badge(self):
        m = MonitorCalibration(hdr_capable=True, hdr_mode=HDRMode.HDR10)
        self.assertEqual(m.hdr_badge, "HDR10")

    def test_hdr_badge_sdr(self):
        m = MonitorCalibration(hdr_capable=False)
        self.assertEqual(m.hdr_badge, "SDR")

    def test_color_bar(self):
        m = MonitorCalibration(red_gain=1.0, green_gain=0.5, blue_gain=0.0)
        cb = m.color_bar
        self.assertIn("🟥", cb)

    def test_display_name_primary(self):
        m = MonitorCalibration(name="Main", is_primary=True)
        self.assertIn("⭐", m.display_name)


class TestColorCalibrationManager(unittest.TestCase):
    def setUp(self):
        self.mgr = ColorCalibrationManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.monitors), 0)
        self.assertGreater(len(self.mgr.profiles), 0)

    def test_selected_monitor(self):
        m = self.mgr.selected_monitor
        self.assertIsNotNone(m)

    def test_select_monitor(self):
        self.mgr.select_monitor(1)
        self.assertEqual(self.mgr._selected_monitor, 1)

    def test_set_brightness(self):
        result = self.mgr.set_brightness(0, 60)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].brightness, 60)

    def test_set_contrast(self):
        result = self.mgr.set_contrast(0, 90)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].contrast, 90)

    def test_set_saturation(self):
        result = self.mgr.set_saturation(0, 70)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].saturation, 70)

    def test_set_color_temperature(self):
        result = self.mgr.set_color_temperature(0, 4500)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].color_temperature, 4500)

    def test_set_rgb_gain(self):
        result = self.mgr.set_rgb_gain(0, 1.1, 0.9, 1.0)
        self.assertTrue(result)
        self.assertAlmostEqual(self.mgr.monitors[0].red_gain, 1.1)

    def test_set_preset(self):
        result = self.mgr.set_preset(0, CalibrationPreset.GAMING)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].preset, CalibrationPreset.GAMING)
        self.assertEqual(self.mgr.monitors[0].brightness, 85)

    def test_set_hdr_mode(self):
        result = self.mgr.set_hdr_mode(0, HDRMode.HDR10)
        self.assertTrue(result)
        self.assertEqual(self.mgr.monitors[0].hdr_mode, HDRMode.HDR10)

    def test_toggle_night_light(self):
        initial = self.mgr.night_light.enabled
        self.mgr.toggle_night_light()
        self.assertNotEqual(self.mgr.night_light.enabled, initial)

    def test_apply_profile(self):
        result = self.mgr.apply_profile(0, 1)
        self.assertTrue(result)

    def test_import_profile(self):
        count = len(self.mgr.profiles)
        p = self.mgr.import_profile("Test", "/tmp/test.icc")
        self.assertEqual(len(self.mgr.profiles), count + 1)

    def test_delete_profile_user(self):
        count = len(self.mgr.profiles)
        result = self.mgr.delete_profile(3)  # user profile
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.profiles), count - 1)

    def test_delete_profile_builtin_fails(self):
        result = self.mgr.delete_profile(0)  # builtin
        self.assertFalse(result)

    def test_calibration_progress(self):
        progress = self.mgr.calibration_progress
        self.assertGreater(progress, 0)

    def test_advance_calibration(self):
        result = self.mgr.advance_calibration()
        self.assertTrue(result)

    def test_reset_lut(self):
        result = self.mgr.reset_lut(0)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.monitors[0].lut_gamma), 16)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_monitor, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_monitor, 0)

    def test_search_profiles(self):
        results = self.mgr.search_profiles("srgb")
        self.assertGreater(len(results), 0)

    def test_search_monitors(self):
        results = self.mgr.search_monitors("asus")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("monitors", stats)
        self.assertIn("profiles", stats)
        self.assertIn("night_light", stats)


if __name__ == "__main__":
    unittest.main()
