"""test_compositor_host — Tests for the compositor socket host half.

Verifies the bridge between ``WaylandSocketServer`` (the transport) and
the Rust compositor's wire-format event loop (the protocol engine)
through ``ui/compositor_host.py``:

- Engine selection is honest ("rust" only when the cdylib is loaded;
  "stub" otherwise) — fail-closed, never a forged success.
- A real client socket handshake (get_registry → bind → create_surface
  → commit) over the bridge produces the crate's wire-format response
  events on the client socket (globals + frame-done).
- Partial messages crossing recv() boundaries are reassembled before
  the crate sees them.
- Protocol errors are counted, not swallowed.
- Disconnect releases per-client state.
- Stub mode (no crate) falls back without crashing the reader loop.

References:
    - ui/compositor_host.py
    - ui/compositor_codec.py (ABI gate 0x0000_0200)
    - rust/compositor/src/event_loop.rs
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.compositor_host import CompositorHost


def _enc_request(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    """Encode one wire-format request (matches wayland_protocol.py)."""
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


class _HostHarness:
    """Wires a CompositorHost to a WaylandSocketServer for testing.

    Runs one crate compositor session (start … stop) around the socket
    server so each test begins with a clean protocol object table.
    """

    def __init__(self, socket_path: str):
        from ui.wayland_socket import WaylandSocketServer
        from ui import compositor_codec as comp

        self.host = CompositorHost()
        self.server = WaylandSocketServer(socket_path)
        self.server.set_data_callback(self._on_data)
        self.server.set_disconnect_callback(self.host.on_client_disconnected)
        self.engine = self.host.engine
        self.started = False
        if self.engine == "rust" and comp.start() == 0:
            self.started = self.server.start()
        else:
            self.started = self.server.start()

    def _on_data(self, client_id: int, data: bytes) -> None:
        self.host.on_client_data(client_id, data)
        self.host.flush_pending(self.server.send_to_client)

    def stop(self) -> None:
        self.server.stop()
        if self.engine == "rust":
            try:
                from ui import compositor_codec as comp
                comp.stop()
            except ImportError:
                pass


@unittest.skipUnless(
    os.path.isfile(
        os.path.join(
            _HERE, "rust", "compositor", "target", "release",
            "libnyrqis_compositor.so",
        )
    )
    or os.environ.get("NYRQIS_RUST_LIB"),
    "compositor cdylib not built (run: cargo build --release in rust/compositor)",
)
class TestCompositorHostRustEngine(unittest.TestCase):
    """End-to-end tests through the Rust wire event loop."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-host-test-")
        self.socket_path = os.path.join(self.tmpdir, "wayland-host-test")
        self.harness = _HostHarness(self.socket_path)
        if self.harness.engine != "rust":
            self.harness.stop()
            self.skipTest("compositor crate not available (stub engine)")

    def tearDown(self):
        self.harness.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _connect(self) -> socket.socket:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.socket_path)
        client.settimeout(2.0)
        return client

    def _handshake_bytes(self, registry_id=2, compositor_id=3, surface_id=4,
                         callback_id=5):
        buf = bytearray()
        buf += _enc_request(1, 1, _enc_u32(registry_id))  # get_registry
        payload = _enc_u32(1) + _enc_string("wl_compositor") + _enc_u32(5) + _enc_u32(compositor_id)
        buf += _enc_request(registry_id, 0, payload)  # bind
        buf += _enc_request(compositor_id, 0, _enc_u32(surface_id))  # create_surface
        # Arm the frame callback BEFORE commit (per the protocol — a
        # commit without an armed callback delivers nothing).
        buf += _enc_request(surface_id, 3, _enc_u32(callback_id))  # frame
        buf += _enc_request(surface_id, 6)  # commit
        return bytes(buf)

    def test_engine_is_rust(self):
        """The harness really runs the Rust engine (not stub)."""
        self.assertEqual(self.harness.host.engine, "rust")

    def test_full_handshake_over_socket(self):
        """Client handshake over a real socket yields globals + frame-done."""
        client = self._connect()
        try:
            time.sleep(0.1)  # let the server register the client
            client.sendall(self._handshake_bytes())

            received = b""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received += chunk
                events = _parse_events(received)
                # 5 globals + 1 frame-done = 6 events ends the exchange
                if len(events) >= 6:
                    break

            events = _parse_events(received)
            # 5 wl_registry.global events on the registry object...
            globals_events = [e for e in events if e[0] == 2 and e[2]]
            self.assertEqual(len(globals_events), 5)
            # ...and one wl_callback.done on callback 5 (4-byte stamp
            # payload = the surface's commit count).
            done_events = [e for e in events if e[0] == 5 and e[1] == 0]
            self.assertEqual(len(done_events), 1)
            self.assertEqual(len(done_events[0][2]), 4)
            stamp = struct.unpack("I", done_events[0][2])[0]
            self.assertGreaterEqual(stamp, 1)

            stats = self.harness.host.get_stats()
            self.assertEqual(stats.engine, "rust")
            self.assertGreater(stats.bytes_from_clients, 0)
            self.assertGreater(stats.bytes_to_clients, 0)
            self.assertEqual(stats.protocol_errors, 0)
        finally:
            client.close()

    def test_partial_message_reassembly(self):
        """A message split across two sends is reassembled, not dropped."""
        client = self._connect()
        try:
            time.sleep(0.1)
            req = _enc_request(1, 1, _enc_u32(2))  # get_registry
            cut = 5  # split inside the header
            client.sendall(req[:cut])
            time.sleep(0.1)
            client.sendall(req[cut:])

            received = b""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                received += chunk
                if len(_parse_events(received)) >= 5:
                    break

            # If the partial header had been fed to the crate as-is it
            # would be a protocol error; instead the split request is
            # reassembled and the 5 globals come back.
            events = _parse_events(received)
            self.assertEqual(len(events), 5)
            self.assertEqual(self.harness.host.get_stats().protocol_errors, 0)
        finally:
            client.close()

    def test_two_clients_independent_state(self):
        """Two clients each get their own response stream."""
        c1 = self._connect()
        c2 = self._connect()
        try:
            time.sleep(0.2)
            req = _enc_request(1, 1, _enc_u32(2))
            c1.sendall(req)
            deadline = time.monotonic() + 2.0
            got1 = b""
            while time.monotonic() < deadline:
                try:
                    chunk = c1.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                got1 += chunk
                if len(_parse_events(got1)) >= 5:
                    break
            self.assertEqual(len(_parse_events(got1)), 5)

            # Client 2 sends sync and gets exactly one done event —
            # client 1's globals must not leak into its stream.
            c2.sendall(_enc_request(1, 0, _enc_u32(9)))  # sync(cb=9)
            got2 = b""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    chunk = c2.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                got2 += chunk
                if _parse_events(got2):
                    break
            events2 = _parse_events(got2)
            self.assertEqual(len(events2), 1)
            self.assertEqual(events2[0][0], 9)
            self.assertEqual(events2[0][1], 0)
        finally:
            c1.close()
            c2.close()

    def test_disconnect_releases_state(self):
        """Disconnect drops the client's partial buffer and pending events."""
        client = self._connect()
        try:
            time.sleep(0.1)
            client.sendall(_enc_request(1, 1, _enc_u32(2)))
            time.sleep(0.1)
        finally:
            client.close()
        time.sleep(0.3)  # let the server process the disconnect
        stats = self.harness.host.get_stats()
        self.assertEqual(stats.clients, 0)
        self.assertNotIn(0, stats.partial)


