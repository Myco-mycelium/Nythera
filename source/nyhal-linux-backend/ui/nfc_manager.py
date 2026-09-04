"""
Nyrqis OS - NFC/RFID Tag Manager
Tag reader, data display, writing, and automation triggers.

Features:
- NFC tag discovery and reading (NDEF, MIFARE, NTAG, ISO 14443)
- Tag data display (text, URL, vCard, WiFi, Bluetooth)
- Tag writing (text, URL, contact, WiFi config)
- Tag emulation (HCE)
- Automation triggers (launch app, toggle WiFi, open URL)
- Tag history with timestamps
- Reader/writer status monitoring
- Anti-collision for multiple tags
"""

import time
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


class NFCTagType(Enum):
    NTAG213 = "NTAG213"
    NTAG215 = "NTAG215"
    NTAG216 = "NTAG216"
    MIFARE_CLASSIC_1K = "MIFARE Classic 1K"
    MIFARE_CLASSIC_4K = "MIFARE Classic 4K"
    MIFARE_DESFIRE = "MIFARE DESFire"
    MIFARE_ULTRALIGHT = "MIFARE Ultralight"
    ISO14443A = "ISO 14443A"
    ISO15693 = "ISO 15693"
    FELICA = "FeliCa"
    ISO16941 = "ISO 16941"


class NDEFRecordType(Enum):
    TEXT = "text"
    URL = "url"
    VCARD = "vcard"
    WIFI = "wifi"
    BLUETOOTH = "bluetooth"
    EMAIL = "email"
    PHONE = "phone"
    SMS = "sms"
    GEO = "geo"
    URI = "uri"
    MIMETYPE = "mimetype"
    EMPTY = "empty"


class TagState(Enum):
    ABSENT = "absent"
    PRESENT = "present"
    READING = "reading"
    WRITING = "writing"
    LOCKED = "locked"
    ERROR = "error"


class TriggerAction(Enum):
    OPEN_URL = "open_url"
    LAUNCH_APP = "launch_app"
    TOGGLE_WIFI = "toggle_wifi"
    TOGGLE_BLUETOOTH = "toggle_bluetooth"
    SET_VOLUME = "set_volume"
    RUN_COMMAND = "run_command"
    SEND_INTENT = "send_intent"
    SHOW_NOTIFICATION = "show_notification"
    CONNECT_WIFI = "connect_wifi"
    SET_TIMER = "set_timer"


class ReaderBackend(Enum):
    LIBNFC = "libnfc"
    NFC_PYTHON = "nfcpy"
    PCSC = "PC/SC"
    ANDROID_HCE = "Android HCE"
    MANUAL = "manual"


TAG_TYPE_ICONS = {
    NFCTagType.NTAG213: "🏷️", NFCTagType.NTAG215: "🏷️",
    NFCTagType.NTAG216: "🏷️", NFCTagType.MIFARE_CLASSIC_1K: "💳",
    NFCTagType.MIFARE_CLASSIC_4K: "💳", NFCTagType.MIFARE_DESFIRE: "💳",
    NFCTagType.MIFARE_ULTRALIGHT: "🎫", NFCTagType.ISO14443A: "📡",
    NFCTagType.ISO15693: "📡", NFCTagType.FELICA: "🎌",
    NFCTagType.ISO16941: "📡",
}

STATE_ICONS = {
    TagState.ABSENT: "⚫", TagState.PRESENT: "🟢",
    TagState.READING: "📖", TagState.WRITING: "✏️",
    TagState.LOCKED: "🔒", TagState.ERROR: "❌",
}

TRIGGER_ICONS = {
    TriggerAction.OPEN_URL: "🌐", TriggerAction.LAUNCH_APP: "📱",
    TriggerAction.TOGGLE_WIFI: "📶", TriggerAction.TOGGLE_BLUETOOTH: "📡",
    TriggerAction.SET_VOLUME: "🔊", TriggerAction.RUN_COMMAND: "⌨️",
    TriggerAction.SEND_INTENT: "📤", TriggerAction.SHOW_NOTIFICATION: "🔔",
    TriggerAction.CONNECT_WIFI: "📶", TriggerAction.SET_TIMER: "⏱️",
}


