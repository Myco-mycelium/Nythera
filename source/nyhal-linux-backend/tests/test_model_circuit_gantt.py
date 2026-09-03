"""Tests for 3D Model Viewer, Circuit Simulator, and Gantt Chart."""
import unittest
from ui.model_viewer import (
    ModelViewer, Vec3, Material, Mesh, Camera, Transform, BoundingBox,
    ViewMode, ProjectionType, ShadingModel, MeshType,
    create_cube, create_sphere, create_cylinder, create_torus, create_monkey
)
from ui.circuit_sim import (
    CircuitSimulator, Component, Wire, Pin, AnalysisResult,
    ComponentType, SignalType, AnalysisType, PinType
)
from ui.gantt_chart import (
    GanttChart, Project, Task, Milestone, Resource, TaskDependency,
    TaskStatus, TaskPriority, MilestoneType, ResourceRole, DependencyType
)


# ==================== ModelViewer Tests ====================

class TestVec3(unittest.TestCase):
    def test_create(self):
        v = Vec3(1, 2, 3)
        self.assertEqual(v.x, 1)

    def test_add(self):
        v = Vec3(1, 2, 3) + Vec3(4, 5, 6)
        self.assertEqual(v.x, 5)

    def test_sub(self):
        v = Vec3(5, 5, 5) - Vec3(1, 1, 1)
        self.assertEqual(v.x, 4)

    def test_mul(self):
        v = Vec3(2, 3, 4) * 2
        self.assertEqual(v.x, 4)

    def test_length(self):
        self.assertAlmostEqual(Vec3(3, 4, 0).length(), 5.0)

    def test_normalized(self):
        n = Vec3(3, 0, 0).normalized()
        self.assertAlmostEqual(n.x, 1.0)

    def test_to_tuple(self):
        self.assertEqual(Vec3(1, 2, 3).to_tuple(), (1, 2, 3))


class TestMaterial(unittest.TestCase):
    def test_create(self):
        m = Material("Test", (255, 0, 0))
        self.assertEqual(m.name, "Test")
        self.assertEqual(m.diffuse_color, (255, 0, 0))

    def test_color_hex(self):
        m = Material("T", (255, 128, 0))
        self.assertEqual(m.color_hex, "#ff8000")

    def test_bars(self):
        m = Material("T", metallic=0.5, roughness=0.8, opacity=0.7)
        self.assertIn("█", m.metallic_bar)
        self.assertIn("█", m.roughness_bar)
        self.assertIn("█", m.opacity_bar)


class TestMesh(unittest.TestCase):
    def test_cube(self):
        m = create_cube("Test")
        self.assertEqual(m.mesh_type, MeshType.CUBE)
        self.assertEqual(m.vertex_count, 8)

    def test_sphere(self):
        m = create_sphere()
        self.assertEqual(m.mesh_type, MeshType.SPHERE)
        self.assertGreater(m.vertex_count, 100)

    def test_cylinder(self):
        m = create_cylinder()
        self.assertEqual(m.mesh_type, MeshType.CYLINDER)

    def test_torus(self):
        m = create_torus()
        self.assertEqual(m.mesh_type, MeshType.TORUS)

    def test_monkey(self):
        m = create_monkey()
        self.assertEqual(m.mesh_type, MeshType.MONKEY)

    def test_stats(self):
        m = create_cube()
        self.assertIn("V:", m.stats)

    def test_visibility(self):
        m = create_cube()
        self.assertTrue(m.visible)
        m.visible = False
        self.assertFalse(m.visible)


class TestCamera(unittest.TestCase):
    def test_create(self):
        cam = Camera()
        self.assertEqual(cam.fov, 60.0)
        self.assertEqual(cam.projection, ProjectionType.PERSPECTIVE)

    def test_distance(self):
        cam = Camera()
        d = cam.distance
        self.assertGreater(d, 0)

    def test_distance_bar(self):
        cam = Camera()
        bar = cam.distance_bar
        self.assertIn("█", bar)


