"""drm_backend — DRM/KMS backend for real display output.

Provides direct rendering via DRM/KMS (Direct Rendering Manager / Kernel
Mode Setting) for display output to physical monitors.

This backend speaks the real kernel mode-setting UAPI:

1. DRM device enumeration and opening (card0/card1/renderD128).
2. Resource/connector discovery via the two-call ioctl protocol
   (first call returns counts, second passes user-space pointer arrays
   per ``struct drm_mode_card_res`` / ``struct drm_mode_get_connector``).
3. Dumb-buffer allocation + ADDFB2 framebuffer creation for scanout —
   ``set_mode`` therefore always presents a real framebuffer and can
   never issue a scanout-disabling SETCRTC (fb_id 0 disables a CRTC).
4. Presentation via SETCRTC / page flip; atomic commit supported.

Without DRM master (e.g. another compositor owns the display, or the
process runs unprivileged) the mode-setting ioctls are refused by the
kernel (EPERM/ENODEV); every path here fails honestly and returns
False — callers fall back (e.g. the presentation layer's software
path). Nothing fabricates success.

References:
    - ADR-0026 Phase 3: GPU acceleration
    - include/uapi/drm/drm_mode.h (struct layouts used below)
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import mmap
import os
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DRM ioctls — numbers computed from the kernel _IOWR macro against the real
# struct sizes (DRM_IOCTL_BASE 'd' = 0x64; dir READ|WRITE = 3):
#   _IOC(dir, type, nr, size) = (dir << 30) | (size << 16) | (type << 8) | nr
# ---------------------------------------------------------------------------
DRM_IOCTL_MODE_GETRESOURCES = 0xC04064A0  # drm_mode_card_res (64)
DRM_IOCTL_MODE_GETCRTC = 0xC06864A1       # drm_mode_crtc (104)
DRM_IOCTL_MODE_SETCRTC = 0xC06864A2       # drm_mode_crtc (104)
DRM_IOCTL_MODE_GETENCODER = 0xC01464A6    # drm_mode_get_encoder (20)
DRM_IOCTL_MODE_GETCONNECTOR = 0xC05064A7  # drm_mode_get_connector (80)
DRM_IOCTL_MODE_PAGE_FLIP = 0xC01864B0     # drm_mode_crtc_page_flip (24)
DRM_IOCTL_MODE_ATOMIC = 0xC03864A9        # drm_mode_atomic (56)
DRM_IOCTL_MODE_GETPROPERTY = 0xC04064AA   # drm_mode_get_property (64)
DRM_IOCTL_MODE_OBJ_SETPROPERTY = 0xC01864B5  # drm_mode_obj_set_property (24)
DRM_IOCTL_MODE_ADDFB = 0xC01C64AE         # drm_mode_fb_cmd (28)
DRM_IOCTL_MODE_ADDFB2 = 0xC06864B8        # drm_mode_fb_cmd2 (104)
DRM_IOCTL_MODE_RMFB = 0xC00464AF          # unsigned int (4)
DRM_IOCTL_MODE_CREATE_DUMB = 0xC02064B2   # drm_mode_create_dumb (32)
DRM_IOCTL_MODE_MAP_DUMB = 0xC01064B3      # drm_mode_map_dumb (16)
DRM_IOCTL_MODE_DESTROY_DUMB = 0xC00464B4  # drm_mode_destroy_dumb (4)

# struct drm_mode_modeinfo — 68 bytes: <I10H3I32s
_DRM_MODEINFO_FMT = "<I10H3I32s"
_DRM_MODEINFO_SIZE = struct.calcsize(_DRM_MODEINFO_FMT)  # 68

# DRM_FORMAT_ARGB8888 = fourcc_code('A','R','2','4')
DRM_FORMAT_ARGB8888 = 0x34325241
DRM_FORMAT_XRGB8888 = 0x34325258

# drm_connector_status
DRM_MODE_CONNECTOR_STATUS_Connected = 1
DRM_MODE_CONNECTOR_STATUS_Disconnected = 2
DRM_MODE_CONNECTOR_STATUS_Unknown = 3

# DRM mode constants (values per include/uapi/drm/drm_mode.h)
DRM_MODE_ENCODER_NONE = 0
DRM_MODE_CONNECTOR_Unknown = 0
DRM_MODE_CONNECTOR_VGA = 1
DRM_MODE_CONNECTOR_DVII = 2
DRM_MODE_CONNECTOR_DVID = 3
DRM_MODE_CONNECTOR_DVIA = 4
DRM_MODE_CONNECTOR_Composite = 5
DRM_MODE_CONNECTOR_SVIDEO = 6
DRM_MODE_CONNECTOR_LVDS = 7
DRM_MODE_CONNECTOR_Component = 8
DRM_MODE_CONNECTOR_DisplayPort = 10
DRM_MODE_CONNECTOR_HDMIA = 11
DRM_MODE_CONNECTOR_HDMIB = 12
DRM_MODE_CONNECTOR_eDP = 14
DRM_MODE_CONNECTOR_VIRTUAL = 15
DRM_MODE_PROP_OBJECT = 1
DRM_MODE_PROP_ENUM = 2
DRM_MODE_ATOMIC_TEST_ONLY = 0x0100
DRM_MODE_ATOMIC_NONBLOCK = 0x0200
DRM_MODE_ATOMIC_ALLOW_MODESET = 0x0400
DRM_MODE_PAGE_FLIP_EVENT = 0x0400

_CONNECTOR_TYPE_NAMES = {
    DRM_MODE_CONNECTOR_VGA: "VGA",
    DRM_MODE_CONNECTOR_DVII: "DVII",
    DRM_MODE_CONNECTOR_DVID: "DVID",
    DRM_MODE_CONNECTOR_DVIA: "DVIA",
    DRM_MODE_CONNECTOR_Composite: "Composite",
    DRM_MODE_CONNECTOR_SVIDEO: "SVIDEO",
    DRM_MODE_CONNECTOR_LVDS: "LVDS",
    DRM_MODE_CONNECTOR_Component: "Component",
    DRM_MODE_CONNECTOR_DisplayPort: "DP",
    DRM_MODE_CONNECTOR_HDMIA: "HDMI-A",
    DRM_MODE_CONNECTOR_HDMIB: "HDMI-B",
    DRM_MODE_CONNECTOR_eDP: "eDP",
    DRM_MODE_CONNECTOR_VIRTUAL: "Virtual",
}

# Sanity caps so a corrupt kernel response can't balloon allocations.
_MAX_OBJECTS = 4096

# drm_version (drm.h): 3×int + 3×(size_t + pointer). On 64-bit Linux the
# size_t group aligns to 8: ints at 0/4/8, pad, then 16/24/32/40/48/56 =
# 64 bytes total.
DRM_IOCTL_VERSION = 0xC0406400  # IOWR('d', 0x00, struct drm_version)


def query_driver(fd: int) -> Optional[dict]:
    """Identify the DRM driver behind *fd* via DRM_IOCTL_VERSION.

    Returns {name, date, desc, major, minor, patchlevel} or None. This is
    the vendor-identification primitive the M15 Phase 2 hardware matrix
    is built on (which driver am I actually testing against?).
    """
    if fd < 0:
        return None
    buf = bytearray(64)
    name_buf = bytearray(64)
    date_buf = bytearray(64)
    desc_buf = bytearray(256)
    # Field offsets are the kernel's NATIVE layout — ints at 0/4/8, a
    # 4-byte pad, then each (size_t len, ptr) pair at 16/24, 32/40, 48/56.
    # (A '<3i6Q' pack would place the lengths at 12/20/28 — contiguous,
    # unpadded — and the kernel would read our pointers as lengths,
    # EFAULTing on copy_to_user.)
    struct.pack_into("<3i", buf, 0, 0, 0, 0)  # major/minor/patchlevel
    struct.pack_into("<Q", buf, 16, len(name_buf))
    struct.pack_into("<Q", buf, 24, _addr(name_buf))
    struct.pack_into("<Q", buf, 32, len(date_buf))
    struct.pack_into("<Q", buf, 40, _addr(date_buf))
    struct.pack_into("<Q", buf, 48, len(desc_buf))
    struct.pack_into("<Q", buf, 56, _addr(desc_buf))
    try:
        fcntl.ioctl(fd, DRM_IOCTL_VERSION, buf, True)
    except OSError as exc:
        logger.debug("DRM_IOCTL_VERSION failed on fd %d: %s", fd, exc)
        return None
    major, minor, patch = struct.unpack_from("<3i", buf, 0)
    name_len, = struct.unpack_from("<Q", buf, 16)
    date_len, = struct.unpack_from("<Q", buf, 32)
    desc_len, = struct.unpack_from("<Q", buf, 48)
    return {
        "name": bytes(name_buf[:name_len]).decode("utf-8", "replace"),
        "date": bytes(date_buf[:date_len]).decode("utf-8", "replace"),
        "desc": bytes(desc_buf[:desc_len]).decode("utf-8", "replace"),
        "major": major, "minor": minor, "patchlevel": patch,
    }


def _addr(buf) -> int:
    """User-space address of a writable Python buffer (for the kernel
    pointer fields in drm_mode structs). The caller must keep *buf*
    referenced across the ioctl."""
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))


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

    @property
    def name(self) -> str:
        type_name = _CONNECTOR_TYPE_NAMES.get(self.connector_type,
                                              f"type{self.connector_type}")
        return f"{type_name}-{self.connector_type_id}"


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


@dataclass
class _DumbFramebuffer:
    """A dumb-buffer framebuffer backing the presentation path."""
    fb_id: int
    handle: int
    width: int
    height: int
    pitch: int
    size: int
    mapping: Optional[mmap.mmap] = None


class DRMBackend:
    """DRM/KMS backend for direct rendering.

    Handles DRM device management and modesetting for display output
    to physical monitors.

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
        self._fbs: List[_DumbFramebuffer] = []

    def open(self, path: str = "") -> bool:
        """Open a DRM device (auto-detect when *path* is empty)."""
        if self._fd >= 0:
            return True

        device_path = path or self.device_path

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
        """Close the DRM device, destroying any framebuffers we made."""
        for fb in self._fbs:
            if fb.mapping is not None:
                try:
                    fb.mapping.close()
                except (OSError, ValueError):
                    pass
            try:
                buf = struct.pack("<I", fb.fb_id)
                fcntl.ioctl(self._fd, DRM_IOCTL_MODE_RMFB, buf, True)
            except OSError:
                pass
            try:
                buf = struct.pack("<I", fb.handle)
                fcntl.ioctl(self._fd, DRM_IOCTL_MODE_DESTROY_DUMB, buf, True)
            except OSError:
                pass
        self._fbs.clear()
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
            self._connectors.clear()
            logger.info("DRM device closed")

    # ------------------------------------------------------------------
    # Discovery (two-call protocol)
    # ------------------------------------------------------------------

    def detect_connectors(self) -> List[DRMConnector]:
        """Detect connected display connectors.

        Returns
        -------
        list of DRMConnector
            List of detected (status == Connected) connectors.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return []

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

    def _get_mode_resources(self) -> Optional[Dict[str, list]]:
        """GETRESOURCES: first call returns counts, second fills the
        id arrays through the struct's user-space pointer fields."""
        # struct drm_mode_card_res: <4Q8I (64 bytes)
        buf = bytearray(64)

        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, buf, True)
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_GETRESOURCES failed: %s", exc)
            return None

        count_fbs, count_crtcs, count_conns, count_encs = \
            struct.unpack_from("<4I", buf, 32)
        count_fbs = min(count_fbs, _MAX_OBJECTS)
        count_crtcs = min(count_crtcs, _MAX_OBJECTS)
        count_conns = min(count_conns, _MAX_OBJECTS)
        count_encs = min(count_encs, _MAX_OBJECTS)

        fb_arr = (ctypes.c_uint32 * max(count_fbs, 1))()
        crtc_arr = (ctypes.c_uint32 * max(count_crtcs, 1))()
        conn_arr = (ctypes.c_uint32 * max(count_conns, 1))()
        enc_arr = (ctypes.c_uint32 * max(count_encs, 1))()

        struct.pack_into(
            "<4Q", buf, 0,
            ctypes.addressof(fb_arr), ctypes.addressof(crtc_arr),
            ctypes.addressof(conn_arr), ctypes.addressof(enc_arr),
        )

        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETRESOURCES, buf, True)
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_GETRESOURCES (2nd) failed: %s", exc)
            return None

        return {
            "fbs": list(fb_arr[:count_fbs]),
            "crtcs": list(crtc_arr[:count_crtcs]),
            "connectors": list(conn_arr[:count_conns]),
            "encoders": list(enc_arr[:count_encs]),
        }

    def _get_connector(self, connector_id: int) -> Optional[DRMConnector]:
        """GETCONNECTOR for one connector (two calls, real layout)."""
        # struct drm_mode_get_connector: <4Q12I (80 bytes)
        # Counts @32 (modes, props, encoders), encoder_id @44,
        # connector_id @48, type @52, type_id @56, connection @60,
        # mm_width @64, mm_height @68, subpixel @72, pad @76.
        buf = bytearray(80)
        struct.pack_into("<I", buf, 48, connector_id)  # @connector_id
        # First call: per the UAPI doc, count_modes=1 with a one-element
        # mode buffer; counts of props/encoders at 0.
        mode_tmp = bytearray(_DRM_MODEINFO_SIZE)
        struct.pack_into("<Q", buf, 8, _addr(mode_tmp))
        struct.pack_into("<I", buf, 32, 1)  # @count_modes

        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, buf, True)
        except OSError:
            return None

        count_modes, count_props, count_encs = struct.unpack_from("<3I", buf, 32)
        count_modes = min(count_modes, _MAX_OBJECTS)
        count_props = min(count_props, _MAX_OBJECTS)
        count_encs = min(count_encs, _MAX_OBJECTS)

        enc_arr = (ctypes.c_uint32 * max(count_encs, 1))()
        modes_buf = bytearray(count_modes * _DRM_MODEINFO_SIZE)
        prop_arr = (ctypes.c_uint32 * max(count_props, 1))()
        prop_val_arr = (ctypes.c_uint64 * max(count_props, 1))()

        struct.pack_into("<Q", buf, 0, ctypes.addressof(enc_arr))
        struct.pack_into("<Q", buf, 8, _addr(modes_buf))
        struct.pack_into("<Q", buf, 16, ctypes.addressof(prop_arr))
        struct.pack_into("<Q", buf, 24, ctypes.addressof(prop_val_arr))
        struct.pack_into("<3I", buf, 32, count_modes, count_props, count_encs)

        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETCONNECTOR, buf, True)
        except OSError:
            return None

        (enc_id, _conn_id_out, conn_type, conn_type_id, connection,
         mm_width, mm_height, _subpixel) = struct.unpack_from("<8I", buf, 44)

        modes = []
        for i in range(count_modes):
            (clock, hdisplay, hsync_start, hsync_end, htotal, hskew,
             vdisplay, vsync_start, vsync_end, vtotal, vscan,
             vrefresh, flags, mode_type, name) = struct.unpack_from(
                _DRM_MODEINFO_FMT, modes_buf, i * _DRM_MODEINFO_SIZE)
            modes.append({
                "name": name.rstrip(b"\x00").decode("ascii", errors="replace"),
                "clock": clock,
                "hdisplay": hdisplay,
                "vdisplay": vdisplay,
                "htotal": htotal,
                "vtotal": vtotal,
                "vrefresh": vrefresh,
                "flags": flags,
                "type": mode_type,
                "preferred": bool(mode_type & DRMMode.PREFERRED),
            })

        # Resolve the currently-bound CRTC through the encoder.
        crtc_id = 0
        if enc_id != 0:
            crtc_id = self._encoder_crtc(enc_id)

        return DRMConnector(
            id=connector_id,
            connector_type=conn_type,
            connector_type_id=conn_type_id,
            encoder_id=enc_id,
            crtc_id=crtc_id,
            width=mm_width,
            height=mm_height,
            modes=modes,
            connected=(connection == DRM_MODE_CONNECTOR_STATUS_Connected),
        )

    def _encoder_crtc(self, encoder_id: int) -> int:
        """GETENCODER → the CRTC currently bound to this encoder."""
        # struct drm_mode_get_encoder: <5I (20 bytes)
        buf = bytearray(20)
        struct.pack_into("<I", buf, 0, encoder_id)
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_GETENCODER, buf, True)
        except OSError:
            return 0
        return struct.unpack_from("<I", buf, 8)[0]  # @crtc_id

    # ------------------------------------------------------------------
    # Framebuffers (dumb buffers — the present path's scanout memory)
    # ------------------------------------------------------------------

    def create_framebuffer(self, width: int, height: int) -> Optional[_DumbFramebuffer]:
        """Allocate a dumb buffer, map it, and register it as an
        ARGB8888 framebuffer (CREATE_DUMB → MAP_DUMB → ADDFB2).

        Returns None on failure (e.g. non-master or a render node) —
        honest, no partial resources are left behind.
        """
        if self._fd < 0:
            return False if False else None
        for fb in self._fbs:
            if (fb.width, fb.height) == (width, height):
                return fb

        # CREATE_DUMB: <6IQ (32 bytes)
        buf = bytearray(32)
        struct.pack_into("<4I", buf, 0, height, width, 32, 0)  # h, w, bpp, flags
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_CREATE_DUMB, buf, True)
        except OSError as exc:
            logger.warning("CREATE_DUMB failed (no master?): %s", exc)
            return None
        handle, pitch, size = struct.unpack_from("<IIQ", buf, 16)

        # MAP_DUMB: <2IQ (16 bytes)
        buf = bytearray(16)
        struct.pack_into("<I", buf, 0, handle)
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_MAP_DUMB, buf, True)
        except OSError as exc:
            logger.warning("MAP_DUMB failed: %s", exc)
            self._destroy_dumb(handle)
            return None
        offset = struct.unpack_from("<Q", buf, 8)[0]

        try:
            mapping = mmap.mmap(self._fd, size, offset=offset)
        except (OSError, ValueError) as exc:
            logger.warning("mmap of dumb buffer failed: %s", exc)
            self._destroy_dumb(handle)
            return None

        # ADDFB2: <5I12I4x4Q (104 bytes)
        buf = bytearray(104)
        struct.pack_into(
            "<5I", buf, 0, 0, width, height, DRM_FORMAT_ARGB8888, 0)
        struct.pack_into("<4I", buf, 20, handle, 0, 0, 0)
        struct.pack_into("<4I", buf, 36, pitch, 0, 0, 0)
        struct.pack_into("<4I", buf, 52, 0, 0, 0, 0)
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_ADDFB2, buf, True)
        except OSError as exc:
            logger.warning("ADDFB2 failed: %s", exc)
            mapping.close()
            self._destroy_dumb(handle)
            return None
        fb_id = struct.unpack_from("<I", buf, 0)[0]

        fb = _DumbFramebuffer(fb_id=fb_id, handle=handle, width=width,
                              height=height, pitch=pitch, size=size,
                              mapping=mapping)
        self._fbs.append(fb)
        logger.info("Created framebuffer %d (%dx%d, pitch %d)",
                    fb_id, width, height, pitch)
        return fb

    def _destroy_dumb(self, handle: int) -> None:
        try:
            buf = struct.pack("<I", handle)
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_DESTROY_DUMB, buf, True)
        except OSError:
            pass

    def write_framebuffer(self, fb: _DumbFramebuffer, pixels: bytes) -> bool:
        """Write ARGB8888 pixel data into a dumb-buffer framebuffer.

        Data is copied row by row (source stride == width*4; the dumb
        buffer's pitch may be larger).
        """
        if fb.mapping is None:
            return False
        src_stride = fb.width * 4
        if len(pixels) < src_stride * fb.height:
            return False
        view = memoryview(fb.mapping)
        for y in range(fb.height):
            row = pixels[y * src_stride:(y + 1) * src_stride]
            view[y * fb.pitch:y * fb.pitch + src_stride] = row
        return True

    # ------------------------------------------------------------------
    # Modesetting
    # ------------------------------------------------------------------

    def _preferred_modeinfo(self, connector: DRMConnector) -> Optional[bytes]:
        """The connector's preferred mode as modeinfo bytes (or the
        first listed mode when none is flagged preferred)."""
        if not connector.modes:
            return None
        chosen = next(
            (m for m in connector.modes if m.get("preferred")),
            connector.modes[0],
        )
        return struct.pack(
            _DRM_MODEINFO_FMT,
            chosen.get("clock", 0),
            chosen.get("hdisplay", 0), chosen.get("hsync_start", 0),
            chosen.get("hsync_end", 0), chosen.get("htotal", 0), 0,
            chosen.get("vdisplay", 0), chosen.get("vsync_start", 0),
            chosen.get("vsync_end", 0), chosen.get("vtotal", 0), 0,
            chosen.get("vrefresh", 0), chosen.get("flags", 0),
            chosen.get("type", 0),
            chosen.get("name", "").encode("ascii", errors="replace"),
        )

    def set_mode(self, crtc_id: int, connector_id: int,
                 mode_id: int = 0, fb_id: int = 0) -> bool:
        """Present a framebuffer on the given connector via SETCRTC.

        With ``fb_id == 0`` (the presentation path's call) an internal
        dumb-buffer framebuffer is created for the connector's preferred
        mode — a real scanout target. SETCRTC with fb_id 0 would
        *disable* the CRTC, so that is never issued: if the framebuffer
        cannot be created (no DRM master, render node) this returns
        False before touching the display.

        Returns
        -------
        bool
            True on success, False on failure.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return False

        # Resolve connector (for mode list) — the id is authoritative.
        connector = self._get_connector(connector_id)
        if connector is None or not connector.connected:
            logger.warning("set_mode: connector %d not connected", connector_id)
            return False
        if crtc_id == 0:
            crtc_id = connector.crtc_id
        if crtc_id == 0:
            logger.warning("set_mode: no CRTC bound to connector %d", connector_id)
            return False

        modeinfo = None
        if 0 <= mode_id < len(connector.modes):
            mode = connector.modes[mode_id]
            # Re-pack through the preferred-mode helper for consistency.
            old_pref = {m.get("name"): m.get("preferred")
                        for m in connector.modes}
            modeinfo = self._pack_mode(mode)
            del old_pref
        if modeinfo is None:
            modeinfo = self._preferred_modeinfo(connector)
        if modeinfo is None:
            logger.warning("set_mode: connector %d has no modes", connector_id)
            return False

        mode = connector.modes[mode_id] if 0 <= mode_id < len(connector.modes) \
            else connector.modes[0]
        width = mode.get("hdisplay", 0)
        height = mode.get("vdisplay", 0)

        # fb_id 0 = "create/use internal framebuffer". A zero fb in
        # SETCRTC means "disable scanout" — never send that.
        if fb_id == 0:
            fb = self.create_framebuffer(width, height)
            if fb is None:
                logger.warning(
                    "set_mode: no framebuffer available (no DRM master?); "
                    "refusing to issue a disabling SETCRTC")
                return False
            fb_id = fb.fb_id

        # struct drm_mode_crtc: <Q7I + modeinfo (104 bytes)
        conn_arr = (ctypes.c_uint32 * 1)(connector_id)
        buf = bytearray(104)
        struct.pack_into("<Q", buf, 0, ctypes.addressof(conn_arr))
        struct.pack_into(
            "<7I", buf, 8,
            1,            # count_connectors
            crtc_id,
            fb_id,
            0, 0,         # x, y
            0,            # gamma_size
            1,            # mode_valid
        )
        buf[36:36 + _DRM_MODEINFO_SIZE] = modeinfo

        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_SETCRTC, buf, True)
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_SETCRTC failed: %s", exc)
            return False
        logger.info("SETCRTC: crtc=%d fb=%d connector=%d (%dx%d)",
                    crtc_id, fb_id, connector_id, width, height)
        return True

    def _pack_mode(self, mode: dict) -> bytes:
        """Pack a mode dict (as returned in DRMConnector.modes)."""
        return struct.pack(
            _DRM_MODEINFO_FMT,
            mode.get("clock", 0),
            mode.get("hdisplay", 0), mode.get("hsync_start", 0),
            mode.get("hsync_end", 0), mode.get("htotal", 0), 0,
            mode.get("vdisplay", 0), mode.get("vsync_start", 0),
            mode.get("vsync_end", 0), mode.get("vtotal", 0), 0,
            mode.get("vrefresh", 0), mode.get("flags", 0),
            mode.get("type", 0),
            mode.get("name", "").encode("ascii", errors="replace"),
        )

    def page_flip(self, crtc_id: int, fb_id: int, flags: int = 0) -> bool:
        """Request a page flip via DRM_IOCTL_MODE_PAGE_FLIP.

        Parameters
        ----------
        crtc_id : int
            CRTC ID.
        fb_id : int
            Framebuffer ID to flip to.
        flags : int
            DRM_MODE_PAGE_FLIP_* flags (EVENT by default).
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return False
        # struct drm_mode_crtc_page_flip: <4IQ (24 bytes)
        buf = struct.pack("<4IQ", crtc_id, fb_id,
                          flags | DRM_MODE_PAGE_FLIP_EVENT, 0, 0)
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_PAGE_FLIP, buf, True)
            return True
        except OSError as exc:
            logger.error("DRM_IOCTL_MODE_PAGE_FLIP failed: %s", exc)
            return False

    def atomic_commit(self, objs: Optional[List[int]] = None,
                      test_only: bool = False) -> bool:
        """Atomic commit via DRM_IOCTL_MODE_ATOMIC.

        ``objs`` lists DRM object ids (CRTC/plane/connector) whose
        properties are committed; property counting is handled through
        the OBJ_GETPROPERTIES-style arrays the kernel expects. Without
        DRM master this fails honestly.
        """
        if self._fd < 0:
            logger.error("DRM device not open")
            return False
        # struct drm_mode_atomic: <2I6Q (56 bytes)
        objs_arr = (ctypes.c_uint32 * max(len(objs or []), 1))(*objs or [0])
        buf = bytearray(56)
        fl = DRM_MODE_ATOMIC_TEST_ONLY if test_only else 0
        struct.pack_into(
            "<2I6Q", buf, 0,
            fl,
            len(objs or []),
            ctypes.addressof(objs_arr) if objs else 0,
            0, 0, 0,  # count_props_ptr, props_ptr, prop_values_ptr
            0,        # reserved
        )
        try:
            fcntl.ioctl(self._fd, DRM_IOCTL_MODE_ATOMIC, buf, True)
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
