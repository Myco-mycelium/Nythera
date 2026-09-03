"""
Nyrqis VFS Manager — virtual filesystem management application.

Features:
- Local filesystem browsing with tree view
- FUSE mount point management
- Network filesystem support (NFS, SMB/CIFS, SSHFS, WebDAV)
- Mount/unmount operations with progress
- File permissions and ownership display
- Disk usage per mount point
- Filesystem type detection
- Bookmark frequently accessed paths
- Keyboard navigation throughout
"""

import time
import hashlib
import os
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class FilesystemType(Enum):
    EXT4 = "ext4"
    XFS = "xfs"
    BTRFS = "btrfs"
    NTFS = "NTFS"
    FAT32 = "vfat"
    TMPFS = "tmpfs"
    NFS = "nfs"
    SMB_CIFS = "cifs"
    SSHFS = "sshfs"
    FUSE = "fuse"
    WEBDAV = "webdav"
    PROC = "proc"
    SYSFS = "sysfs"
    DEVPTS = "devpts"
    OVERLAY = "overlay"
    UNKNOWN = "unknown"


class MountStatus(Enum):
    MOUNTED = "mounted"
    UNMOUNTED = "unmounted"
    MOUNTING = "mounting"
    UNMOUNTING = "unmounting"
    ERROR = "error"


class NetworkFS(Enum):
    NFS = "NFS"
    SMB = "SMB/CIFS"
    SSHFS = "SSHFS"
    WEBDAV = "WebDAV"
    FTP = "FTP"


FS_ICONS = {
    FilesystemType.EXT4: "🐧",
    FilesystemType.XFS: "🦊",
    FilesystemType.BTRFS: "🌳",
    FilesystemType.NTFS: "🪟",
    FilesystemType.FAT32: "💾",
    FilesystemType.TMPFS: "⚡",
    FilesystemType.NFS: "🌐",
    FilesystemType.SMB_CIFS: "🏢",
    FilesystemType.SSHFS: "🔐",
    FilesystemType.FUSE: "🔌",
    FilesystemType.WEBDAV: "☁️",
    FilesystemType.PROC: "⚙️",
    FilesystemType.SYSFS: "🔧",
    FilesystemType.OVERLAY: "📦",
    FilesystemType.UNKNOWN: "❓",
}

STATUS_ICONS = {
    MountStatus.MOUNTED: "🟢",
    MountStatus.UNMOUNTED: "⚫",
    MountStatus.MOUNTING: "🟡",
    MountStatus.UNMOUNTING: "🟡",
    MountStatus.ERROR: "🔴",
}


@dataclass
class MountPoint:
    """A filesystem mount point."""
    source: str  # device or server:path
    mount_point: str  # /path
    filesystem: FilesystemType = FilesystemType.UNKNOWN
    status: MountStatus = MountStatus.MOUNTED
    options: List[str] = field(default_factory=list)
    # Size
    total_kb: int = 0
    used_kb: int = 0
    available_kb: int = 0
    # Network specific
    network_type: Optional[NetworkFS] = None
    server: str = ""
    share: str = ""
    username: str = ""
    domain: str = ""
    # FUSE specific
    fuse_type: str = ""
    # Metadata
    mounted_at: float = 0.0
    mount_id: str = ""

    def __post_init__(self):
        if not self.mount_id:
            self.mount_id = hashlib.md5(f"{self.source}{self.mount_point}".encode()).hexdigest()[:8]

    @property
    def icon(self) -> str:
        return FS_ICONS.get(self.filesystem, "❓")

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "❓")

    @property
    def display(self) -> str:
        return f"{self.status_icon} {self.mount_point} [{self.filesystem.value}]"

    @property
    def size_str(self) -> str:
        if self.total_kb <= 0:
            return "—"
        return f"{self.total_kb // 1048576} GB"

    @property
    def usage_str(self) -> str:
        if self.total_kb <= 0:
            return "—"
        pct = self.used_kb / self.total_kb * 100
        return f"{self.used_kb // 1048576} / {self.total_kb // 1048576} GB ({pct:.0f}%)"

    @property
    def usage_bar(self) -> str:
        if self.total_kb <= 0:
            return ""
        pct = self.used_kb / self.total_kb * 100
        filled = int(pct / 100 * 20)
        return "█" * filled + "░" * (20 - filled)

    @property
    def options_str(self) -> str:
        return ",".join(self.options) if self.options else "default"

    @property
    def is_network(self) -> bool:
        return self.filesystem in (FilesystemType.NFS, FilesystemType.SMB_CIFS,
                                    FilesystemType.SSHFS, FilesystemType.WEBDAV)

    @property
    def mount_time_str(self) -> str:
        if self.mounted_at <= 0:
            return "—"
        diff = time.time() - self.mounted_at
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.mounted_at).strftime("%b %d %H:%M")