class TestModelViewer(unittest.TestCase):
    def setUp(self):
        self.viewer = ModelViewer()

    def test_initial_state(self):
        self.assertGreater(len(self.viewer._meshes), 0)
        self.assertEqual(self.viewer._selected_mesh, 0)

    def test_selected_mesh(self):
        mesh = self.viewer.selected_mesh
        self.assertIsNotNone(mesh)

    def test_select_mesh(self):
        self.viewer.select_mesh(2)
        self.assertEqual(self.viewer._selected_mesh, 2)

    def test_select_invalid(self):
        self.viewer.select_mesh(99)
        self.assertEqual(self.viewer._selected_mesh, 0)

    def test_total_vertices(self):
        self.assertGreater(self.viewer.total_vertices, 0)

    def test_total_faces(self):
        self.assertGreater(self.viewer.total_faces, 0)

    def test_visible_meshes(self):
        self.assertGreater(self.viewer.visible_meshes, 0)

    def test_toggle_visibility(self):
        self.viewer.toggle_visibility(0)
        self.assertFalse(self.viewer._meshes[0].visible)
        self.viewer.toggle_visibility(0)
        self.assertTrue(self.viewer._meshes[0].visible)

    def test_duplicate_mesh(self):
        count = len(self.viewer._meshes)
        self.viewer.duplicate_mesh(0)
        self.assertEqual(len(self.viewer._meshes), count + 1)

    def test_add_mesh(self):
        count = len(self.viewer._meshes)
        self.viewer.add_mesh(MeshType.SPHERE)
        self.assertEqual(len(self.viewer._meshes), count + 1)

    def test_set_view_mode(self):
        self.viewer.set_view_mode(ViewMode.WIREFRAME)
        self.assertEqual(self.viewer._view_mode, ViewMode.WIREFRAME)

    def test_orbit(self):
        old_x = self.viewer._camera.position.x
        self.viewer.orbit(0.1, 0.1)
        self.assertNotEqual(self.viewer._camera.position.x, old_x)

    def test_zoom(self):
        old_dist = self.viewer._camera.distance
        self.viewer.zoom(1.0)
        self.assertNotEqual(self.viewer._camera.distance, old_dist)

    def test_frame_all(self):
        self.viewer.frame_all()
        self.assertIsNotNone(self.viewer._camera.target)

    def test_render(self):
        lines = self.viewer.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("NYRQIS 3D MODEL VIEWER" in l for l in lines))

    def test_scene_stats(self):
        stats = self.viewer.scene_stats
        self.assertIn("Meshes:", stats)

    def test_handle_input(self):
        self.viewer.handle_input("w")  # wireframe
        self.assertEqual(self.viewer._view_mode, ViewMode.WIREFRAME)
        self.viewer.handle_input("s")  # solid
        self.assertEqual(self.viewer._view_mode, ViewMode.SOLID)

    def test_history(self):
        self.viewer.add_mesh(MeshType.CUBE)
        self.assertGreater(len(self.viewer._history), 0)


# ==================== CircuitSimulator Tests ====================

class TestPin(unittest.TestCase):
    def test_create(self):
        p = Pin("1", PinType.BIDIRECTIONAL)
        self.assertFalse(p.connected)

    def test_voltage_str(self):
        p = Pin("1", PinType.BIDIRECTIONAL)
        p.voltage = 3.3
        self.assertEqual(p.voltage_str, "3.30V")

    def test_current_str(self):
        p = Pin("1", PinType.BIDIRECTIONAL)
        p.current = 0.015
        self.assertEqual(p.current_str, "15.0mA")

    def test_status_icon(self):
        p = Pin("1", PinType.BIDIRECTIONAL)
        self.assertEqual(p.status_icon, "⚪")
        p.connected = True
        self.assertEqual(p.status_icon, "🟢")


