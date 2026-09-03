"""
Nyrqis Repo Manager — package repository management application.

Features:
- Browse and search package repositories
- Install, update, and remove packages
- Dependency resolution with conflict detection
- Version tracking and changelog
- Repository configuration (add/remove/mirror)
- Package categories and tags
- Download statistics and popularity
- Signing key management
- Local package cache management
- Keyboard navigation throughout
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class PackageStatus(Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    UPDATABLE = "updatable"
    REMOVED = "removed"
    BROKEN = "broken"


class RepoStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    MIRROR = "mirror"
    TESTING = "testing"


class SignatureStatus(Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    UNSIGNED = "unsigned"
    REVOKED = "revoked"


PKG_STATUS_ICONS = {
    PackageStatus.INSTALLED: "✅",
    PackageStatus.AVAILABLE: "📦",
    PackageStatus.UPDATABLE: "⬆️",
    PackageStatus.REMOVED: "❌",
    PackageStatus.BROKEN: "⚠️",
}

REPO_STATUS_ICONS = {
    RepoStatus.ACTIVE: "🟢",
    RepoStatus.DISABLED: "🔴",
    RepoStatus.MIRROR: "🔄",
    RepoStatus.TESTING: "🟡",
}

SIGN_ICONS = {
    SignatureStatus.TRUSTED: "🔐",
    SignatureStatus.UNTRUSTED: "⚠️",
    SignatureStatus.UNSIGNED: "🔓",
    SignatureStatus.REVOKED: "🚫",
}


@dataclass
class Package:
    """A software package."""
    name: str
    version: str
    description: str = ""
    maintainer: str = ""
    license: str = ""
    homepage: str = ""
    repository: str = ""
    status: PackageStatus = PackageStatus.AVAILABLE
    signature: SignatureStatus = SignatureStatus.TRUSTED
    # Size
    download_size_kb: int = 0
    installed_size_kb: int = 0
    # Dependencies
    dependencies: List[str] = field(default_factory=list)
    optional_deps: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    # Metadata
    category: str = ""
    tags: List[str] = field(default_factory=list)
    downloads: int = 0
    popularity: float = 0.0
    last_updated: float = field(default_factory=time.time)
    installed_at: float = 0.0
    # Changelog
    changelog: List[Tuple[str, str]] = field(default_factory=list)  # (version, note)

    @property
    def display_size(self) -> str:
        if self.installed_size_kb >= 1048576:
            return f"{self.installed_size_kb / 1048576:.1f} GB"
        elif self.installed_size_kb >= 1024:
            return f"{self.installed_size_kb / 1024:.1f} MB"
        return f"{self.installed_size_kb} KB"

    @property
    def status_icon(self) -> str:
        return PKG_STATUS_ICONS.get(self.status, "❓")

    @property
    def signature_icon(self) -> str:
        return SIGN_ICONS.get(self.signature, "❓")

    @property
    def display_name(self) -> str:
        return f"{self.status_icon} {self.name} {self.version}"

    @property
    def downloads_str(self) -> str:
        if self.downloads >= 1000000:
            return f"{self.downloads / 1000000:.1f}M"
        elif self.downloads >= 1000:
            return f"{self.downloads / 1000:.1f}K"
        return str(self.downloads)

    @property
    def popularity_bar(self) -> str:
        filled = int(self.popularity / 100 * 20)
        return "█" * filled + "░" * (20 - filled)


@dataclass
class Repository:
    """A package repository."""
    name: str
    url: str
    status: RepoStatus = RepoStatus.ACTIVE
    description: str = ""
    priority: int = 100
    package_count: int = 0
    last_sync: float = 0.0
    gpg_key: str = ""
    mirror_url: str = ""
    includes: List[str] = field(default_factory=list)
    excludes: List[str] = field(default_factory=list)

    @property
    def status_icon(self) -> str:
        return REPO_STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.name} ({self.package_count} packages)"

    @property
    def sync_ago(self) -> str:
        if self.last_sync <= 0:
            return "never"
        diff = time.time() - self.last_sync
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return f"{int(diff // 86400)}d ago"


@dataclass
class InstallTransaction:
    """A package install/update/remove transaction."""
    packages: List[str] = field(default_factory=list)
    action: str = "install"  # install, update, remove
    status: str = "pending"  # pending, running, completed, failed
    progress: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    errors: List[str] = field(default_factory=list)
    transaction_id: str = ""

    def __post_init__(self):
        if not self.transaction_id:
            self.transaction_id = hashlib.md5(f"{time.time()}".encode()).hexdigest()[:8]

    @property
    def progress_pct(self) -> float:
        return self.progress * 100

    @property
    def progress_bar(self) -> str:
        filled = int(self.progress * 30)
        return "█" * filled + "░" * (30 - filled)

    @property
    def duration_str(self) -> str:
        if self.started_at <= 0:
            return "—"
        end = self.completed_at if self.completed_at > 0 else time.time()
        d = end - self.started_at
        return f"{d:.1f}s"


# ─── Repo Manager ────────────────────────────────────────────────────────


class RepoManager:
    """
    Package repository manager for Nyrqis OS.
    """

    def __init__(self):
        self._packages: List[Package] = []
        self._repositories: List[Repository] = []
        self._transactions: List[InstallTransaction] = []
        self._current_transaction: Optional[InstallTransaction] = None
        self._selected_index: int = 0
        self._view_mode: str = "packages"  # packages, repos, installed, updates, queue
        self._search_query: str = ""
        self._filter_status: Optional[PackageStatus] = None
        self._filter_category: str = ""
        self._sort_by: str = "name"  # name, downloads, size, updated
        self._sort_desc: bool = False
        self._install_queue: List[str] = []

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._packages = [
            Package("nyrqis-kernel", "6.11.0", "Nyrqis OS Linux kernel",
                    "Nyrqis Team", "GPL-2.0", "https://nyrqis.os/kernel",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=85000, download_size_kb=32000,
                    downloads=245000, popularity=95.0,
                    category="system", tags=["kernel", "core"],
                    installed_at=now - 86400,
                    changelog=[("6.11.0", "NVIDIA DRM fix"), ("6.10.5", "Stability improvements")]),
            Package("nyrqis-desktop", "2.4.0", "Nyrqis desktop environment",
                    "Nyrqis Team", "MIT", "https://nyrqis.os/desktop",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=45000, download_size_kb=18000,
                    downloads=189000, popularity=88.0,
                    category="desktop", tags=["desktop", "gui", "wayland"],
                    installed_at=now - 86400,
                    changelog=[("2.4.0", "New theme engine"), ("2.3.2", "Bug fixes")]),
            Package("firefox", "130.0", "Mozilla Firefox web browser",
                    "Mozilla Corp", "MPL-2.0", "https://mozilla.org",
                    "nyrqis-stable", PackageStatus.UPDATABLE,
                    installed_size_kb=280000, download_size_kb=65000,
                    downloads=890000, popularity=92.0,
                    category="web", tags=["browser", "internet"],
                    installed_at=now - 604800,
                    changelog=[("130.0", "Enhanced tracking protection"), ("129.0", "Performance improvements")]),
            Package("code", "1.92.0", "Visual Studio Code editor",
                    "Microsoft", "MIT", "https://code.visualstudio.com",
                    "nyrqis-stable", PackageStatus.UPDATABLE,
                    installed_size_kb=350000, download_size_kb=95000,
                    downloads=1200000, popularity=96.0,
                    category="development", tags=["editor", "ide", "code"],
                    installed_at=now - 1209600),
            Package("python", "3.12.5", "Python programming language",
                    "Python Team", "PSF", "https://python.org",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=52000, download_size_kb=25000,
                    downloads=2100000, popularity=90.0,
                    category="development", tags=["python", "language", "runtime"],
                    installed_at=now - 259200),
            Package("docker", "27.1.0", "Container platform",
                    "Docker Inc", "Apache-2.0", "https://docker.com",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=120000, download_size_kb=45000,
                    downloads=670000, popularity=85.0,
                    category="system", tags=["container", "devops"]),
            Package("vim", "9.1.0", "Vi IMproved text editor",
                    "Bram Moolenaar", "Vim License", "https://vim.org",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=3500, download_size_kb=1800,
                    downloads=1500000, popularity=78.0,
                    category="development", tags=["editor", "terminal", "text"],
                    installed_at=now - 518400),
            Package("gimp", "2.10.38", "GNU Image Manipulation Program",
                    "GIMP Team", "GPL-3.0", "https://gimp.org",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=180000, download_size_kb=55000,
                    downloads=420000, popularity=72.0,
                    category="graphics", tags=["image", "editor", "photo"]),
            Package("blender", "4.2.0", "3D creation suite",
                    "Blender Foundation", "GPL-3.0", "https://blender.org",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=650000, download_size_kb=220000,
                    downloads=310000, popularity=80.0,
                    category="graphics", tags=["3d", "modeling", "animation"]),
            Package("obsidian", "1.6.5", "Knowledge management tool",
                    "Obsidian", "Proprietary", "https://obsidian.md",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=140000, download_size_kb=75000,
                    downloads=560000, popularity=82.0,
                    category="productivity", tags=["notes", "knowledge", "markdown"]),
            Package("neovim", "0.10.1", "Hyperextensible Vim-based text editor",
                    "Neovim Team", "Apache-2.0", "https://neovim.io",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=4500, download_size_kb=2200,
                    downloads=450000, popularity=76.0,
                    category="development", tags=["editor", "terminal", "vim"]),
            Package("nodejs", "22.6.0", "JavaScript runtime",
                    "OpenJS Foundation", "MIT", "https://nodejs.org",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=65000, download_size_kb=28000,
                    downloads=1800000, popularity=88.0,
                    category="development", tags=["javascript", "runtime", "npm"]),
            Package("ripgrep", "14.1.0", "Fast recursive grep",
                    "Andrew Gallant", "MIT", "https://github.com/BurntSushi/ripgrep",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=800, download_size_kb=400,
                    downloads=380000, popularity=70.0,
                    category="development", tags=["grep", "search", "cli"],
                    installed_at=now - 172800),
            Package("ffmpeg", "7.0.1", "Complete multimedia framework",
                    "FFmpeg Team", "LGPL/GPL", "https://ffmpeg.org",
                    "nyrqis-stable", PackageStatus.INSTALLED,
                    installed_size_kb=95000, download_size_kb=42000,
                    downloads=920000, popularity=86.0,
                    category="multimedia", tags=["video", "audio", "codec"],
                    installed_at=now - 345600),
            Package("spotify-client", "1.2.20", "Spotify music player",
                    "Spotify AB", "Proprietary", "https://spotify.com",
                    "nyrqis-stable", PackageStatus.AVAILABLE,
                    installed_size_kb=210000, download_size_kb=85000,
                    downloads=780000, popularity=84.0,
                    category="multimedia", tags=["music", "streaming", "audio"]),
        ]

        self._repositories = [
            Repository("nyrqis-stable", "https://repo.nyrqis.os/stable",
                       RepoStatus.ACTIVE, "Main stable repository", 100, 15000,
                       now - 3600, "0xABCDEF01"),
            Repository("nyrqis-testing", "https://repo.nyrqis.os/testing",
                       RepoStatus.TESTING, "Testing/beta packages", 50, 3200,
                       now - 7200, "0xABCDEF01"),
            Repository("nyrqis-unstable", "https://repo.nyrqis.os/unstable",
                       RepoStatus.DISABLED, "Bleeding edge packages", 30, 1800,
                       now - 86400, "0xABCDEF01"),
            Repository("flatpak-flathub", "https://flathub.org/repo",
                       RepoStatus.ACTIVE, "Flathub flatpak repository", 100, 8500,
                       now - 1800, ""),
            Repository("nyrqis-backports", "https://repo.nyrqis.os/backports",
                       RepoStatus.ACTIVE, "Backported packages", 80, 450,
                       now - 43200, "0xABCDEF01"),
        ]

    # ── Package Operations ────────────────────────────────────────────

    def install_package(self, name: str) -> bool:
        for pkg in self._packages:
            if pkg.name == name and pkg.status != PackageStatus.INSTALLED:
                # Check deps
                missing = self._resolve_deps(pkg)
                tx = InstallTransaction(
                    packages=[name] + missing,
                    action="install", status="running",
                    started_at=time.time(), progress=1.0,
                )
                self._transactions.insert(0, tx)
                pkg.status = PackageStatus.INSTALLED
                pkg.installed_at = time.time()
                tx.status = "completed"
                tx.completed_at = time.time()
                return True
        return False

    def remove_package(self, name: str) -> bool:
        for pkg in self._packages:
            if pkg.name == name and pkg.status == PackageStatus.INSTALLED:
                tx = InstallTransaction(
                    packages=[name],
                    action="remove", status="running",
                    started_at=time.time(), progress=1.0,
                )
                self._transactions.insert(0, tx)
                pkg.status = PackageStatus.AVAILABLE
                tx.status = "completed"
                tx.completed_at = time.time()
                return True
        return False

    def update_package(self, name: str) -> bool:
        for pkg in self._packages:
            if pkg.name == name and pkg.status == PackageStatus.UPDATABLE:
                tx = InstallTransaction(
                    packages=[name],
                    action="update", status="running",
                    started_at=time.time(), progress=1.0,
                )
                self._transactions.insert(0, tx)
                pkg.status = PackageStatus.INSTALLED
                tx.status = "completed"
                tx.completed_at = time.time()
                return True
        return False

    def update_all(self) -> int:
        count = 0
        for pkg in self._packages:
            if pkg.status == PackageStatus.UPDATABLE:
                self.update_package(pkg.name)
                count += 1
        return count

    def _resolve_deps(self, pkg: Package) -> List[str]:
        """Resolve missing dependencies."""
        installed = {p.name for p in self._packages if p.status == PackageStatus.INSTALLED}
        missing = []
        for dep in pkg.dependencies:
            if dep not in installed:
                missing.append(dep)
        return missing

    def add_to_queue(self, name: str) -> bool:
        if name not in self._install_queue:
            self._install_queue.append(name)
            return True
        return False

    def remove_from_queue(self, name: str) -> bool:
        if name in self._install_queue:
            self._install_queue.remove(name)
            return True
        return False

    # ── Search & Filter ───────────────────────────────────────────────

    def search(self, query: str) -> List[Package]:
        self._search_query = query
        if not query:
            return self._get_filtered_packages()
        q = query.lower()
        return [p for p in self._get_filtered_packages()
                if q in p.name.lower() or q in p.description.lower()
                or q in p.category.lower() or any(q in t for t in p.tags)]

    def set_filter_status(self, status: Optional[PackageStatus]) -> None:
        self._filter_status = status
        self._selected_index = 0

    def _get_filtered_packages(self) -> List[Package]:
        pkgs = list(self._packages)
        if self._filter_status:
            pkgs = [p for p in pkgs if p.status == self._filter_status]
        if self._filter_category:
            pkgs = [p for p in pkgs if p.category == self._filter_category]
        pkgs = self._sort_packages(pkgs)
        return pkgs

    def _sort_packages(self, pkgs: List[Package]) -> List[Package]:
        key_map = {
            "name": lambda p: p.name.lower(),
            "downloads": lambda p: p.downloads,
            "size": lambda p: p.installed_size_kb,
            "updated": lambda p: p.last_updated,
            "popularity": lambda p: p.popularity,
        }
        key = key_map.get(self._sort_by, key_map["name"])
        return sorted(pkgs, key=key, reverse=self._sort_desc)

    def cycle_sort(self) -> str:
        sorts = ["name", "downloads", "size", "updated", "popularity"]
        idx = sorts.index(self._sort_by) if self._sort_by in sorts else 0
        self._sort_by = sorts[(idx + 1) % len(sorts)]
        return self._sort_by

    # ── Navigation ────────────────────────────────────────────────────

    def select_up(self) -> None:
        self._selected_index = max(0, self._selected_index - 1)

    def select_down(self) -> None:
        items = self._get_display_list()
        self._selected_index = min(len(items) - 1, self._selected_index + 1)

    def get_selected_item(self):
        items = self._get_display_list()
        if 0 <= self._selected_index < len(items):
            return items[self._selected_index]
        return None

    def _get_display_list(self) -> list:
        if self._view_mode == "repos":
            return self._repositories
        elif self._view_mode == "installed":
            return [p for p in self._packages if p.status == PackageStatus.INSTALLED]
        elif self._view_mode == "updates":
            return [p for p in self._packages if p.status == PackageStatus.UPDATABLE]
        return self.search(self._search_query)

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def packages(self) -> List[Package]:
        return list(self._packages)

    @property
    def repositories(self) -> List[Repository]:
        return list(self._repositories)

    @property
    def installed_count(self) -> int:
        return sum(1 for p in self._packages if p.status == PackageStatus.INSTALLED)

    @property
    def updatable_count(self) -> int:
        return sum(1 for p in self._packages if p.status == PackageStatus.UPDATABLE)

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._packages if p.status == PackageStatus.AVAILABLE)

    @property
    def total_installed_size(self) -> int:
        return sum(p.installed_size_kb for p in self._packages
                   if p.status == PackageStatus.INSTALLED)

    @property
    def total_size_str(self) -> str:
        kb = self.total_installed_size
        if kb >= 1048576:
            return f"{kb / 1048576:.1f} GB"
        elif kb >= 1024:
            return f"{kb / 1024:.1f} MB"
        return f"{kb} KB"

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def install_queue(self) -> List[str]:
        return list(self._install_queue)

    @property
    def transactions(self) -> List[InstallTransaction]:
        return list(self._transactions)

    @property
    def categories(self) -> List[str]:
        cats = sorted(set(p.category for p in self._packages if p.category))
        return cats

    # ── Rendering ─────────────────────────────────────────────────────

    def render_packages(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📦 Package Manager")
        lines.append("─" * width)
        lines.append(f" {self.installed_count} installed | {self.updatable_count} updatable | {self.available_count} available | {self.total_size_str}")

        sort_desc = " ↓" if self._sort_desc else " ↑"
        lines.append(f" Sort: {self._sort_by}{sort_desc} | Filter: {self._filter_status.value if self._filter_status else 'all'}")
        lines.append("─" * width)

        pkgs = self.search(self._search_query)
        if not pkgs:
            lines.append("  No packages found.")
        else:
            for i, pkg in enumerate(pkgs[:15]):
                marker = "▸" if i == self._selected_index else " "
                in_queue = " 📥" if pkg.name in self._install_queue else ""
                lines.append(f"{marker} {pkg.status_icon} {pkg.name} {pkg.version}{in_queue}")
                lines.append(f"   {pkg.description[:width - 5]}")
                lines.append(f"   {pkg.display_size} | {pkg.downloads_str} downloads | {pkg.category}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  I:Install  U:Update  R:Remove")
        lines.append(" Q:Add to queue  S:Sort  Tab:Installed  Esc:Clear")
        return lines

    def render_repos(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📡 Repositories")
        lines.append("─" * width)

        for i, repo in enumerate(self._repositories):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {repo.display}")
            lines.append(f"   {repo.url}")
            lines.append(f"   {repo.description}")
            lines.append(f"   Synced: {repo.sync_ago} | Priority: {repo.priority}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Toggle  Esc:Back")
        return lines

    def render_installed(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ✅ Installed Packages ({self.installed_count})")
        lines.append("─" * width)

        pkgs = [p for p in self._packages if p.status == PackageStatus.INSTALLED]
        for i, pkg in enumerate(pkgs):
            marker = "▸" if i == self._selected_index else " "
            updatable = " ⬆️" if pkg.status == PackageStatus.UPDATABLE else ""
            lines.append(f"{marker} {pkg.name} {pkg.version}{updatable}")
            lines.append(f"   {pkg.display_size} | {pkg.category}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  R:Remove  U:Update all")
        lines.append(" Esc:Back")
        return lines

    def render_updates(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⬆️  Available Updates ({self.updatable_count})")
        lines.append("─" * width)

        pkgs = [p for p in self._packages if p.status == PackageStatus.UPDATABLE]
        if not pkgs:
            lines.append("  All packages are up to date! 🎉")
        else:
            for i, pkg in enumerate(pkgs):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {pkg.name} {pkg.version} → latest")
                lines.append(f"   {pkg.display_size} | {pkg.category}")
            lines.append("")
            lines.append(" U:Update all")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Details  Esc:Back")
        return lines

    def render_queue(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 📥 Install Queue ({len(self._install_queue)})")
        lines.append("─" * width)

        if not self._install_queue:
            lines.append("  Queue is empty. Press Q on a package to add it.")
        else:
            for i, name in enumerate(self._install_queue):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {name}")

        lines.append("")
        if self._transactions:
            lines.append(" 📋 Recent Transactions:")
            for tx in self._transactions[:3]:
                icon = "✅" if tx.status == "completed" else "❌"
                lines.append(f" {icon} {tx.action.title()}: {', '.join(tx.packages)} ({tx.duration_str})")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Process  Del:Remove  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "repos": self.render_repos,
            "installed": self.render_installed,
            "updates": self.render_updates,
            "queue": self.render_queue,
        }
        renderer = renderers.get(self._view_mode, self.render_packages)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "repos":
            return self._handle_repos_key(key)
        elif self._view_mode == "installed":
            return self._handle_installed_key(key)
        elif self._view_mode == "updates":
            return self._handle_updates_key(key)
        elif self._view_mode == "queue":
            return self._handle_queue_key(key)
        return self._handle_packages_key(key)

    def _handle_packages_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "i":
            pkg = self.get_selected_item()
            if pkg:
                return "install" if self.install_package(pkg.name) else "install_failed"
        elif key == "u":
            pkg = self.get_selected_item()
            if pkg and pkg.status == PackageStatus.UPDATABLE:
                return "update" if self.update_package(pkg.name) else "update_failed"
        elif key == "r":
            pkg = self.get_selected_item()
            if pkg and pkg.status == PackageStatus.INSTALLED:
                return "remove" if self.remove_package(pkg.name) else "remove_failed"
        elif key == "q":
            pkg = self.get_selected_item()
            if pkg:
                self.add_to_queue(pkg.name)
                return "queue_add"
        elif key == "s":
            self.cycle_sort()
            return "sort"
        elif key == "\t":
            self.set_view("installed")
            return "installed"
        elif key == "r":
            self.set_view("repos")
            return "repos"
        elif key == "Escape":
            self._search_query = ""
            self._filter_status = None
            return "clear"
        return None

    def _handle_repos_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("packages")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            repo = self.get_selected_item()
            if repo:
                if repo.status == RepoStatus.ACTIVE:
                    repo.status = RepoStatus.DISABLED
                else:
                    repo.status = RepoStatus.ACTIVE
                return "toggle_repo"
        return None

    def _handle_installed_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("packages")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "r":
            pkg = self.get_selected_item()
            if pkg:
                return "remove" if self.remove_package(pkg.name) else "remove_failed"
        elif key == "u":
            return "update_all" if self.update_all() > 0 else "no_updates"
        return None

    def _handle_updates_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("packages")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "u":
            return "update_all" if self.update_all() > 0 else "no_updates"
        return None

    def _handle_queue_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("packages")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Delete":
            if 0 <= self._selected_index < len(self._install_queue):
                name = self._install_queue[self._selected_index]
                self.remove_from_queue(name)
                return "queue_remove"
        return None
