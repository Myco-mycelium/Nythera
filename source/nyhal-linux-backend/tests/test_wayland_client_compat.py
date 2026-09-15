"""test_wayland_client_compat — Real-client handshake over the compositor socket.

Drives the full protocol sequence the reference weston clients (and the
toolkit-independent parts of GTK4/Qt6 handshakes) perform against the
compositor's wire event loop, over a real Unix domain socket:

- get_registry → the five advertised globals
- bind wl_shm (v1) → wl_shm.format events (ARGB8888, XRGB8888) — the
  client picks its pixel format from these; without them it cannot
  draw at all
- bind wl_output (v3) → geometry, mode(current|preferred), done —
  toolkits block until done
- bind wl_seat (v7) → capabilities(pointer|keyboard), name; then
  get_pointer/get_keyboard — device objects must join the table
- bind xdg_wm_base → get_xdg_surface → get_toplevel → first commit →
  the initial xdg_toplevel.configure + xdg_surface.configure(serial)
  pair, ordered toplevel-before-surface
- the "boring" requests every client interleaves (damage,
  damage_buffer, frame, set_opaque/input_region, set_buffer_scale) —
  none may be a protocol error
- wl_display.error is queued before a protocol-error disconnect (the
  client learns WHY it was dropped)

Unit-speed: real socket, real cdylib, no headless compositor process.

References:
    - ui/compositor_host.py (the socket host half)
    - rust/compositor/src/event_loop.rs (ABI 0x0000_0400)
    - tests/test_compositor_host.py (the transport-level tests)
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
import sys
import tempfile
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.compositor_host import CompositorHost  # noqa: E402


# ---------------------------------------------------------------------------
# Wire-format encoders (client side)
# ---------------------------------------------------------------------------

def _enc_request(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return struct.pack("II", object_id, (size << 16) | (opcode & 0xFFFF)) + payload


def _enc_u32(v: int) -> bytes:
    return struct.pack("I", v)


def _enc_string(s: str) -> bytes:
    raw = s.encode("utf-8") + b"\x00"
    out = struct.pack("I", len(raw)) + raw
    while len(out) % 4:
        out += b"\x00"
    return out


def _parse_events(data: bytes):
    """Parse a byte stream into (object_id, opcode, payload) events."""
    events = []
    offset = 0
    while offset + 8 <= len(data):
        object_id, size_opcode = struct.unpack_from("II", data, offset)
        size = size_opcode >> 16
        opcode = size_opcode & 0xFFFF
        if size < 8 or offset + size > len(data):
            break
        events.append((object_id, opcode, data[offset + 8 : offset + size]))
        offset += size
    return events


NUM_GLOBALS = 5


class _ClientSession:
    """One fake client: socket + object-id allocation + send/recv."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.next_id = 2  # wl_display is 1
        self.registry = None

    def new_id(self) -> int:
        oid = self.next_id
        self.next_id += 1
        return oid

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def handshake_registry(self) -> int:
        self.registry = self.new_id()
        self.send(_enc_request(1, 1, _enc_u32(self.registry)))
        return self.registry

    def bind(self, name: int, interface: str, version: int) -> int:
        oid = self.new_id()
        payload = _enc_u32(name) + _enc_string(interface) + _enc_u32(version) + _enc_u32(oid)
        self.send(_enc_request(self.registry, 0, payload))
        return oid

    def recv_until(self, min_events: int, timeout: float = 3.0):
        """Receive until ``min_events`` NEW events have arrived (the
        globals consumed earlier do not count — each call only sees
        bytes that arrive after it starts)."""
        received = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(_parse_events(received)) < min_events:
            try:
                self.sock.settimeout(0.25)
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            received += chunk
        return _parse_events(received)


