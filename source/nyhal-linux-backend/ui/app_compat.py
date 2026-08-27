#!/usr/bin/env python3
"""App compatibility layer for running Android APKs and Windows .exe/.msi apps.

This module provides a compatibility framework that translates app
requests to Nyrqis native operations. It handles:

- Android APK manifest parsing and permission mapping
- Windows PE/MSI manifest parsing and API translation
- App lifecycle management (install, launch, terminate)
- Permission bridging (Android permissions → Nyrqis capabilities)

Design principles (Apple HIG + cross-platform):
- Apps run in sandboxed containers with minimal permissions
- UI is rendered through the Nyrqis compositor (HIG-compliant)
- Native performance via direct translation where possible

References:
    - Android APK format: https://developer.android.com/guide/topics/manifest/manifest-intro
    - Windows PE format: https://docs.microsoft.com/en-us/windows/win32/debug/pe-format
    - Nyrqis container primitives: NPS-017 §4.1
"""

import hashlib
import json
import logging
import os
import struct
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App types and platforms
# ---------------------------------------------------------------------------

class AppPlatform(Enum):
    """Supported app platforms."""
    ANDROID = "android"
    WINDOWS = "windows"
    NYRQIS = "nyrqis"  # Native Nyrqis apps


class AppStatus(Enum):
    """App lifecycle status."""
    INSTALLED = "installed"
    RUNNING = "running"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


# ---------------------------------------------------------------------------
# Android APK compatibility
# ---------------------------------------------------------------------------

# Android permissions → Nyrqis capabilities mapping
ANDROID_PERMISSION_MAP: Dict[str, str] = {
    "android.permission.INTERNET": "CAP_NETWORK_SOCKET",
    "android.permission.ACCESS_NETWORK_STATE": "CAP_SYSTEM_INFO",
    "android.permission.READ_EXTERNAL_STORAGE": "CAP_FILESYSTEM_READ",
    "android.permission.WRITE_EXTERNAL_STORAGE": "CAP_FILESYSTEM_WRITE",
    "android.permission.CAMERA": "CAP_MEDIA_IMAGES",
    "android.permission.RECORD_AUDIO": "CAP_MEDIA_AUDIO",
    "android.permission.ACCESS_FINE_LOCATION": "CAP_NETWORK_SOCKET",
    "android.permission.ACCESS_COARSE_LOCATION": "CAP_NETWORK_SOCKET",
    "android.permission.VIBRATE": "CAP_DEVICE_VIBRATE",
    "android.permission.WAKE_LOCK": "CAP_SYSTEM_INFO",
    "android.permission.RECEIVE_BOOT_COMPLETED": "CAP_SYSTEM_INFO",
    "android.permission.FOREGROUND_SERVICE": "CAP_SYSTEM_INFO",
    "android.permission.POST_NOTIFICATIONS": "CAP_SYSTEM_INFO",
    "android.permission.BLUETOOTH": "CAP_NETWORK_SOCKET",
    "android.permission.NFC": "CAP_NETWORK_SOCKET",
    "android.permission.READ_CONTACTS": "CAP_FILESYSTEM_READ",
    "android.permission.READ_CALENDAR": "CAP_FILESYSTEM_READ",
    "android.permission.SEND_SMS": "CAP_NETWORK_SOCKET",
    "android.permission.CALL_PHONE": "CAP_NETWORK_SOCKET",
}

# Android API levels (for version compatibility)
ANDROID_API_LEVELS = {
    "1.0": 1, "1.1": 2, "1.5": 3, "1.6": 4,
    "2.0": 5, "2.0.1": 6, "2.1": 7, "2.2": 8,
    "2.3": 9, "2.3.3": 10, "3.0": 11, "3.1": 12,
    "3.2": 13, "4.0": 14, "4.0.3": 15, "4.1": 16,
    "4.2": 17, "4.3": 18, "4.4": 19, "5.0": 21,
    "5.1": 22, "6.0": 23, "7.0": 24, "7.1": 25,
    "8.0": 26, "8.1": 27, "9.0": 28, "10": 29,
    "11": 30, "12": 31, "12L": 32, "13": 33,
    "14": 34, "15": 35,
}


@dataclass
class AndroidManifest:
    """Parsed Android APK manifest."""
    package_name: str = ""
    version_name: str = ""
    version_code: int = 0
    min_sdk_version: int = 21
    target_sdk_version: int = 34
    permissions: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    activities: List[Dict[str, Any]] = field(default_factory=list)
    services: List[Dict[str, Any]] = field(default_factory=list)
    receivers: List[Dict[str, Any]] = field(default_factory=list)
    providers: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)


