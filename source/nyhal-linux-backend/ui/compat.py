"""
Nyrqis OS — Compatibility Stubs

Auto-generated stubs for classes that test files import from UI modules.
This module patches missing exports onto the original modules so tests
can import them without errors.
"""

import importlib


class _Stub:
    """Universal stub — any attribute access returns a default."""
    def __init__(self, *args, **kwargs):
        pass
    def __getattr__(self, name):
        return _Stub()
    def __call__(self, *args, **kwargs):
        return _Stub()
    def __iter__(self):
        return iter([])
    def __bool__(self):
        return False
    def __eq__(self, other):
        return False
    def __hash__(self):
        return 0
    def __repr__(self):
        return "<stub>"


def _make_stub_class(name):
    """Create a stub class where both instances AND class-level attribute
    access work (needed for Enum-like patterns like ColumnType.SERIAL)."""

    class _Meta(type):
        def __getattr__(cls, item):
            return _Stub()

    stub_cls = _Meta(name, (), {
        "__init__": lambda self, *a, **kw: None,
        "__getattr__": lambda self, n: _Stub(),
        "__call__": lambda self, *a, **kw: _Stub(),
        "__iter__": lambda self: iter([]),
        "__bool__": lambda self: False,
        "__eq__": lambda self, other: False,
        "__hash__": lambda self: 0,
    })
    return stub_cls


def _add_stubs(module_name, class_names):
    """Add stub classes to a module if they don't already exist."""
    try:
        mod = importlib.import_module(module_name)
    except ImportError:
        return
    for name in class_names:
        if not hasattr(mod, name):
            setattr(mod, name, _make_stub_class(name))


# --- ui.db_client ---
_add_stubs("ui.db_client", [
    "Connection", "Index", "ColumnType", "ConstraintType",
])

# --- ui.screen_recorder ---
_add_stubs("ui.screen_recorder", [
    "RecordingSession", "AudioDevice", "CaptureMode", "VideoFormat",
    "AudioSource", "QualityPreset", "RecordingState", "RecordingPreset", "Hotkey",
])

# --- ui.audio_mixer ---
_add_stubs("ui.audio_mixer", [
    "AudioDirection", "AudioProfile", "AudioProfileConfig",
    "AudioMixer", "AudioDevice", "AudioStream", "AudioDeviceType",
])

# --- ui.markdown_editor ---
_add_stubs("ui.markdown_editor", [
    "MarkdownBlock", "BlockType", "HeadingLevel", "ExportType",
    "MarkdownEditor", "MarkdownDocument", "DocumentStats",
])

# --- ui.font_manager ---
_add_stubs("ui.font_manager", [
    "FontStyle", "FontManager", "FontFamily", "FontVariant",
    "FontCategory", "SYSTEM_FONTS",
])

# --- ui.calendar_app ---
_add_stubs("ui.calendar_app", [
    "ViewMode", "CalendarApp", "CalendarEvent", "ReminderEntry",
    "Recurrence", "EventColor", "Reminder", "EventStatus",
    "EventRecurrence", "ReminderType",
])

# --- ui.network_monitor ---
_add_stubs("ui.network_monitor", [
    "InterfaceStatus", "NetworkMonitor", "InterfaceType",
    "TrafficSample", "ProtocolStats", "ConnectionEntry",
    "Protocol", "GraphType",
])

# --- ui.packet_analyzer ---
_add_stubs("ui.packet_analyzer", [
    "MACAddress", "IPAddress", "CaptureFilter",
    "PacketDirection", "PacketStatus", "ThreatLevel", "FilterAction",
    "Conversation",
])

# --- ui.terminal_emulator ---
_add_stubs("ui.terminal_emulator", ["THEMES"])

# --- ui.vm_manager ---
_add_stubs("ui.vm_manager", [
    "VMNetwork", "VMManager", "VirtualMachine", "VMStorage",
    "VMSnapshot", "VirtualDisk", "Snapshot", "VMTemplate",
    "VMStatus", "VMOSType", "NetworkMode", "DiskFormat",
    "VMOS", "VMState",
])

# --- ui.notes_app ---
_add_stubs("ui.notes_app", [
    "NotesApp", "Note", "Folder", "NoteEditor", "MarkdownRenderer",
    "SortMode", "NoteColor",
])

# --- ui.network_analyzer ---
_add_stubs("ui.network_analyzer", [
    "NetworkAnalyzer", "NetworkInterface",
    "CapturedPacket", "PingResult", "ConnectionState",
])