class TestComponent(unittest.TestCase):
    def test_resistor(self):
        c = Component("R1", ComponentType.RESISTOR, 1000, "Ω")
        self.assertIn("kΩ", c.value_str)

    def test_capacitor(self):
        c = Component("C1", ComponentType.CAPACITOR, 100e-9, "F")
        self.assertIn("nF", c.value_str)

    def test_icon(self):
        c = Component("R1", ComponentType.RESISTOR, 100, "Ω")
        self.assertIn(".", c.icon)

    def test_pins(self):
        c = Component("R1", ComponentType.RESISTOR, 100, "Ω")
        self.assertEqual(len(c.pins), 2)


class TestWire(unittest.TestCase):
    def test_create(self):
        w = Wire(0, "R1", 0, "C1", 1)
        self.assertEqual(w.wire_id, 0)

    def test_length(self):
        w = Wire(0, "R1", 0, "C1", 1, points=[(0, 0), (1, 0), (2, 0)])
        self.assertEqual(w.length_str, "3")


class TestCircuitSimulator(unittest.TestCase):
    def setUp(self):
        self.sim = CircuitSimulator()

    def test_initial_state(self):
        self.assertGreater(len(self.sim._components), 0)
        self.assertGreater(len(self.sim._wires), 0)

    def test_selected_component(self):
        comp = self.sim.selected_component
        self.assertIsNotNone(comp)

    def test_select_component(self):
        self.sim.select_component(3)
        self.assertEqual(self.sim._selected_component, 3)

    def test_total_components(self):
        self.assertGreater(self.sim.total_components, 0)

    def test_total_wires(self):
        self.assertGreater(self.sim.total_wires, 0)

    def test_connected_pins(self):
        self.assertGreaterEqual(self.sim.connected_pins, 0)

    def test_run_analysis(self):
        result = self.sim.run_analysis(AnalysisType.TRANSIENT)
        self.assertTrue(result.success)
        self.assertGreater(result.data_points, 0)

    def test_run_ac_sweep(self):
        result = self.sim.run_analysis(AnalysisType.AC_SWEEP)
        self.assertTrue(result.success)

    def test_render(self):
        lines = self.sim.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("NYRQIS CIRCUIT SIMULATOR" in l for l in lines))

    def test_handle_input(self):
        self.sim.handle_input("v")  # toggle values
        self.assertFalse(self.sim._show_values)
        self.sim.handle_input("v")  # toggle back

    def test_analysis_results(self):
        self.sim.run_analysis(AnalysisType.OPERATING_POINT)
        self.assertGreater(len(self.sim._analysis_results), 0)

    def test_history(self):
        self.sim.run_analysis(AnalysisType.TRANSIENT)
        self.assertGreater(len(self.sim._history), 0)


# ==================== GanttChart Tests ====================

class TestTask(unittest.TestCase):
    def test_create(self):
        t = Task(0, "Test Task", 0, 5, 0.5, TaskStatus.IN_PROGRESS)
        self.assertEqual(t.progress, 0.5)

    def test_progress_bar(self):
        t = Task(0, "T", 0, 5, 0.5)
        bar = t.progress_bar
        self.assertIn("█", bar)

    def test_end_day(self):
        t = Task(0, "T", 10, 5)
        self.assertEqual(t.end_day, 15)

    def test_gantt_bar(self):
        t = Task(0, "T", 0, 5, 0.3)
        bar = t.gantt_bar
        self.assertIn("█", bar)

    def test_status_icon(self):
        t = Task(0, "T", 0, 5, 0, TaskStatus.COMPLETED)
        self.assertEqual(t.status_icon, "✅")

    def test_priority_icon(self):
        t = Task(0, "T", 0, 5, 0, TaskStatus.NOT_STARTED, TaskPriority.CRITICAL)
        self.assertEqual(t.priority_icon, "🔴")


