"""test_wayland_opcode_header_contract — ADR-0027 Rule 1 enforcement.

Rule 1: protocol constants are wire-verified or header-verified, never
memory-verified. The five wrong opcode tables (0.29.13) were
memory-recalled; they were corrected against the canonical
``wayland-client-protocol.h`` / ``wayland.xml`` and pinned by
byte-exact client-compat tests.

The canonical check is executable wherever the sources are present.
There are TWO canonical files: ``wayland.xml`` (wl_* interfaces,
installed by libwayland-dev) and ``xdg-shell.xml`` (xdg_* interfaces,
from wayland-protocols / libwayland-protocols or the xdg-shell source
tree). Both are optional on this host: when a file is absent, its
class of constants is unchecked HERE and the byte-exact client-compat
suite and the real-client CI job remain the active enforcement layer.

The first canonical run (2026-09-17) disproved two more memory-recalled
constants: ``WL_DISPLAY_DELETE_ID`` was 2 (canonical: event 1 —
requests and events number in separate sequences) and ``wl_shm`` had a
``destroy`` request (canonical: create_pool + release only). Both
fixed; this test keeps them honest.

Parse errors here mean the XML grammar drifted or a constant is wrong:
both fail loudly, never silently pass.
"""

import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from ui.wayland_protocol import WLEvent  # noqa: E402

# Canonical protocol XML: installed by libwayland-dev (the same package
# that provides wayland-client-protocol.h). Common locations.
_WAYLAND_XML_CANDIDATES = [
    os.environ.get("WAYLAND_XML"),
    "/usr/share/wayland/wayland.xml",
    "/usr/share/wayland/stable/wayland.xml",
    "/usr/local/share/wayland/wayland.xml",
]


def _find_wayland_xml():
    for c in _WAYLAND_XML_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return None


def _parse_canonical_opcodes(xml_path):
    """Extract {interface: {request_name: opcode, event_name: opcode}}
    from wayland.xml (opcodes are per-interface, in document order)."""
    canonical = {}
    root = ET.parse(xml_path).getroot()
    for iface in root.iter("interface"):
        tables = {}
        for kind in ("request", "event"):
            for i, elem in enumerate(iface.findall(kind)):
                tables[(kind, elem.get("name"))] = i
        canonical[iface.get("name")] = tables
    return canonical


# WLEvent/WLRequest member names → (interface, kind, wire name).
# Grouped per interface; the prefix mapping is mechanical.
_NAME_MAP = {
    # wl_display
    "WL_DISPLAY_ERROR": ("wl_display", "event", "error"),
    "WL_DISPLAY_DELETE_ID": ("wl_display", "event", "delete_id"),
    # wl_registry
    "WL_REGISTRY_GLOBAL": ("wl_registry", "event", "global"),
    "WL_REGISTRY_GLOBAL_REMOVE": ("wl_registry", "event", "global_remove"),
    "WL_REGISTRY_BIND": ("wl_registry", "request", "bind"),
    # wl_callback
    "WL_CALLBACK_DONE": ("wl_callback", "event", "done"),
    # wl_compositor
    "WL_COMPOSITOR_CREATE_SURFACE": ("wl_compositor", "request", "create_surface"),
    "WL_COMPOSITOR_CREATE_REGION": ("wl_compositor", "request", "create_region"),
    # wl_shm
    "WL_SHM_CREATE_POOL": ("wl_shm", "request", "create_pool"),
    "WL_SHM_RELEASE": ("wl_shm", "request", "release"),
    "WL_SHM_FORMAT": ("wl_shm", "event", "format"),
    # wl_shm_pool
    "WL_SHM_POOL_CREATE_BUFFER": ("wl_shm_pool", "request", "create_buffer"),
    "WL_SHM_POOL_DESTROY": ("wl_shm_pool", "request", "destroy"),
    "WL_SHM_POOL_RESIZE": ("wl_shm_pool", "request", "resize"),
    # wl_buffer
    "WL_BUFFER_RELEASE": ("wl_buffer", "event", "release"),
    # wl_output
    "WL_OUTPUT_GEOMETRY": ("wl_output", "event", "geometry"),
    "WL_OUTPUT_MODE": ("wl_output", "event", "mode"),
    "WL_OUTPUT_DONE": ("wl_output", "event", "done"),
    "WL_OUTPUT_SCALE": ("wl_output", "event", "scale"),
    # wl_seat
    "WL_SEAT_CAPABILITIES": ("wl_seat", "event", "capabilities"),
    "WL_SEAT_NAME": ("wl_seat", "event", "name"),
    # wl_pointer
    "WL_POINTER_ENTER": ("wl_pointer", "event", "enter"),
    "WL_POINTER_LEAVE": ("wl_pointer", "event", "leave"),
    "WL_POINTER_MOTION": ("wl_pointer", "event", "motion"),
    "WL_POINTER_BUTTON": ("wl_pointer", "event", "button"),
    "WL_POINTER_AXIS": ("wl_pointer", "event", "axis"),
    # wl_keyboard
    "WL_KEYBOARD_KEYMAP": ("wl_keyboard", "event", "keymap"),
    "WL_KEYBOARD_ENTER": ("wl_keyboard", "event", "enter"),
    "WL_KEYBOARD_LEAVE": ("wl_keyboard", "event", "leave"),
    "WL_KEYBOARD_KEY": ("wl_keyboard", "event", "key"),
    "WL_KEYBOARD_MODIFIERS": ("wl_keyboard", "event", "modifiers"),
    # wl_surface
    "WL_SURFACE_ENTER": ("wl_surface", "event", "enter"),
    "WL_SURFACE_LEAVE": ("wl_surface", "event", "leave"),
}