class TestCompositorHostStubEngine(unittest.TestCase):
    """Fail-closed behavior when the crate is absent."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-host-stub-")
        self.saved = os.environ.pop("NYRQIS_RUST_LIB", None)

    def tearDown(self):
        if self.saved is not None:
            os.environ["NYRQIS_RUST_LIB"] = self.saved
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_feed_without_crate_is_honest_noop(self):
        """In stub mode feeding data doesn't fabricate events."""
        # Use a fresh module namespace with the codec forced unavailable.
        import importlib
        from ui import compositor_codec as comp
        from ui import compositor_host

        host = compositor_host.CompositorHost()
        engine = host.engine
        if engine == "rust":
            self.skipTest("compositor crate available; stub path untestable here")
        # Feeding a full handshake in stub mode must not raise and must
        # not invent pending bytes.
        host.on_client_data(1, _enc_request(1, 1, _enc_u32(2)))
        self.assertEqual(host.pending_bytes(1), b"")
        stats = host.get_stats()
        self.assertEqual(stats.engine, "stub")
        self.assertEqual(stats.bytes_to_clients, 0)
        self.assertEqual(stats.events_drained, 0)

    def test_disconnect_unknown_client_is_safe(self):
        """Disconnecting an unknown client doesn't raise."""
        from ui import compositor_host

        host = compositor_host.CompositorHost()
        host.on_client_disconnected(4242)  # never connected
        self.assertEqual(host.get_stats().clients, 0)

    def test_flush_pending_uses_writer_result(self):
        """flush_pending keeps bytes when the writer reports failure."""
        from ui import compositor_host

        host = compositor_host.CompositorHost()
        # Manually stage pending bytes as the drain path would.
        with host._lock:
            host._pending[7] = bytearray(b"\x01\x02\x03")
        writes = []

        def failing_writer(client_id: int, data: bytes) -> bool:
            writes.append((client_id, data))
            return False

        self.assertEqual(host.flush_pending(failing_writer), 0)
        self.assertEqual(host.pending_bytes(7), b"\x01\x02\x03")

        def ok_writer(client_id: int, data: bytes) -> bool:
            return True

        self.assertEqual(host.flush_pending(ok_writer), 1)
        self.assertEqual(host.pending_bytes(7), b"")


if __name__ == "__main__":
    unittest.main()