class TestResource(unittest.TestCase):
    def test_create(self):
        r = Resource("Alice", ResourceRole.DEVELOPER, 0.8)
        self.assertEqual(r.allocation, 0.8)

    def test_allocation_bar(self):
        r = Resource("Alice", ResourceRole.DEVELOPER, 0.5)
        bar = r.allocation_bar
        self.assertIn("█", bar)
        self.assertIn("░", bar)

    def test_available_hours(self):
        r = Resource("Alice", ResourceRole.DEVELOPER, 1.0)
        r.booked_hours = 20
        self.assertEqual(r.available_hours, 20.0)

    def test_status_icon(self):
        r = Resource("Alice", ResourceRole.DEVELOPER, 0.5)
        self.assertEqual(r.status_icon, "🟢")
        r.allocation = 1.0
        self.assertEqual(r.status_icon, "🟡")
        r.available = False
        self.assertEqual(r.status_icon, "🔴")


class TestMilestone(unittest.TestCase):
    def test_create(self):
        m = Milestone("Launch", MilestoneType.END, 0)
        self.assertEqual(m.name, "Launch")

    def test_icon(self):
        m = Milestone("Start", MilestoneType.START, 0)
        self.assertEqual(m.icon, "🟢")
        m2 = Milestone("Deadline", MilestoneType.DEADLINE, 0)
        self.assertEqual(m2.icon, "⏰")


class TestGanttChart(unittest.TestCase):
    def setUp(self):
        self.gantt = GanttChart()

    def test_initial_state(self):
        self.assertIsNotNone(self.gantt.project)
        self.assertGreater(len(self.gantt.project.tasks), 0)

    def test_selected_task(self):
        task = self.gantt.selected_task
        self.assertIsNotNone(task)

    def test_select_task(self):
        self.gantt.select_task(5)
        self.assertEqual(self.gantt._selected_task, 5)

    def test_overall_progress(self):
        p = self.gantt.project
        self.assertGreater(p.overall_progress, 0)

    def test_progress_bar(self):
        bar = self.gantt.project.progress_bar
        self.assertIsInstance(bar, str)

    def test_total_tasks(self):
        self.assertGreater(self.gantt.project.total_tasks, 0)

    def test_completed_tasks(self):
        self.assertGreater(self.gantt.project.completed_tasks, 0)

    def test_budget(self):
        self.assertGreater(self.gantt.project.total_budget, 0)

    def test_critical_path(self):
        cp = self.gantt.critical_path
        self.assertGreater(len(cp), 0)

    def test_update_progress(self):
        self.gantt.update_progress(4, 0.5)  # Task 4 is in progress at 0.3
        task = [t for t in self.gantt.project.tasks if t.id == 4][0]
        self.assertEqual(task.progress, 0.5)

    def test_add_task(self):
        count = self.gantt.project.total_tasks
        self.gantt.add_task("New Task", 0, 5)
        self.assertEqual(self.gantt.project.total_tasks, count + 1)

    def test_resources(self):
        self.assertGreater(len(self.gantt.project.resources), 0)

    def test_milestones(self):
        self.assertGreater(len(self.gantt.project.milestones), 0)

    def test_dependencies(self):
        self.assertGreater(len(self.gantt.project.dependencies), 0)

    def test_render(self):
        lines = self.gantt.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("NYRQIS GANTT CHART" in l for l in lines))

    def test_handle_input(self):
        self.gantt.handle_input("d")  # toggle dependencies
        self.assertFalse(self.gantt._show_dependencies)

    def test_history(self):
        self.gantt.add_task("Test", 0, 5)
        self.assertGreater(len(self.gantt._history), 0)

    def test_total_duration(self):
        self.assertGreater(self.gantt.project.total_duration, 0)

    def test_effort(self):
        self.assertGreater(self.gantt.project.total_effort, 0)


class TestDependencyType(unittest.TestCase):
    def test_values(self):
        self.assertEqual(DependencyType.FINISH_START.value, "FS")
        self.assertEqual(DependencyType.START_START.value, "SS")
        self.assertEqual(DependencyType.FINISH_FINISH.value, "FF")
        self.assertEqual(DependencyType.START_FINISH.value, "SF")


if __name__ == "__main__":
    unittest.main()
