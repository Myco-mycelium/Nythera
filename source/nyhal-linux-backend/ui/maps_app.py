"""
Nyrqis Maps — mapping and location application.

Features:
- Interactive map with zoom/pan (simulated with ASCII grid)
- Location search with geocoding
- Favorites/bookmarks for places
- Distance calculator between points
- Coordinate display (lat/long, UTM)
- Map layers (terrain, satellite, traffic)
- Route planning with waypoints
- Location history
- POI categories (restaurants, hotels, gas stations, etc.)
- Keyboard navigation
"""

import time
import math
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class MapLayer(Enum):
    STANDARD = "Standard"
    TERRAIN = "Terrain"
    SATELLITE = "Satellite"
    TRAFFIC = "Traffic"
    TRANSIT = "Transit"
    BICYCLE = "Bicycle"


class POICategory(Enum):
    RESTAURANT = "Restaurant"
    HOTEL = "Hotel"
    GAS_STATION = "Gas Station"
    HOSPITAL = "Hospital"
    PARK = "Park"
    STORE = "Store"
    SCHOOL = "School"
    BANK = "Bank"
    AIRPORT = "Airport"
    PHARMACY = "Pharmacy"


POICATEGORY_ICONS = {
    POICategory.RESTAURANT: "🍽️",
    POICategory.HOTEL: "🏨",
    POICategory.GAS_STATION: "⛽",
    POICategory.HOSPITAL: "🏥",
    POICategory.PARK: "🌳",
    POICategory.STORE: "🏪",
    POICategory.SCHOOL: "🏫",
    POICategory.BANK: "🏦",
    POICategory.AIRPORT: "✈️",
    POICategory.PHARMACY: "💊",
}


@dataclass
class Location:
    """A geographic location."""
    name: str
    latitude: float
    longitude: float
    address: str = ""
    category: POICategory = POICategory.RESTAURANT
    rating: float = 0.0
    phone: str = ""
    hours: str = ""
    notes: str = ""
    location_id: str = ""

    def __post_init__(self):
        if not self.location_id:
            self.location_id = hashlib.md5(f"{self.name}{self.latitude}".encode()).hexdigest()[:8]

    @property
    def coord_str(self) -> str:
        lat_dir = "N" if self.latitude >= 0 else "S"
        lon_dir = "E" if self.longitude >= 0 else "W"
        return f"{abs(self.latitude):.4f}°{lat_dir}, {abs(self.longitude):.4f}°{lon_dir}"

    @property
    def icon(self) -> str:
        return POICATEGORY_ICONS.get(self.category, "📍")

    @property
    def rating_str(self) -> str:
        if self.rating <= 0:
            return ""
        stars = int(self.rating)
        return "⭐" * stars + f" {self.rating:.1f}"