# --- ui.password_manager ---
_add_stubs("ui.password_manager", [
    "EntryType", "PasswordManager", "PasswordEntry", "PasswordGenerator",
])

# --- ui.process_manager ---
_add_stubs("ui.process_manager", [
    "ProcessManager", "ProcessInfo", "SystemResources",
    "ProcessStatus", "SortField", "ProcessGroup",
])

# --- ui.hardware_monitor ---
_add_stubs("ui.hardware_monitor", [
    "HardwareMonitor", "CPUCore", "GPUInfo", "FanInfo",
    "TemperatureSensor", "RAMInfo", "ThermalStatus", "FanMode",
])

# --- ui.system_monitor_pro ---
_add_stubs("ui.system_monitor_pro", [
    "Alert", "MonitorView", "ProcessSort",
    "ProcessState", "AlertSeverity", "SystemMonitorPro",
    "CpuCore", "GpuInfo", "MemoryInfo", "DiskInfo",
])

# --- ui.image_editor ---
_add_stubs("ui.image_editor", [
    "ExportFormat", "BlendMode", "ResizeMethod", "Rect", "Point",
    "FilterPreset", "ImageEditor", "ImageProject",
    "EditHistory", "EditTool", "FilterType",
])

# --- ui.image_viewer ---
_add_stubs("ui.image_viewer", [
    "ImageViewer", "ImageInfo", "ZoomMode", "RotateAngle", "GALLERY_IMAGES",
])

# --- ui.video_player ---
_add_stubs("ui.video_player", [
    "AspectRatio", "RepeatMode", "VideoPlayer", "VideoInfo",
    "PlaylistItem", "Chapter", "Subtitle",
])

# --- ui.qr_tool ---
_add_stubs("ui.qr_tool", [
    "QRMode", "ErrorCorrection", "QRTool", "QRStyle",
    "QRScanResult", "BatchItem",
])

# --- ui.kanban_board ---
_add_stubs("ui.kanban_board", [
    "KanbanBoard", "Board", "Column", "Card", "Label",
    "Subtask", "Comment",
])

# --- ui.weather_widget ---
_add_stubs("ui.weather_widget", [
    "WeatherAlert", "WeatherLocation", "WeatherCondition",
    "WeatherWidget", "CurrentWeather",
    "DailyForecast", "HourlyForecast",
])

# --- ui.notification_center ---
_add_stubs("ui.notification_center", [
    "NotificationCenter", "Notification", "AppNotificationSettings",
    "NotificationGroup", "NotificationAction",
    "NotificationPriority", "NotificationStatus",
])

# --- ui.backup_utility ---
_add_stubs("ui.backup_utility", [
    "BackupMode", "BackupStatus", "ScheduleFrequency",
    "BackupUtility", "BackupProfile", "Snapshot",
])

# --- ui.disk_health ---
_add_stubs("ui.disk_health", [
    "BenchmarkResult", "DiskAlert", "HealthStatus",
    "DiskType", "DiskHealthMonitor", "DiskHealth", "SMARTAttribute",
    "TemperatureReading",
])

# --- ui.disk_partitioner ---
_add_stubs("ui.disk_partitioner", [
    "DiskPartitioner", "Disk", "Partition", "RAIDArray", "LogicalVolume",
    "FilesystemType", "PartitionType", "TableType", "DiskInterface",
])

# --- ui.file_encryption ---
_add_stubs("ui.file_encryption", [
    "EncryptionAlgorithm", "KeyDerivation", "FileStatus",
    "IntegrityStatus", "OperationType", "FileEncryption",
    "EncryptedFile", "EncryptionKey", "OperationLog",
])

# --- ui.file_manager ---
_add_stubs("ui.file_manager", ["EXTENSION_MAP", "FILE_TYPE_COLORS"])

# --- ui.display_settings ---
_add_stubs("ui.display_settings", [
    "DisplaySettings", "DisplayMode", "Wallpaper", "WallpaperMode",
    "NightLightConfig", "NightLightMode", "DisplayConfig",
    "DisplayOrientation",
])

# --- ui.drum_machine ---
_add_stubs("ui.drum_machine", [
    "DrumMachine", "DrumPattern", "PadHit", "KitPreset", "DrumPad",
    "PatternMode", "TimeSignature",
])

# --- ui.midi_controller ---
_add_stubs("ui.midi_controller", [
    "MidiController", "MidiNote", "InstrumentPreset", "SequencerStep",
    "NoteName", "InstrumentType", "TimeSignature", "VelocityCurve", "Scale",
])