class AndroidCompat:
    """Android APK compatibility layer.
    
    Parses APK manifests and translates Android concepts to Nyrqis:
    - Android permissions → Nyrqis capabilities
    - Activities → Windows/views
    - Services → Background tasks
    - Content providers → File system access
    """
    
    def __init__(self):
        self.installed_apps: Dict[str, AndroidManifest] = {}
    
    def parse_apk(self, apk_path: str) -> Optional[AndroidManifest]:
        """Parse an APK file and extract its manifest.
        
        The APK is a ZIP file containing:
        - AndroidManifest.xml (binary XML)
        - classes.dex (Dalvik bytecode)
        - resources.arsc (compiled resources)
        - META-INF/ (signatures)
        - lib/ (native libraries)
        """
        if not os.path.exists(apk_path):
            logger.error(f"APK not found: {apk_path}")
            return None
        
        try:
            with zipfile.ZipFile(apk_path, 'r') as apk:
                # Check for required files
                if 'AndroidManifest.xml' not in apk.namelist():
                    logger.error(f"Invalid APK: missing AndroidManifest.xml")
                    return None
                
                # Parse binary XML (simplified)
                manifest_data = apk.read('AndroidManifest.xml')
                manifest = self._parse_binary_xml(manifest_data)
                
                # Extract DEX info
                dex_files = [f for f in apk.namelist() if f.endswith('.dex')]
                
                # Extract native libs
                native_libs = [f for f in apk.namelist() 
                             if f.startswith('lib/') and f.endswith('.so')]
                
                logger.info(f"Parsed APK: {manifest.package_name} "
                           f"v{manifest.version_name} "
                           f"({len(manifest.permissions)} permissions, "
                           f"{len(native_libs)} native libs)")
                
                return manifest
                
        except zipfile.BadZipFile:
            logger.error(f"Invalid ZIP/APK: {apk_path}")
            return None
        except Exception as e:
            logger.error(f"Failed to parse APK: {e}")
            return None
    
    def _parse_binary_xml(self, data: bytes) -> AndroidManifest:
        """Parse Android binary XML format (simplified).
        
        This is a simplified parser that extracts key manifest elements.
        For production use, a full AXML parser would be needed.
        """
        manifest = AndroidManifest()
        
        # Try to extract strings from the binary XML
        # Binary XML uses a string pool at the beginning
        try:
            # Look for common patterns in the binary data
            text = data.decode('utf-8', errors='ignore')
            
            # Extract package name (simplified heuristic)
            if 'package=' in text:
                start = text.index('package=') + 9
                end = text.index('"', start)
                manifest.package_name = text[start:end]
            
            # Extract permissions (simplified)
            for perm in ANDROID_PERMISSION_MAP:
                if perm in text:
                    manifest.permissions.append(perm)
            
        except Exception:
            pass
        
        return manifest
    
    def map_permissions(self, manifest: AndroidManifest) -> Set[str]:
        """Map Android permissions to Nyrqis capabilities."""
        capabilities = set()
        for perm in manifest.permissions:
            cap = ANDROID_PERMISSION_MAP.get(perm)
            if cap:
                capabilities.add(cap)
        return capabilities
    
    def install(self, apk_path: str) -> Optional[str]:
        """Install an APK and return the app ID."""
        manifest = self.parse_apk(apk_path)
        if manifest is None:
            return None
        
        app_id = f"android:{manifest.package_name}"
        self.installed_apps[app_id] = manifest
        
        logger.info(f"Installed Android app: {app_id}")
        return app_id
    
    def get_launch_intent(self, app_id: str) -> Optional[Dict[str, Any]]:
        """Get the launch intent for an installed app."""
        manifest = self.installed_apps.get(app_id)
        if manifest is None:
            return None
        
        # Find the main/launcher activity
        main_activity = None
        for activity in manifest.activities:
            filters = activity.get('intent_filters', [])
            for f in filters:
                if 'android.intent.action.MAIN' in f.get('actions', []):
                    if 'android.intent.category.LAUNCHER' in f.get('categories', []):
                        main_activity = activity
                        break
            if main_activity:
                break
        
        if main_activity is None and manifest.activities:
            main_activity = manifest.activities[0]
        
        if main_activity:
            return {
                'type': 'activity',
                'component': main_activity.get('name', ''),
                'package': manifest.package_name,
                'capabilities': list(self.map_permissions(manifest)),
            }
        
        return None


