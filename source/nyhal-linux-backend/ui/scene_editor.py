"""3D Scene Editor — object placement, camera controls, material editing for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple
import time
import math


class ObjectType(Enum):
    CUBE = "Cube"
    SPHERE = "Sphere"
    CYLINDER = "Cylinder"
    CONE = "Cone"
    TORUS = "Torus"
    PLANE = "Plane"
    MONKEY = "Monkey"
    CAMERA = "Camera"
    LIGHT = "Light"
    EMPTY = "Empty"
    MESH = "Mesh"
    ARMATURE = "Armature"


class LightType(Enum):
    POINT = "Point"
    SUN = "Sun"
    SPOT = "Spot"
    AREA = "Area"
    IES = "IES"


class MaterialMode(Enum):
    SOLID = "Solid"
    WIREFRAME = "Wireframe"
    TEXTURED = "Textured"
    MATERIAL = "Material"
    RENDERED = "Rendered"


class TransformMode(Enum):
    TRANSLATE = "Translate"
    ROTATE = "Rotate"
    SCALE = "Scale"


class SnapTarget(Enum):
    GRID = "Grid"
    VERTEX = "Vertex"
    EDGE = "Edge"
    FACE = "Face"
    INCREMENT = "Increment"


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def length(self) -> float:
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)

    def __add__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x+o.x, self.y+o.y, self.z+o.z)

    def __sub__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x-o.x, self.y-o.y, self.z-o.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x*s, self.y*s, self.z*s)


@dataclass
class Material:
    name: str = "Material"
    base_color: Tuple[int, int, int] = (200, 200, 200)
    metallic: float = 0.0
    roughness: float = 0.5
    normal_strength: float = 1.0
    emission: Tuple[int, int, int] = (0, 0, 0)
    emission_strength: float = 0.0
    alpha: float = 1.0
    texture_path: str = ""
    normal_map: str = ""

    @property
    def color_hex(self) -> str:
        return f"#{self.base_color[0]:02x}{self.base_color[1]:02x}{self.base_color[2]:02x}"

    @property
    def metallic_bar(self) -> str:
        return "█" * int(self.metallic * 10) + "░" * (10 - int(self.metallic * 10))

    @property
    def roughness_bar(self) -> str:
        return "█" * int(self.roughness * 10) + "░" * (10 - int(self.roughness * 10))


@dataclass
class SceneObject:
    id: int
    name: str
    obj_type: ObjectType = ObjectType.CUBE
    position: Vec3 = field(default_factory=Vec3)
    rotation: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(1, 1, 1))
    visible: bool = True
    locked: bool = False
    selected: bool = False
    material: Material = field(default_factory=Material)
    parent_id: int = -1
    vertex_count: int = 0
    face_count: int = 0

    @property
    def type_icon(self) -> str:
        icons = {
            ObjectType.CUBE: "📦", ObjectType.SPHERE: "🔵", ObjectType.CYLINDER: "🔶",
            ObjectType.CONE: "🔺", ObjectType.TORUS: "🍩", ObjectType.PLANE: "⬜",
            ObjectType.MONKEY: "🐵", ObjectType.CAMERA: "📷", ObjectType.LIGHT: "💡",
            ObjectType.EMPTY: "◇", ObjectType.MESH: "🔧", ObjectType.ARMATURE: "🦴",
        }
        return icons.get(self.obj_type, "?")

    @property
    def position_str(self) -> str:
        return f"({self.position.x:.2f}, {self.position.y:.2f}, {self.position.z:.2f})"

    @property
    def rotation_deg(self) -> str:
        return f"({math.degrees(self.rotation.x):.1f}°, {math.degrees(self.rotation.y):.1f}°, {math.degrees(self.rotation.z):.1f}°)"

    @property
    def scale_str(self) -> str:
        return f"({self.scale.x:.2f}, {self.scale.y:.2f}, {self.scale.z:.2f})"


@dataclass
class SceneLight:
    obj_id: int
    light_type: LightType = LightType.POINT
    color: Tuple[int, int, int] = (255, 255, 255)
    intensity: float = 1.0
    radius: float = 10.0
    spot_angle: float = 45.0
    cast_shadow: bool = True

    @property
    def intensity_bar(self) -> str:
        filled = int(min(self.intensity, 2.0) / 2.0 * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class SceneCamera:
    obj_id: int
    fov: float = 60.0
    near: float = 0.1
    far: float = 1000.0
    ortho: bool = False
    ortho_scale: float = 1.0

    @property
    def fov_bar(self) -> str:
        filled = int(self.fov / 180 * 10)
        return "█" * filled + "░" * (10 - filled)


@dataclass
class Keyframe:
    frame: int = 0
    position: Vec3 = field(default_factory=Vec3)
    rotation: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(1, 1, 1))
    interpolation: str = "Bezier"


@dataclass
class Animation:
    name: str = "Action"
    start_frame: int = 1
    end_frame: int = 250
    fps: float = 30.0
    current_frame: int = 1
    keyframes: Dict[int, List[Keyframe]] = field(default_factory=dict)
    looping: bool = True

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.fps

    @property
    def frame_bar(self) -> str:
        progress = (self.current_frame - self.start_frame) / max(1, self.end_frame - self.start_frame)
        filled = int(progress * 30)
        return "░" * filled + "▼" + "░" * (30 - filled)


@dataclass
class ViewportSettings:
    mode: MaterialMode = MaterialMode.SOLID
    show_grid: bool = True
    show_axes: bool = True
    show_wireframe: bool = False
    show_normals: bool = False
    show_origin: bool = True
    show_outline: bool = True
    show_shadow: bool = False
    show_gizmo: bool = True
    shading: str = "Studio"
    pivot: str = "Median Point"
    orientation: str = "Global"
    snap: bool = False
    snap_target: SnapTarget = SnapTarget.INCREMENT

    @property
    def mode_icon(self) -> str:
        return {"Solid": "▣", "Wireframe": "▦", "Textured": "▧", "Material": "▨", "Rendered": "▩"}.get(self.mode.value, "?")


class SceneEditor:
    def __init__(self):
        self._objects: List[SceneObject] = []
        self._lights: List[SceneLight] = []
        self._cameras: List[SceneCamera] = []
        self._selected_object: int = 0
        self._viewport = ViewportSettings()
        self._transform_mode = TransformMode.TRANSLATE
        self._animation = Animation()
        self._render_width: int = 1920
        self._render_height: int = 1080
        self._render_samples: int = 128
        self._history: List[str] = []
        self._undo_stack: List[str] = []
        self._create_samples()

    def _create_samples(self):
        self._objects = [
            SceneObject(0, "Cube", ObjectType.CUBE, Vec3(0, 0, 0), vertex_count=8, face_count=12,
                        material=Material("Cube Mat", (180, 120, 60), 0.0, 0.4)),
            SceneObject(1, "Sphere", ObjectType.SPHERE, Vec3(3, 0, 0), vertex_count=482, face_count=480,
                        material=Material("Sphere Mat", (60, 120, 200), 0.8, 0.2)),
            SceneObject(2, "Suzanne", ObjectType.MONKEY, Vec3(-3, 0, 0), vertex_count=507, face_count=498,
                        material=Material("Monkey Mat", (160, 160, 160), 0.0, 0.5)),
            SceneObject(3, "Floor", ObjectType.PLANE, Vec3(0, -1, 0), scale=Vec3(10, 10, 10),
                        vertex_count=4, face_count=2,
                        material=Material("Floor Mat", (80, 80, 80), 0.0, 0.8)),
            SceneObject(4, "Camera", ObjectType.CAMERA, Vec3(5, 4, 6), rotation=Vec3(-0.6, 0.6, 0)),
            SceneObject(5, "Key Light", ObjectType.LIGHT, Vec3(4, 5, 3)),
            SceneObject(6, "Fill Light", ObjectType.LIGHT, Vec3(-3, 3, 1)),
            SceneObject(7, "Torus", ObjectType.TORUS, Vec3(0, 2, 0), vertex_count=400, face_count=400,
                        material=Material("Torus Mat", (200, 60, 120), 0.6, 0.3)),
            SceneObject(8, "Cylinder", ObjectType.CYLINDER, Vec3(6, 0, 0), vertex_count=98, face_count=64,
                        material=Material("Cyl Mat", (120, 180, 60))),
        ]

        self._lights = [
            SceneLight(5, LightType.POINT, (255, 240, 220), 2.0, 20.0, cast_shadow=True),
            SceneLight(6, LightType.AREA, (200, 200, 255), 0.5, 5.0, cast_shadow=False),
        ]

        self._cameras = [
            SceneCamera(4, fov=50.0),
        ]

        self._animation = Animation("Scene Animation", 1, 250, 30.0, 1, looping=True)

    @property
    def selected_object(self) -> Optional[SceneObject]:
        if 0 <= self._selected_object < len(self._objects):
            return self._objects[self._selected_object]
        return None

    @property
    def total_objects(self) -> int:
        return len(self._objects)

    @property
    def total_vertices(self) -> int:
        return sum(o.vertex_count for o in self._objects if o.visible)

    @property
    def total_faces(self) -> int:
        return sum(o.face_count for o in self._objects if o.visible)

    @property
    def visible_objects(self) -> int:
        return sum(1 for o in self._objects if o.visible)

    def select_object(self, idx: int):
        if 0 <= idx < len(self._objects):
            self._selected_object = idx

    def add_object(self, obj_type: ObjectType, position: Vec3 = None):
        obj_id = max(o.id for o in self._objects) + 1 if self._objects else 0
        pos = position or Vec3(0, 0, 0)
        verts = {ObjectType.CUBE: (8, 12), ObjectType.SPHERE: (482, 480),
                 ObjectType.CYLINDER: (98, 64), ObjectType.PLANE: (4, 2),
                 ObjectType.TORUS: (400, 400), ObjectType.MONKEY: (507, 498)}
        v, f = verts.get(obj_type, (0, 0))
        obj = SceneObject(obj_id, f"{obj_type.value}.{obj_id:03d}", obj_type, pos,
                          vertex_count=v, face_count=f)
        self._objects.append(obj)
        self._history.append(f"Added {obj_type.value}")

    def delete_selected(self):
        if self.selected_object:
            name = self.selected_object.name
            self._objects.pop(self._selected_object)
            self._selected_object = min(self._selected_object, len(self._objects) - 1)
            self._history.append(f"Deleted {name}")

    def duplicate_selected(self):
        import copy
        if self.selected_object:
            new = copy.deepcopy(self.selected_object)
            new.id = max(o.id for o in self._objects) + 1
            new.name = f"{new.name}.001"
            new.position = Vec3(new.position.x + 2, new.position.y, new.position.z)
            self._objects.append(new)
            self._history.append(f"Duplicated {new.name}")

    def handle_input(self, key: str):
        key = key.lower()
        if key == "g":
            self._transform_mode = TransformMode.TRANSLATE
        elif key == "r":
            self._transform_mode = TransformMode.ROTATE
        elif key == "s":
            self._transform_mode = TransformMode.SCALE
        elif key == "h":
            if self.selected_object:
                self.selected_object.visible = not self.selected_object.visible
        elif key == "n":
            self.add_object(ObjectType.CUBE)
        elif key == "d":
            self.delete_selected()
        elif key == "x":
            self.duplicate_selected()

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS 3D SCENE EDITOR                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        vp = self._viewport
        lines.append(f"  Mode: {vp.mode.value}  Shading: {vp.shading}  Transform: {self._transform_mode.value}")
        lines.append(f"  Grid: {'ON' if vp.show_grid else 'OFF'}  Axes: {'ON' if vp.show_axes else 'OFF'}  Wireframe: {'ON' if vp.show_wireframe else 'OFF'}  Snap: {'ON' if vp.snap else 'OFF'}")
        lines.append(f"  Objects: {self.visible_objects}/{self.total_objects}  V: {self.total_vertices}  F: {self.total_faces}  Lights: {len(self._lights)}  Cameras: {len(self._cameras)}")
        lines.append("")

        # Viewport preview
        lines.append("  ┌─── 3D VIEWPORT ─────────────────────────────────────────────────┐")
        lines.append("  │        ╱│                                                       │")
        lines.append("  │       ╱ │  ◐ Sphere    ◇ Cube                                 │")
        lines.append("  │      ╱  │        ◈ Torus                                      │")
        lines.append("  │     ◈───┼──────────────────────────────────────────────────────│")
        lines.append("  │    ╱    │                                                      │")
        lines.append("  └────────────────────────────────────────────────────────────────┘")
        lines.append("")

        # Outliner
        lines.append("  ── Outliner ──")
        for i, obj in enumerate(self._objects):
            sel = "▶" if i == self._selected_object else " "
            vis = "👁" if obj.visible else "👁‍🗨"
            lock = "🔒" if obj.locked else ""
            lines.append(f"  {sel} {vis} {lock} {obj.type_icon} {obj.name}")
        lines.append("")

        # Selected object
        obj = self.selected_object
        if obj:
            lines.append(f"  ── {obj.name} ({obj.obj_type.value}) ──")
            lines.append(f"  Pos: {obj.position_str}  Rot: {obj.rotation_deg}  Scale: {obj.scale_str}")
            lines.append(f"  Verts: {obj.vertex_count}  Faces: {obj.face_count}")
            m = obj.material
            lines.append(f"  Material: {m.name}  Color: {m.color_hex}  Metallic: [{m.metallic_bar}] {m.metallic:.0%}  Rough: [{m.roughness_bar}] {m.roughness:.0%}")
            lines.append("")

        # Lights
        if self._lights:
            lines.append("  ── Lights ──")
            for light in self._lights:
                obj_name = ""
                for o in self._objects:
                    if o.id == light.obj_id:
                        obj_name = o.name
                        break
                lines.append(f"  💡 {obj_name}  {light.light_type.value}  [{light.intensity_bar}] {light.intensity:.1f}  Shadow: {'ON' if light.cast_shadow else 'OFF'}")
            lines.append("")

        # Animation
        anim = self._animation
        lines.append(f"  ── Animation ──")
        lines.append(f"  {anim.name}  Frame: {anim.current_frame}/{anim.end_frame}  FPS: {anim.fps}  Duration: {anim.duration_s:.1f}s  Loop: {'ON' if anim.looping else 'OFF'}")
        lines.append(f"  Timeline: [{anim.frame_bar}]")
        lines.append("")

        # Render settings
        lines.append(f"  ── Render ──")
        lines.append(f"  Resolution: {self._render_width}x{self._render_height}  Samples: {self._render_samples}")
        lines.append("")

        lines.append("  [G]Move [R]otate [S]cale [H]Hide [N]Add [D]Delete [X]Duplicate")
        lines.append("  [1]Solid [2]Wire [3]Tex [4]Mat [5]Render  [↑↓]Select Object")
        return lines
