"""
Tests for Vehicle Dashboard, Data Pipeline Builder, and Dashboard Builder.
"""
import unittest
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.vehicle_dashboard import (
    VehicleDashboard, SpeedGauge, RPMGauge, FuelGauge, TripComputer,
    TemperatureGauge, TirePressure, DTC, ServiceInterval, DrivingScore,
    FuelType, DriveMode, GearMode, AlertLevel, DTCStatus,
)
from ui.data_pipeline import (
    DataPipelineManager, Pipeline, PipelineNode, NodeConnection, PipelineLog,
    PipelineSchedule, PipelineTemplate, NodePort, DataPreview,
    NodeType, NodeStatus, PipelineStatus, DataSourceType, TransformType,
    OutputType, JoinType, AggFunction, ScheduleFreq,
)
from ui.dashboard_builder import (
    DashboardBuilder, Dashboard, DashboardPage, DashboardWidget,
    DataSource, DashboardTheme, DashboardTemplate, WidgetConfig,
    WidgetPosition, Threshold, RefreshInterval,
    WidgetType, DataSourceType as DS2, ThresholdType,
)


# ─── Vehicle Dashboard Tests ──────────────────────────────────────────────


class TestSpeedGauge(unittest.TestCase):
    def test_create(self):
        g = SpeedGauge(current_speed=65.0)
        self.assertEqual(g.current_speed, 65.0)

    def test_speed_str(self):
        g = SpeedGauge(current_speed=120.0)
        self.assertEqual(g.speed_str, "120")

    def test_speed_bar(self):
        g = SpeedGauge(current_speed=130.0)
        bar = g.speed_bar
        self.assertIn("█", bar)

    def test_cruise_str(self):
        g = SpeedGauge(cruise_active=True, cruise_speed=100.0)
        self.assertIn("100", g.cruise_str)


class TestFuelGauge(unittest.TestCase):
    def test_create(self):
        fg = FuelGauge(level_percent=72.0)
        self.assertEqual(fg.level_percent, 72.0)

    def test_level_bar(self):
        fg = FuelGauge(level_percent=50)
        bar = fg.level_bar
        self.assertIn("█", bar)

    def test_status_icon(self):
        fg = FuelGauge(level_percent=60)
        self.assertEqual(fg.status_icon, "🟢")

    def test_status_low(self):
        fg = FuelGauge(level_percent=15)
        self.assertEqual(fg.status_icon, "🟠")


class TestTripComputer(unittest.TestCase):
    def test_create(self):
        tc = TripComputer("Trip A", 150.5, 3600)
        self.assertEqual(tc.name, "Trip A")

    def test_distance_str(self):
        tc = TripComputer(distance_km=234.5)
        self.assertEqual(tc.distance_str, "234.5 km")

    def test_economy_str(self):
        tc = TripComputer(fuel_economy_l100=7.2)
        self.assertEqual(tc.economy_str, "7.2 L/100km")


class TestTirePressure(unittest.TestCase):
    def test_create(self):
        tp = TirePressure("FL", 34.5, 35.0)
        self.assertEqual(tp.position, "FL")

    def test_status_ok(self):
        tp = TirePressure(pressure_psi=35.0, recommended_psi=35.0)
        self.assertIn("OK", tp.status)

    def test_status_low(self):
        tp = TirePressure(pressure_psi=28.0, recommended_psi=35.0)
        self.assertIn("Low", tp.status)


class TestDTC(unittest.TestCase):
    def test_create(self):
        dtc = DTC("P0420", "Catalyst Efficiency", DTCStatus.ACTIVE, "Engine")
        self.assertEqual(dtc.code, "P0420")

    def test_status_icon(self):
        dtc = DTC(status=DTCStatus.ACTIVE)
        self.assertEqual(dtc.status_icon, "🔴")


class TestServiceInterval(unittest.TestCase):
    def test_create(self):
        si = ServiceInterval("Oil Change", 15000, 365, 40000, time.time() - 86400 * 30, 43000)
        self.assertEqual(si.name, "Oil Change")

    def test_urgency(self):
        si = ServiceInterval("Oil Change", 15000, 365, 40000, time.time() - 86400 * 30, 43000)
        self.assertIn("OK", si.urgency)


class TestDrivingScore(unittest.TestCase):
    def test_create(self):
        ds = DrivingScore(overall=87.5)
        self.assertEqual(ds.overall, 87.5)

    def test_grade(self):
        ds = DrivingScore(overall=92.0)
        self.assertEqual(ds.grade, "A")

    def test_overall_bar(self):
        ds = DrivingScore(overall=75.0)
        bar = ds.overall_bar
        self.assertIn("█", bar)