@dataclass
class FileEntry:
    """A file/directory entry in browsing view."""
    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    permissions: str = ""
    owner: str = ""
    group: str = ""
    modified: float = 0.0
    file_type: str = ""

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return "<DIR>"
        if self.size >= 1073741824:
            return f"{self.size / 1073741824:.1f} GB"
        elif self.size >= 1048576:
            return f"{self.size / 1048576:.1f} MB"
        elif self.size >= 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size} B"

    @property
    def icon(self) -> str:
        if self.is_dir:
            return "📁"
        ext = self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""
        icon_map = {
            "py": "🐍", "js": "📜", "ts": "📜", "rs": "🦀",
            "go": "🐹", "c": "⚙️", "h": "⚙️", "cpp": "⚙️",
            "md": "📖", "txt": "📄", "log": "📋",
            "png": "🖼️", "jpg": "🖼️", "svg": "🖼️", "gif": "🖼️",
            "mp3": "🎵", "wav": "🎵", "flac": "🎵",
            "mp4": "🎬", "mkv": "🎬", "avi": "🎬",
            "zip": "📦", "tar": "📦", "gz": "📦",
            "pdf": "📕", "doc": "📘", "xls": "📗",
            "json": "📋", "yaml": "📋", "toml": "📋",
            "sh": "🖥️", "bash": "🖥️",
        }
        return icon_map.get(ext, "📄")

    @property
    def mod_time_str(self) -> str:
        if self.modified <= 0:
            return "—"
        return datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")


@dataclass
class Bookmark:
    """A filesystem bookmark."""
    name: str
    path: str
    icon: str = "⭐"
    created: float = field(default_factory=time.time)


# ─── VFS Manager ─────────────────────────────────────────────────────────


