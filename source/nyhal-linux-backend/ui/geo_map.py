"""Geographic Map Viewer — Marker placement, route drawing, and layer management.

Features:
- Coordinate system with lat/lng grid
- Multiple map layers: roads, terrain, satellite, buildings, weather
- Marker placement with categories and popups
- Route drawing with distance calculation
- Area/polygon selection
- Measurement tools
- Map presets: world, city, custom
"""

from __future__ import annotations

import time
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum


class MarkerCategory(Enum):
    PIN = "pin"
    STAR = "star"
    FLAG = "flag"
    HOME = "home"
    WORK = "work"
    FOOD = "food"
    HOTEL = "hotel"
    TRANSPORT = "transport"
    DANGER = "danger"
    INFO = "info"


class MapLayer(Enum):
    ROADS = "roads"
    TERRAIN = "terrain"
    SATELLITE = "satellite"
    BUILDINGS = "buildings"
    WEATHER = "weather"
    TRAFFIC = "traffic"
    BOUNDARIES = "boundaries"

    @property
    def icon(self) -> str:
        icons = {
            MapLayer.ROADS: "🛣", MapLayer.TERRAIN: "🏔", MapLayer.SATELLITE: "🛰",
            MapLayer.BUILDINGS: "🏢", MapLayer.WEATHER: "🌤", MapLayer.TRAFFIC: "🚗",
            MapLayer.BOUNDARIES: "📍",
        }
        return icons.get(self, "?")


class RouteMode(Enum):
    WALK = "walk"
    DRIVE = "drive"
    CYCLE = "cycle"
    TRANSIT = "transit"

    @property
    def icon(self) -> str:
        icons = {
            RouteMode.WALK: "🚶", RouteMode.DRIVE: "🚗",
            RouteMode.CYCLE: "🚴", RouteMode.TRANSIT: "🚌",
        }
        return icons.get(self, "?")

    @property
    def speed_kmh(self) -> float:
        speeds = {RouteMode.WALK: 5, RouteMode.DRIVE: 60, RouteMode.CYCLE: 20, RouteMode.TRANSIT: 30}
        return speeds.get(self, 10)


