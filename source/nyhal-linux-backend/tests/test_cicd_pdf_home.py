"""Tests for CI/CD Pipeline, PDF Editor, and Home Automation."""
import unittest
import time
from ui.cicd_pipeline import (
    CICDPipeline, PipelineRun, Stage, StageStep, Deployment,
    PipelineStatus, StageStatus, DeployTarget, TriggerType,
)
from ui.pdf_editor import (
    PDFEditor, Page, Annotation, FormField, Bookmark, PageText,
    AnnotationType, FormFieldType, StampType, Permission, ExportFormat,
)
from ui.home_auto import (
    HomeAuto, SmartDevice, Scene, EnergyReading, Alert,
    DeviceType, DeviceState, Room, SceneType, AlertType,
)


# ==================== CICDPipeline Tests ====================

class TestStageStep(unittest.TestCase):
    def test_create(self):
        s = StageStep("Test", StageStatus.SUCCESS, 30, "pytest")
        self.assertEqual(s.name, "Test")

    def test_status_icon(self):
        s = StageStep("T", StageStatus.SUCCESS)
        self.assertEqual(s.status_icon, "✅")


class TestStage(unittest.TestCase):
    def test_create(self):
        s = Stage("Build", StageStatus.RUNNING, [StageStep("compile", StageStatus.SUCCESS)])
        self.assertEqual(s.name, "Build")

    def test_progress(self):
        s = Stage("T", steps=[
            StageStep("a", StageStatus.SUCCESS),
            StageStep("b", StageStatus.PENDING),
        ])
        self.assertEqual(s.progress, 0.5)

    def test_progress_bar(self):
        s = Stage("T", steps=[StageStep("a", StageStatus.SUCCESS)])
        bar = s.progress_bar
        self.assertIn("█", bar)

    def test_duration(self):
        s = Stage("T", started_at=time.time() - 10)
        self.assertGreater(s.duration_s, 0)


class TestDeployment(unittest.TestCase):
    def test_create(self):
        d = Deployment("dep-1", DeployTarget.STAGING, "v1.0")
        self.assertEqual(d.version, "v1.0")

    def test_status_icon(self):
        d = Deployment("d", DeployTarget.PRODUCTION, status=PipelineStatus.SUCCESS)
        self.assertEqual(d.status_icon, "✅")

    def test_env_icon(self):
        d = Deployment("d", DeployTarget.PRODUCTION)
        self.assertIn("🔴", d.env_icon)


class TestPipelineRun(unittest.TestCase):
    def test_create(self):
        r = PipelineRun(1, "Build", PipelineStatus.SUCCESS)
        self.assertEqual(r.pipeline_name, "Build")

    def test_status_icon(self):
        r = PipelineRun(1, "T", PipelineStatus.RUNNING)
        self.assertEqual(r.status_icon, "🔄")

    def test_trigger_icon(self):
        r = PipelineRun(1, "T", trigger=TriggerType.PR)
        self.assertEqual(r.trigger_icon, "🔀")

    def test_stage_flow(self):
        r = PipelineRun(1, "T", stages=[
            Stage("Lint", StageStatus.SUCCESS),
            Stage("Test", StageStatus.RUNNING),
        ])
        flow = r.stage_flow
        self.assertIn("Lint", flow)
        self.assertIn("Test", flow)


class TestCICDPipeline(unittest.TestCase):
    def setUp(self):
        self.cicd = CICDPipeline()

    def test_initial_state(self):
        self.assertGreater(self.cicd.total_runs, 0)

    def test_selected_run(self):
        run = self.cicd.selected_run
        self.assertIsNotNone(run)

    def test_select_run(self):
        self.cicd.select_run(2)
        self.assertEqual(self.cicd._selected_run, 2)

    def test_running_pipelines(self):
        self.assertGreater(self.cicd.running_pipelines, 0)

    def test_success_rate(self):
        rate = self.cicd.success_rate
        self.assertIn("%", rate)

    def test_render(self):
        lines = self.cicd.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("CI/CD PIPELINE" in l for l in lines))


# ==================== PDFEditor Tests ====================

class TestPageText(unittest.TestCase):
    def test_create(self):
        pt = PageText(1, "Hello World", 0, 0, 100, 14)
        self.assertEqual(pt.word_count, 2)


class TestAnnotation(unittest.TestCase):
    def test_create(self):
        a = Annotation(1, 1, AnnotationType.HIGHLIGHT, 0, 0)
        self.assertEqual(a.annotation_type, AnnotationType.HIGHLIGHT)

    def test_type_icon(self):
        a = Annotation(1, 1, AnnotationType.NOTE)
        self.assertEqual(a.type_icon, "📌")


class TestFormField(unittest.TestCase):
    def test_create(self):
        f = FormField("name", FormFieldType.TEXT, "John")
        self.assertTrue(f.filled)

    def test_empty(self):
        f = FormField("name", FormFieldType.TEXT)
        self.assertFalse(f.filled)

    def test_status_icon(self):
        f = FormField("name", FormFieldType.TEXT, "John", required=True)
        self.assertEqual(f.status_icon, "✅")
        f2 = FormField("name", FormFieldType.TEXT, required=True)
        self.assertEqual(f2.status_icon, "⚠️")