class TestVehicleDashboard(unittest.TestCase):
    def setUp(self):
        self.vd = VehicleDashboard()

    def test_initial_state(self):
        self.assertGreater(len(self.vd.trips), 0)
        self.assertGreater(len(self.vd.temperatures), 0)
        self.assertGreater(len(self.vd.tires), 0)

    def test_toggle_cruise(self):
        initial = self.vd.speed.cruise_active
        self.vd.toggle_cruise()
        self.assertNotEqual(self.vd.speed.cruise_active, initial)

    def test_set_drive_mode(self):
        self.vd.set_drive_mode(DriveMode.SPORT)
        self.assertEqual(self.vd.drive_mode, DriveMode.SPORT)

    def test_clear_dtc(self):
        result = self.vd.clear_dtc(0)
        self.assertTrue(result)
        self.assertEqual(self.vd.dtc_codes[0].status, DTCStatus.CLEARED)

    def test_reset_trip(self):
        result = self.vd.reset_trip(0)
        self.assertTrue(result)
        self.assertEqual(self.vd.trips[0].distance_km, 0)

    def test_get_active_dtcs(self):
        active = self.vd.get_active_dtcs()
        self.assertGreater(len(active), 0)

    def test_navigation(self):
        self.vd.select_down()
        self.assertEqual(self.vd._selected_trip, 1)
        self.vd.select_up()
        self.assertEqual(self.vd._selected_trip, 0)

    def test_stats(self):
        stats = self.vd.get_stats()
        self.assertIn("speed", stats)
        self.assertIn("fuel_percent", stats)
        self.assertIn("driving_score", stats)


# ─── Data Pipeline Tests ─────────────────────────────────────────────────


class TestPipelineNode(unittest.TestCase):
    def test_create(self):
        n = PipelineNode(1, "Source", NodeType.SOURCE, 100, 200)
        self.assertEqual(n.name, "Source")

    def test_status_icon(self):
        n = PipelineNode(status=NodeStatus.RUNNING)
        self.assertEqual(n.status_icon, "🔄")

    def test_rows_str(self):
        n = PipelineNode(rows_processed=50000)
        self.assertIn("K", n.rows_str)


class TestPipeline(unittest.TestCase):
    def test_create(self):
        p = Pipeline(id=1, name="Test", status=PipelineStatus.DRAFT)
        self.assertEqual(p.name, "Test")

    def test_status_icon(self):
        p = Pipeline(status=PipelineStatus.RUNNING)
        self.assertEqual(p.status_icon, "🔄")

    def test_node_count(self):
        p = Pipeline(nodes=[PipelineNode(1), PipelineNode(2)])
        self.assertEqual(p.node_count, 2)


class TestDataPipelineManager(unittest.TestCase):
    def setUp(self):
        self.mgr = DataPipelineManager()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.pipelines), 0)
        self.assertGreater(len(self.mgr.templates), 0)

    def test_selected_pipeline(self):
        p = self.mgr.selected_pipeline
        self.assertIsNotNone(p)

    def test_select_pipeline(self):
        self.mgr.select_pipeline(2)
        self.assertEqual(self.mgr._selected_pipeline, 2)

    def test_create_pipeline(self):
        count = len(self.mgr.pipelines)
        p = self.mgr.create_pipeline("New Pipeline", "Test")
        self.assertEqual(len(self.mgr.pipelines), count + 1)

    def test_delete_pipeline(self):
        count = len(self.mgr.pipelines)
        result = self.mgr.delete_pipeline(2)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.pipelines), count - 1)

    def test_run_pipeline(self):
        result = self.mgr.run_pipeline(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.pipelines[0].status, PipelineStatus.RUNNING)

    def test_stop_pipeline(self):
        self.mgr.run_pipeline(0)
        result = self.mgr.stop_pipeline(0)
        self.assertTrue(result)
        self.assertEqual(self.mgr.pipelines[0].status, PipelineStatus.FAILED)

    def test_add_node(self):
        count = len(self.mgr.pipelines[0].nodes)
        node = self.mgr.add_node(0, "New Node", NodeType.FILTER)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.mgr.pipelines[0].nodes), count + 1)

    def test_connect_nodes(self):
        result = self.mgr.connect_nodes(0, 1, "output", 99, "input")
        self.assertTrue(result)

    def test_create_from_template(self):
        count = len(self.mgr.pipelines)
        p = self.mgr.create_from_template(0, "From Template")
        self.assertIsNotNone(p)
        self.assertEqual(len(self.mgr.pipelines), count + 1)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_pipeline, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_pipeline, 0)

    def test_search_pipelines(self):
        results = self.mgr.search_pipelines("sales")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("total_pipelines", stats)
        self.assertIn("total_nodes", stats)


# ─── Dashboard Builder Tests ──────────────────────────────────────────────