# ---------------------------------------------------------------------------
# Windows .exe/.msi compatibility
# ---------------------------------------------------------------------------

# Windows API modules → Nyrqis capability mapping
WINDOWS_API_MAP: Dict[str, List[str]] = {
    "kernel32.dll": ["CAP_FILESYSTEM_WRITE", "CAP_PROCESS_SPAWN"],
    "user32.dll": ["CAP_IPC_SEND"],  # Window management
    "gdi32.dll": ["CAP_IPC_SEND"],   # Graphics
    "advapi32.dll": ["CAP_SYSTEM_INFO"],  # Security
    "shell32.dll": ["CAP_FILESYSTEM_WRITE", "CAP_IPC_SEND"],
    "ole32.dll": ["CAP_IPC_SEND"],  # COM
    "ws2_32.dll": ["CAP_NETWORK_SOCKET"],  # Winsock
    "wininet.dll": ["CAP_NETWORK_SOCKET"],  # Internet
    "winmm.dll": ["CAP_MEDIA_AUDIO"],  # Multimedia
    "msvcrt.dll": [],  # C runtime (no special caps)
    "ntdll.dll": ["CAP_SYSTEM_INFO"],  # NT layer
}

# Windows subsystem types
WINDOWS_SUBSYSTEMS = {
    1: "native",
    2: "windows",    # GUI
    3: "console",    # Console
    5: "os2",
    7: "posix",
    8: "native_windows",
    9: "windows_ce",
    10: "efi_application",
    14: "efi_boot_service_driver",
    15: "efi_runtime_driver",
    16: "efi_rom",
    10: "xbox",
}


@dataclass
class WindowsPEInfo:
    """Parsed Windows PE executable info."""
    filename: str = ""
    is_dll: bool = False
    is_exe: bool = False
    is_msi: bool = False
    subsystem: int = 0
    machine: int = 0
    timestamp: int = 0
    imported_dlls: List[str] = field(default_factory=list)
    resources: Dict[str, Any] = field(default_factory=dict)
    version_info: Dict[str, str] = field(default_factory=dict)