class TestWaylandClientCompat(unittest.TestCase):
    """The real-client handshake sequence, end to end over a socket."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="nyrqis-clicompat-")
        cls.socket_path = os.path.join(cls.tmpdir, "wayland-compat")
        cls.host = CompositorHost()
        from ui.wayland_socket import WaylandSocketServer

        cls.server = WaylandSocketServer(cls.socket_path)
        cls.server.set_data_callback(cls._on_data)
        cls.server.set_disconnect_callback(cls.host.on_client_disconnected)
        if cls.host.engine != "rust":
            raise unittest.SkipTest("compositor crate not available (stub engine)")
        from ui import compositor_codec as comp

        assert comp.add_output(1280, 720, "compat-test") >= 0
        assert comp.start() == 0
        assert cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        from ui import compositor_codec as comp

        comp.stop()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    @staticmethod
    def _on_data(client_id: int, data: bytes) -> None:
        TestWaylandClientCompat.host.on_client_data(client_id, data)
        TestWaylandClientCompat.host.flush_pending(
            TestWaylandClientCompat.server.send_to_client
        )

    def _connect(self) -> _ClientSession:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        time.sleep(0.1)  # let the server register the client
        return _ClientSession(sock)

    # -- the shared handshake every client performs ------------------------

    def _globals(self, sess: _ClientSession):
        sess.handshake_registry()
        events = sess.recv_until(NUM_GLOBALS)
        self.assertEqual(len(events), NUM_GLOBALS)
        names = {}
        for i, (obj, op, payload) in enumerate(events):
            self.assertEqual((obj, op), (sess.registry, 0))
            name = struct.unpack_from("I", payload, 0)[0]
            # interface string: u32 length + bytes
            slen = struct.unpack_from("I", payload, 4)[0]
            iface = payload[8 : 8 + slen].rstrip(b"\x00").decode()
            version = struct.unpack_from("I", payload, 8 + slen)[0]
            self.assertEqual(name, i + 1)
            names[iface] = (name, version)
        return names

    def test_registry_advertises_the_client_set(self):
        sess = self._connect()
        try:
            names = self._globals(sess)
            self.assertEqual(
                set(names),
                {"wl_compositor", "wl_shm", "wl_output", "wl_seat", "xdg_wm_base"},
            )
        finally:
            sess.sock.close()

    def test_shm_bind_delivers_formats(self):
        """weston-simple-shm picks its pixel format from wl_shm.format."""
        sess = self._connect()
        try:
            self._globals(sess)
            shm = sess.bind(2, "wl_shm", 1)
            events = sess.recv_until(2)
            self.assertEqual(len(events), 2)
            formats = []
            for obj, op, payload in events:
                self.assertEqual((obj, op), (shm, 0))  # wl_shm.format
                formats.append(struct.unpack("I", payload)[0])
            self.assertEqual(formats, [0, 1])  # ARGB8888, XRGB8888
        finally:
            sess.sock.close()

    def test_output_bind_delivers_geometry_mode_done(self):
        sess = self._connect()
        try:
            self._globals(sess)
            output = sess.bind(3, "wl_output", 3)
            events = sess.recv_until(4)
            self.assertEqual([o for o, _, _ in events], [output] * 4)
            ops = [op for _, op, _ in events]
            # geometry, mode, scale (v2+), done (v2+) — declaration
            # order per the spec: done=2, scale=3, scale precedes done.
            self.assertEqual(ops, [0, 1, 3, 2])
            geom = events[0][2]
            x, y = struct.unpack_from("ii", geom, 0)
            mm_w, mm_h = struct.unpack_from("ii", geom, 8)
            self.assertEqual((x, y), (0, 0))
            self.assertEqual((mm_w, mm_h), (1280 * 254 // 960, 720 * 254 // 960))  # 96 dpi
            mode = events[1][2]
            flags, w, h, refresh = struct.unpack_from("iiii", mode, 0)
            self.assertEqual((flags, w, h, refresh), (1, 1280, 720, 60000))
            scale = struct.unpack("i", events[2][2])[0]
            self.assertEqual(scale, 1)
            self.assertEqual(events[3][2], b"")  # done: no payload
        finally:
            sess.sock.close()

    def test_seat_bind_then_devices(self):
        sess = self._connect()
        try:
            self._globals(sess)
            seat = sess.bind(4, "wl_seat", 7)
            events = sess.recv_until(2)
            self.assertEqual([(o, op) for o, op, _ in events],
                             [(seat, 0), (seat, 1)])  # capabilities, name
            caps = struct.unpack("I", events[0][2])[0]
            self.assertEqual(caps, 3)  # pointer | keyboard
            # get_pointer / get_keyboard: device objects join the table.
            pointer = sess.new_id()
            sess.send(_enc_request(seat, 0, _enc_u32(pointer)))
            keyboard = sess.new_id()
            sess.send(_enc_request(seat, 1, _enc_u32(keyboard)))
            time.sleep(0.2)
            # No protocol error: a follow-up request still dispatches.
            cb = sess.new_id()
            sess.send(_enc_request(1, 0, _enc_u32(cb)))  # wl_display.sync
            events = sess.recv_until(1)
            self.assertEqual([(o, op) for o, op, _ in events], [(cb, 0)])
        finally:
            sess.sock.close()

    def test_xdg_toplevel_full_flow(self):
        """bind xdg_wm_base → get_xdg_surface → get_toplevel → commit
        → initial configure pair, ordered toplevel-before-surface."""
        sess = self._connect()
        try:
            self._globals(sess)
            compositor = sess.bind(1, "wl_compositor", 5)
            wm_base = sess.bind(5, "xdg_wm_base", 5)
            surface = sess.new_id()
            sess.send(_enc_request(compositor, 0, _enc_u32(surface)))
            xdg_surface = sess.new_id()
            # xdg_wm_base.get_xdg_surface is opcode 2 (destroy=0,
            # create_positioner=1, get_xdg_surface=2, pong=3).
            sess.send(_enc_request(wm_base, 2, _enc_u32(xdg_surface) + _enc_u32(surface)))
            toplevel = sess.new_id()
            sess.send(_enc_request(xdg_surface, 1, _enc_u32(toplevel)))
            # The boring requests every client interleaves — none may be
            # a protocol error.
            sess.send(_enc_request(surface, 2, struct.pack("iiii", 0, 0, 10, 10)))  # damage
            sess.send(_enc_request(surface, 9, struct.pack("iiii", 0, 0, 10, 10)))  # damage_buffer
            region = sess.new_id()
            sess.send(_enc_request(compositor, 1, _enc_u32(region)))  # create_region
            sess.send(_enc_request(surface, 5, _enc_u32(region)))  # set_opaque_region
            sess.send(_enc_request(surface, 4, _enc_u32(region)))  # set_input_region
            sess.send(_enc_request(surface, 7, _enc_u32(1)))  # set_buffer_scale
            frame_cb = sess.new_id()
            sess.send(_enc_request(surface, 3, _enc_u32(frame_cb)))  # frame
            # First commit: the initial configure pair must arrive
            # (the armed frame callback also delivers on this commit —
            # frame-done first, then toplevel.configure, then
            # xdg_surface.configure).
            sess.send(_enc_request(surface, 6))  # commit
            events = sess.recv_until(3)
            self.assertEqual([(o, op) for o, op, _ in events],
                             [(frame_cb, 0), (toplevel, 0), (xdg_surface, 0)])
            # toplevel.configure: 0, 0, empty states array.
            w, h = struct.unpack_from("ii", events[1][2], 0)
            arr_len = struct.unpack_from("I", events[1][2], 8)[0]
            self.assertEqual((w, h, arr_len), (0, 0, 0))
            # xdg_surface.configure: a serial the client could echo in
            # ack_configure.
            serial = struct.unpack("I", events[2][2])[0]
            self.assertIsInstance(serial, int)
            # ack_configure + a second commit: nothing further arrives
            # (the initial configure fires exactly once).
            sess.send(_enc_request(xdg_surface, 4, _enc_u32(serial)))
            sess.send(_enc_request(surface, 6))  # commit
            time.sleep(0.3)
            self.assertEqual(sess.recv_until(1), [])
        finally:
            sess.sock.close()

    def test_shm_pool_buffer_attach_commit(self):
        """The weston-simple-shm content path: pool → buffer → attach →
        commit, with a frame callback delivered on the commit."""
        sess = self._connect()
        try:
            self._globals(sess)
            compositor = sess.bind(1, "wl_compositor", 5)
            shm = sess.bind(2, "wl_shm", 1)
            # Drain the two wl_shm.format events the bind queues.
            fmts = sess.recv_until(2)
            self.assertEqual([(o, op) for o, op, _ in fmts], [(shm, 0)] * 2)
            surface = sess.new_id()
            sess.send(_enc_request(compositor, 0, _enc_u32(surface)))
            pool = sess.new_id()
            # create_pool(new_id, fd placeholder, size) — the fd itself
            # travels out-of-band (not exercised here; the host's fd
            # path has its own tests).
            sess.send(_enc_request(shm, 0, _enc_u32(pool) + _enc_u32(0) + _enc_u32(4096)))
            buffer = sess.new_id()
            payload = (
                _enc_u32(buffer)
                + struct.pack("iiiii", 0, 4, 4, 16, 0)  # off, w, h, stride, ARGB8888
            )
            sess.send(_enc_request(pool, 0, payload))  # create_buffer
            sess.send(_enc_request(pool, 1))           # pool.destroy
            sess.send(_enc_request(surface, 1, _enc_u32(buffer)))  # attach
            frame_cb = sess.new_id()
            sess.send(_enc_request(surface, 3, _enc_u32(frame_cb)))  # frame
            sess.send(_enc_request(surface, 6))  # commit
            # frame-done + wl_buffer.release for the attached buffer.
            events = sess.recv_until(2)
            done = [e for e in events if e[0] == frame_cb and e[1] == 0]
            self.assertEqual(len(done), 1)
            stamp = struct.unpack("I", done[0][2])[0]
            self.assertGreaterEqual(stamp, 1)
        finally:
            sess.sock.close()

    def test_protocol_error_delivers_display_error_event(self):
        """On a protocol error the client sees wl_display.error (it
        learns WHY) before the disconnect, not a silent socket close."""
        sess = self._connect()
        try:
            self._globals(sess)
            # Touch an object that does not exist (id 200).
            sess.send(_enc_request(200, 6))  # wl_surface.commit on nothing
            events = sess.recv_until(1)
            self.assertTrue(events, "wl_display.error expected")
            obj, op, payload = events[0]
            self.assertEqual((obj, op), (1, 0))  # wl_display.error
            bad_object = struct.unpack_from("I", payload, 0)[0]
            self.assertEqual(bad_object, 200)
            message = payload[8:].rstrip(b"\x00").decode(errors="replace")
            self.assertIn("unknown object", message)
        finally:
            sess.sock.close()

    def test_bind_over_advertised_version_is_rejected(self):
        sess = self._connect()
        try:
            self._globals(sess)
            # wl_compositor is advertised at 5; bind at 9.
            sess.bind(1, "wl_compositor", 9)
            events = sess.recv_until(1)
            self.assertTrue(events)
            obj, op, _ = events[0]
            self.assertEqual((obj, op), (1, 0))  # wl_display.error
        finally:
            sess.sock.close()


if __name__ == "__main__":
    unittest.main()
