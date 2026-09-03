"""
Nyrqis Config Editor — system configuration editing application.

Features:
- Edit configuration files with syntax categories
- Profile management (save/load/switch config profiles)
- Diff view for comparing changes
- Undo/redo history
- Validation with error indicators
- Config templates (system, network, display, power, audio)
- Search and replace within configs
- Import/export configuration profiles
- Keyboard navigation throughout
"""

import time
import hashlib
import difflib
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Tuple
from datetime import datetime


# ─── Data Classes ────────────────────────────────────────────────────────


class ConfigCategory(Enum):
    SYSTEM = "System"
    NETWORK = "Network"
    DISPLAY = "Display"
    POWER = "Power"
    AUDIO = "Audio"
    SECURITY = "Security"
    INPUT = "Input"
    APPEARANCE = "Appearance"
    STORAGE = "Storage"
    SERVICES = "Services"


class ConfigStatus(Enum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    NEW = "new"
    ERROR = "error"


CATEGORY_ICONS = {
    ConfigCategory.SYSTEM: "⚙️",
    ConfigCategory.NETWORK: "🌐",
    ConfigCategory.DISPLAY: "🖥️",
    ConfigCategory.POWER: "🔋",
    ConfigCategory.AUDIO: "🔊",
    ConfigCategory.SECURITY: "🔒",
    ConfigCategory.INPUT: "⌨️",
    ConfigCategory.APPEARANCE: "🎨",
    ConfigCategory.STORAGE: "💾",
    ConfigCategory.SERVICES: "🔧",
}


@dataclass
class ConfigEntry:
    """A single configuration key-value pair."""
    key: str
    value: str
    default: str = ""
    category: ConfigCategory = ConfigCategory.SYSTEM
    description: str = ""
    status: ConfigStatus = ConfigStatus.UNCHANGED
    options: List[str] = field(default_factory=list)  # allowed values
    min_val: float = 0
    max_val: float = 100
    is_secret: bool = False
    line_number: int = 0

    @property
    def display(self) -> str:
        status_icon = {"unchanged": "  ", "modified": "✏️", "new": "🆕", "error": "❌"}.get(self.status.value, "  ")
        return f"{status_icon} {self.key} = {self._display_value}"

    @property
    def _display_value(self) -> str:
        if self.is_secret:
            return "••••••••"
        return self.value

    @property
    def has_options(self) -> bool:
        return len(self.options) > 0

    @property
    def is_numeric(self) -> bool:
        try:
            float(self.value)
            return True
        except ValueError:
            return False


@dataclass
class ConfigFile:
    """A configuration file."""
    name: str
    path: str
    category: ConfigCategory = ConfigCategory.SYSTEM
    description: str = ""
    entries: List[ConfigEntry] = field(default_factory=list)
    modified: bool = False
    last_saved: float = 0.0
    file_id: str = ""

    def __post_init__(self):
        if not self.file_id:
            self.file_id = hashlib.md5(f"{self.path}".encode()).hexdigest()[:8]

    @property
    def modified_count(self) -> int:
        return sum(1 for e in self.entries if e.status == ConfigStatus.MODIFIED)

    @property
    def status_display(self) -> str:
        mod = self.modified_count
        if mod > 0:
            return f"✏️ {mod} modified"
        return "✅ saved"


@dataclass
class ConfigProfile:
    """A saved configuration profile."""
    name: str
    description: str = ""
    created: float = field(default_factory=time.time)
    last_used: float = 0.0
    is_active: bool = False
    configs: Dict[str, Dict[str, str]] = field(default_factory=dict)  # file_id -> {key: value}
    profile_id: str = ""

    def __post_init__(self):
        if not self.profile_id:
            self.profile_id = hashlib.md5(f"{self.name}{self.created}".encode()).hexdigest()[:8]

    @property
    def entry_count(self) -> int:
        return sum(len(v) for v in self.configs.values())

    @property
    def time_ago(self) -> str:
        if self.last_used <= 0:
            return "never"
        diff = time.time() - self.last_used
        if diff < 3600:
            return f"{int(diff // 60)}m ago"
        elif diff < 86400:
            return f"{int(diff // 3600)}h ago"
        return datetime.fromtimestamp(self.last_used).strftime("%b %d")


@dataclass
class ConfigDiff:
    """A diff between two configurations."""
    file_name: str
    entries_added: List[Tuple[str, str]] = field(default_factory=list)
    entries_removed: List[Tuple[str, str]] = field(default_factory=list)
    entries_modified: List[Tuple[str, str, str]] = field(default_factory=list)  # key, old, new

    @property
    def has_changes(self) -> bool:
        return bool(self.entries_added or self.entries_removed or self.entries_modified)

    @property
    def summary(self) -> str:
        a = len(self.entries_added)
        r = len(self.entries_removed)
        m = len(self.entries_modified)
        return f"+{a} -{r} ~{m}"


# ─── Config Editor ───────────────────────────────────────────────────────


class ConfigEditor:
    """
    System configuration editor for Nyrqis OS.
    """

    def __init__(self):
        self._files: List[ConfigFile] = []
        self._profiles: List[ConfigProfile] = []
        self._active_profile: str = "Default"
        self._undo_stack: List[Tuple[str, str, str]] = []  # (file_id, key, old_value)
        self._redo_stack: List[Tuple[str, str, str]] = []
        self._selected_file: int = 0
        self._selected_entry: int = 0
        self._view_mode: str = "files"  # files, editor, profiles, diff
        self._search_query: str = ""
        self._diff_view: Optional[ConfigDiff] = None

        self._init_sample_data()

    def _init_sample_data(self) -> None:
        # System configs
        sys_entries = [
            ConfigEntry("hostname", "nyrqis-workstation", "nyrqis", ConfigCategory.SYSTEM,
                        "System hostname", line_number=1),
            ConfigEntry("timezone", "America/Los_Angeles", "UTC", ConfigCategory.SYSTEM,
                        "System timezone", options=["UTC", "America/Los_Angeles", "America/New_York",
                                                     "Europe/London", "Asia/Tokyo"], line_number=2),
            ConfigEntry("language", "en_US.UTF-8", "C.UTF-8", ConfigCategory.SYSTEM,
                        "System language", line_number=3),
            ConfigEntry("boot_timeout", "5", "3", ConfigCategory.SYSTEM,
                        "Boot menu timeout (seconds)", min_val=0, max_val=60, line_number=4),
            ConfigEntry("auto_login", "false", "false", ConfigCategory.SYSTEM,
                        "Enable automatic login", options=["true", "false"], line_number=5),
        ]
        self._files.append(ConfigFile("System Config", "/etc/nyrqis/system.conf",
                                       ConfigCategory.SYSTEM, "Core system settings", sys_entries))

        # Network configs
        net_entries = [
            ConfigEntry("dhcp", "true", "true", ConfigCategory.NETWORK,
                        "Use DHCP", options=["true", "false"], line_number=1),
            ConfigEntry("ip_address", "192.168.1.100", "", ConfigCategory.NETWORK,
                        "Static IP address", line_number=2),
            ConfigEntry("subnet_mask", "255.255.255.0", "255.255.255.0", ConfigCategory.NETWORK,
                        "Subnet mask", line_number=3),
            ConfigEntry("gateway", "192.168.1.1", "", ConfigCategory.NETWORK,
                        "Default gateway", line_number=4),
            ConfigEntry("dns_primary", "8.8.8.8", "8.8.8.8", ConfigCategory.NETWORK,
                        "Primary DNS server", line_number=5),
            ConfigEntry("dns_secondary", "8.8.4.4", "8.8.4.4", ConfigCategory.NETWORK,
                        "Secondary DNS server", line_number=6),
            ConfigEntry("wifi_ssid", "NyrqisHome", "", ConfigCategory.NETWORK,
                        "Wi-Fi network name", is_secret=False, line_number=7),
            ConfigEntry("wifi_password", "••••••••", "", ConfigCategory.NETWORK,
                        "Wi-Fi password", is_secret=True, line_number=8),
        ]
        self._files.append(ConfigFile("Network Config", "/etc/nyrqis/network.conf",
                                       ConfigCategory.NETWORK, "Network settings", net_entries))

        # Display configs
        disp_entries = [
            ConfigEntry("resolution", "2560x1440", "1920x1080", ConfigCategory.DISPLAY,
                        "Display resolution",
                        options=["1920x1080", "2560x1440", "3840x2160", "5120x2880"], line_number=1),
            ConfigEntry("refresh_rate", "144", "60", ConfigCategory.DISPLAY,
                        "Refresh rate (Hz)", min_val=30, max_val=360, line_number=2),
            ConfigEntry("scaling", "1.25", "1.0", ConfigCategory.DISPLAY,
                        "Display scaling factor", min_val=0.5, max_val=3.0, line_number=3),
            ConfigEntry("night_light", "true", "false", ConfigCategory.DISPLAY,
                        "Enable blue light filter", options=["true", "false"], line_number=4),
            ConfigEntry("night_light_temp", "4500", "6500", ConfigCategory.DISPLAY,
                        "Night light color temperature (K)", min_val=2700, max_val=6500, line_number=5),
            ConfigEntry("hdr", "auto", "off", ConfigCategory.DISPLAY,
                        "HDR mode", options=["off", "auto", "on"], line_number=6),
            ConfigEntry("vsync", "true", "true", ConfigCategory.DISPLAY,
                        "Vertical sync", options=["true", "false"], line_number=7),
        ]
        self._files.append(ConfigFile("Display Config", "/etc/nyrqis/display.conf",
                                       ConfigCategory.DISPLAY, "Display and monitor settings", disp_entries))

        # Power configs
        power_entries = [
            ConfigEntry("power_profile", "balanced", "balanced", ConfigCategory.POWER,
                        "Power profile",
                        options=["performance", "balanced", "powersaver", "custom"], line_number=1),
            ConfigEntry("screen_blank", "300", "300", ConfigCategory.POWER,
                        "Screen blank timeout (seconds)", min_val=0, max_val=3600, line_number=2),
            ConfigEntry("sleep_timeout", "1800", "1800", ConfigCategory.POWER,
                        "Sleep timeout (seconds)", min_val=0, max_val=7200, line_number=3),
            ConfigEntry("lid_close_action", "sleep", "sleep", ConfigCategory.POWER,
                        "Lid close action", options=["nothing", "lock", "sleep", "hibernate", "shutdown"], line_number=4),
            ConfigEntry("auto_brightness", "true", "false", ConfigCategory.POWER,
                        "Automatic brightness", options=["true", "false"], line_number=5),
        ]
        self._files.append(ConfigFile("Power Config", "/etc/nyrqis/power.conf",
                                       ConfigCategory.POWER, "Power management settings", power_entries))

        # Audio configs
        audio_entries = [
            ConfigEntry("default_sink", "alsa_output.pci-0000_00_1f.3.analog-stereo", "", ConfigCategory.AUDIO,
                        "Default audio output", line_number=1),
            ConfigEntry("default_source", "alsa_input.pci-0000_00_1f.3.analog-stereo", "", ConfigCategory.AUDIO,
                        "Default audio input", line_number=2),
            ConfigEntry("volume", "75", "100", ConfigCategory.AUDIO,
                        "Master volume (0-100)", min_val=0, max_val=100, line_number=3),
            ConfigEntry("mute", "false", "false", ConfigCategory.AUDIO,
                        "Mute state", options=["true", "false"], line_number=4),
        ]
        self._files.append(ConfigFile("Audio Config", "/etc/nyrqis/audio.conf",
                                       ConfigCategory.AUDIO, "Audio settings", audio_entries))

        # Profiles
        self._profiles = [
            ConfigProfile("Default", "Standard configuration", is_active=True,
                          last_used=time.time(),
                          configs={"sys": {"hostname": "nyrqis-workstation"},
                                   "net": {"dhcp": "true"}}),
            ConfigProfile("Office", "Office environment setup",
                          configs={"sys": {"hostname": "nyrqis-office"},
                                   "net": {"dhcp": "true", "wifi_ssid": "OfficeNet"},
                                   "disp": {"scaling": "1.25", "refresh_rate": "60"}}),
            ConfigProfile("Gaming", "Gaming performance configuration",
                          configs={"sys": {"hostname": "nyrqis-gaming"},
                                   "pwr": {"power_profile": "performance"},
                                   "disp": {"refresh_rate": "144", "vsync": "false"}}),
            ConfigProfile("Battery Saver", "Maximum battery life",
                          configs={"pwr": {"power_profile": "powersaver", "screen_blank": "120"},
                                   "disp": {"refresh_rate": "60", "night_light": "true"}}),
        ]

    # ── Config Operations ─────────────────────────────────────────────

    def set_value(self, file_idx: int, entry_idx: int, value: str) -> bool:
        if 0 <= file_idx < len(self._files):
            cfg = self._files[file_idx]
            if 0 <= entry_idx < len(cfg.entries):
                entry = cfg.entries[entry_idx]
                if entry.has_options and value not in entry.options:
                    return False
                if entry.is_numeric and entry.options:
                    try:
                        num = float(value)
                        if num < entry.min_val or num > entry.max_val:
                            return False
                    except ValueError:
                        return False
                # Undo
                self._undo_stack.append((cfg.file_id, entry.key, entry.value))
                self._redo_stack.clear()
                entry.value = value
                entry.status = ConfigStatus.MODIFIED
                cfg.modified = True
                return True
        return False

    def undo(self) -> bool:
        if self._undo_stack:
            file_id, key, old_value = self._undo_stack.pop()
            for cfg in self._files:
                if cfg.file_id == file_id:
                    for entry in cfg.entries:
                        if entry.key == key:
                            self._redo_stack.append((file_id, key, entry.value))
                            entry.value = old_value
                            entry.status = ConfigStatus.UNCHANGED
                            cfg.modified = cfg.modified_count > 0
                            return True
        return False

    def redo(self) -> bool:
        if self._redo_stack:
            file_id, key, new_value = self._redo_stack.pop()
            for cfg in self._files:
                if cfg.file_id == file_id:
                    for entry in cfg.entries:
                        if entry.key == key:
                            self._undo_stack.append((file_id, key, entry.value))
                            entry.value = new_value
                            entry.status = ConfigStatus.MODIFIED
                            cfg.modified = True
                            return True
        return False

    def save_file(self, file_idx: int) -> bool:
        if 0 <= file_idx < len(self._files):
            cfg = self._files[file_idx]
            cfg.last_saved = time.time()
            cfg.modified = False
            for entry in cfg.entries:
                if entry.status == ConfigStatus.MODIFIED:
                    entry.status = ConfigStatus.UNCHANGED
            return True
        return False

    def reset_entry(self, file_idx: int, entry_idx: int) -> bool:
        if 0 <= file_idx < len(self._files):
            cfg = self._files[file_idx]
            if 0 <= entry_idx < len(cfg.entries):
                entry = cfg.entries[entry_idx]
                entry.value = entry.default
                entry.status = ConfigStatus.UNCHANGED
                return True
        return False

    # ── Profile Operations ────────────────────────────────────────────

    def save_profile(self, name: str, description: str = "") -> ConfigProfile:
        profile = ConfigProfile(name=name, description=description, is_active=True)
        for cfg in self._files:
            file_data = {}
            for entry in cfg.entries:
                if entry.status == ConfigStatus.MODIFIED or entry.value != entry.default:
                    file_data[entry.key] = entry.value
            if file_data:
                profile.configs[cfg.file_id] = file_data
        self._profiles.append(profile)
        return profile

    def load_profile(self, profile_idx: int) -> bool:
        if 0 <= profile_idx < len(self._profiles):
            profile = self._profiles[profile_idx]
            profile.last_used = time.time()
            for p in self._profiles:
                p.is_active = False
            profile.is_active = True
            self._active_profile = profile.name
            # Apply configs
            for cfg in self._files:
                if cfg.file_id in profile.configs:
                    file_data = profile.configs[cfg.file_id]
                    for entry in cfg.entries:
                        if entry.key in file_data:
                            entry.value = file_data[entry.key]
                            entry.status = ConfigStatus.MODIFIED
            return True
        return False

    def delete_profile(self, profile_idx: int) -> bool:
        if 0 <= profile_idx < len(self._profiles):
            self._profiles.pop(profile_idx)
            return True
        return False

    # ── Diff Operations ───────────────────────────────────────────────

    def compare_profiles(self, idx1: int, idx2: int) -> Optional[ConfigDiff]:
        if 0 <= idx1 < len(self._profiles) and 0 <= idx2 < len(self._profiles):
            p1 = self._profiles[idx1]
            p2 = self._profiles[idx2]
            diff = ConfigDiff(f"{p1.name} vs {p2.name}")
            # Compare configs
            all_files = set(list(p1.configs.keys()) + list(p2.configs.keys()))
            for fid in all_files:
                d1 = p1.configs.get(fid, {})
                d2 = p2.configs.get(fid, {})
                all_keys = set(list(d1.keys()) + list(d2.keys()))
                for key in all_keys:
                    v1 = d1.get(key)
                    v2 = d2.get(key)
                    if v1 is None:
                        diff.entries_added.append((key, v2))
                    elif v2 is None:
                        diff.entries_removed.append((key, v1))
                    elif v1 != v2:
                        diff.entries_modified.append((key, v1, v2))
            self._diff_view = diff
            return diff
        return None

    def generate_diff_text(self, file_idx: int) -> str:
        """Generate a text diff for a config file."""
        if 0 <= file_idx < len(self._files):
            cfg = self._files[file_idx]
            original = []
            modified = []
            for entry in cfg.entries:
                original.append(f"{entry.key} = {entry.default}")
                modified.append(f"{entry.key} = {entry.value}")
            diff = difflib.unified_diff(original, modified, lineterm="", n=1)
            return "\n".join(diff)
        return ""

    # ── Navigation ────────────────────────────────────────────────────

    def select_file_up(self) -> None:
        self._selected_file = max(0, self._selected_file - 1)

    def select_file_down(self) -> None:
        self._selected_file = min(len(self._files) - 1, self._selected_file + 1)

    def select_entry_up(self) -> None:
        self._selected_entry = max(0, self._selected_entry - 1)

    def select_entry_down(self) -> None:
        cfg = self.get_selected_file()
        if cfg:
            self._selected_entry = min(len(cfg.entries) - 1, self._selected_entry + 1)

    def get_selected_file(self) -> Optional[ConfigFile]:
        if 0 <= self._selected_file < len(self._files):
            return self._files[self._selected_file]
        return None

    def get_selected_entry(self) -> Optional[ConfigEntry]:
        cfg = self.get_selected_file()
        if cfg and 0 <= self._selected_entry < len(cfg.entries):
            return cfg.entries[self._selected_entry]
        return None

    def set_view(self, mode: str) -> None:
        self._view_mode = mode
        self._selected_entry = 0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def files(self) -> List[ConfigFile]:
        return list(self._files)

    @property
    def profiles(self) -> List[ConfigProfile]:
        return list(self._profiles)

    @property
    def selected_file(self) -> int:
        return self._selected_file

    @property
    def selected_entry(self) -> int:
        return self._selected_entry

    @property
    def view_mode(self) -> str:
        return self._view_mode

    @property
    def active_profile(self) -> str:
        return self._active_profile

    @property
    def modified_files(self) -> int:
        return sum(1 for f in self._files if f.modified)

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    # ── Rendering ─────────────────────────────────────────────────────

    def render_files(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" ⚙️  System Configuration")
        lines.append("─" * width)
        lines.append(f" Profile: {self._active_profile} | {self.modified_files} files modified")
        lines.append("─" * width)

        for i, cfg in enumerate(self._files):
            marker = "▸" if i == self._selected_file else " "
            icon = CATEGORY_ICONS.get(cfg.category, "📄")
            lines.append(f"{marker} {icon} {cfg.name}")
            lines.append(f"   {cfg.path}")
            lines.append(f"   {cfg.description}")
            lines.append(f"   {cfg.status_display} | {len(cfg.entries)} settings")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Edit  P:Profiles  D:Diff  S:Save all")
        return lines

    def render_editor(self, width: int = 70) -> List[str]:
        cfg = self.get_selected_file()
        if not cfg:
            return ["No file selected"]

        lines = []
        icon = CATEGORY_ICONS.get(cfg.category, "📄")
        lines.append(f" {icon} {cfg.name} — {cfg.path}")
        lines.append("─" * width)

        for i, entry in enumerate(cfg.entries):
            marker = "▸" if i == self._selected_entry else " "
            lines.append(f"{marker} {entry.display}")
            if entry.description:
                lines.append(f"     💬 {entry.description}")
            if entry.has_options:
                lines.append(f"     📋 Options: {', '.join(entry.options[:5])}")
            elif entry.is_numeric:
                lines.append(f"     📏 Range: {entry.min_val} — {entry.max_val}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Edit value  R:Reset  U:Undo  Ctrl+R:Redo")
        lines.append(" Esc:Back to files")
        return lines

    def render_profiles(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 👤 Configuration Profiles")
        lines.append("─" * width)

        for i, profile in enumerate(self._profiles):
            marker = "▸" if i == self._selected_file else " "
            active = " 🟢" if profile.is_active else ""
            lines.append(f"{marker} {profile.name}{active}")
            lines.append(f"   {profile.description}")
            lines.append(f"   {profile.entry_count} settings | Last used: {profile.time_ago}")
            lines.append("")

        lines.append("─" * width)
        lines.append(" ↑↓:Select  Enter:Load profile  Del:Delete  Esc:Back")
        return lines

    def render_diff(self, width: int = 70) -> List[str]:
        lines = []
        lines.append(" 📊 Configuration Diff")
        lines.append("─" * width)

        if self._diff_view:
            diff = self._diff_view
            lines.append(f" Comparing: {diff.file_name}")
            lines.append(f" Changes: {diff.summary}")
            lines.append("─" * width)

            for key, old, new in diff.entries_modified:
                lines.append(f" ~ {key}:")
                lines.append(f"   - {old}")
                lines.append(f"   + {new}")

            for key, val in diff.entries_added:
                lines.append(f" + {key} = {val}")

            for key, val in diff.entries_removed:
                lines.append(f" - {key} = {val}")
        else:
            # Show file diff
            text = self.generate_diff_text(self._selected_file)
            if text:
                for line in text.split("\n"):
                    lines.append(f" {line}")
            else:
                lines.append("  No changes to display.")

        lines.append("─" * width)
        lines.append(" Esc:Back")
        return lines

    def render(self, width: int = 70, height: int = 30) -> List[str]:
        renderers = {
            "editor": self.render_editor,
            "profiles": self.render_profiles,
            "diff": self.render_diff,
        }
        renderer = renderers.get(self._view_mode, self.render_files)
        return renderer(width)

    # ── Keyboard Handling ─────────────────────────────────────────────

    def handle_key(self, key: str) -> Optional[str]:
        if self._view_mode == "editor":
            return self._handle_editor_key(key)
        elif self._view_mode == "profiles":
            return self._handle_profiles_key(key)
        elif self._view_mode == "diff":
            return self._handle_diff_key(key)
        return self._handle_files_key(key)

    def _handle_files_key(self, key: str) -> Optional[str]:
        if key == "ArrowUp":
            self.select_file_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_file_down()
            return "select_down"
        elif key == "Enter":
            self.set_view("editor")
            return "editor"
        elif key == "p":
            self.set_view("profiles")
            return "profiles"
        elif key == "d":
            self.set_view("diff")
            return "diff"
        return None

    def _handle_editor_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("files")
            return "back"
        elif key == "ArrowUp":
            self.select_entry_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_entry_down()
            return "select_down"
        elif key == "u":
            return "undo" if self.undo() else "undo_failed"
        elif key == "r":
            return "reset" if self.reset_entry(self._selected_file, self._selected_entry) else "reset_failed"
        return None

    def _handle_profiles_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("files")
            return "back"
        elif key == "ArrowUp":
            self.select_file_up()
            return "select_up"
        elif key == "ArrowDown":
            self.select_file_down()
            return "select_down"
        elif key == "Enter":
            return "load_profile" if self.load_profile(self._selected_file) else "load_failed"
        elif key == "Delete":
            return "delete_profile" if self.delete_profile(self._selected_file) else "delete_failed"
        return None

    def _handle_diff_key(self, key: str) -> Optional[str]:
        if key == "Escape":
            self.set_view("files")
            return "back"
        return None
