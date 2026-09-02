#!/usr/bin/env python3
"""package_manager — Nyrqis package/app manager UI.

A full app store and package management interface:

- App catalog with categories and search
- Install, update, uninstall workflows
- Download progress tracking
- Dependency resolution
- App ratings and reviews
- Installed apps management
- Update notifications
- Package details (screenshots, description, permissions)
- Sorting and filtering (by name, rating, downloads, size)
- Keyboard navigation
- Renderable app cards and detail views

References:
    - ADR-0025 §9: runtime consumption
    - doc #14: Nyrqis Desktop Shell as a running product
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class PackageState(Enum):
    """Package installation state."""
    AVAILABLE = "available"
    INSTALLING = "installing"
    INSTALLED = "installed"
    UPDATING = "updating"
    UPDATE_AVAILABLE = "update_available"
    UNINSTALLING = "uninstalling"
    ERROR = "error"


class AppCategory(Enum):
    """App categories."""
    SYSTEM = "System"
    DEVELOPER = "Developer"
    INTERNET = "Internet"
    MULTIMEDIA = "Multimedia"
    PRODUCTIVITY = "Productivity"
    GAMES = "Games"
    UTILITIES = "Utilities"
    EDUCATION = "Education"
    OTHER = "Other"


@dataclass
class PackageInfo:
    """App/package information."""
    id: str
    name: str
    version: str
    author: str = ""
    description: str = ""
    long_description: str = ""
    category: AppCategory = AppCategory.OTHER
    icon: str = ""
    screenshot_urls: List[str] = field(default_factory=list)
    homepage: str = ""
    license: str = ""
    size_bytes: int = 0
    rating: float = 0.0
    rating_count: int = 0
    downloads: int = 0
    state: PackageState = PackageState.AVAILABLE
    installed_version: str = ""
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    last_updated: float = 0.0
    download_progress: float = 0.0  # 0.0 - 1.0

    @property
    def is_installed(self) -> bool:
        return self.state in (PackageState.INSTALLED, PackageState.UPDATE_AVAILABLE)

    @property
    def has_update(self) -> bool:
        return self.state == PackageState.UPDATE_AVAILABLE

    @property
    def display_size(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        elif self.size_bytes < 1024 ** 3:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"
        return f"{self.size_bytes / (1024 ** 3):.1f} GB"

    @property
    def stars(self) -> str:
        full = int(self.rating)
        half = 1 if self.rating - full >= 0.5 else 0
        empty = 5 - full - half
        return "★" * full + ("½" if half else "") + "☆" * empty


@dataclass
class AppReview:
    """A user review."""
    user: str
    rating: int          # 1-5
    title: str = ""
    text: str = ""
    timestamp: float = 0.0
    helpful: int = 0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class InstallTask:
    """Background install/update task."""
    package_id: str
    task_type: str     # "install", "update", "uninstall"
    progress: float = 0.0
    status: str = "pending"
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# Package manager UI
# ---------------------------------------------------------------------------

class PackageManager:
    """App store and package management UI.

    Parameters
    ----------
    session : DesktopSession, optional
        The desktop session.
    """

    def __init__(self, session=None) -> None:
        self._session = session
        self._catalog: Dict[str, PackageInfo] = {}
        self._reviews: Dict[str, List[AppReview]] = {}
        self._tasks: List[InstallTask] = []
        self._visible = False

        # View state
        self._view: str = "store"     # store, installed, updates, detail
        self._search_query: str = ""
        self._selected_index: int = 0
        self._selected_package: Optional[str] = None
        self._sort_by: str = "name"   # name, rating, downloads, size
        self._sort_reverse: bool = False
        self._filter_category: Optional[AppCategory] = None
        self._scroll_offset: int = 0

        self._callbacks: List[Callable] = []

        # Seed with sample packages
        self._seed_catalog()

    def _seed_catalog(self) -> None:
        """Seed with sample applications."""
        apps = [
            ("nyrqis-terminal", "Nyrqis Terminal", "1.0.0", "Nyrqis Team",
             "Advanced terminal emulator with tabs, splits, and GPU rendering",
             AppCategory.SYSTEM, "▸", 2_000_000, 4.8, 1250, 45000),
            ("nyrqis-files", "Nyrqis Files", "1.2.0", "Nyrqis Team",
             "File manager with dual pane, bookmarks, and file previews",
             AppCategory.SYSTEM, "📁", 3_500_000, 4.6, 980, 38000),
            ("nyrqis-browser", "Nyfox Browser", "2.1.0", "Nyrqis Team",
             "Privacy-focused web browser with built-in ad blocking",
             AppCategory.INTERNET, "🌐", 15_000_000, 4.7, 2100, 92000),
            ("nyrqis-code", "Nycode Editor", "3.0.0", "Nycode Team",
             "Lightweight code editor with LSP support and extensions",
             AppCategory.DEVELOPER, "⌨", 8_000_000, 4.9, 3200, 120000),
            ("nyrqis-music", "Nymusic", "1.1.0", "Nyrqis Team",
             "Music player with equalizer and playlist management",
             AppCategory.MULTIMEDIA, "♪", 5_000_000, 4.3, 650, 22000),
            ("nyrqis-photos", "Nyphotos", "1.0.0", "Nyrqis Team",
             "Photo viewer and editor with filters and batch processing",
             AppCategory.MULTIMEDIA, "📷", 4_000_000, 4.4, 720, 25000),
            ("nyrqis-settings", "Nyrqis Settings", "1.0.0", "Nyrqis Team",
             "System settings and preferences panel",
             AppCategory.SYSTEM, "⚙", 1_000_000, 4.5, 500, 15000),
            ("nyrqis-monitor", "Nyrqis Monitor", "1.0.0", "Nyrqis Team",
             "Real-time system resource monitor with graphs",
             AppCategory.UTILITIES, "📊", 1_500_000, 4.6, 380, 18000),
            ("nyrqis-calc", "Nycalculator", "1.0.0", "Community",
             "Scientific calculator with unit conversion",
             AppCategory.UTILITIES, "🔢", 800_000, 4.2, 290, 8000),
            ("nyrqis-notes", "Nynotes", "1.0.0", "Community",
             "Markdown note-taking app with sync and tags",
            AppCategory.PRODUCTIVITY, "📝", 2_200_000, 4.5, 410, 15000),
            ("nyrqis-snake", "Nysnake", "1.0.0", "Community",
             "Classic snake game with modern graphics",
             AppCategory.GAMES, "🐍", 1_200_000, 4.0, 180, 5000),
            ("nyrqis-clock", "Nyclock", "1.0.0", "Community",
             "World clock with alarms and timers",
             AppCategory.UTILITIES, "🕐", 600_000, 4.1, 150, 3000),
        ]

        for pkg_id, name, ver, author, desc, cat, icon, size, rating, rcount, dl in apps:
            self._catalog[pkg_id] = PackageInfo(
                id=pkg_id, name=name, version=ver, author=author,
                description=desc, category=cat, icon=icon,
                size_bytes=size, rating=rating, rating_count=rcount,
                downloads=dl, last_updated=time.time() - 86400 * 30,
            )

        # Mark some as installed
        for pid in ["nyrqis-terminal", "nyrqis-files", "nyrqis-settings"]:
            self._catalog[pid].state = PackageState.INSTALLED
            self._catalog[pid].installed_version = self._catalog[pid].version

        # Mark one as having an update
        self._catalog["nyrqis-files"].state = PackageState.UPDATE_AVAILABLE

    # -- View management -----------------------------------------------

    def show(self) -> None:
        self._visible = True
        self._view = "store"
        self._selected_index = 0
        self._dispatch("shown")

    def hide(self) -> None:
        self._visible = False
        self._dispatch("hidden")

    def toggle(self) -> bool:
        if self._visible:
            self.hide()
        else:
            self.show()
        return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    def set_view(self, view: str) -> None:
        """Switch view: store, installed, updates, detail."""
        self._view = view
        self._selected_index = 0
        self._scroll_offset = 0

    @property
    def current_view(self) -> str:
        return self._view

    # -- Search and filter ---------------------------------------------

    def search(self, query: str) -> None:
        self._search_query = query
        self._selected_index = 0
        self._scroll_offset = 0

    def set_sort(self, key: str) -> None:
        if self._sort_by == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_by = key
            self._sort_reverse = False

    def set_category(self, category: Optional[AppCategory]) -> None:
        self._filter_category = category
        self._selected_index = 0

    def scroll(self, delta: int) -> None:
        self._scroll_offset = max(0, self._scroll_offset + delta)

    # -- Package list --------------------------------------------------

    def get_packages(self) -> List[PackageInfo]:
        """Get filtered and sorted package list."""
        packages = list(self._catalog.values())

        # Filter by view
        if self._view == "installed":
            packages = [p for p in packages if p.is_installed]
        elif self._view == "updates":
            packages = [p for p in packages if p.has_update]

        # Filter by search
        if self._search_query:
            q = self._search_query.lower()
            packages = [p for p in packages
                        if q in p.name.lower() or q in p.description.lower()
                        or q in p.author.lower()]

        # Filter by category
        if self._filter_category:
            packages = [p for p in packages if p.category == self._filter_category]

        # Sort
        reverse = self._sort_reverse
        if self._sort_by == "name":
            packages.sort(key=lambda p: p.name.lower(), reverse=reverse)
        elif self._sort_by == "rating":
            packages.sort(key=lambda p: p.rating, reverse=reverse or True)
        elif self._sort_by == "downloads":
            packages.sort(key=lambda p: p.downloads, reverse=reverse or True)
        elif self._sort_by == "size":
            packages.sort(key=lambda p: p.size_bytes, reverse=reverse)

        return packages

    @property
    def packages(self) -> List[PackageInfo]:
        return self.get_packages()

    @property
    def package_count(self) -> int:
        return len(self.get_packages())

    @property
    def installed_count(self) -> int:
        return sum(1 for p in self._catalog.values() if p.is_installed)

    @property
    def update_count(self) -> int:
        return sum(1 for p in self._catalog.values() if p.has_update)

    # -- Package actions -----------------------------------------------

    def select_package(self, package_id: str) -> bool:
        """Select a package for detail view."""
        if package_id in self._catalog:
            self._selected_package = package_id
            self._view = "detail"
            return True
        return False

    def install_package(self, package_id: str) -> bool:
        """Start installing a package."""
        pkg = self._catalog.get(package_id)
        if pkg is None or pkg.is_installed:
            return False

        pkg.state = PackageState.INSTALLING
        pkg.download_progress = 0.0
        task = InstallTask(
            package_id=package_id,
            task_type="install",
            started_at=time.time(),
        )
        self._tasks.append(task)

        # Simulate installation
        pkg.download_progress = 1.0
        pkg.state = PackageState.INSTALLED
        pkg.installed_version = pkg.version
        task.progress = 1.0
        task.status = "completed"
        task.completed_at = time.time()

        self._dispatch("installed", package_id)
        return True

    def update_package(self, package_id: str) -> bool:
        """Start updating a package."""
        pkg = self._catalog.get(package_id)
        if pkg is None or not pkg.has_update:
            return False

        pkg.state = PackageState.UPDATING
        task = InstallTask(
            package_id=package_id,
            task_type="update",
            started_at=time.time(),
        )
        self._tasks.append(task)

        # Simulate update
        pkg.state = PackageState.INSTALLED
        pkg.installed_version = pkg.version
        task.status = "completed"
        task.completed_at = time.time()

        self._dispatch("updated", package_id)
        return True

    def uninstall_package(self, package_id: str) -> bool:
        """Uninstall a package."""
        pkg = self._catalog.get(package_id)
        if pkg is None or not pkg.is_installed:
            return False

        pkg.state = PackageState.AVAILABLE
        pkg.installed_version = ""
        pkg.download_progress = 0.0

        self._dispatch("uninstalled", package_id)
        return True

    def get_package(self, package_id: str) -> Optional[PackageInfo]:
        return self._catalog.get(package_id)

    def get_reviews(self, package_id: str) -> List[AppReview]:
        return list(self._reviews.get(package_id, []))

    def add_review(self, package_id: str, review: AppReview) -> None:
        if package_id not in self._reviews:
            self._reviews[package_id] = []
        self._reviews[package_id].append(review)

    @property
    def active_tasks(self) -> List[InstallTask]:
        return [t for t in self._tasks if t.status == "pending"]

    @property
    def categories(self) -> List[AppCategory]:
        """Get all categories that have packages."""
        cats = set(p.category for p in self._catalog.values())
        return sorted(cats, key=lambda c: c.value)

    @property
    def selected_package(self) -> Optional[PackageInfo]:
        if self._selected_package:
            return self._catalog.get(self._selected_package)
        return None

    @property
    def selected_index(self) -> int:
        return self._selected_index

    def navigate_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def navigate_down(self) -> None:
        max_idx = len(self.get_packages()) - 1
        self._selected_index = min(max_idx, self._selected_index + 1)

    def activate_selected(self) -> Optional[PackageInfo]:
        """Open detail view of selected package."""
        pkgs = self.get_packages()
        if 0 <= self._selected_index < len(pkgs):
            pkg = pkgs[self._selected_index]
            self.select_package(pkg.id)
            return pkg
        return None

    # -- Rendering -----------------------------------------------------

    def render(self, width: int = 1920, height: int = 1080) -> Any:
        """Render the package manager UI."""
        if not self._visible:
            return None

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_bold = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except (OSError, IOError):
            font = font_bold = font_title = font_small = ImageFont.load_default()

        # Panel background
        px, py = 80, 40
        pw, ph = width - 160, height - 80
        draw.rounded_rectangle(
            [px, py, px + pw, py + ph],
            radius=16, fill=(25, 25, 30, 240), outline=(60, 60, 70))

        # Title
        draw.text((px + 20, py + 16), "Nyrqis Store", fill=(220, 220, 220), font=font_title)

        # Navigation tabs
        tabs = [
            ("store", "Store"),
            ("installed", f"Installed ({self.installed_count})"),
            ("updates", f"Updates ({self.update_count})"),
        ]
        tab_x = px + 200
        for tab_id, label in tabs:
            is_active = (self._view == tab_id)
            tw = len(label) * 9 + 20
            if is_active:
                draw.rounded_rectangle(
                    [tab_x, py + 14, tab_x + tw, py + 38],
                    radius=6, fill=(80, 140, 255, 80))
            draw.text((tab_x + 10, py + 18), label,
                      fill=(230, 230, 230) if is_active else (140, 140, 140),
                      font=font)
            tab_x += tw + 8

        # Content
        content_y = py + 50
        content_h = ph - 60

        if self._view == "detail" and self.selected_package:
            self._render_detail(draw, px + 20, content_y, pw - 40, content_h,
                                font, font_bold, font_small)
        else:
            self._render_list(draw, px + 20, content_y, pw - 40, content_h,
                              font, font_bold, font_small)

        return img

    def _render_list(self, draw, x, y, w, h, font, font_bold, font_small):
        """Render the package list."""
        packages = self.get_packages()

        # Category chips
        chip_y = y
        draw.text((x, chip_y), "Category:", fill=(140, 140, 140), font=font_small)
        chip_x = x + 70
        draw.text((chip_x, chip_y), "All",
                  fill=(80, 140, 255) if self._filter_category is None else (140, 140, 140),
                  font=font_small)
        chip_x += 30

        for cat in self.categories:
            draw.text((chip_x, chip_y), cat.value,
                      fill=(80, 140, 255) if self._filter_category == cat else (140, 140, 140),
                      font=font_small)
            chip_x += len(cat.value) * 7 + 16

        # Package cards
        card_y = chip_y + 24
        card_h = 80
        visible_count = min(len(packages), h // (card_h + 8))

        for i, pkg in enumerate(packages[self._scroll_offset:self._scroll_offset + visible_count]):
            cy = card_y + i * (card_h + 8)
            is_selected = (i + self._scroll_offset == self._selected_index)

            # Card background
            bg = (45, 45, 58) if is_selected else (35, 35, 48)
            draw.rounded_rectangle(
                [x, cy, x + w, cy + card_h],
                radius=8, fill=bg, outline=(80, 140, 255) if is_selected else (50, 50, 65))

            # Icon
            draw.rounded_rectangle(
                [x + 12, cy + 12, x + 52, cy + 52],
                radius=8, fill=(60, 60, 80))
            draw.text((x + 20, cy + 18), pkg.icon,
                      fill=(200, 200, 200), font=font_bold)

            # Name and version
            draw.text((x + 64, cy + 10), pkg.name,
                      fill=(230, 230, 230), font=font_bold)
            draw.text((x + 64 + len(pkg.name) * 10 + 8, cy + 12),
                      f"v{pkg.version}",
                      fill=(120, 120, 120), font=font_small)

            # Description
            desc = pkg.description[:60]
            draw.text((x + 64, cy + 30), desc,
                      fill=(160, 160, 160), font=font_small)

            # Rating
            draw.text((x + 64, cy + 48), pkg.stars,
                      fill=(255, 200, 60), font=font_small)
            draw.text((x + 110, cy + 48),
                      f"{pkg.rating} ({pkg.rating_count})",
                      fill=(120, 120, 120), font=font_small)

            # Size
            draw.text((x + w - 80, cy + 12), pkg.display_size,
                      fill=(120, 120, 120), font=font_small)

            # State badge
            if pkg.is_installed:
                draw.rounded_rectangle(
                    [x + w - 80, cy + 48, x + w - 12, cy + 68],
                    radius=4, fill=(60, 140, 80))
                draw.text((x + w - 72, cy + 50), "Installed",
                          fill=(255, 255, 255), font=font_small)
            elif pkg.has_update:
                draw.rounded_rectangle(
                    [x + w - 80, cy + 48, x + w - 12, cy + 68],
                    radius=4, fill=(220, 160, 40))
                draw.text((x + w - 72, cy + 50), "Update",
                          fill=(255, 255, 255), font=font_small)

        if not packages:
            draw.text((x + 20, card_y + 20), "No packages found",
                      fill=(120, 120, 120), font=font)

    def _render_detail(self, draw, x, y, w, h, font, font_bold, font_small):
        """Render package detail view."""
        pkg = self.selected_package
        if pkg is None:
            return

        # Back button
        draw.text((x, y), "← Back", fill=(80, 140, 255), font=font)

        # Package header
        hy = y + 30
        draw.rounded_rectangle(
            [x, hy, x + 56, hy + 56],
            radius=12, fill=(60, 60, 80))
        draw.text((x + 14, hy + 12), pkg.icon,
                  fill=(200, 200, 200), font=font_bold)
        draw.text((x + 68, hy + 4), pkg.name,
                  fill=(230, 230, 230), font=font_bold)
        draw.text((x + 68, hy + 24), f"v{pkg.version} by {pkg.author}",
                  fill=(140, 140, 140), font=font_small)
        draw.text((x + 68, hy + 40), pkg.stars,
                  fill=(255, 200, 60), font=font)

        # Action button
        btn_x = x + w - 160
        if pkg.is_installed:
            if pkg.has_update:
                draw.rounded_rectangle(
                    [btn_x, hy + 8, btn_x + 150, hy + 44],
                    radius=8, fill=(220, 160, 40))
                draw.text((btn_x + 40, hy + 14), "Update",
                          fill=(255, 255, 255), font=font_bold)
            else:
                draw.rounded_rectangle(
                    [btn_x, hy + 8, btn_x + 150, hy + 44],
                    radius=8, fill=(220, 60, 60))
                draw.text((btn_x + 28, hy + 14), "Uninstall",
                          fill=(255, 255, 255), font=font_bold)
        else:
            draw.rounded_rectangle(
                [btn_x, hy + 8, btn_x + 150, hy + 44],
                radius=8, fill=(60, 140, 80))
            draw.text((btn_x + 46, hy + 14), "Install",
                      fill=(255, 255, 255), font=font_bold)

        # Info
        iy = hy + 70
        info_items = [
            f"Size: {pkg.display_size}",
            f"Category: {pkg.category.value}",
            f"Downloads: {pkg.downloads:,}",
            f"License: {pkg.license or 'Proprietary'}",
        ]
        for item in info_items:
            draw.text((x, iy), item, fill=(160, 160, 160), font=font_small)
            iy += 18

        # Description
        iy += 10
        draw.text((x, iy), "Description", fill=(200, 200, 200), font=font_bold)
        iy += 22
        desc = pkg.description
        while desc:
            line = desc[:70]
            desc = desc[70:]
            draw.text((x, iy), line, fill=(160, 160, 160), font=font_small)
            iy += 16

        # Permissions
        if pkg.permissions:
            iy += 10
            draw.text((x, iy), "Permissions", fill=(200, 200, 200), font=font_bold)
            iy += 22
            for perm in pkg.permissions[:5]:
                draw.text((x + 10, iy), f"• {perm}",
                          fill=(160, 160, 160), font=font_small)
                iy += 16

    # -- Callbacks -----------------------------------------------------

    def on_event(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _dispatch(self, event_type: str, data: Any = None) -> None:
        for cb in self._callbacks:
            try:
                cb(event_type, data)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (
            f"PackageManager(packages={len(self._catalog)}, "
            f"installed={self.installed_count}, "
            f"updates={self.update_count})"
        )


__all__ = [
    "PackageManager", "PackageInfo", "PackageState", "AppCategory",
    "AppReview", "InstallTask",
]
