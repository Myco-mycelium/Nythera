"""
Nyrqis OS - Package Manager GUI
Search, install, update, and dependency visualization.
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Set


class PackageStatus(Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    UPDATABLE = "updatable"
    REMOVABLE = "removable"
    BROKEN = "broken"
    HELD = "held"


class PackageCategory(Enum):
    SYSTEM = "system"
    DEVELOPMENT = "development"
    UTILITIES = "utilities"
    INTERNET = "internet"
    MULTIMEDIA = "multimedia"
    GRAPHICS = "graphics"
    OFFICE = "office"
    GAMES = "games"
    SECURITY = "security"
    LIBRARIES = "libraries"


@dataclass
class Package:
    name: str
    id: str = ""
    version: str = ""
    latest_version: str = ""
    description: str = ""
    category: PackageCategory = PackageCategory.SYSTEM
    app_category: Optional[str] = None
    status: PackageStatus = PackageStatus.AVAILABLE
    size_bytes: int = 0
    installed_size_bytes: int = 0
    dependencies: List[str] = field(default_factory=list)
    reverse_deps: List[str] = field(default_factory=list)
    maintainer: str = ""
    homepage: str = ""
    license: str = ""
    repo: str = "main"
    download_count: int = 0
    rating: float = 0.0
    last_updated: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = self.name.lower().replace(' ', '-')

    @property
    def is_installed(self) -> bool:
        return self.status in (PackageStatus.INSTALLED, PackageStatus.REMOVABLE, PackageStatus.UPDATABLE)

    @property
    def has_update(self) -> bool:
        return self.status == PackageStatus.UPDATABLE

    @property
    def status_icon(self) -> str:
        icons = {
            PackageStatus.INSTALLED: "✅", PackageStatus.AVAILABLE: "📥",
            PackageStatus.UPDATABLE: "🔄", PackageStatus.REMOVABLE: "🗑️",
            PackageStatus.BROKEN: "❌", PackageStatus.HELD: "🔒",
        }
        return icons.get(self.status, "?")

    @property
    def size_display(self) -> str:
        s = self.size_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def update_available(self) -> bool:
        return self.status == PackageStatus.UPDATABLE

    @property
    def rating_stars(self) -> str:
        full = int(self.rating)
        return "★" * full + "☆" * (5 - full)


@dataclass
class PackageOperation:
    package_name: str
    operation: str = "install"  # install, remove, update, upgrade
    status: str = "pending"  # pending, running, completed, failed
    progress: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0
    log: List[str] = field(default_factory=list)

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def operation_icon(self) -> str:
        icons = {"install": "📥", "remove": "🗑️", "update": "🔄", "upgrade": "⬆️"}
        return icons.get(self.operation, "?")


@dataclass
class Repository:
    name: str
    url: str = ""
    enabled: bool = True
    trusted: bool = True
    package_count: int = 0
    last_sync: float = 0.0
    priority: int = 100


class PackageManager:
    def __init__(self):
        self.packages: List[Package] = []
        self.operations: List[PackageOperation] = []
        self.repositories: List[Repository] = []
        self.search_query: str = ""
        self.selected_packages: List[str] = []
        self.current_package: Optional[Package] = None
        self.auto_update: bool = False
        self._visible: bool = False
        self._selected_index: int = 0
        self._selected_pkg: Optional[Package] = None
        self._view: str = "all"
        self._callbacks: list = []
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()
        self.packages = [
            Package(name="nyrqis-kernel", version="1.0.0-rc1", latest_version="1.0.0-rc1",
                     description="Nyrqis OS custom kernel with optimized Wayland support",
                     category=PackageCategory.SYSTEM, status=PackageStatus.INSTALLED,
                     size_bytes=85000000, installed_size_bytes=280000000,
                     dependencies=["linux-firmware", "base"], maintainer="Nyrqis Team",
                     homepage="https://github.com/Myco-mycelium/Nythera", license="GPL-2.0",
                     repo="nyrqis", download_count=15000, rating=4.8,
                     last_updated=now - 86400 * 7),
            Package(name="nyrqis-compositor", version="0.9.5", latest_version="1.0.0",
                     description="Nyrqis Wayland compositor with Vulkan rendering",
                     category=PackageCategory.SYSTEM, status=PackageStatus.UPDATABLE,
                     size_bytes=45000000, installed_size_bytes=120000000,
                     dependencies=["nyrqis-kernel", "vulkan-tools", "mesa"],
                     maintainer="Nyrqis Team", license="MIT", repo="nyrqis",
                     download_count=12000, rating=4.7, last_updated=now - 86400 * 3),
            Package(name="nyrqis-shell", version="0.8.2", latest_version="0.9.0",
                     description="Nyrqis desktop shell with widgets and panels",
                     category=PackageCategory.SYSTEM, status=PackageStatus.UPDATABLE,
                     size_bytes=32000000, installed_size_bytes=85000000,
                     dependencies=["nyrqis-compositor", "gtk4", "libadwaita"],
                     maintainer="Nyrqis Team", license="MIT", repo="nyrqis",
                     download_count=11000, rating=4.6, last_updated=now - 86400 * 5),
            Package(name="firefox-nyrqis", version="128.0", latest_version="129.0",
                     description="Firefox with Nyrqis OS integration and theming",
                     category=PackageCategory.INTERNET, status=PackageStatus.UPDATABLE,
                     size_bytes=120000000, installed_size_bytes=350000000,
                     dependencies=["gtk3", "alsa-lib", "nss"],
                     maintainer="Mozilla", license="MPL-2.0", repo="community",
                     download_count=50000, rating=4.5, last_updated=now - 86400 * 2),
            Package(name="rust", version="1.80.0", latest_version="1.81.0",
                     description="Rust programming language toolchain",
                     category=PackageCategory.DEVELOPMENT, status=PackageStatus.UPDATABLE,
                     size_bytes=250000000, installed_size_bytes=750000000,
                     dependencies=["gcc", "libc"],
                     maintainer="Mozilla", license="MIT/Apache-2.0", repo="community",
                     download_count=100000, rating=4.9, last_updated=now - 86400),
            Package(name="code-server", version="4.90.4", latest_version="4.91.0",
                     description="VS Code in the browser",
                     category=PackageCategory.DEVELOPMENT, status=PackageStatus.UPDATABLE,
                     size_bytes=95000000, installed_size_bytes=280000000,
                     dependencies=["nodejs", "python3"],
                     maintainer="Coder", license="MIT", repo="community",
                     download_count=35000, rating=4.4, last_updated=now - 86400 * 4),
            Package(name="gimp", version="2.10.38", latest_version="2.10.38",
                     description="GNU Image Manipulation Program",
                     category=PackageCategory.GRAPHICS, status=PackageStatus.INSTALLED,
                     size_bytes=75000000, installed_size_bytes=250000000,
                     dependencies=["gtk3", "babl", "gegl"],
                     maintainer="GIMP Team", license="GPL-3.0", repo="community",
                     download_count=80000, rating=4.3, last_updated=now - 86400 * 30),
            Package(name="blender", version="4.1.0", latest_version="4.2.0",
                     description="3D creation suite",
                     category=PackageCategory.GRAPHICS, status=PackageStatus.AVAILABLE,
                     size_bytes=180000000, installed_size_bytes=650000000,
                     dependencies=["python3", "opengl", "vulkan"],
                     maintainer="Blender Foundation", license="GPL-2.0", repo="community",
                     download_count=45000, rating=4.7, last_updated=now - 86400 * 14),
            Package(name="obs-studio", version="30.1.0", latest_version="30.1.0",
                     description="Open Broadcaster Software",
                     category=PackageCategory.MULTIMEDIA, status=PackageStatus.INSTALLED,
                     size_bytes=65000000, installed_size_bytes=200000000,
                     dependencies=["ffmpeg", "qt6-base", "pipewire"],
                     maintainer="OBS Project", license="GPL-2.0", repo="community",
                     download_count=60000, rating=4.6, last_updated=now - 86400 * 21),
            Package(name="neovim", version="0.10.0", latest_version="0.10.1",
                     description="Hyperextensible Vim-based text editor",
                     category=PackageCategory.DEVELOPMENT, status=PackageStatus.UPDATABLE,
                     size_bytes=12000000, installed_size_bytes=35000000,
                     dependencies=["libuv", "luajit"],
                     maintainer="Neovim Team", license="Apache-2.0", repo="community",
                     download_count=75000, rating=4.8, last_updated=now - 86400 * 10),
            Package(name="docker", version="27.0.0", latest_version="27.0.3",
                     description="Container platform",
                     category=PackageCategory.SYSTEM, status=PackageStatus.UPDATABLE,
                     size_bytes=55000000, installed_size_bytes=180000000,
                     dependencies=["containerd", "runc", "iptables"],
                     maintainer="Docker Inc.", license="Apache-2.0", repo="community",
                     download_count=120000, rating=4.5, last_updated=now - 86400 * 2),
            Package(name="signal-desktop", version="7.12.0", latest_version="7.14.0",
                     description="Signal private messenger",
                     category=PackageCategory.INTERNET, status=PackageStatus.UPDATABLE,
                     size_bytes=110000000, installed_size_bytes=320000000,
                     dependencies=["gtk3", "libnotify"],
                     maintainer="Signal Foundation", license="GPL-3.0", repo="community",
                     download_count=90000, rating=4.7, last_updated=now - 86400 * 3),
            Package(name="htop", version="3.3.0", latest_version="3.3.0",
                     description="Interactive process viewer",
                     category=PackageCategory.UTILITIES, status=PackageStatus.INSTALLED,
                     size_bytes=800000, installed_size_bytes=2500000,
                     dependencies=["ncurses"],
                     maintainer="htop developers", license="GPL-2.0", repo="community",
                     download_count=200000, rating=4.8, last_updated=now - 86400 * 60),
            Package(name="yt-dlp", version="2024.7.7", latest_version="2024.8.1",
                     description="YouTube downloader and more",
                     category=PackageCategory.MULTIMEDIA, status=PackageStatus.UPDATABLE,
                     size_bytes=2000000, installed_size_bytes=8000000,
                     dependencies=["python3"],
                     maintainer="yt-dlp team", license="Unlicense",
                     repo="community", download_count=150000, rating=4.6,
                     last_updated=now - 86400 * 5),
        ]

        self.repositories = [
            Repository(name="nyrqis", url="https://repo.nyrqis.dev/stable",
                        enabled=True, trusted=True, package_count=45,
                        last_sync=now - 3600),
            Repository(name="community", url="https://repo.archlinux.org/community",
                        enabled=True, trusted=True, package_count=12500,
                        last_sync=now - 7200),
            Repository(name="extra", url="https://repo.archlinux.org/extra",
                        enabled=True, trusted=True, package_count=8200,
                        last_sync=now - 7200),
            Repository(name="aur", url="https://aur.archlinux.org",
                        enabled=True, trusted=False, package_count=75000,
                        last_sync=now - 86400),
        ]

    def search(self, query: str) -> List[Package]:
        self.search_query = query
        q = query.lower()
        return [p for p in self.packages if q in p.name.lower() or q in p.description.lower()]

    def get_installed(self) -> List[Package]:
        return [p for p in self.packages if p.status == PackageStatus.INSTALLED]

    def get_updatable(self) -> List[Package]:
        return [p for p in self.packages if p.status == PackageStatus.UPDATABLE]

    def get_available(self) -> List[Package]:
        return [p for p in self.packages if p.status in (PackageStatus.AVAILABLE, PackageStatus.UPDATABLE)]

    def select_package(self, name: str) -> Optional[Package]:
        pkg = next((p for p in self.packages if p.name == name), None)
        if pkg:
            self.current_package = pkg
        return pkg

    def install_package(self, name: str) -> Optional[PackageOperation]:
        pkg = next((p for p in self.packages if p.name == name), None)
        if pkg and pkg.status == PackageStatus.AVAILABLE:
            op = PackageOperation(package_name=name, operation="install",
                                   status="completed", progress=100.0)
            self.operations.append(op)
            pkg.status = PackageStatus.INSTALLED
            pkg.installed_size_bytes = pkg.size_bytes * 3
            return op
        return None

    def remove_package(self, name: str) -> Optional[PackageOperation]:
        pkg = next((p for p in self.packages if p.name == name), None)
        if pkg and pkg.status == PackageStatus.INSTALLED:
            op = PackageOperation(package_name=name, operation="remove",
                                   status="completed", progress=100.0)
            self.operations.append(op)
            pkg.status = PackageStatus.AVAILABLE
            return op
        return None

    def update_package(self, name: str) -> Optional[PackageOperation]:
        pkg = next((p for p in self.packages if p.name == name), None)
        if pkg and pkg.status == PackageStatus.UPDATABLE:
            op = PackageOperation(package_name=name, operation="update",
                                   status="completed", progress=100.0)
            self.operations.append(op)
            pkg.version = pkg.latest_version
            pkg.status = PackageStatus.INSTALLED
            return op
        return None

    def upgrade_all(self) -> int:
        count = 0
        for pkg in self.packages:
            if pkg.status == PackageStatus.UPDATABLE:
                self.update_package(pkg.name)
                count += 1
        return count

    def get_dependencies(self, name: str) -> List[Package]:
        pkg = next((p for p in self.packages if p.name == name), None)
        if not pkg:
            return []
        deps = []
        for dep_name in pkg.dependencies:
            dep = next((p for p in self.packages if p.name == dep_name), None)
            if dep:
                deps.append(dep)
        return deps

    def get_reverse_dependencies(self, name: str) -> List[Package]:
        return [p for p in self.packages if name in p.dependencies]

    def get_category_count(self) -> Dict[str, int]:
        counts = {}
        for p in self.packages:
            cat = p.category.value
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def get_stats(self) -> Dict:
        return {
            "total_packages": len(self.packages),
            "installed": len(self.get_installed()),
            "updatable": len(self.get_updatable()),
            "available": len(self.get_available()),
            "repositories": len(self.repositories),
            "operations": len(self.operations),
        }

    # -- New test-facing API --

    @property
    def visible(self) -> bool:
        return getattr(self, '_visible', False)

    @property
    def package_count(self) -> int:
        return len(self.packages)

    @property
    def installed_count(self) -> int:
        return len([p for p in self.packages if p.is_installed])

    @property
    def update_count(self) -> int:
        return len([p for p in self.packages if p.has_update])

    @property
    def selected_index(self) -> int:
        return getattr(self, '_selected_index', 0)

    @property
    def selected_package(self) -> Optional[Package]:
        return getattr(self, '_selected_pkg', None)

    @property
    def current_view(self) -> str:
        return getattr(self, '_view', 'all')

    @property
    def categories(self) -> Dict[str, int]:
        return self.get_category_count()

    def show(self):
        self._visible = True
        self._emit('shown', {})

    def hide(self):
        self._visible = False

    def toggle(self) -> bool:
        self._visible = not self._visible
        if self._visible:
            self._emit('shown', {})
        return self._visible

    def set_category(self, category):
        cat_val = category.value if hasattr(category, 'value') else category
        # Filter and set category on each remaining package
        self.packages = [p for p in self.packages
                         if getattr(p, 'app_category', None) == category
                         or (p.category and p.category.value == cat_val)]
        for p in self.packages:
            p.category = category

    def set_sort(self, sort_key: str):
        if sort_key == 'rating':
            self.packages.sort(key=lambda p: p.rating, reverse=True)
        elif sort_key == 'name':
            self.packages.sort(key=lambda p: p.name)
        elif sort_key == 'size':
            self.packages.sort(key=lambda p: p.size_bytes, reverse=True)

    def set_view(self, view: str):
        self._view = view
        if view == 'installed':
            self.packages = [p for p in self.packages if p.is_installed]

    def get_package(self, pkg_id: str) -> Optional[Package]:
        for p in self.packages:
            if p.id == pkg_id:
                return p
        return None

    def install_package(self, pkg_id: str) -> bool:
        pkg = self.get_package(pkg_id)
        if pkg and not pkg.is_installed:
            pkg.status = PackageStatus.INSTALLED
            return True
        return False

    def uninstall_package(self, pkg_id: str) -> bool:
        pkg = self.get_package(pkg_id)
        if pkg and pkg.is_installed:
            pkg.status = PackageStatus.AVAILABLE
            return True
        return False

    def update_package(self, pkg_id: str) -> bool:
        pkg = self.get_package(pkg_id)
        if pkg and pkg.has_update:
            pkg.version = pkg.latest_version
            pkg.status = PackageStatus.INSTALLED
            return True
        return False

    def navigate_down(self):
        idx = getattr(self, '_selected_index', 0)
        if idx < len(self.packages) - 1:
            self._selected_index = idx + 1

    def navigate_up(self):
        idx = getattr(self, '_selected_index', 0)
        if idx > 0:
            self._selected_index = idx - 1

    def activate_selected(self) -> Optional[Package]:
        idx = getattr(self, '_selected_index', 0)
        if 0 <= idx < len(self.packages):
            self._selected_pkg = self.packages[idx]
            self._view = 'detail'
            return self._selected_pkg
        return None

    def select_package(self, pkg_id: str):
        pkg = self.get_package(pkg_id)
        if pkg:
            self._selected_pkg = pkg
            self._view = 'detail'

    def render(self) -> Optional[List[str]]:
        if not self.visible:
            return None
        lines = [f"=== Packages ({self.package_count}) ==="]
        for p in self.packages[:20]:
            lines.append(f"  {p.status_icon} {p.name} {p.version}")
        return lines

    def on_event(self, callback):
        self._callbacks = getattr(self, '_callbacks', [])
        self._callbacks.append(callback)

    def _emit(self, event_type: str, data: dict):
        for cb in getattr(self, '_callbacks', []):
            try:
                cb(event_type, data)
            except Exception:
                pass


class AppCategory(Enum):
    SYSTEM = "system"
    DEVELOPMENT = "development"
    UTILITIES = "utilities"
    INTERNET = "internet"
    MULTIMEDIA = "multimedia"
    GRAPHICS = "graphics"
    OFFICE = "office"
    GAMES = "games"
    OTHER = "other"


@dataclass
class PackageInfo:
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    size_bytes: int = 0
    state: Optional[str] = None
    rating: float = 0.0
    installed: bool = False
    app_category: Optional[AppCategory] = None

    def __post_init__(self):
        if not self.id:
            self.id = self.name.lower().replace(' ', '-') if self.name else ''

    @property
    def is_installed(self) -> bool:
        if self.state is not None:
            if hasattr(self.state, 'value'):
                return self.state.value in ('installed',)
            return self.state in ('installed', 'INSTALLED')
        return self.installed

    @property
    def display_size(self) -> str:
        s = self.size_bytes
        if s < 1024:
            return f"{s} B"
        elif s < 1024 * 1024:
            return f"{s / 1024:.1f} KB"
        elif s < 1024 * 1024 * 1024:
            return f"{s / (1024 * 1024):.1f} MB"
        return f"{s / (1024 * 1024 * 1024):.2f} GB"

    @property
    def stars(self) -> str:
        full = int(self.rating)
        return '★' * full + '☆' * (5 - full)


class PackageState(Enum):
    AVAILABLE = "available"
    INSTALLED = "installed"
    UPDATABLE = "updatable"
    REMOVED = "removed"