@dataclass
class NDEFRecord:
    record_type: NDEFRecordType = NDEFRecordType.TEXT
    payload: str = ""
    language: str = ""
    mime_type: str = ""
    url: str = ""
    tnf: int = 0x02  # Type Name Format

    @property
    def type_name(self) -> str:
        return self.record_type.value

    @property
    def display_payload(self) -> str:
        if self.record_type == NDEFRecordType.WIFI:
            return "WiFi Config"
        elif self.record_type == NDEFRecordType.VCARD:
            return "Contact"
        elif self.record_type == NDEFRecordType.BLUETOOTH:
            return "Bluetooth Pairing"
        return self.payload[:50] + "..." if len(self.payload) > 50 else self.payload


@dataclass
class NFCTag:
    uid: str = ""
    tag_type: NFCTagType = NFCTagType.NTAG213
    state: TagState = TagState.ABSENT
    atr: str = ""
    atqa: str = ""
    sak: int = 0
    ndef_records: List[NDEFRecord] = field(default_factory=list)
    total_reads: int = 0
    total_writes: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    read_count: int = 0
    is_writable: bool = True
    is_locked: bool = False
    max_data_bytes: int = 144
    used_data_bytes: int = 0
    manufacturer: str = ""
    product: str = ""
    Nickname: str = ""

    @property
    def state_icon(self) -> str:
        return STATE_ICONS.get(self.state, "❓")

    @property
    def type_icon(self) -> str:
        return TAG_TYPE_ICONS.get(self.tag_type, "❓")

    @property
    def uid_display(self) -> str:
        return ":".join(self.uid[i:i+2] for i in range(0, len(self.uid), 2))

    @property
    def capacity_bar(self) -> str:
        if self.max_data_bytes == 0:
            return ""
        pct = (self.used_data_bytes / self.max_data_bytes) * 100
        filled = int(pct / 5)
        return "█" * filled + "░" * (20 - filled)

    @property
    def capacity_str(self) -> str:
        return f"{self.used_data_bytes}/{self.max_data_bytes} bytes"

    @property
    def record_count(self) -> int:
        return len(self.ndef_records)

    @property
    def first_seen_str(self) -> str:
        if self.first_seen == 0:
            return "N/A"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.first_seen))

    @property
    def last_seen_str(self) -> str:
        if self.last_seen == 0:
            return "N/A"
        return time.strftime("%H:%M:%S", time.localtime(self.last_seen))

    @property
    def display_name(self) -> str:
        return self.Nickname if self.Nickname else self.uid_display

    @property
    def sak_hex(self) -> str:
        return f"0x{self.sak:02X}"

    @property
    def memory_info(self) -> str:
        return f"{self.type_icon} {self.tag_type.value} ({self.capacity_str})"


@dataclass
class AutomationTrigger:
    name: str = ""
    tag_uid: str = ""
    action: TriggerAction = TriggerAction.SHOW_NOTIFICATION
    parameters: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    last_triggered: float = 0.0
    trigger_count: int = 0
    cooldown_s: int = 5

    @property
    def action_icon(self) -> str:
        return TRIGGER_ICONS.get(self.action, "❓")

    @property
    def last_triggered_str(self) -> str:
        if self.last_triggered == 0:
            return "Never"
        return time.strftime("%H:%M:%S", time.localtime(self.last_triggered))

    @property
    def param_display(self) -> str:
        if not self.parameters:
            return "No params"
        parts = [f"{k}={v}" for k, v in list(self.parameters.items())[:3]]
        return ", ".join(parts)


@dataclass
class TagOperation:
    timestamp: float = 0.0
    operation: str = ""  # read, write, emulate, lock, erase
    tag_uid: str = ""
    tag_type: str = ""
    success: bool = True
    details: str = ""
    duration_ms: int = 0

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    @property
    def icon(self) -> str:
        icons = {
            "read": "📖", "write": "✏️", "emulate": "👻",
            "lock": "🔒", "erase": "🗑️",
        }
        return icons.get(self.operation, "❓")

    @property
    def status_icon(self) -> str:
        return "✅" if self.success else "❌"


@dataclass
class ReaderConfig:
    backend: ReaderBackend = ReaderBackend.LIBNFC
    auto_poll: bool = True
    poll_interval_ms: int = 500
    max_retries: int = 3
    timeout_ms: int = 5000
    emulate_mode: bool = False
    emulate_tag_type: NFCTagType = NFCTagType.NTAG213
    anti_collision: bool = True

    @property
    def backend_display(self) -> str:
        return self.backend.value