@dataclass
class LatLng:
    lat: float = 0.0
    lng: float = 0.0

    @property
    def lat_str(self) -> str:
        ns = "N" if self.lat >= 0 else "S"
        return f"{abs(self.lat):.4f}° {ns}"

    @property
    def lng_str(self) -> str:
        ew = "E" if self.lng >= 0 else "W"
        return f"{abs(self.lng):.4f}° {ew}"

    @property
    def display(self) -> str:
        return f"{self.lat_str}, {self.lng_str}"

    def distance_to(self, other: 'LatLng') -> float:
        """Haversine distance in km."""
        R = 6371.0
        dlat = math.radians(other.lat - self.lat)
        dlng = math.radians(other.lng - self.lng)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(self.lat)) * math.cos(math.radians(other.lat)) *
             math.sin(dlng / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class Marker:
    id: int = 0
    position: LatLng = field(default_factory=LatLng)
    category: MarkerCategory = MarkerCategory.PIN
    label: str = ""
    description: str = ""
    color: str = "red"
    visible: bool = True
    popup_text: str = ""

    @property
    def category_icon(self) -> str:
        icons = {
            MarkerCategory.PIN: "📍", MarkerCategory.STAR: "⭐",
            MarkerCategory.FLAG: "🚩", MarkerCategory.HOME: "🏠",
            MarkerCategory.WORK: "💼", MarkerCategory.FOOD: "🍽",
            MarkerCategory.HOTEL: "🏨", MarkerCategory.TRANSPORT: "🚌",
            MarkerCategory.DANGER: "⚠️", MarkerCategory.INFO: "ℹ️",
        }
        return icons.get(self.category, "📍")


@dataclass
class RoutePoint:
    position: LatLng = field(default_factory=LatLng)
    name: str = ""
    instruction: str = ""


@dataclass
class Route:
    name: str = ""
    mode: RouteMode = RouteMode.DRIVE
    points: List[RoutePoint] = field(default_factory=list)
    color: str = "blue"
    visible: bool = True
    distance_km: float = 0.0
    duration_min: float = 0.0

    @property
    def point_count(self) -> int:
        return len(self.points)

    @property
    def duration_str(self) -> str:
        if self.duration_min < 60:
            return f"{self.duration_min:.0f}min"
        h = int(self.duration_min // 60)
        m = int(self.duration_min % 60)
        return f"{h}h {m}m"

    @property
    def distance_str(self) -> str:
        if self.distance_km < 1:
            return f"{self.distance_km * 1000:.0f}m"
        return f"{self.distance_km:.1f}km"


@dataclass
class MapArea:
    name: str = ""
    center: LatLng = field(default_factory=LatLng)
    zoom: int = 12
    points: List[LatLng] = field(default_factory=list)
    color: str = "blue"
    fill_opacity: float = 0.3

    @property
    def area_km2(self) -> float:
        if len(self.points) < 3:
            return 0.0
        # Shoelace formula approximation
        n = len(self.points)
        area = 0.0
        for i in range(n):
            j = (i + 1) % n
            area += self.points[i].lng * self.points[j].lat
            area -= self.points[j].lng * self.points[i].lat
        return abs(area) * 111.32 * 111.32 / 2.0

    @property
    def point_count(self) -> int:
        return len(self.points)


@dataclass
class Measurement:
    name: str = ""
    start: LatLng = field(default_factory=LatLng)
    end: LatLng = field(default_factory=LatLng)

    @property
    def distance_km(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def distance_str(self) -> str:
        d = self.distance_km
        if d < 1:
            return f"{d * 1000:.0f}m"
        return f"{d:.2f}km"


class GeoMap:
    def __init__(self):
        self._markers: List[Marker] = []
        self._routes: List[Route] = []
        self._areas: List[MapArea] = []
        self._measurements: List[Measurement] = []
        self._center: LatLng = LatLng(37.7749, -122.4194)  # San Francisco
        self._zoom: int = 12
        self._active_layers: List[MapLayer] = [MapLayer.ROADS, MapLayer.BUILDINGS]
        self._selected_marker: int = 0
        self._view_mode: str = "map"  # map, markers, routes, layers, measurements
        self._grid_visible: bool = True
        self._show_labels: bool = True
        self._create_samples()

    def _create_samples(self):
        sf = LatLng(37.7749, -122.4194)
        self._center = sf

        # Markers
        self._markers = [
            Marker(0, LatLng(37.7749, -122.4194), MarkerCategory.HOME, "Nyrqis HQ", "Main office and development center"),
            Marker(1, LatLng(37.7849, -122.4094), MarkerCategory.WORK, "Data Center", "Cloud infrastructure"),
            Marker(2, LatLng(37.7649, -122.4294), MarkerCategory.FOOD, "Cafe Miso", "Best matcha in the city"),
            Marker(3, LatLng(37.7949, -122.3994), MarkerCategory.TRANSPORT, "Ferry Building", "Transit hub"),
            Marker(4, LatLng(37.7549, -122.4394), MarkerCategory.HOTEL, "Hotel Nyrqis", "Partner hotel"),
            Marker(5, LatLng(37.7849, -122.4494), MarkerCategory.STAR, "Golden Gate View", "Scenic viewpoint"),
            Marker(6, LatLng(37.7649, -122.4094), MarkerCategory.FOOD, "Ramen Ichiban", "Late night ramen"),
            Marker(7, LatLng(37.7749, -122.4294), MarkerCategory.INFO, "Convention Center", "Tech conferences"),
            Marker(8, LatLng(37.8049, -122.4194), MarkerCategory.DANGER, "Construction Zone", "Road work ahead"),
            Marker(9, LatLng(37.7749, -122.3894), MarkerCategory.FLAG, "East Pier", "Waterfront park"),
        ]

        # Routes
        self._routes = [
            Route(
                name="Commute to Data Center",
                mode=RouteMode.DRIVE,
                points=[
                    RoutePoint(LatLng(37.7749, -122.4194), "Nyrqis HQ", "Start"),
                    RoutePoint(LatLng(37.7799, -122.4144), "Market St", "Turn right"),
                    RoutePoint(LatLng(37.7849, -122.4094), "Data Center", "Arrive"),
                ],
                distance_km=2.3, duration_min=8,
            ),
            Route(
                name="Lunch Walk",
                mode=RouteMode.WALK,
                points=[
                    RoutePoint(LatLng(37.7749, -122.4194), "Nyrqis HQ", "Start"),
                    RoutePoint(LatLng(37.7649, -122.4294), "Cafe Miso", "Arrive"),
                ],
                distance_km=1.5, duration_min=18,
            ),
            Route(
                name="Ferry to Data Center",
                mode=RouteMode.TRANSIT,
                points=[
                    RoutePoint(LatLng(37.7949, -122.3994), "Ferry Building", "Board ferry"),
                    RoutePoint(LatLng(37.7849, -122.4094), "Data Center", "Arrive"),
                ],
                distance_km=3.1, duration_min=25,
            ),
        ]

        # Areas
        self._areas = [
            MapArea("Office Zone", sf, 14, [
                LatLng(37.7739, -122.4204), LatLng(37.7759, -122.4204),
                LatLng(37.7759, -122.4184), LatLng(37.7739, -122.4184),
            ]),
            MapArea("City Park", LatLng(37.7694, -122.4862), 15, [
                LatLng(37.7680, -122.4900), LatLng(37.7710, -122.4900),
                LatLng(37.7710, -122.4820), LatLng(37.7680, -122.4820),
            ]),
        ]

        # Measurements
        self._measurements = [
            Measurement("HQ to Cafe", sf, LatLng(37.7649, -122.4294)),
            Measurement("City Width", LatLng(37.7749, -122.5), LatLng(37.7749, -122.35)),
        ]

    @property
    def center(self) -> LatLng:
        return self._center

    @property
    def zoom(self) -> int:
        return self._zoom

    @property
    def active_layers(self) -> List[MapLayer]:
        return self._active_layers

    def toggle_layer(self, layer: MapLayer):
        if layer in self._active_layers:
            self._active_layers.remove(layer)
        else:
            self._active_layers.append(layer)

    def set_zoom(self, z: int):
        self._zoom = max(1, min(20, z))

    def pan_to(self, lat: float, lng: float):
        self._center = LatLng(lat, lng)

    def select_marker(self, idx: int):
        if 0 <= idx < len(self._markers):
            self._selected_marker = idx

    def set_view(self, mode: str):
        if mode in ("map", "markers", "routes", "layers", "measurements"):
            self._view_mode = mode

    def zoom_in(self):
        self._zoom = min(20, self._zoom + 1)

    def zoom_out(self):
        self._zoom = max(1, self._zoom - 1)

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS GEO MAP VIEWER                                   ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        lines.append(f"  📍 {self._center.display}  🔍 Zoom: {self._zoom}  🗺 Layers: {len(self._active_layers)}  📌 {len(self._markers)} markers  🛤 {len(self._routes)} routes")
        lines.append("")

        if self._view_mode == "map":
            # ASCII map grid
            lines.append("  ── Map View ──")
            grid_size = 15
            half = grid_size // 2
            grid = [["·" for _ in range(grid_size)] for _ in range(grid_size)]

            # Place markers on grid
            for marker in self._markers:
                dx = marker.position.lng - self._center.lng
                dy = marker.position.lat - self._center.lat
                scale = 0.01 / max(0.001, self._zoom / 12.0)
                gx = int(dx / scale) + half
                gy = half - int(dy / scale)
                if 0 <= gx < grid_size and 0 <= gy < grid_size:
                    grid[gy][gx] = marker.category_icon[0]

            # Center crosshair
            grid[half][half] = "+"

            # Header with coordinates
            lines.append(f"  Lat: {self._center.lat_str}  Lng: {self._center.lng_str}")
            for row in grid:
                lines.append("  " + " ".join(row))

            lines.append("")
            # Layers
            layer_str = " ".join(f"{l.icon}{l.value}" for l in self._active_layers)
            lines.append(f"  Active: {layer_str}")

        elif self._view_mode == "markers":
            lines.append("  ── Markers ──")
            for i, m in enumerate(self._markers):
                sel = "▶" if i == self._selected_marker else " "
                lines.append(f"  {sel} {m.category_icon} {m.label}")
                lines.append(f"      {m.position.display}  {m.description}")

        elif self._view_mode == "routes":
            lines.append("  ── Routes ──")
            for r in self._routes:
                visible = "👁" if r.visible else "👁‍🗨"
                lines.append(f"  {r.mode.icon} {visible} {r.name}  {r.distance_str}  {r.duration_str}")
                for pt in r.points:
                    lines.append(f"      → {pt.name}: {pt.position.display}")

        elif self._view_mode == "layers":
            lines.append("  ── Map Layers ──")
            for layer in MapLayer:
                active = "●" if layer in self._active_layers else "○"
                lines.append(f"  {active} {layer.icon} {layer.value}")

        elif self._view_mode == "measurements":
            lines.append("  ── Measurements ──")
            for m in self._measurements:
                lines.append(f"  📏 {m.name}: {m.distance_str}")
                lines.append(f"      {m.start.display} → {m.end.display}")
            lines.append("")
            lines.append("  ── Areas ──")
            for a in self._areas:
                lines.append(f"  🔲 {a.name}: {a.area_km2:.2f} km² ({a.point_count} vertices)")

        lines.append("")
        lines.append("  [M]arkers [R]outes [L]ayers [↑↓]Zoom [Z]oom [P]an [T]ools")
        return lines