class WindowsCompat:
    """Windows .exe/.msi compatibility layer.
    
    Translates Windows executable concepts to Nyrqis:
    - PE headers → App metadata
    - DLL imports → Required capabilities
    - Win32 API → Nyrqis primitives
    - Registry → Configuration storage
    - MSI → Package management
    """
    
    # PE magic numbers
    MZ_MAGIC = b'MZ'
    PE_MAGIC = b'PE\x00\x00'
    MSI_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'  # OLE2 compound
    
    def __init__(self):
        self.installed_apps: Dict[str, WindowsPEInfo] = {}
    
    def parse_pe(self, exe_path: str) -> Optional[WindowsPEInfo]:
        """Parse a Windows PE executable."""
        if not os.path.exists(exe_path):
            logger.error(f"PE file not found: {exe_path}")
            return None
        
        try:
            with open(exe_path, 'rb') as f:
                # Check MZ header
                magic = f.read(2)
                if magic != self.MZ_MAGIC:
                    # Check if it's an MSI
                    f.seek(0)
                    if f.read(8) == self.MSI_MAGIC:
                        return self._parse_msi(exe_path)
                    logger.error(f"Not a valid PE or MSI: {exe_path}")
                    return None
                
                # Read PE offset
                f.seek(0x3C)
                pe_offset = struct.unpack('<I', f.read(4))[0]
                
                # Read PE signature
                f.seek(pe_offset)
                pe_magic = f.read(4)
                if pe_magic != self.PE_MAGIC:
                    logger.error(f"Invalid PE signature: {exe_path}")
                    return None
                
                info = WindowsPEInfo(filename=os.path.basename(exe_path))
                info.is_exe = True
                
                # Read COFF header
                f.read(4)  # Skip PE signature
                info.machine = struct.unpack('<H', f.read(2))[0]
                f.read(2)  # Number of sections
                f.read(4)  # TimeDateStamp
                f.read(4)  # PointerToSymbolTable
                f.read(4)  # NumberOfSymbols
                characteristics = struct.unpack('<H', f.read(2))[0]
                
                # Check if DLL
                info.is_dll = bool(characteristics & 0x2000)
                info.is_exe = not info.is_dll
                
                # Read optional header
                optional_magic = struct.unpack('<H', f.read(2))[0]
                if optional_magic == 0x20B:  # PE32+
                    f.read(22)  # Skip to Subsystem
                    info.subsystem = struct.unpack('<H', f.read(2))[0]
                elif optional_magic == 0x10B:  # PE32
                    f.read(22)
                    info.subsystem = struct.unpack('<H', f.read(2))[0]
                
                logger.info(f"Parsed PE: {info.filename} "
                           f"({'DLL' if info.is_dll else 'EXE'}, "
                           f"subsystem={info.subsystem})")
                
                return info
                
        except Exception as e:
            logger.error(f"Failed to parse PE: {e}")
            return None
    
    def _parse_msi(self, msi_path: str) -> WindowsPEInfo:
        """Parse an MSI installer package."""
        info = WindowsPEInfo(filename=os.path.basename(msi_path))
        info.is_msi = True
        info.is_exe = False
        
        logger.info(f"Parsed MSI: {info.filename}")
        return info
    
    def get_required_capabilities(self, info: WindowsPEInfo) -> Set[str]:
        """Determine required Nyrqis capabilities from PE imports."""
        capabilities = set()
        for dll in info.imported_dlls:
            caps = WINDOWS_API_MAP.get(dll.lower(), [])
            capabilities.update(caps)
        return capabilities
    
    def get_subsystem_type(self, info: WindowsPEInfo) -> str:
        """Get the Windows subsystem type."""
        return WINDOWS_SUBSYSTEMS.get(info.subsystem, "unknown")
    
    def install(self, exe_path: str) -> Optional[str]:
        """Install a Windows app and return the app ID."""
        info = self.parse_pe(exe_path)
        if info is None:
            return None
        
        app_id = f"windows:{info.filename}"
        self.installed_apps[app_id] = info
        
        logger.info(f"Installed Windows app: {app_id}")
        return app_id
    
    def get_launch_config(self, app_id: str) -> Optional[Dict[str, Any]]:
        """Get launch configuration for a Windows app."""
        info = self.installed_apps.get(app_id)
        if info is None:
            return None
        
        subsystem = self.get_subsystem_type(info)
        caps = self.get_required_capabilities(info)
        
        return {
            'type': 'executable',
            'subsystem': subsystem,
            'is_gui': subsystem == 'windows',
            'is_console': subsystem == 'console',
            'capabilities': list(caps),
            'wine_compat': True,  # Use Wine translation layer
        }


# ---------------------------------------------------------------------------
# Unified App Manager
# ---------------------------------------------------------------------------

@dataclass
class AppInfo:
    """Unified app information."""
    app_id: str
    platform: AppPlatform
    name: str
    version: str = ""
    status: AppStatus = AppStatus.INSTALLED
    capabilities: List[str] = field(default_factory=list)
    manifest: Any = None  # Platform-specific manifest
    container_id: Optional[str] = None  # Nyrqis container running this app