class TestWidgetConfig(unittest.TestCase):
    def test_create(self):
        wc = WidgetConfig(WidgetType.LINE_CHART, "CPU Usage")
        self.assertEqual(wc.title, "CPU Usage")

    def test_type_icon(self):
        wc = WidgetConfig(widget_type=WidgetType.BAR_CHART)
        self.assertEqual(wc.type_icon, "📊")


class TestDashboardWidget(unittest.TestCase):
    def test_create(self):
        w = DashboardWidget(1, WidgetConfig(WidgetType.STAT_CARD, "CPU"), WidgetPosition(0, 0, 3, 2))
        self.assertEqual(w.id, 1)

    def test_value_str(self):
        w = DashboardWidget(value=42.5, config=WidgetConfig(decimal_places=1, unit="%"))
        self.assertEqual(w.value_str, "42.5%")


class TestDashboard(unittest.TestCase):
    def test_create(self):
        d = Dashboard(id=1, name="Test", created=time.time())
        self.assertEqual(d.name, "Test")

    def test_total_widgets(self):
        d = Dashboard(pages=[DashboardPage(widgets=[DashboardWidget(), DashboardWidget()])])
        self.assertEqual(d.total_widgets, 2)


class TestDashboardBuilder(unittest.TestCase):
    def setUp(self):
        self.mgr = DashboardBuilder()

    def test_initial_state(self):
        self.assertGreater(len(self.mgr.dashboards), 0)
        self.assertGreater(len(self.mgr.data_sources), 0)
        self.assertGreater(len(self.mgr.templates), 0)

    def test_selected_dashboard(self):
        d = self.mgr.selected_dashboard
        self.assertIsNotNone(d)

    def test_select_dashboard(self):
        self.mgr.select_dashboard(1)
        self.assertEqual(self.mgr._selected_dashboard, 1)

    def test_create_dashboard(self):
        count = len(self.mgr.dashboards)
        d = self.mgr.create_dashboard("New Dashboard", "Test")
        self.assertEqual(len(self.mgr.dashboards), count + 1)

    def test_delete_dashboard(self):
        count = len(self.mgr.dashboards)
        result = self.mgr.delete_dashboard(1)
        self.assertTrue(result)
        self.assertEqual(len(self.mgr.dashboards), count - 1)

    def test_duplicate_dashboard(self):
        count = len(self.mgr.dashboards)
        copy = self.mgr.duplicate_dashboard(0)
        self.assertIsNotNone(copy)
        self.assertEqual(len(self.mgr.dashboards), count + 1)

    def test_add_page(self):
        result = self.mgr.add_page(0, "New Page")
        self.assertTrue(result)

    def test_add_widget(self):
        w = self.mgr.add_widget(0, 0, WidgetType.GAUGE, "New Gauge")
        self.assertIsNotNone(w)

    def test_remove_widget(self):
        result = self.mgr.remove_widget(0, 0, 0)
        self.assertTrue(result)

    def test_add_data_source(self):
        count = len(self.mgr.data_sources)
        ds = self.mgr.add_data_source("Test Source", DS2.REST_API, "http://test.com")
        self.assertEqual(len(self.mgr.data_sources), count + 1)

    def test_toggle_data_source(self):
        result = self.mgr.toggle_data_source(0)
        self.assertTrue(result)
        self.assertFalse(self.mgr.data_sources[0].enabled)

    def test_create_from_template(self):
        count = len(self.mgr.dashboards)
        d = self.mgr.create_from_template(0, "From Template")
        self.assertIsNotNone(d)
        self.assertEqual(len(self.mgr.dashboards), count + 1)

    def test_navigation(self):
        self.mgr.select_down()
        self.assertEqual(self.mgr._selected_dashboard, 1)
        self.mgr.select_up()
        self.assertEqual(self.mgr._selected_dashboard, 0)

    def test_search_dashboards(self):
        results = self.mgr.search_dashboards("system")
        self.assertGreater(len(results), 0)

    def test_search_data_sources(self):
        results = self.mgr.search_data_sources("system")
        self.assertGreater(len(results), 0)

    def test_stats(self):
        stats = self.mgr.get_stats()
        self.assertIn("dashboards", stats)
        self.assertIn("total_widgets", stats)
        self.assertIn("data_sources", stats)


class TestDashboardTemplate(unittest.TestCase):
    def test_create(self):
        t = DashboardTemplate("System Monitor", "CPU, RAM, disk", "Monitoring", 8)
        self.assertEqual(t.name, "System Monitor")

    def test_icon(self):
        t = DashboardTemplate(category="DevOps")
        self.assertEqual(t.icon, "🔧")


if __name__ == "__main__":
    unittest.main()
