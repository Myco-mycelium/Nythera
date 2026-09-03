"""3D Model Viewer — rotation, zoom, material inspector for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import math
import time


class ViewMode(Enum):
    SOLID = "Solid"
    WIREFRAME = "Wireframe"
    TEXTURED = "Textured"
    VERTEX_COLOR = "Vertex Color"
    NORMALS = "Normals"
    UV = "UV Map"


class ProjectionType(Enum):
    PERSPECTIVE = "Perspective"
    ORTHOGRAPHIC = "Orthographic"


class ShadingModel(Enum):
    FLAT = "Flat"
    SMOOTH = "Smooth"
    PHONG = "Phong"
    PBR = "PBR"


class MeshType(Enum):
    CUBE = "Cube"
    SPHERE = "Sphere"
    CYLINDER = "Cylinder"
    CONE = "Cone"
    TORUS = "Torus"
    PLANE = "Plane"
    MONKEY = "Monkey"
    CUSTOM = "Custom"


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)

    def normalized(self) -> "Vec3":
        l = self.length()
        if l == 0:
            return Vec3(0, 0, 0)
        return Vec3(self.x / l, self.y / l, self.z / l)

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)


@dataclass
class Material:
    name: str = "Default"
    diffuse_color: Tuple[int, int, int] = (200, 200, 200)
    specular_color: Tuple[int, int, int] = (255, 255, 255)
    metallic: float = 0.0
    roughness: float = 0.5
    opacity: float = 1.0
    emissive: Tuple[int, int, int] = (0, 0, 0)
    texture_path: str = ""
    normal_map: str = ""
    double_sided: bool = False
    wireframe: bool = False

    @property
    def color_hex(self) -> str:
        return f"#{self.diffuse_color[0]:02x}{self.diffuse_color[1]:02x}{self.diffuse_color[2]:02x}"

    @property
    def color_bar(self) -> str:
        r, g, b = self.diffuse_color
        return f"[██████] rgb({r},{g},{b})"

    @property
    def metallic_bar(self) -> str:
        return "█" * int(self.metallic * 10) + "░" * (10 - int(self.metallic * 10))

    @property
    def roughness_bar(self) -> str:
        return "█" * int(self.roughness * 10) + "░" * (10 - int(self.roughness * 10))

    @property
    def opacity_bar(self) -> str:
        return "█" * int(self.opacity * 10) + "░" * (10 - int(self.opacity * 10))


@dataclass
class BoundingBox:
    min: Vec3 = field(default_factory=Vec3)
    max: Vec3 = field(default_factory=Vec3)

    @property
    def center(self) -> Vec3:
        return Vec3(
            (self.min.x + self.max.x) / 2,
            (self.min.y + self.max.y) / 2,
            (self.min.z + self.max.z) / 2,
        )

    @property
    def size(self) -> Vec3:
        return self.max - self.min

    @property
    def volume(self) -> float:
        s = self.size
        return abs(s.x * s.y * s.z)


@dataclass
class Mesh:
    name: str
    mesh_type: MeshType
    vertex_count: int = 0
    face_count: int = 0
    edge_count: int = 0
    material: Material = field(default_factory=Material)
    position: Vec3 = field(default_factory=Vec3)
    rotation: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(1, 1, 1))
    visible: bool = True
    selected: bool = False
    bbox: BoundingBox = field(default_factory=BoundingBox)

    @property
    def triangle_count(self) -> int:
        return self.face_count

    @property
    def stats(self) -> str:
        return f"V:{self.vertex_count} F:{self.face_count} E:{self.edge_count}"

    @property
    def size_str(self) -> str:
        s = self.bbox.size
        return f"{s.x:.1f} x {s.y:.1f} x {s.z:.1f}"


def create_cube(name: str = "Cube") -> Mesh:
    m = Mesh(name, MeshType.CUBE, 8, 12, 6)
    m.material = Material("Cube Material", (180, 120, 60))
    m.bbox = BoundingBox(Vec3(-1, -1, -1), Vec3(1, 1, 1))
    return m


def create_sphere(name: str = "Sphere") -> Mesh:
    m = Mesh(name, MeshType.SPHERE, 482, 480, 960)
    m.material = Material("Sphere Material", (60, 120, 180), metallic=0.8, roughness=0.2)
    m.bbox = BoundingBox(Vec3(-1, -1, -1), Vec3(1, 1, 1))
    return m


def create_cylinder(name: str = "Cylinder") -> Mesh:
    m = Mesh(name, MeshType.CYLINDER, 98, 64, 160)
    m.material = Material("Cylinder Material", (120, 180, 60))
    m.bbox = BoundingBox(Vec3(-1, 0, -1), Vec3(1, 2, 1))
    return m


def create_torus(name: str = "Torus") -> Mesh:
    m = Mesh(name, MeshType.TORUS, 400, 400, 800)
    m.material = Material("Torus Material", (200, 60, 120), metallic=0.6)
    m.bbox = BoundingBox(Vec3(-2, -0.5, -2), Vec3(2, 0.5, 2))
    return m


def create_monkey(name: str = "Suzanne") -> Mesh:
    m = Mesh(name, MeshType.MONKEY, 507, 498, 996)
    m.material = Material("Monkey Material", (160, 160, 160), roughness=0.4)
    m.bbox = BoundingBox(Vec3(-1.5, -1.5, -1.5), Vec3(1.5, 1.5, 1.5))
    return m


@dataclass
class Camera:
    position: Vec3 = field(default_factory=lambda: Vec3(5, 4, 6))
    target: Vec3 = field(default_factory=Vec3)
    fov: float = 60.0
    near: float = 0.1
    far: float = 1000.0
    projection: ProjectionType = ProjectionType.PERSPECTIVE
    ortho_scale: float = 1.0

    @property
    def distance(self) -> float:
        return (self.position - self.target).length()

    @property
    def distance_bar(self) -> str:
        d = min(self.distance / 50, 1.0)
        return "█" * int(d * 10) + "░" * (10 - int(d * 10))


@dataclass
class Transform:
    position: Vec3 = field(default_factory=Vec3)
    rotation: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(1, 1, 1))

    @property
    def position_str(self) -> str:
        return f"({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"

    @property
    def rotation_str(self) -> str:
        return f"({math.degrees(self.rotation.x):.1f}°, {math.degrees(self.rotation.y):.1f}°, {math.degrees(self.rotation.z):.1f}°)"

    @property
    def scale_str(self) -> str:
        return f"({self.scale.x:.2f}, {self.scale.y:.2f}, {self.scale.z:.2f})"


class ModelViewer:
    def __init__(self):
        self._meshes: List[Mesh] = []
        self._selected_mesh: int = 0
        self._camera = Camera()
        self._view_mode: ViewMode = ViewMode.SOLID
        self._shading: ShadingModel = ShadingModel.SMOOTH
        self._show_grid: bool = True
        self._show_axes: bool = True
        self._show_wireframe_overlay: bool = False
        self._show_bounding_boxes: bool = False
        self._show_normals: bool = False
        self._render_width: int = 800
        self._render_height: int = 600
        self._fps: float = 60.0
        self._frame_time_ms: float = 16.67
        self._is_rendering: bool = False
        self._zoom_level: float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._material_inspector_open: bool = False
        self._history: List[str] = []
        self._create_samples()
        self._start_time = time.time()

    def _create_samples(self):
        self._meshes = [
            create_cube("Default Cube"),
            create_sphere("Sphere"),
            create_cylinder("Cylinder"),
            create_torus("Torus"),
            create_monkey("Suzanne"),
        ]
        # Add a plane
        plane = Mesh("Ground Plane", MeshType.PLANE, 4, 2, 4)
        plane.material = Material("Plane Material", (80, 80, 80))
        plane.position = Vec3(0, -1, 0)
        plane.bbox = BoundingBox(Vec3(-5, -1.01, -5), Vec3(5, -0.99, 5))
        self._meshes.append(plane)
        self._selected_mesh = 0

    @property
    def selected_mesh(self) -> Optional[Mesh]:
        if 0 <= self._selected_mesh < len(self._meshes):
            return self._meshes[self._selected_mesh]
        return None

    @property
    def total_vertices(self) -> int:
        return sum(m.vertex_count for m in self._meshes if m.visible)

    @property
    def total_faces(self) -> int:
        return sum(m.face_count for m in self._meshes if m.visible)

    @property
    def visible_meshes(self) -> int:
        return sum(1 for m in self._meshes if m.visible)

    @property
    def scene_stats(self) -> str:
        return f"Meshes: {len(self._meshes)} | V: {self.total_vertices} | F: {self.total_faces} | FPS: {self._fps:.0f}"

    @property
    def render_time_ms(self) -> str:
        return f"{self._frame_time_ms:.1f}ms"

    def select_mesh(self, idx: int):
        if 0 <= idx < len(self._meshes):
            for m in self._meshes:
                m.selected = False
            self._selected_mesh = idx
            self._meshes[idx].selected = True

    def toggle_visibility(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_mesh
        if 0 <= i < len(self._meshes):
            self._meshes[i].visible = not self._meshes[i].visible

    def delete_mesh(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_mesh
        if 0 <= i < len(self._meshes) and len(self._meshes) > 1:
            name = self._meshes[i].name
            self._meshes.pop(i)
            self._selected_mesh = min(self._selected_mesh, len(self._meshes) - 1)
            self._history.append(f"Deleted {name}")

    def duplicate_mesh(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_mesh
        if 0 <= i < len(self._meshes):
            import copy
            new_mesh = copy.deepcopy(self._meshes[i])
            new_mesh.name = f"{new_mesh.name}.001"
            new_mesh.position = Vec3(new_mesh.position.x + 2, new_mesh.position.y, new_mesh.position.z)
            self._meshes.append(new_mesh)
            self._history.append(f"Duplicated {self._meshes[i].name}")

    def add_mesh(self, mesh_type: MeshType):
        creators = {
            MeshType.CUBE: create_cube,
            MeshType.SPHERE: create_sphere,
            MeshType.CYLINDER: create_cylinder,
            MeshType.TORUS: create_torus,
            MeshType.MONKEY: create_monkey,
        }
        creator = creators.get(mesh_type)
        if creator:
            count = sum(1 for m in self._meshes if m.mesh_type == mesh_type) + 1
            mesh = creator(f"{mesh_type.value}.{count:03d}")
            mesh.position = Vec3(count * 3, 0, 0)
            self._meshes.append(mesh)
            self._history.append(f"Added {mesh.name}")

    def set_view_mode(self, mode: ViewMode):
        self._view_mode = mode
        self._history.append(f"View: {mode.value}")

    def set_shading(self, shading: ShadingModel):
        self._shading = shading

    def orbit(self, dx: float, dy: float):
        """Rotate camera around target."""
        r = self._camera.distance
        theta = math.atan2(self._camera.position.z, self._camera.position.x) + dx * 0.01
        phi = math.acos(max(-1, min(1, self._camera.position.y / r))) + dy * 0.01
        phi = max(0.1, min(math.pi - 0.1, phi))
        self._camera.position = Vec3(
            r * math.sin(phi) * math.cos(theta),
            r * math.cos(phi),
            r * math.sin(phi) * math.sin(theta),
        )

    def zoom(self, delta: float):
        factor = 1.0 - delta * 0.1
        self._camera.position = self._camera.position * factor
        self._zoom_level *= factor

    def pan(self, dx: float, dy: float):
        self._pan_x += dx * 0.01
        self._pan_y += dy * 0.01

    def focus_selected(self):
        mesh = self.selected_mesh
        if mesh:
            self._camera.target = mesh.position
            self._camera.position = Vec3(
                mesh.position.x + 5,
                mesh.position.y + 4,
                mesh.position.z + 6,
            )
            self._history.append(f"Focused on {mesh.name}")

    def frame_all(self):
        if self._meshes:
            all_min = Vec3(float('inf'), float('inf'), float('inf'))
            all_max = Vec3(float('-inf'), float('-inf'), float('-inf'))
            for m in self._meshes:
                if m.visible:
                    all_min.x = min(all_min.x, m.bbox.min.x)
                    all_min.y = min(all_min.y, m.bbox.min.y)
                    all_min.z = min(all_min.z, m.bbox.min.z)
                    all_max.x = max(all_max.x, m.bbox.max.x)
                    all_max.y = max(all_max.y, m.bbox.max.y)
                    all_max.z = max(all_max.z, m.bbox.max.z)
            center = Vec3(
                (all_min.x + all_max.x) / 2,
                (all_min.y + all_max.y) / 2,
                (all_min.z + all_max.z) / 2,
            )
            size = (all_max - all_min).length()
            self._camera.target = center
            self._camera.position = Vec3(
                center.x + size,
                center.y + size * 0.75,
                center.z + size,
            )
            self._history.append("Frame All")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "w":
            self.set_view_mode(ViewMode.WIREFRAME)
        elif key == "s":
            self.set_view_mode(ViewMode.SOLID)
        elif key == "t":
            self.set_view_mode(ViewMode.TEXTURED)
        elif key == "n":
            self.set_view_mode(ViewMode.NORMALS)
        elif key == "u":
            self.set_view_mode(ViewMode.UV)
        elif key == "g":
            self._show_grid = not self._show_grid
        elif key == "a":
            self._show_axes = not self._show_axes
        elif key == "b":
            self._show_bounding_boxes = not self._show_bounding_boxes
        elif key == "o":
            self.focus_selected()
        elif key == "f":
            self.frame_all()
        elif key == "h":
            self._show_wireframe_overlay = not self._show_wireframe_overlay
        elif key == "d":
            self.delete_mesh()
        elif key == "+":
            self.add_mesh(MeshType.CUBE)
        elif key == "1":
            self.set_shading(ShadingModel.FLAT)
        elif key == "2":
            self.set_shading(ShadingModel.SMOOTH)
        elif key == "3":
            self.set_shading(ShadingModel.PHONG)
        elif key == "4":
            self.set_shading(ShadingModel.PBR)
        elif key == "p":
            self._camera.projection = (
                ProjectionType.ORTHOGRAPHIC
                if self._camera.projection == ProjectionType.PERSPECTIVE
                else ProjectionType.PERSPECTIVE
            )

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS 3D MODEL VIEWER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Camera info
        cam = self._camera
        lines.append(f"  📷 Cam: [{cam.distance_bar}] {cam.distance:.1f}  FOV: {cam.fov:.0f}°  {cam.projection.value}")
        lines.append(f"  View: {self._view_mode.value}  Shading: {self._shading.value}  Grid: {'ON' if self._show_grid else 'OFF'}  Axes: {'ON' if self._show_axes else 'OFF'}")
        lines.append(f"  {self.scene_stats}  Render: {self.render_time_ms}")
        lines.append("")

        # Viewport
        lines.append("  ┌─── VIEWPORT ────────────────────────────────────────────────┐")
        if self._show_grid:
            lines.append("  │ · · · · · · · · · · · · · · · · · · · · · · · · · · · · · │")
            lines.append("  │ · · · · · · · · · · · · · · · · · · · · · · · · · · · · · │")
            lines.append("  │ · · · · · · · · · · + · · · · · · · · · · · · · · · · · · │")
            lines.append("  │ · · · · · · · · · · · · · · · · · · · · · · · · · · · · · │")
            lines.append("  │ · · · · · · · · · · · · · · · · · · · · · · · · · · · · · │")
        else:
            lines.append("  │                                                             │")
            lines.append("  │                         ◇                                   │")
            lines.append("  │                                                             │")
        lines.append("  └─────────────────────────────────────────────────────────────┘")
        lines.append("")

        # Outliner
        lines.append("  ── Outliner ──")
        for i, m in enumerate(self._meshes):
            sel = "▶" if i == self._selected_mesh else " "
            vis = "👁" if m.visible else "👁‍🗨"
            lock = "🔒" if not m.visible else ""
            type_icon = {
                MeshType.CUBE: "📦", MeshType.SPHERE: "🔵", MeshType.CYLINDER: "🔶",
                MeshType.CONE: "🔺", MeshType.TORUS: "🍩", MeshType.PLANE: "⬜",
                MeshType.MONKEY: "🐵", MeshType.CUSTOM: "🔧",
            }.get(m.mesh_type, "?")
            lines.append(f"  {sel} {vis} {type_icon} {m.name}  {m.stats}")
        lines.append("")

        # Material inspector
        mesh = self.selected_mesh
        if mesh:
            mat = mesh.material
            lines.append(f"  ── Material: {mat.name} ──")
            lines.append(f"  Color: {mat.color_hex} {mat.color_bar}")
            lines.append(f"  Metallic: [{mat.metallic_bar}] {mat.metallic:.0%}")
            lines.append(f"  Roughness: [{mat.roughness_bar}] {mat.roughness:.0%}")
            lines.append(f"  Opacity: [{mat.opacity_bar}] {mat.opacity:.0%}")
            if mat.texture_path:
                lines.append(f"  Texture: {mat.texture_path}")
            if mat.normal_map:
                lines.append(f"  Normal Map: {mat.normal_map}")
            lines.append("")

            # Transform
            lines.append(f"  ── Transform: {mesh.name} ──")
            lines.append(f"  Pos: {mesh.position.to_tuple()}  Rot: ({math.degrees(mesh.rotation.x):.0f}°, {math.degrees(mesh.rotation.y):.0f}°, {math.degrees(mesh.rotation.z):.0f}°)  Scale: {mesh.scale.to_tuple()}")
            lines.append(f"  BBox: {mesh.size_str}  Volume: {mesh.bbox.volume:.1f}")
            lines.append("")

        lines.append("  [S]olid [W]ire [T]extured [N]ormals [U]V  [G]rid [A]xes [B]Box")
        lines.append("  [O]focus [F]rame [+]Add [D]el [1-4]Shade [P]rojection")
        return lines