class AppManager:
    """Unified app manager for all platforms.
    
    Manages the lifecycle of Android, Windows, and native Nyrqis apps:
    - Install/uninstall
    - Launch/terminate
    - Permission management
    - Resource allocation
    """
    
    def __init__(self):
        self.android = AndroidCompat()
        self.windows = WindowsCompat()
        self.apps: Dict[str, AppInfo] = {}
        self._next_container_id = 1
    
    def install(self, app_path: str) -> Optional[str]:
        """Install an app from a file path."""
        ext = os.path.splitext(app_path)[1].lower()
        
        if ext == '.apk':
            return self._install_android(app_path)
        elif ext in ('.exe', '.msi'):
            return self._install_windows(app_path)
        elif ext == '.napp':
            return self._install_nyrqis(app_path)
        else:
            logger.error(f"Unsupported app format: {ext}")
            return None
    
    def _install_android(self, apk_path: str) -> Optional[str]:
        """Install an Android APK."""
        app_id = self.android.install(apk_path)
        if app_id is None:
            return None
        
        manifest = self.android.installed_apps[app_id]
        caps = list(self.android.map_permissions(manifest))
        
        info = AppInfo(
            app_id=app_id,
            platform=AppPlatform.ANDROID,
            name=manifest.package_name,
            version=manifest.version_name,
            capabilities=caps,
            manifest=manifest,
        )
        self.apps[app_id] = info
        return app_id
    
    def _install_windows(self, exe_path: str) -> Optional[str]:
        """Install a Windows .exe or .msi."""
        app_id = self.windows.install(exe_path)
        if app_id is None:
            return None
        
        pe_info = self.windows.installed_apps[app_id]
        caps = list(self.windows.get_required_capabilities(pe_info))
        
        info = AppInfo(
            app_id=app_id,
            platform=AppPlatform.WINDOWS,
            name=pe_info.filename,
            version=pe_info.version_info.get('FileVersion', ''),
            capabilities=caps,
            manifest=pe_info,
        )
        self.apps[app_id] = info
        return app_id
    
    def _install_nyrqis(self, napp_path: str) -> Optional[str]:
        """Install a native Nyrqis app (.napp package)."""
        # Delegate to existing NyApp packager
        try:
            import json
            with zipfile.ZipFile(napp_path, 'r') as napp:
                manifest = json.loads(napp.read('manifest.json'))
                app_id = f"nyrqis:{manifest.get('id', os.path.basename(napp_path))}"
                
                info = AppInfo(
                    app_id=app_id,
                    platform=AppPlatform.NYRQIS,
                    name=manifest.get('name', app_id),
                    version=manifest.get('version', '1.0'),
                    capabilities=manifest.get('capabilities', []),
                    manifest=manifest,
                )
                self.apps[app_id] = info
                return app_id
                
        except Exception as e:
            logger.error(f"Failed to install .napp: {e}")
            return None
    
    def uninstall(self, app_id: str) -> bool:
        """Uninstall an app."""
        if app_id in self.apps:
            del self.apps[app_id]
            logger.info(f"Uninstalled app: {app_id}")
            return True
        return False
    
    def get_app(self, app_id: str) -> Optional[AppInfo]:
        """Get app information."""
        return self.apps.get(app_id)
    
    def list_apps(self, platform: Optional[AppPlatform] = None) -> List[AppInfo]:
        """List installed apps, optionally filtered by platform."""
        apps = list(self.apps.values())
        if platform:
            apps = [a for a in apps if a.platform == platform]
        return apps
    
    def launch(self, app_id: str) -> Optional[Dict[str, Any]]:
        """Launch an app and return its container configuration."""
        info = self.apps.get(app_id)
        if info is None:
            return None
        
        # Create container config based on platform
        from backend.container import ContainerConfig
        
        container_config = ContainerConfig(
            name=f"app-{info.app_id.replace(':', '-')}",
            command=self._get_launch_command(info),
            capabilities=info.capabilities,
        )
        
        # Platform-specific setup
        if info.platform == AppPlatform.ANDROID:
            # Android apps need network and filesystem access
            container_config.network = True
            
        elif info.platform == AppPlatform.WINDOWS:
            # Windows apps need Wine compatibility
            launch_config = self.windows.get_launch_config(app_id)
            if launch_config:
                container_config.network = launch_config.get('is_gui', False)
        
        info.status = AppStatus.RUNNING
        info.container_id = container_config.name
        
        return {
            'app_id': app_id,
            'container_config': container_config,
            'platform': info.platform.value,
        }
    
    def _get_launch_command(self, info: AppInfo) -> List[str]:
        """Get the launch command for an app."""
        if info.platform == AppPlatform.ANDROID:
            # Android apps run through the ART runtime
            return ["/system/bin/app_process", info.name]
        elif info.platform == AppPlatform.WINDOWS:
            # Windows apps run through Wine
            return ["wine", info.name]
        elif info.platform == AppPlatform.NYRQIS:
            # Native apps run directly
            if info.manifest and isinstance(info.manifest, dict):
                return info.manifest.get('command', [info.name])
            return [info.name]
        return [info.name]
    
    def terminate(self, app_id: str) -> bool:
        """Terminate a running app."""
        info = self.apps.get(app_id)
        if info and info.status == AppStatus.RUNNING:
            info.status = AppStatus.TERMINATED
            info.container_id = None
            logger.info(f"Terminated app: {app_id}")
            return True
        return False


# ---------------------------------------------------------------------------
# Global app manager instance
# ---------------------------------------------------------------------------

_app_manager: Optional[AppManager] = None


def get_app_manager() -> AppManager:
    """Get or create the global app manager."""
    global _app_manager
    if _app_manager is None:
        _app_manager = AppManager()
    return _app_manager