class TestPDFEditor(unittest.TestCase):
    def setUp(self):
        self.editor = PDFEditor()

    def test_initial_state(self):
        self.assertGreater(self.editor.total_pages, 0)

    def test_selected_page(self):
        page = self.editor.selected_page
        self.assertIsNotNone(page)

    def test_select_page(self):
        self.editor.select_page(1)
        self.assertEqual(self.editor._selected_page, 1)

    def test_total_annotations(self):
        self.assertGreater(self.editor.total_annotations, 0)

    def test_form_fields(self):
        self.assertGreater(self.editor.total_form_fields, 0)

    def test_filled_fields(self):
        self.assertGreater(self.editor.filled_fields, 0)

    def test_add_annotation(self):
        count = self.editor.total_annotations
        self.editor.add_annotation(1, AnnotationType.HIGHLIGHT, 100, 100, "Test")
        self.assertEqual(self.editor.total_annotations, count + 1)

    def test_fill_form(self):
        self.editor.fill_form_field("reviewer_name", "Alice")
        field = next(f for f in self.editor._form_fields if f.name == "reviewer_name")
        self.assertEqual(field.value, "Alice")

    def test_extract_text(self):
        text = self.editor.extract_text(1)
        self.assertIn("Nyrqis", text)

    def test_bookmarks(self):
        self.assertGreater(len(self.editor._bookmarks), 0)

    def test_metadata(self):
        self.assertIn("Title", self.editor._metadata)

    def test_render(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("PDF EDITOR" in l for l in lines))


# ==================== HomeAuto Tests ====================

class TestSmartDevice(unittest.TestCase):
    def test_create(self):
        d = SmartDevice("Light", DeviceType.LIGHT, Room.LIVING_ROOM, DeviceState.ON, brightness=80)
        self.assertTrue(d.is_on)

    def test_state_icon(self):
        d = SmartDevice("T", state=DeviceState.ON)
        self.assertEqual(d.state_icon, "💡")

    def test_brightness_bar(self):
        d = SmartDevice("T", brightness=50)
        bar = d.brightness_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_signal_bar(self):
        d = SmartDevice("T", signal_strength=75)
        bar = d.signal_bar
        self.assertIn("█", bar)

    def test_battery_bar(self):
        d = SmartDevice("T", battery=60)
        bar = d.battery_bar
        self.assertIn("█", bar)

    def test_battery_wired(self):
        d = SmartDevice("T", battery=-1)
        self.assertIn("Wired", d.battery_bar)


class TestScene(unittest.TestCase):
    def test_create(self):
        s = Scene("Movie Night", devices={"TV": {"state": "On"}})
        self.assertEqual(s.device_count, 1)

    def test_active_icon(self):
        s = Scene("T", active=True)
        self.assertEqual(s.active_icon, "🟢")


class TestEnergyReading(unittest.TestCase):
    def test_create(self):
        e = EnergyReading(total_watts=3000, solar_watts=2000)
        self.assertIn("█", e.solar_bar)

    def test_battery_bar(self):
        e = EnergyReading(battery_level=80)
        bar = e.battery_bar
        self.assertIn("█", bar)


class TestAlert(unittest.TestCase):
    def test_create(self):
        a = Alert(time.time(), AlertType.MOTION, "Sensor", "Hallway", "Motion detected")
        self.assertFalse(a.acknowledged)

    def test_time_str(self):
        a = Alert(time.time())
        self.assertIn(":", a.time_str)

    def test_severity_icon(self):
        a = Alert(severity=3)
        self.assertEqual(a.severity_icon, "🚨")


class TestHomeAuto(unittest.TestCase):
    def setUp(self):
        self.home = HomeAuto()

    def test_initial_state(self):
        self.assertGreater(self.home.total_devices, 0)
        self.assertGreater(self.home.active_devices, 0)

    def test_selected_device(self):
        d = self.home.selected_device
        self.assertIsNotNone(d)

    def test_select_device(self):
        self.home.select_device(5)
        self.assertEqual(self.home._selected_device, 5)

    def test_total_power(self):
        self.assertGreater(self.home.total_power, 0)

    def test_devices_by_room(self):
        rooms = self.home.devices_by_room
        self.assertIn("Living Room", rooms)

    def test_toggle_device(self):
        d = self.home.selected_device
        old = d.is_on
        self.home.toggle_device()
        self.assertNotEqual(d.is_on, old)

    def test_trigger_scene(self):
        self.home.trigger_scene(0)
        self.assertTrue(self.home._scenes[0].active)

    def test_scenes(self):
        self.assertGreater(len(self.home._scenes), 0)

    def test_energy(self):
        self.assertGreater(self.home._energy.total_watts, 0)

    def test_alerts(self):
        self.assertGreater(len(self.home._alerts), 0)

    def test_render(self):
        lines = self.home.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("HOME AUTOMATION" in l for l in lines))


class TestDeviceType(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(DeviceType.LIGHT.value, "Light")
        self.assertEqual(DeviceType.THERMOSTAT.value, "Thermostat")


if __name__ == "__main__":
    unittest.main()