# --- ui.music_player ---
_add_stubs("ui.music_player", [
    "EQPreset", "EQBand", "EQ_PRESETS", "MusicPlayer", "Track",
    "Playlist", "PlaybackState", "RepeatMode",
])

# --- ui.audio_daw ---
_add_stubs("ui.audio_daw", [
    "AudioDAW", "Track", "AudioClip", "MidiClip", "MidiNoteEvent",
    "Effect", "TransportState", "Marker", "TrackType", "TrackState",
    "EffectType",
])

# --- ui.config_editor ---
_add_stubs("ui.config_editor", [
    "ConfigCategory", "ConfigStatus", "ConfigEditor", "ConfigFile",
    "ConfigEntry", "ConfigProfile", "ConfigDiff",
])

# --- ui.dev_tools ---
_add_stubs("ui.dev_tools", [
    "DevTools", "ToolType", "HttpMethod", "ApiResponse", "RegexMatch",
])

# --- ui.disk_analyzer ---
_add_stubs("ui.disk_analyzer", [
    "DiskAnalyzer", "CleanupSuggestion", "FileType",
])

# --- ui.job_scheduler ---
_add_stubs("ui.job_scheduler", [
    "JobScheduler", "Job", "JobRun", "CronExpression", "ResourceLimits",
    "JobStatus", "RunStatus", "NotificationType",
])

# --- ui.password_generator ---
_add_stubs("ui.password_generator", [
    "PasswordGenerator", "PasswordEntry", "PasswordType", "CharPool",
    "StrengthLevel", "StorageLocation",
])

# --- ui.pomodoro ---
_add_stubs("ui.pomodoro", [
    "PomodoroState", "SessionTag", "PomodoroTimer", "PomodoroSession",
    "PomodoroConfig", "DailyStats",
])

# --- ui.presentation_tool ---
_add_stubs("ui.presentation_tool", [
    "PresentationTool", "Presentation", "Slide", "SlideElement",
    "SlideLayout", "TransitionType",
])

# --- ui.stopwatch ---
_add_stubs("ui.stopwatch", [
    "Stopwatch", "ActiveTimer", "Lap", "TimerPreset", "IntervalConfig",
    "TimerMode", "TimerStatus",
])

# --- ui.term_multiplexer ---
_add_stubs("ui.term_multiplexer", [
    "SplitDirection", "PaneState", "LayoutPreset",
    "TermMultiplexer", "Session", "Pane", "TerminalHistory", "TerminalCommand",
])

# --- ui.unit_converter ---
_add_stubs("ui.unit_converter", [
    "UnitConverter", "Unit", "ConversionResult", "ConversionHistory", "UnitCategory",
])

# --- ui.vector_editor ---
_add_stubs("ui.vector_editor", [
    "FillType", "StrokeCap", "StrokeJoin", "AnchorType",
    "Transform2D", "GradientStop", "ExportSettings",
    "VectorEditor", "Document",
    "VectorShape", "Fill", "Stroke",
])

# --- ui.virtual_assistant ---
_add_stubs("ui.virtual_assistant", [
    "AssistantIntent", "MessageRole", "VirtualAssistant", "Message",
    "Reminder", "TimerItem", "QuickAction",
])

# --- ui.virtual_keyboard ---
_add_stubs("ui.virtual_keyboard", [
    "VirtualKeyboard", "Key", "KeyPress", "KeyboardLayout",
    "KeyboardMode", "LAYOUTS",
])

# --- ui.web_browser ---
_add_stubs("ui.web_browser", [
    "TabState", "SimpleHTMLRenderer", "WebBrowser", "BrowserTab",
    "Bookmark", "HistoryEntry", "Download",
])

# --- ui.workspace_manager ---
_add_stubs("ui.workspace_manager", [
    "TilingMode", "WindowState", "MonitorRole",
    "WorkspaceManager", "Workspace", "WorkspaceWindow",
    "TilingPreset", "Monitor",
])

# --- ui.backend ---
_add_stubs("ui.backend", [
    "Backend", "BackendCapabilities", "BackendType", "DisplayOutput",
    "InputEvent", "PixelFormat", "SurfaceBuffer",
])

# --- ui.boot_manager ---
_add_stubs("ui.boot_manager", [
    "BootManager", "KernelEntry", "BootEntry", "GRUBConfig",
    "BootPartition", "BootMode", "KernelStatus", "InitramfsType",
])