# xdg-shell constants come from xdg-shell.xml — a SEPARATE canonical
# file from wayland.xml (looking them up in wayland.xml is exactly the
# false-mismatch that failed the first canonical run). Same parse rule:
# opcodes are per-interface, in document order, per kind.
_XDG_NAME_MAP = {
    # xdg_wm_base
    "XDG_WM_BASE_PING": ("xdg_wm_base", "event", "ping"),
    "XDG_WM_BASE_GET_XDG_SURFACE": ("xdg_wm_base", "request", "get_xdg_surface"),
    "XDG_WM_BASE_PONG": ("xdg_wm_base", "request", "pong"),
    # xdg_surface
    "XDG_SURFACE_GET_TOPLEVEL": ("xdg_surface", "request", "get_toplevel"),
    "XDG_SURFACE_GET_POPUP": ("xdg_surface", "request", "get_popup"),
    "XDG_SURFACE_SET_WINDOW_GEOMETRY": ("xdg_surface", "request", "set_window_geometry"),
    "XDG_SURFACE_ACK_CONFIGURE": ("xdg_surface", "request", "ack_configure"),
    # xdg_toplevel
    "XDG_TOPLEVEL_DESTROY": ("xdg_toplevel", "request", "destroy"),
    "XDG_TOPLEVEL_SET_PARENT": ("xdg_toplevel", "request", "set_parent"),
    "XDG_TOPLEVEL_SET_TITLE": ("xdg_toplevel", "request", "set_title"),
    "XDG_TOPLEVEL_SET_APP_ID": ("xdg_toplevel", "request", "set_app_id"),
    "XDG_TOPLEVEL_SHOW_WINDOW_MENU": ("xdg_toplevel", "request", "show_window_menu"),
    "XDG_TOPLEVEL_MOVE": ("xdg_toplevel", "request", "move"),
    "XDG_TOPLEVEL_RESIZE": ("xdg_toplevel", "request", "resize"),
    "XDG_TOPLEVEL_SET_MAX_SIZE": ("xdg_toplevel", "request", "set_max_size"),
    "XDG_TOPLEVEL_SET_MIN_SIZE": ("xdg_toplevel", "request", "set_min_size"),
    "XDG_TOPLEVEL_SET_MAXIMIZED": ("xdg_toplevel", "request", "set_maximized"),
    "XDG_TOPLEVEL_UNSET_MAXIMIZED": ("xdg_toplevel", "request", "unset_maximized"),
    "XDG_TOPLEVEL_SET_FULLSCREEN": ("xdg_toplevel", "request", "set_fullscreen"),
    "XDG_TOPLEVEL_UNSET_FULLSCREEN": ("xdg_toplevel", "request", "unset_fullscreen"),
    "XDG_TOPLEVEL_SET_MINIMIZED": ("xdg_toplevel", "request", "set_minimized"),
    "XDG_TOPLEVEL_CONFIGURE": ("xdg_toplevel", "event", "configure"),
    "XDG_TOPLEVEL_CLOSE": ("xdg_toplevel", "event", "close"),
    "XDG_TOPLEVEL_CONFIGURE_BOUNDS": ("xdg_toplevel", "event", "configure_bounds"),
}