@dataclass
class Route:
    """A planned route."""
    name: str
    waypoints: List[Location] = field(default_factory=list)
    distance_km: float = 0.0
    duration_minutes: float = 0.0
    route_type: str = "driving"  # driving, walking, cycling
    created: float = field(default_factory=time.time)
    route_id: str = ""

    def __post_init__(self):
        if not self.route_id:
            self.route_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def distance_str(self) -> str:
        if self.distance_km < 1:
            return f"{self.distance_km * 1000:.0f} m"
        return f"{self.distance_km:.1f} km"

    @property
    def duration_str(self) -> str:
        h = int(self.duration_minutes // 60)
        m = int(self.duration_minutes % 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m} min"

    @property
    def summary(self) -> str:
        return f"{self.distance_str} · {self.duration_str} · {self.route_type}"


@dataclass
class MapState:
    """Current map view state."""
    center_lat: float = 37.7749
    center_lon: float = -122.4194
    zoom: int = 12
    layer: MapLayer = MapLayer.STANDARD
    show_pois: bool = True
    show_traffic: bool = False
    show_labels: bool = True


# ─── Maps App ────────────────────────────────────────────────────────────


class MapsApp:
    """
    Maps and location application for Nyrqis OS.
    """

    def __init__(self):
        self._map_state = MapState()
        self._locations: List[Location] = []
        self._favorites: List[Location] = []
        self._routes: List[Route] = []
        self._search_results: List[Location] = []
        self._search_query: str = ""
        self._selected_index: int = 0
        self._view_mode: str = "map"  # map, search, favorites, route, details
        self._route_mode: bool = False
        self._route_waypoints: List[Location] = []

        # Callbacks
        self._on_location_change: List[Callable] = []

        # Init sample data
        self._init_sample_data()

    def _init_sample_data(self) -> None:
        self._locations = [
            Location("Golden Gate Park", 37.7694, -122.4862, "Golden Gate Park, San Francisco, CA",
                     POICategory.PARK, 4.7, "", "Open 24 hours"),
            Location("Fisherman's Wharf", 37.8080, -122.4177, "Fisherman's Wharf, San Francisco, CA",
                     POICategory.RESTAURANT, 4.2, "", "Open 10AM-10PM"),
            Location("Union Square", 37.7879, -122.4074, "Union Square, San Francisco, CA",
                     POICategory.STORE, 4.4, "", "Open 10AM-9PM"),
            Location("Chinatown", 37.7941, -122.4078, "Chinatown, San Francisco, CA",
                     POICategory.RESTAURANT, 4.5, "", "Open 24 hours"),
            Location("Mission District", 37.7599, -122.4148, "Mission District, San Francisco, CA",
                     POICategory.RESTAURANT, 4.3, "", ""),
            Location("SFO Airport", 37.6213, -122.3790, "San Francisco International Airport",
                     POICategory.AIRPORT, 4.3, "+1-650-821-8211", "Open 24 hours"),
            Location("UCSF Medical Center", 37.7631, -122.4586, "505 Parnassus Ave, San Francisco, CA",
                     POICategory.HOSPITAL, 4.1, "+1-415-476-1000", "Open 24 hours"),
            Location("Fillmore Center", 37.7845, -122.4332, "1455 Fillmore St, San Francisco, CA",
                     POICategory.HOTEL, 4.0, "+1-415-771-9800", "Check-in: 3PM"),
            Location("Berkeley Marina", 37.8651, -122.3169, "Berkeley Marina, Berkeley, CA",
                     POICategory.PARK, 4.4, "", "Open 6AM-10PM"),
            Location("Sausalito", 37.8591, -122.4853, "Sausalito, CA",
                     POICategory.RESTAURANT, 4.6, "", ""),
        ]

        self._favorites = [
            self._locations[0],  # Golden Gate Park
            self._locations[4],  # Mission District
        ]

        self._routes = [
            Route("Airport to Hotel", [self._locations[5], self._locations[7]],
                  25.3, 35, "driving"),
            Route("Tourist Route", [self._locations[1], self._locations[0], self._locations[3]],
                  12.5, 45, "walking"),
        ]

    # ── Search ────────────────────────────────────────────────────────

    def search(self, query: str) -> List[Location]:
        self._search_query = query
        if not query:
            self._search_results = []
            return []
        q = query.lower()
        self._search_results = [
            loc for loc in self._locations
            if q in loc.name.lower() or q in loc.address.lower() or q in loc.category.value.lower()
        ]
        return self._search_results

    # ── Location Operations ───────────────────────────────────────────

    def pan_map(self, dx: float, dy: float) -> None:
        self._map_state.center_lat += dy
        self._map_state.center_lon += dx

    def zoom_in(self) -> int:
        self._map_state.zoom = min(20, self._map_state.zoom + 1)
        return self._map_state.zoom

    def zoom_out(self) -> int:
        self._map_state.zoom = max(1, self._map_state.zoom - 1)
        return self._map_state.zoom

    def set_layer(self, layer: MapLayer) -> None:
        self._map_state.layer = layer

    def cycle_layer(self) -> MapLayer:
        layers = list(MapLayer)
        idx = layers.index(self._map_state.layer)
        self._map_state.layer = layers[(idx + 1) % len(layers)]
        return self._map_state.layer

    def go_to_location(self, location: Location) -> None:
        self._map_state.center_lat = location.latitude
        self._map_state.center_lon = location.longitude

    def add_favorite(self, location: Location) -> bool:
        if location not in self._favorites:
            self._favorites.append(location)
            return True
        return False

    def remove_favorite(self, location_id: str) -> bool:
        for i, loc in enumerate(self._favorites):
            if loc.location_id == location_id:
                self._favorites.pop(i)
                return True
        return False

    def is_favorite(self, location_id: str) -> bool:
        return any(loc.location_id == location_id for loc in self._favorites)

    # ── Route Planning ────────────────────────────────────────────────

    def start_route(self) -> None:
        self._route_mode = True
        self._route_waypoints.clear()

    def add_waypoint(self, location: Location) -> None:
        self._route_waypoints.append(location)

    def finish_route(self) -> Optional[Route]:
        if len(self._route_waypoints) >= 2:
            # Calculate approximate distance
            total_dist = 0
            for i in range(len(self._route_waypoints) - 1):
                p1 = self._route_waypoints[i]
                p2 = self._route_waypoints[i + 1]
                total_dist += self._haversine(p1.latitude, p1.longitude, p2.latitude, p2.longitude)

            route = Route(
                name=f"Route to {self._route_waypoints[-1].name}",
                waypoints=list(self._route_waypoints),
                distance_km=total_dist,
                duration_minutes=total_dist * 4,  # ~15 km/h average
            )
            self._routes.append(route)
            self._route_mode = False
            self._route_waypoints.clear()
            return route
        return None

    def cancel_route(self) -> None:
        self._route_mode = False
        self._route_waypoints.clear()

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in km."""
        R = 6371  # Earth's radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.asin(math.sqrt(a))
        return R * c

    def calculate_distance(self, loc1: Location, loc2: Location) -> float:
        return self._haversine(loc1.latitude, loc1.longitude, loc2.latitude, loc2.longitude)

    # ── Selection ─────────────────────────────────────────────────────

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        max_idx = len(self._get_current_list()) - 1
        self._selected_index = min(max_idx, self._selected_index + 1)

    def _get_current_list(self) -> List:
        if self._view_mode == "search":
            return self._search_results
        elif self._view_mode == "favorites":
            return self._favorites
        elif self._view_mode == "route":
            return self._routes
        return self._locations

    def get_selected_location(self) -> Optional[Location]:
        lst = self._get_current_list()
        if 0 <= self._selected_index < len(lst):
            item = lst[self._selected_index]
            if isinstance(item, Location):
                return item
        return None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def map_state(self) -> MapState:
        return self._map_state

    @property
    def favorites(self) -> List[Location]:
        return list(self._favorites)

    @property
    def routes(self) -> List[Route]:
        return list(self._routes)

    @property
    def search_results(self) -> List[Location]:
        return list(self._search_results)

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def route_mode(self) -> bool:
        return self._route_mode

    @property
    def route_waypoints(self) -> List[Location]:
        return list(self._route_waypoints)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_map(self, width: int = 60, height: int = 20) -> List[str]:
        lines = []
        ms = self._map_state

        # Header
        lines.append(f" 🗺️  Nyrqis Maps — {ms.layer.value}")
        lines.append(f" 📍 {ms.center_lat:.4f}, {ms.center_lon:.4f} (zoom: {ms.zoom})")
        lines.append("─" * width)

        # ASCII map grid
        grid_h = min(height - 6, 15)
        grid_w = min(width - 4, 50)

        for y in range(grid_h):
            line = " │"
            for x in range(grid_w):
                # Calculate lat/lon for this cell
                cell_lat = ms.center_lat + (grid_h / 2 - y) * 0.01 / ms.zoom
                cell_lon = ms.center_lon + (x - grid_w / 2) * 0.01 / ms.zoom

                # Check if any location is nearby
                char = "·"
                for loc in self._locations:
                    dist = abs(loc.latitude - cell_lat) + abs(loc.longitude - cell_lon)
                    if dist < 0.005 / ms.zoom:
                        char = loc.icon
                        break

                # Center marker
                if y == grid_h // 2 and x == grid_w // 2:
                    char = "◎"

                line += char
            line += "│"
            lines.append(line)

        lines.append("─" * width)

        # Route info
        if self._route_mode:
            lines.append(f" 🛣️  Route mode: {len(self._route_waypoints)} waypoints")
        elif self._routes:
            lines.append(f" 📌 {len(self._favorites)} favorites · {len(self._routes)} routes")

        lines.append("─" * width)
        lines.append(" Arrow keys:Pan  +/-:Zoom  S:Search  F:Favorites  R:Route  L:Layer")
        return lines

    def render_search(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(" 🔍 Location Search")
        lines.append("─" * width)
        lines.append(f" Query: {self._search_query}")
        lines.append("─" * width)

        results = self._search_results
        if not results and self._search_query:
            lines.append("  No results found.")
        elif not results:
            lines.append("  Type to search for locations...")
        else:
            for i, loc in enumerate(results):
                marker = "▸" if i == self._selected_index else " "
                fav = " ⭐" if self.is_favorite(loc.location_id) else ""
                lines.append(f"{marker} {loc.icon} {loc.name}{fav}")
                lines.append(f"   {loc.coord_str}")
                if loc.address:
                    lines.append(f"   📍 {loc.address[:width - 5]}")
                if loc.rating > 0:
                    lines.append(f"   {loc.rating_str}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  G:Go to  ⭐:Favorite")
        return lines

    def render_favorites(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" ⭐ Favorite Places ({len(self._favorites)})")
        lines.append("─" * width)

        if not self._favorites:
            lines.append("  No favorites yet.")
            lines.append("  Search for a place and press ⭐ to add.")
        else:
            for i, loc in enumerate(self._favorites):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {loc.icon} {loc.name}")
                lines.append(f"   {loc.coord_str}")
                if loc.rating > 0:
                    lines.append(f"   {loc.rating_str}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Go to  Del:Remove  S:Search  Esc:Back")
        return lines

    def render_routes(self, width: int = 60) -> List[str]:
        lines = []
        lines.append(f" 🛣️  Saved Routes ({len(self._routes)})")
        lines.append("─" * width)

        if not self._routes:
            lines.append("  No routes saved.")
        else:
            for i, route in enumerate(self._routes):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {route.name}")
                lines.append(f"   {route.summary}")
                lines.append(f"   Waypoints: {len(route.waypoints)}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  N:New route  Del:Delete  Esc:Back")
        return lines

    def render_details(self, width: int = 60) -> List[str]:
        loc = self.get_selected_location()
        if not loc:
            return ["No location selected"]

        lines = []
        lines.append(f" {loc.icon} {loc.name}")
        lines.append("─" * width)
        lines.append(f" 📍 {loc.address}")
        lines.append(f" 🌐 {loc.coord_str}")
        if loc.rating > 0:
            lines.append(f" ⭐ {loc.rating_str}")
        if loc.phone:
            lines.append(f" 📞 {loc.phone}")
        if loc.hours:
            lines.append(f" 🕐 {loc.hours}")
        if loc.notes:
            lines.append(f" 📝 {loc.notes}")
        lines.append("─" * width)
        lines.append(" G:Go to  ⭐:Favorite  R:Route  Esc:Back")
        return lines

    def render(self, width: int = 60, height: int = 30) -> List[str]:
        renderers = {
            "search": self.render_search,
            "favorites": self.render_favorites,
            "route": self.render_routes,
            "details": self.render_details,
        }
        renderer = renderers.get(self._view_mode, self.render_map)
        return renderer(width, height) if self._view_mode == "map" else renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "search":
            return self._handle_search_key(key)
        elif self._view_mode == "favorites":
            return self._handle_favorites_key(key)
        elif self._view_mode == "route":
            return self._handle_route_key(key)
        elif self._view_mode == "details":
            return self._handle_details_key(key)
        return self._handle_map_key(key)

    def _handle_map_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.pan_map(0, 0.005)
            return "pan_up"
        elif key == "ArrowDown":
            self.pan_map(0, -0.005)
            return "pan_down"
        elif key == "ArrowLeft":
            self.pan_map(-0.005, 0)
            return "pan_left"
        elif key == "ArrowRight":
            self.pan_map(0.005, 0)
            return "pan_right"
        elif key == "+" or key == "=":
            self.zoom_in()
            return "zoom_in"
        elif key == "-":
            self.zoom_out()
            return "zoom_out"
        elif key == "s":
            self._view_mode = "search"
            self._selected_index = 0
            return "search"
        elif key == "f":
            self._view_mode = "favorites"
            self._selected_index = 0
            return "favorites"
        elif key == "r":
            self._view_mode = "route"
            self._selected_index = 0
            return "routes"
        elif key == "l":
            self.cycle_layer()
            return "cycle_layer"
        return None

    def _handle_search_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "map"
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            self._view_mode = "details"
            return "details"
        elif key == "g":
            loc = self.get_selected_location()
            if loc:
                self.go_to_location(loc)
                self._view_mode = "map"
            return "go_to"
        elif key == "*":
            loc = self.get_selected_location()
            if loc:
                if self.is_favorite(loc.location_id):
                    self.remove_favorite(loc.location_id)
                else:
                    self.add_favorite(loc)
            return "toggle_favorite"
        return None

    def _handle_favorites_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "map"
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            loc = self.get_selected_location()
            if loc:
                self.go_to_location(loc)
                self._view_mode = "map"
            return "go_to"
        elif key == "Delete":
            loc = self.get_selected_location()
            if loc:
                self.remove_favorite(loc.location_id)
            return "remove_favorite"
        return None

    def _handle_route_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "map"
            return "back"
        elif key == "n":
            self.start_route()
            self._view_mode = "map"
            return "new_route"
        return None

    def _handle_details_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self._view_mode = "search"
            return "back"
        elif key == "g":
            loc = self.get_selected_location()
            if loc:
                self.go_to_location(loc)
                self._view_mode = "map"
            return "go_to"
        elif key == "*":
            loc = self.get_selected_location()
            if loc:
                if self.is_favorite(loc.location_id):
                    self.remove_favorite(loc.location_id)
                else:
                    self.add_favorite(loc)
            return "toggle_favorite"
        return None
