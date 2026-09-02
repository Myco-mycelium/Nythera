"""drm_backend — DRM/KMS backend for real display output.

Provides direct rendering via DRM/KMS (Direct Rendering Manager / Kernel
Mode Setting) for display output to physical monitors.

This backend handles:
1. DRM device enumeration and opening
2. Connector and CRTC detection
3. Atomic modesetting for display configuration
4. Frame presentation via DRM page flip

References:
    - ADR-0026 Phase 3: GPU acceleration
    - DRM/KMS API: https://docs.kernel.org/gpu/drm-internals.html
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

logger = logging.getLogger(__name__)


# DRM ioctl constants
DRM_IOCTL_MODE_GETRESOURCES = 0xc04064a0
DRM_IOCTL_MODE_GETCONNECTOR = 0xc45864a7
DRM_IOCTL_MODE_GETCRTC = 0xc44864a1
DRM_IOCTL_MODE_SETCRTC = 0xc44864a2
DRM_IOCTL_MODE_GET_ENCODER = 0xc41864a4
DRM_IOCTL_MODE_ATOMIC = 0xc03064a9
DRM_IOCTL_MODE_GETPROPERTY = 0xc43864aa
DRM_IOCTL_MODE_OBJ_SETPROPERTY = 0xc01864b3

# DRM mode constants
DRM_MODE_ENCODER_NONE = 0
DRM_MODE_CONNECTOR_Unknown = 0
DRM_MODE_CONNECTOR_VGA = 2
DRM_MODE_CONNECTOR_DVID = 4
DRM_MODE_CONNECTOR_DisplayPort = 10
DRM_MODE_CONNECTOR_HDMIA = 11
DRM_MODE_PROP_OBJECT = 1
DRM_MODE_PROP_ENUM = 2
DRM_MODE_ATOMIC_TEST_ONLY = 0x0100
DRM_MODE_ATOMIC_NONBLOCK = 0x0200
DRM_MODE_PAGE_FLIP_EVENT = 0x0400


class DRMMode(IntEnum):
    """DRM mode type constants."""
    PREFERRED = 1 << 3
    BUILTIN = 1 << 4
    CLOCK_C = 1 << 5


@dataclass
class DRMConnector:
    """A DRM connector."""
    id: int
    connector_type: int
    connector_type_id: int
    encoder_id: int
    crtc_id: int
    width: int
    height: int
    modes: List[dict]
    connected: bool


@dataclass
class DRMModeInfo:
    """DRM mode information."""
    mode_id: int
    name: str
    clock: int
    hdisplay: int
    hsync_start: int
    hsync_end: int
    htotal: int
    vdisplay: int
    vsync_start: int
    vsync_end: int
    vtotal: int
    vrefresh: int
    preferred: bool


class DRMBackend:
    """DRM/KMS backend for direct rendering.
    
    Handles DRM device management and atomic modesetting for
    display output to physical monitors.
    
    Usage:
        backend = DRMBackend()
        backend.open()
        connectors = backend.detect_connectors()
        backend.close()
    """
    
    def __init__(self, device_path: str = ""):
        self.device_path = device_path
        self._fd: int = -1
        self._connectors: List[DRMConnector] = []
        self._resources = None
    
    def open(self, path: str = "") -> bool:
        """Open a DRM device.
        
        Parameters
        ----------
        path : str
            Path to DRM device. If empty, auto-detect.
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if self._fd >= 0:
            return True
        
        device_path = path or self.device_path
        
        # Auto-detect device path
        if not device_path:
            for candidate in ["/dev/dri/card0", "/dev/dri/card1",
                              "/dev/dri/renderD128", "/dev/dri/renderD129"]:
                if os.path.exists(candidate):
                    device_path = candidate
                    break
        
        if not device_path or not os.path.exists(device_path):
            logger.error("No DRM device found")
            return False
        
        try:
            self._fd = os.open(device_path, os.O_RDWR | os.O_CLOEXEC)
            self.device_path = device_path
            logger.info("Opened DRM device: %s (fd=%d)", device_path, self._fd)
            return True
        except OSError as exc:
            logger.error("Failed to open DRM device %s: %s", device_path, exc)
            return False
    
    def close(self):
        """Close the DRM device."""
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
            self._connectors.clear()
            logger.info("DRM device closed")
    
    def detect_connectors(self) -> List[DRMConnector]:
        """Detect connected display connectors.
        
        Returns
        -------
        list of DRMConnector
            List of detected connectors.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return []
        
        # Get mode resources
        resources = self._get_mode_resources()
        if resources is None:
            return []
        
        connectors = []
        for conn_id in resources.get("connectors", []):
            connector = self._get_connector(conn_id)
            if connector and connector.connected:
                connectors.append(connector)
        
        self._connectors = connectors
        logger.info("Detected %d connected connectors", len(connectors))
        return connectors
    
    def _get_mode_resources(self) -> Optional[dict]:
        """Get DRM mode resources."""
        # DRM_MODE_GETRESOURCES
        # max.crtcs, max.connectors, max.encoders, count.crtcs, count.connectors, count.encoders
        # Then: crtcs[], connectors[], encoders[]
        
        buf = struct.pack("IIIIII", 0, 0, 0, 0, 0, 0)
        
        try:
            result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, buf)
            max_crtcs, max_connectors, max_encoders, count_crtcs, count_connectors, count_encoders = \
                struct.unpack("IIIIII", result)
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_GETRESOURCES failed: %s", exc)
            return None
        
        if count_crtcs == 0 and count_connectors == 0:
            return {"crtcs": [], "connectors": [], "encoders": []}
        
        # Allocate buffer for the full data
        # Structure: crtc_ids[count_crtcs], connector_ids[count_connectors], encoder_ids[count_encoders]
        buf_size = 4 * (count_crtcs + count_connectors + count_encoders)
        buf = struct.pack("IIIIII", max_crtcs, max_connectors, max_encoders,
                         count_crtcs, count_connectors, count_encoders)
        buf += b"\x00" * buf_size
        
        try:
            result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, buf)
        except OSError:
            return {"crtcs": [], "connectors": [], "encoders": []}
        
        # Parse result
        offset = 24  # Skip the header
        crtc_ids = []
        for i in range(count_crtcs):
            crtc_id = struct.unpack("I", result[offset:offset+4])[0]
            crtc_ids.append(crtc_id)
            offset += 4
        
        connector_ids = []
        for i in range(count_connectors):
            conn_id = struct.unpack("I", result[offset:offset+4])[0]
            connector_ids.append(conn_id)
            offset += 4
        
        encoder_ids = []
        for i in range(count_encoders):
            enc_id = struct.unpack("I", result[offset:offset+4])[0]
            encoder_ids.append(enc_id)
            offset += 4
        
        return {
            "crtcs": crtc_ids,
            "connectors": connector_ids,
            "encoders": encoder_ids,
        }
    
    def _get_connector(self, connector_id: int) -> Optional[DRMConnector]:
        """Get connector information."""
        # DRM_IOCTL_MODE_GETCONNECTOR
        # connector_id, encoder_id, connector_type, connector_type_id,
        # connection, width_mm, height_mm, subpixel, count_modes, count_encoders
        
        buf = struct.pack("IIHHIHHIii", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        # Set connector_id
        buf = struct.pack("I", connector_id) + buf[4:]
        
        try:
            result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, buf)
        except OSError:
            return None
        
        (conn_id, enc_id, conn_type, conn_type_id,
         connection, width_mm, height_mm, subpixel,
         count_modes, count_encoders) = struct.unpack("IIHHIHHIii", result)
        
        connected = (connection != DRM_MODE_CONNECTOR_Unknown)
        
        # Get modes
        modes = []
        if count_modes > 0:
            # Allocate buffer for mode names
            # Each mode is 68 bytes: name[32] + mode info
            mode_buf = struct.pack("IIHHIHHIii", conn_id, enc_id, conn_type, conn_type_id,
                                  connection, width_mm, height_mm, subpixel,
                                  count_modes, count_encoders)
            mode_buf += b"\x00" * (count_modes * 68)
            
            try:
                result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, mode_buf)
                # Parse modes (simplified)
                offset = 40  # Skip header
                for i in range(count_modes):
                    mode_name = result[offset:offset+32].rstrip(b"\x00").decode("utf-8", errors="replace")
                    # Full mode parsing would go here
                    modes.append({"name": mode_name, "id": i})
                    offset += 68
            except OSError:
                pass
        
        return DRMConnector(
            id=conn_id,
            connector_type=conn_type,
            connector_type_id=conn_type_id,
            encoder_id=enc_id,
            crtc_id=0,
            width=width_mm,
            height=height_mm,
            modes=modes,
            connected=connected,
        )
    
    def set_mode(self, crtc_id: int, connector_id: int,
                 mode_id: int, fb_id: int = 0) -> bool:
        """Set the display mode via DRM_IOCTL_MODE_SETCRTC.
        
        Parameters
        ----------
        crtc_id : int
            CRTC ID to configure.
        connector_id : int
            Connector ID to attach.
        mode_id : int
            Mode ID to use.
        fb_id : int
            Framebuffer ID (0 for disabled).
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return False
        
        # DRM_IOCTL_MODE_SETCRTC: crtc_id, fb_id, x, y, *connectors, count_modes, mode
        # Simplified: set crtc with connector
        buf = struct.pack("IIiII", crtc_id, fb_id, 0, 0, connector_id)
        buf += struct.pack("I", 1)  # count_connectors = 1
        buf += b"\x00" * 64  # mode info (simplified)
        
        try:
            result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_SETCRTC, buf)
            return True
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_SETCRTC failed: %s", exc)
            return False
    
    def page_flip(self, crtc_id: int, fb_id: int, flags: int = 0) -> bool:
        """Request a page flip via DRM_IOCTL_MODE_ATOMIC.
        
        Parameters
        ----------
        crtc_id : int
            CRTC ID.
        fb_id : int
            Framebuffer ID to flip to.
        flags : int
            Atomic flags (DRM_MODE_PAGE_FLIP_EVENT, etc.).
            
        Returns
        -------
        bool
            True on success, False on failure.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return False
        
        # DRM_IOCTL_MODE_ATOMIC: flags, count_objs, objs_ptr, count_props, props_ptr, prop_values_ptr, reserved
        # Simplified atomic commit
        buf = struct.pack("II", flags | DRM_MODE_PAGE_FLIP_EVENT, 0)
        buf += struct.pack("Q", 0)  # objs_ptr
        buf += struct.pack("I", 0)  # count_props
        buf += struct.pack("Q", 0)  # props_ptr
        buf += struct.pack("Q", 0)  # prop_values_ptr
        buf += struct.pack("Q", 0)  # reserved
        
        try:
            result = fcntl.ioctl(self._fd, DRM_IOCTL_MODE_ATOMIC, buf)
            return True
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_ATOMIC failed: %s", exc)
            return False
    
    def get_fd(self) -> int:
        """Get the DRM file descriptor."""
        return self._fd
    
    @property
    def is_open(self) -> bool:
        """Check if the DRM device is open."""
        return self._fd >= 0