class VFSManager:
    """
    Virtual filesystem manager for Nyrqis OS.
    """

    def __init__(self):
        self._mount_points: List[MountPoint] = []
        self._bookmarks: List[Bookmark] = []
        self._current_path: str = "/"
        self._path_history: List[str] = ["/"]
        self._history_index: int = 0
        self._selected_index: int = 0
        self._view_mode: str = "browser"  # browser, mounts, network, bookmarks
        self._sort_by: str = "name"
        self._sort_dirs_first: bool = True
        self._show_hidden: bool = False

        # Network mount dialog state
        self._network_type: NetworkFS = NetworkFS.SMB
        self._network_server: str = ""
        self._network_share: str = ""
        self._network_user: str = ""
        self._network_pass: str = ""

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        now = time.time()
        self._mount_points = [
            MountPoint("/dev/sda3", "/", FilesystemType.BTRFS, MountStatus.MOUNTED,
                       ["rw", "relatime", "compress=zstd"], 512000000, 340000000, 172000000,
                       mounted_at=now - 604800),
            MountPoint("/dev/sda1", "/boot/efi", FilesystemType.FAT32, MountStatus.MOUNTED,
                       ["rw", "umask=0077"], 512000, 128000, 384000,
                       mounted_at=now - 604800),
            MountPoint("/dev/sdb1", "/data", FilesystemType.EXT4, MountStatus.MOUNTED,
                       ["rw", "relatime", "discard"], 1024000000, 680000000, 344000000,
                       mounted_at=now - 604800),
            MountPoint("tmpfs", "/tmp", FilesystemType.TMPFS, MountStatus.MOUNTED,
                       ["nosuid", "nodev"], 8192000, 512000, 7680000,
                       mounted_at=now - 86400),
            MountPoint("tmpfs", "/run", FilesystemType.TMPFS, MountStatus.MOUNTED,
                       ["nosuid", "nodev", "mode=755"], 4096000, 256000, 3840000,
                       mounted_at=now - 86400),
            MountPoint("proc", "/proc", FilesystemType.PROC, MountStatus.MOUNTED,
                       ["nosuid", "noexec", "nodev"], 0, 0, 0),
            MountPoint("sysfs", "/sys", FilesystemType.SYSFS, MountStatus.MOUNTED,
                       ["nosuid", "noexec", "nodev"], 0, 0, 0),
            # Network mounts
            MountPoint("192.168.1.50:/srv/nfs", "/mnt/nfs-share", FilesystemType.NFS,
                       MountStatus.MOUNTED, ["rw", "hard", "intr", "timeo=600"],
                       2048000000, 850000000, 1198000000,
                       NetworkFS.NFS, "192.168.1.50", "/srv/nfs",
                       mounted_at=now - 172800),
            MountPoint("//nas.local/shared", "/mnt/smb-media", FilesystemType.SMB_CIFS,
                       MountStatus.MOUNTED, ["rw", "vers=3.0", "credentials=/etc/samba/creds"],
                       3072000000, 1500000000, 1572000000,
                       NetworkFS.SMB, "nas.local", "/shared", "admin", "WORKGROUP",
                       mounted_at=now - 86400),
            MountPoint("user@server:/home/user", "/mnt/sshfs-remote", FilesystemType.SSHFS,
                       MountStatus.UNMOUNTED, ["follow_symlinks"],
                       0, 0, 0,
                       NetworkFS.SSHFS, "server", "/home/user", "user",
                       mounted_at=0),
        ]

        self._bookmarks = [
            Bookmark("Home", "/home/user", "🏠"),
            Bookmark("Documents", "/home/user/Documents", "📄"),
            Bookmark("Downloads", "/home/user/Downloads", "⬇️"),
            Bookmark("Data Drive", "/data", "💾"),
            Bookmark("NFS Share", "/mnt/nfs-share", "🌐"),
            Bookmark("SMB Media", "/mnt/smb-media", "🏢"),
            Bookmark("System Root", "/", "🐧"),
        ]

        # Create sample file listing
        self._file_cache: Dict[str, List[FileEntry]] = {}

    def get_files(self, path: str) -> List[FileEntry]:
        """Get file listing for a path (simulated)."""
        if path in self._file_cache:
            return self._file_cache[path]

        # Generate simulated entries
        entries = []
        if path == "/":
            entries = [
                FileEntry("bin", "/bin", True, 0, "drwxr-xr-x", "root", "root", time.time() - 864000),
                FileEntry("boot", "/boot", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
                FileEntry("etc", "/etc", True, 0, "drwxr-xr-x", "root", "root", time.time() - 86400),
                FileEntry("home", "/home", True, 0, "drwxr-xr-x", "root", "root", time.time() - 86400),
                FileEntry("dev", "/dev", True, 0, "drwxr-xr-x", "root", "root", time.time()),
                FileEntry("proc", "/proc", True, 0, "dr-xr-xr-x", "root", "root", time.time()),
                FileEntry("sys", "/sys", True, 0, "dr-xr-xr-x", "root", "root", time.time()),
                FileEntry("tmp", "/tmp", True, 0, "drwxrwxrwt", "root", "root", time.time() - 3600),
                FileEntry("var", "/var", True, 0, "drwxr-xr-x", "root", "root", time.time() - 14400),
                FileEntry("usr", "/usr", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
                FileEntry("data", "/data", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
                FileEntry("mnt", "/mnt", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
            ]
        elif path == "/home/user":
            entries = [
                FileEntry("Documents", "/home/user/Documents", True, 0, "drwxr-xr-x", "user", "users", time.time() - 3600),
                FileEntry("Downloads", "/home/user/Downloads", True, 0, "drwxr-xr-x", "user", "users", time.time() - 7200),
                FileEntry("Pictures", "/home/user/Pictures", True, 0, "drwxr-xr-x", "user", "users", time.time() - 14400),
                FileEntry("Music", "/home/user/Music", True, 0, "drwxr-xr-x", "user", "users", time.time() - 86400),
                FileEntry("Videos", "/home/user/Videos", True, 0, "drwxr-xr-x", "user", "users", time.time() - 86400),
                FileEntry("Desktop", "/home/user/Desktop", True, 0, "drwxr-xr-x", "user", "users", time.time() - 1800),
                FileEntry(".bashrc", "/home/user/.bashrc", False, 4200, "-rw-r--r--", "user", "users", time.time() - 604800),
                FileEntry(".config", "/home/user/.config", True, 0, "drwxr-xr-x", "user", "users", time.time() - 86400),
                FileEntry("project.py", "/home/user/project.py", False, 15000, "-rw-r--r--", "user", "users", time.time() - 3600, "python"),
                FileEntry("notes.md", "/home/user/notes.md", False, 8500, "-rw-r--r--", "user", "users", time.time() - 7200, "markdown"),
            ]
        elif path == "/etc":
            entries = [
                FileEntry("hostname", "/etc/hostname", False, 8, "-rw-r--r--", "root", "root", time.time() - 2592000),
                FileEntry("hosts", "/etc/hosts", False, 256, "-rw-r--r--", "root", "root", time.time() - 2592000),
                FileEntry("fstab", "/etc/fstab", False, 1200, "-rw-r--r--", "root", "root", time.time() - 2592000),
                FileEntry("passwd", "/etc/passwd", False, 2800, "-rw-r--r--", "root", "root", time.time() - 2592000),
                FileEntry("nyrqis", "/etc/nyrqis", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
                FileEntry("systemd", "/etc/systemd", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
                FileEntry("grub.d", "/etc/grub.d", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
            ]
        elif path == "/data":
            entries = [
                FileEntry("projects", "/data/projects", True, 0, "drwxr-xr-x", "user", "users", time.time() - 14400),
                FileEntry("backups", "/data/backups", True, 0, "drwxr-xr-x", "user", "users", time.time() - 86400),
                FileEntry("media", "/data/media", True, 0, "drwxr-xr-x", "user", "users", time.time() - 14400),
                FileEntry("database", "/data/database", True, 0, "drwxr-xr-x", "user", "users", time.time() - 3600),
                FileEntry("docker", "/data/docker", True, 0, "drwxr-xr-x", "root", "root", time.time() - 604800),
            ]
        else:
            # Generic empty directory
            entries = [
                FileEntry(".", path + "/.", True, 0, "drwxr-xr-x", "user", "users", time.time()),
                FileEntry("..", path + "/..", True, 0, "drwxr-xr-x", "user", "users", time.time()),
            ]

        self._file_cache[path] = entries
        return entries

    # ── Navigation ────────────────────────────────────────────────────

    def navigate_to(self, path: str) -> bool:
        files = self.get_files(path)
        if files:
            self._current_path = path
            # Update history
            if self._history_index < len(self._path_history) - 1:
                self._path_history = self._path_history[:self._history_index + 1]
            self._path_history.append(path)
            self._history_index = len(self._path_history) - 1
            self._selected_index = 0
            return True
        return False

    def go_back(self) -> bool:
        if self._history_index > 0:
            self._history_index -= 1
            self._current_path = self._path_history[self._history_index]
            self._selected_index = 0
            return True
        return False

    def go_forward(self) -> bool:
        if self._history_index < len(self._path_history) - 1:
            self._history_index += 1
            self._current_path = self._path_history[self._history_index]
            self._selected_index = 0
            return True
        return False

    def go_up(self) -> bool:
        parent = "/".join(self._current_path.rstrip("/").split("/")[:-1]) or "/"
        return self.navigate_to(parent)

    def go_home(self) -> bool:
        return self.navigate_to("/home/user")

    # ── Mount Operations ──────────────────────────────────────────────

    def mount_network(self, server: str, share: str, mount_point: str,
                      fs_type: FilesystemType = FilesystemType.SMB_CIFS,
                      username: str = "") -> MountPoint:
        mp = MountPoint(
            source=f"//{server}/{share}" if fs_type == FilesystemType.SMB_CIFS else f"{server}:{share}",
            mount_point=mount_point,
            filesystem=fs_type,
            status=MountStatus.MOUNTED,
            options=["rw"],
            network_type=NetworkFS.SMB if fs_type == FilesystemType.SMB_CIFS else NetworkFS.NFS,
            server=server,
            share=share,
            username=username,
            total_kb=1000000000,
            mounted_at=time.time(),
        )
        self._mount_points.append(mp)
        return mp

    def unmount(self, mount_id: str) -> bool:
        for mp in self._mount_points:
            if mp.mount_id == mount_id:
                if mp.status == MountStatus.MOUNTED:
                    mp.status = MountStatus.UNMOUNTED
                    mp.mounted_at = 0
                    return True
        return False

    def remount(self, mount_id: str) -> bool:
        for mp in self._mount_points:
            if mp.mount_id == mount_id:
                if mp.status == MountStatus.UNMOUNTED:
                    mp.status = MountStatus.MOUNTED
                    mp.mounted_at = time.time()
                    return True
        return False

    def get_mount_for_path(self, path: str) -> Optional[MountPoint]:
        """Find the mount point for a given path."""
        best = None
        best_len = 0
        for mp in self._mount_points:
            if path.startswith(mp.mount_point) and len(mp.mount_point) > best_len:
                best = mp
                best_len = len(mp.mount_point)
        return best

    # ── Bookmarks ─────────────────────────────────────────────────────

    def add_bookmark(self, name: str, path: str, icon: str = "⭐") -> Bookmark:
        bm = Bookmark(name, path, icon)
        self._bookmarks.append(bm)
        return bm

    def remove_bookmark(self, index: int) -> bool:
        if 0 <= index < len(self._bookmarks):
            self._bookmarks.pop(index)
            return True
        return False

    # ── Selection ─────────────────────────────────────────────────────

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

    def enter_selected(self) -> bool:
        if self._view_mode == "browser":
            files = self.get_files(self._current_path)
            if 0 <= self._selected_index < len(files):
                entry = files[self._selected_index]
                if entry.is_dir and entry.name not in (".", ".."):
                    return self.navigate_to(entry.path)
        return False

    def _get_display_list(self) -> list:
        if self._view_mode == "mounts":
            return self._mount_points
        elif self._view_mode == "bookmarks":
            return self._bookmarks
        elif self._view_mode == "network":
            return [mp for mp in self._mount_points if mp.is_network]
        return self.get_files(self._current_path)

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_index = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def mount_points(self) -> List[MountPoint]:
        return list(self._mount_points)

    @property
    def bookmarks(self) -> List[Bookmark]:
        return list(self._bookmarks)

    @property
    def current_path(self) -> str:
        return self._current_path

    @property
    def path_breadcrumbs(self) -> List[str]:
        parts = self._current_path.strip("/").split("/")
        crumbs = ["/"]
        current = ""
        for part in parts:
            current += "/" + part
            crumbs.append(current)
        return crumbs

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def mounted_count(self) -> int:
        return sum(1 for mp in self._mount_points if mp.status == MountStatus.MOUNTED)

    @property
    def network_count(self) -> int:
        return sum(1 for mp in self._mount_points if mp.is_network)

    # ── Rendering ─────────────────────────────────────────────────────

    def render_browser(self, width: int = 70) -> List[str]:
        lines = []
        crumbs = " > ".join(self.path_breadcrumbs[-4:])
        lines.append(f" 📁 VFS Browser — {crumbs}")
        lines.append("─" * width)

        mp = self.get_mount_for_path(self._current_path)
        if mp:
            lines.append(f" {mp.icon} {mp.filesystem.value} | {mp.usage_str} [{mp.usage_bar}]")
        lines.append("─" * width)

        files = self.get_files(self._current_path)
        if not files:
            lines.append("  Empty directory")
        else:
            for i, entry in enumerate(files):
                marker = "▸" if i == self._selected_index else " "
                lines.append(f"{marker} {entry.icon} {entry.name:<25s} {entry.display_size:>10s}  {entry.mod_time_str}")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Open  Backspace:Back  H:Home")
        lines.append(" M:Mounts  B:Bookmarks  N:Network  ←→:Nav")
        return lines

    def render_mounts(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 💾 Mount Points ({self.mounted_count} mounted)")
        lines.append("─" * width)

        for i, mp in enumerate(self._mount_points):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {mp.display}")

            if mp.total_kb > 0:
                lines.append(f"   {mp.usage_str} [{mp.usage_bar}]")
            if mp.is_network:
                lines.append(f"   Network: {mp.network_type.value if mp.network_type else '?'} → {mp.server}{mp.share}")
            lines.append(f"   Source: {mp.source}")
            lines.append(f"   Options: {mp.options_str}")
            if mp.mounted_at > 0:
                lines.append(f"   Mounted: {mp.mount_time_str}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Browse  U:Unmount/Mount  Esc:Back")
        return lines

    def render_network(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" 🌐 Network Filesystems ({self.network_count})")
        lines.append("─" * width)

        network_mounts = [mp for mp in self._mount_points if mp.is_network]
        if not network_mounts:
            lines.append("  No network filesystems configured.")
        else:
            for mp in network_mounts:
                lines.append(f" {mp.status_icon} {mp.filesystem.value}: {mp.server}{mp.share}")
                lines.append(f"   Mount: {mp.mount_point}")
                if mp.username:
                    lines.append(f"   User: {mp.username}")
                if mp.mounted_at > 0:
                    lines.append(f"   Connected: {mp.mount_time_str}")
                lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Browse  U:Unmount  Esc:Back")
        return lines

    def render_bookmarks(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(f" ⭐ Bookmarks ({len(self._bookmarks)})")
        lines.append("─" * width)

        for i, bm in enumerate(self._bookmarks):
            marker = "▸" if i == self._selected_index else " "
            lines.append(f"{marker} {bm.icon} {bm.name}")
            lines.append(f"   {bm.path}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Navigate  Del:Remove  Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "mounts": self.render_mounts,
            "network": self.render_network,
            "bookmarks": self.render_bookmarks,
        }
        renderer = renderers.get(self._view_mode, self.render_browser)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "mounts":
            return self._handle_mounts_key(key)
        elif self._view_mode == "network":
            return self._handle_network_key(key)
        elif self._view_mode == "bookmarks":
            return self._handle_bookmarks_key(key)
        return self._handle_browser_key(key)

    def _handle_browser_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            return "enter" if self.enter_selected() else "enter_failed"
        elif key == "Backspace":
            return "back" if self.go_back() else "back_failed"
        elif key == "ArrowLeft":
            return "back" if self.go_back() else "back_failed"
        elif key == "ArrowRight":
            return "forward" if self.go_forward() else "forward_failed"
        elif key == "h":
            return "home" if self.go_home() else "home_failed"
        elif key == "m":
            self.set_view("mounts")
            return "mounts"
        elif key == "b":
            self.set_view("bookmarks")
            return "bookmarks"
        elif key == "n":
            self.set_view("network")
            return "network"
        return None

    def _handle_mounts_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browser")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "u":
            mp = self.get_selected_item()
            if mp:
                if mp.status == MountStatus.MOUNTED:
                    self.unmount(mp.mount_id)
                    return "unmount"
                else:
                    self.remount(mp.mount_id)
                    return "mount"
        elif key == "Enter":
            mp = self.get_selected_item()
            if mp and mp.status == MountStatus.MOUNTED:
                self.navigate_to(mp.mount_point)
                self.set_view("browser")
                return "browse"
        return None

    def _handle_network_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browser")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "u":
            network = [mp for mp in self._mount_points if mp.is_network]
            if 0 <= self._selected_index < len(network):
                mp = network[self._selected_index]
                self.unmount(mp.mount_id)
                return "unmount"
        elif key == "Enter":
            network = [mp for mp in self._mount_points if mp.is_network]
            if 0 <= self._selected_index < len(network):
                mp = network[self._selected_index]
                if mp.status == MountStatus.MOUNTED:
                    self.navigate_to(mp.mount_point)
                    self.set_view("browser")
                    return "browse"
        return None

    def _handle_bookmarks_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("browser")
            return "back"
        elif key == "ArrowUp":
            self.select_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_down()
            return "select_down"
        elif key == "Enter":
            bm = self.get_selected_item()
            if bm:
                self.navigate_to(bm.path)
                self.set_view("browser")
                return "navigate"
        elif key == "Delete":
            return "remove_bookmark" if self.remove_bookmark(self._selected_index) else "remove_failed"
        return None