# Every WLEvent member must live in exactly one canonical map — no
# unverified opcodes (Rule 1).
_ALL_NAME_MAP = {**_NAME_MAP, **_XDG_NAME_MAP}

# Canonical protocol XMLs: wayland.xml is installed by libwayland-dev;
# xdg-shell.xml ships with wayland-protocols (package
# libwayland-protocols on Debian) and inside wayland's source tree.
_XDG_SHELL_XML_CANDIDATES = [
    os.environ.get("XDG_SHELL_XML"),
    "/usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml",
    "/usr/local/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml",
]


def _find_xdg_shell_xml():
    for c in _XDG_SHELL_XML_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return None


class TestOpcodeHeaderContract(unittest.TestCase):
    """Every mapped constant must equal its canonical wire opcode."""

    def test_all_mapped_members_exist_in_the_enums(self):
        # FULL coverage: every WLEvent member must be mapped to its
        # canonical source (a missing entry = an unverified opcode =
        # Rule 1 violation). wl_* members verify against wayland.xml,
        # xdg_* members against xdg-shell.xml.
        for member in WLEvent.__members__:
            self.assertIn(
                member, _ALL_NAME_MAP,
                f"WLEvent.{member} is not header/XML-verified — "
                "add it to _NAME_MAP (wayland.xml) or _XDG_NAME_MAP "
                "(xdg-shell.xml) (ADR-0027 Rule 1: never memory-verified)")

    def test_opcodes_match_canonical_xml(self):
        wl_xml = _find_wayland_xml()
        xdg_xml = _find_xdg_shell_xml()
        if not wl_xml and not xdg_xml:
            self.skipTest(
                "neither wayland.xml (libwayland-dev) nor xdg-shell.xml "
                "(libwayland-protocols) installed — the real-client CI job "
                "enforces this rule there")
        canonical = {}
        if wl_xml:
            canonical.update(_parse_canonical_opcodes(wl_xml))
        if xdg_xml:
            canonical.update(_parse_canonical_opcodes(xdg_xml))
        mismatches = []
        for name, value in WLEvent.__members__.items():
            iface, kind, wire = _ALL_NAME_MAP[name]
            table = canonical.get(iface)
            if table is None or (kind, wire) not in table:
                mismatches.append(
                    f"{name}: {iface}.{kind}.{wire} not found in the "
                    "installed canonical XML(s)")
                continue
            want = table[(kind, wire)]
            if value != want:
                mismatches.append(
                    f"{name}: code has {value}, canonical {iface}."
                    f"{kind}.{wire} is {want}")
        self.assertEqual(mismatches, [], "; ".join(mismatches))

    def test_the_five_infamous_tables(self):
        # Direct pins for the tables the real-client run disproved
        # (ADR-0027 Context) plus the two the first canonical-XML run
        # disproved: delete_id is EVENT 1 (separate per-kind sequences
        # — not the merged-sequence 2), and wl_shm has release=1 (no
        # destroy request exists).
        self.assertEqual(WLEvent.WL_DISPLAY_ERROR, 0)
        self.assertEqual(WLEvent.WL_DISPLAY_DELETE_ID, 1)
        self.assertEqual(WLEvent.WL_SHM_RELEASE, 1)
        self.assertEqual(WLEvent.WL_OUTPUT_DONE, 2)
        self.assertEqual(WLEvent.WL_OUTPUT_SCALE, 3)


if __name__ == "__main__":
    unittest.main()