class NFCManager:
    def __init__(self):
        self.tags: List[NFCTag] = []
        self.triggers: List[AutomationTrigger] = []
        self.history: List[TagOperation] = []
        self.config: ReaderConfig = ReaderConfig()
        self._selected_tag: int = 0
        self._selected_trigger: int = 0
        self._view_mode: str = "tags"
        self._reader_connected: bool = True
        self._create_sample_data()

    def _create_sample_data(self):
        now = time.time()

        self.tags = [
            NFCTag(
                uid="04A1B2C3D4E5F6",
                tag_type=NFCTagType.NTAG216,
                state=TagState.PRESENT,
                atr="3B8F8001804F0CA000000306070002B1D031",
                atqa="0044", sak=0x00,
                ndef_records=[
                    NDEFRecord(NDEFRecordType.URL, url="https://nyrqis.dev"),
                    NDEFRecord(NDEFRecordType.TEXT, payload="Welcome to Nyrqis OS!"),
                ],
                total_reads=45, total_writes=3,
                first_seen=now - 86400 * 30, last_seen=now - 300,
                max_data_bytes=888, used_data_bytes=156,
                manufacturer="NXP Semiconductors", product="NTAG216",
                Nickname="Nyrqis Desk Tag",
            ),
            NFCTag(
                uid="A1B2C3D4",
                tag_type=NFCTagType.MIFARE_CLASSIC_1K,
                state=TagState.PRESENT,
                atqa="0004", sak=0x08,
                ndef_records=[
                    NDEFRecord(NDEFRecordType.VCARD,
                               payload="BEGIN:VCARD\nVERSION:3.0\nFN:Myco\nORG:Nyrqis\nEND:VCARD"),
                ],
                total_reads=12, total_writes=1,
                first_seen=now - 86400 * 15, last_seen=now - 3600,
                max_data_bytes=1024, used_data_bytes=95,
                manufacturer="NXP Semiconductors", product="MIFARE Classic 1K",
                Nickname="Myco Badge",
            ),
            NFCTag(
                uid="05B2C3D4E5F6A7",
                tag_type=NFCTagType.NTAG213,
                state=TagState.PRESENT,
                atqa="0044", sak=0x00,
                ndef_records=[
                    NDEFRecord(NDEFRecordType.WIFI,
                               payload="WPA2-PSK:HomeNet5G:p@ssw0rd!"),
                ],
                total_reads=8, total_writes=2,
                first_seen=now - 86400 * 7, last_seen=now - 1800,
                max_data_bytes=144, used_data_bytes=48,
                manufacturer="NXP Semiconductors", product="NTAG213",
                Nickname="WiFi Config Tag",
            ),
            NFCTag(
                uid="D4C3B2A10F1E2D",
                tag_type=NFCTagType.MIFARE_DESFIRE,
                state=TagState.LOCKED,
                atqa="0344", sak=0x20,
                total_reads=120, total_writes=0,
                first_seen=now - 86400 * 60, last_seen=now - 7200,
                max_data_bytes=8192, used_data_bytes=2048,
                is_writable=False, is_locked=True,
                manufacturer="NXP Semiconductors", product="DESFire EV2",
                Nickname="Access Card",
            ),
            NFCTag(
                uid="F1E2D3C4B5A697",
                tag_type=NFCTagType.ISO15693,
                state=TagState.ABSENT,
                ndef_records=[],
                total_reads=3, total_writes=0,
                first_seen=now - 86400 * 90, last_seen=now - 86400,
                max_data_bytes=2048, used_data_bytes=64,
                manufacturer="STMicroelectronics", product="ST25TV",
                Nickname="Library Book",
            ),
        ]

        self.triggers = [
            AutomationTrigger("Open Nyrqis Site", "04A1B2C3D4E5F6",
                              TriggerAction.OPEN_URL, {"url": "https://nyrqis.dev"},
                              enabled=True, last_triggered=now - 300, trigger_count=45),
            AutomationTrigger("Toggle WiFi", "05B2C3D4E5F6A7",
                              TriggerAction.TOGGLE_WIFI, {},
                              enabled=True, last_triggered=now - 1800, trigger_count=12),
            AutomationTrigger("Connect to Home WiFi", "05B2C3D4E5F6A7",
                              TriggerAction.CONNECT_WIFI, {"ssid": "HomeNet5G", "password": "p@ssw0rd!"},
                              enabled=True, trigger_count=0),
            AutomationTrigger("Show Welcome", "04A1B2C3D4E5F6",
                              TriggerAction.SHOW_NOTIFICATION, {"title": "Welcome!", "body": "Hello from Nyrqis"},
                              enabled=True, last_triggered=now - 300, trigger_count=45),
            AutomationTrigger("Run Build Script", "A1B2C3D4",
                              TriggerAction.RUN_COMMAND, {"cmd": "make build-nyrqis"},
                              enabled=False, trigger_count=0),
            AutomationTrigger("Lock Workstation", "D4C3B2A10F1E2D",
                              TriggerAction.RUN_COMMAND, {"cmd": "loginctl lock-session"},
                              enabled=True, last_triggered=now - 7200, trigger_count=8),
            AutomationTrigger("Set Timer 15min", "04A1B2C3D4E5F6",
                              TriggerAction.SET_TIMER, {"minutes": "15", "label": "Pomodoro"},
                              enabled=False, trigger_count=0),
        ]

        self.history = [
            TagOperation(now - 300, "read", "04A1B2C3D4E5F6", "NTAG216", True, "2 records", 12),
            TagOperation(now - 310, "read", "A1B2C3D4", "MIFARE Classic 1K", True, "1 record", 18),
            TagOperation(now - 600, "write", "05B2C3D4E5F6A7", "NTAG213", True, "WiFi config", 45),
            TagOperation(now - 1200, "read", "04A1B2C3D4E5F6", "NTAG216", True, "2 records", 15),
            TagOperation(now - 1800, "read", "D4C3B2A10F1E2D", "DESFire EV2", True, "0 records (locked)", 10),
            TagOperation(now - 3600, "read", "A1B2C3D4", "MIFARE Classic 1K", True, "1 record", 22),
            TagOperation(now - 7200, "write", "04A1B2C3D4E5F6", "NTAG216", True, "URL record", 38),
            TagOperation(now - 8640, "read", "F1E2D3C4B5A697", "ISO15693", False, "Tag not in range", 5000),
            TagOperation(now - 14400, "read", "A1B2C3D4", "MIFARE Classic 1K", True, "1 record", 20),
            TagOperation(now - 28800, "erase", "05B2C3D4E5F6A7", "NTAG213", True, "Cleared 1 record", 35),
        ]

    # ─── Navigation ────────────────────────────────────────────────────

    @property
    def selected_tag(self) -> Optional[NFCTag]:
        if 0 <= self._selected_tag < len(self.tags):
            return self.tags[self._selected_tag]
        return None

    def select_tag(self, idx: int):
        if 0 <= idx < len(self.tags):
            self._selected_tag = idx

    def select_trigger(self, idx: int):
        if 0 <= idx < len(self.triggers):
            self._selected_trigger = idx

    def set_view(self, view: str):
        self._view_mode = view

    def select_down(self):
        if self._view_mode == "tags":
            self._selected_tag = min(self._selected_tag + 1, len(self.tags) - 1)
        elif self._view_mode == "triggers":
            self._selected_trigger = min(self._selected_trigger + 1, len(self.triggers) - 1)

    def select_up(self):
        if self._view_mode == "tags":
            self._selected_tag = max(self._selected_tag - 1, 0)
        elif self._view_mode == "triggers":
            self._selected_trigger = max(self._selected_trigger - 1, 0)

    # ─── Tag Actions ───────────────────────────────────────────────────

    def read_tag(self, idx: int = -1) -> Optional[NFCTag]:
        i = idx if idx >= 0 else self._selected_tag
        if 0 <= i < len(self.tags):
            tag = self.tags[i]
            if tag.state != TagState.ABSENT:
                tag.state = TagState.READING
                tag.read_count += 1
                tag.total_reads += 1
                tag.last_seen = time.time()
                tag.state = TagState.PRESENT
                self.history.insert(0, TagOperation(
                    time.time(), "read", tag.uid, tag.tag_type.value,
                    True, f"{tag.record_count} records", 15
                ))
                return tag
        return None

    def write_tag(self, idx: int, record: NDEFRecord) -> bool:
        i = idx if idx >= 0 else self._selected_tag
        if 0 <= i < len(self.tags):
            tag = self.tags[i]
            if tag.is_writable and not tag.is_locked:
                tag.ndef_records.append(record)
                tag.used_data_bytes += len(record.payload.encode())
                tag.total_writes += 1
                self.history.insert(0, TagOperation(
                    time.time(), "write", tag.uid, tag.tag_type.value,
                    True, f"Added {record.type_name}", 40
                ))
                return True
        return False

    def erase_tag(self, idx: int) -> bool:
        i = idx if idx >= 0 else self._selected_tag
        if 0 <= i < len(self.tags):
            tag = self.tags[i]
            if not tag.is_locked:
                count = len(tag.ndef_records)
                tag.ndef_records.clear()
                tag.used_data_bytes = 0
                self.history.insert(0, TagOperation(
                    time.time(), "erase", tag.uid, tag.tag_type.value,
                    True, f"Cleared {count} records", 35
                ))
                return True
        return False

    def lock_tag(self, idx: int) -> bool:
        if 0 <= idx < len(self.tags):
            tag = self.tags[idx]
            tag.is_locked = True
            tag.is_writable = False
            tag.state = TagState.LOCKED
            self.history.insert(0, TagOperation(
                time.time(), "lock", tag.uid, tag.tag_type.value,
                True, "Tag locked permanently", 10
            ))
            return True
        return False

    def simulate_tag(self) -> NFCTag:
        """Simulate discovering a new tag."""
        tag = NFCTag(
            uid=hashlib.md5(str(time.time()).encode()).hexdigest()[:14].upper(),
            tag_type=NFCTagType.NTAG213,
            state=TagState.PRESENT,
            atqa="0044", sak=0x00,
            max_data_bytes=144,
            first_seen=time.time(), last_seen=time.time(),
        )
        self.tags.append(tag)
        self.history.insert(0, TagOperation(
            time.time(), "read", tag.uid, tag.tag_type.value,
            True, "New tag discovered", 50
        ))
        return tag

    # ─── Trigger Actions ───────────────────────────────────────────────

    def fire_trigger(self, idx: int) -> bool:
        if 0 <= idx < len(self.triggers):
            trigger = self.triggers[idx]
            if trigger.enabled:
                trigger.last_triggered = time.time()
                trigger.trigger_count += 1
                return True
        return False

    def toggle_trigger(self, idx: int) -> bool:
        if 0 <= idx < len(self.triggers):
            self.triggers[idx].enabled = not self.triggers[idx].enabled
            return True
        return False

    def add_trigger(self, name: str, tag_uid: str, action: TriggerAction,
                    params: Dict[str, str] = None) -> AutomationTrigger:
        trigger = AutomationTrigger(name, tag_uid, action, params or {})
        self.triggers.append(trigger)
        return trigger

    def remove_trigger(self, idx: int) -> bool:
        if 0 <= idx < len(self.triggers):
            self.triggers.pop(idx)
            return True
        return False

    # ─── Config ────────────────────────────────────────────────────────

    def toggle_reader(self):
        self._reader_connected = not self._reader_connected
        if not self._reader_connected:
            for tag in self.tags:
                if tag.state in (TagState.PRESENT, TagState.READING, TagState.WRITING):
                    tag.state = TagState.ABSENT

    @property
    def reader_connected(self) -> bool:
        return self._reader_connected

    # ─── Queries ───────────────────────────────────────────────────────

    def get_present_tags(self) -> List[NFCTag]:
        return [t for t in self.tags if t.state != TagState.ABSENT]

    def get_locked_tags(self) -> List[NFCTag]:
        return [t for t in self.tags if t.is_locked]

    def get_writable_tags(self) -> List[NFCTag]:
        return [t for t in self.tags if t.is_writable and not t.is_locked]

    def get_triggers_for_tag(self, uid: str) -> List[AutomationTrigger]:
        return [t for t in self.triggers if t.tag_uid == uid]

    def search_tags(self, query: str) -> List[NFCTag]:
        q = query.lower()
        return [t for t in self.tags if q in t.uid.lower() or q in t.Nickname.lower()
                or q in t.tag_type.value.lower()]

    def search_triggers(self, query: str) -> List[AutomationTrigger]:
        q = query.lower()
        return [t for t in self.triggers if q in t.name.lower()]

    def get_stats(self) -> Dict:
        present = len(self.get_present_tags())
        return {
            "total_tags": len(self.tags),
            "present": present,
            "locked": len(self.get_locked_tags()),
            "writable": len(self.get_writable_tags()),
            "triggers": len(self.triggers),
            "active_triggers": sum(1 for t in self.triggers if t.enabled),
            "total_reads": sum(t.total_reads for t in self.tags),
            "total_writes": sum(t.total_writes for t in self.tags),
            "history": len(self.history),
        }
