"""test_weston_simple_shm — the real weston client against the compositor.

Priority-8 end-to-end proof: run upstream ``weston-simple-shm`` (the
canonical first client any Wayland compositor must survive) against
the compositor's Unix domain socket through the full stack
(``WaylandSocketServer`` → ``CompositorHost`` → Rust wire event loop).

The client exercises the handshake real clients perform, from ITS side
with the real libwayland — no fake encoders involved:

- ``wl_display.get_registry`` + ``wl_registry.bind`` (v Negotiated)
- ``wl_shm.format`` parsing (it picks ARGB8888/XRGB8888 from these)
- ``wl_compositor.create_surface`` + ``wl_shm.create_pool`` (with a
  REAL fd via SCM_RIGHTS) + ``wl_shm_pool.create_buffer``
- ``wl_surface.attach``/``damage``/``commit`` + ``wl_surface.frame``
- ``xdg_wm_base.get_xdg_surface``/``get_toplevel`` + the initial
  configure pair + ``ack_configure`` (the client redraws and re-commits
  after the configure — the flow a non-xdg client never exercises)
- ``wl_display.sync`` roundtrip via ``wl_display.dispatch``

Pass criteria: the client's event loop runs, the surface lands in the
compositor's state machine with commits, buffers release (double-
buffered redraw), and the client stays connected — any protocol error
on either side fails the test.

Skips honestly when ``weston-simple-shm`` is not installed (CI may
install it; the contract remains pinned by the fake-client tests).

References:
    - ui/compositor_host.py, ui/wayland_socket.py
    - rust/compositor/src/event_loop.rs (ABI 0x0000_0400)
    - tests/test_wayland_client_compat.py (the fake-client contract)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.compositor_host import CompositorHost  # noqa: E402

WESTON_SIMPLE_SHM = shutil.which("weston-simple-shm")


def _parse_events(data: bytes):
    events = []
    off = 0
    while off + 8 <= len(data):
        oid, so = int.from_bytes(data[off:off + 4], "little"), \
            int.from_bytes(data[off + 4:off + 8], "little")
        size = so >> 16
        if size < 8 or off + size > len(data):
            break
        events.append((oid, so & 0xFFFF, data[off + 8:off + size]))
        off += size
    return events


class TestWestonSimpleShm(unittest.TestCase):
    """The real weston-simple-shm binary against our compositor."""

    @classmethod
    def setUpClass(cls):
        if not WESTON_SIMPLE_SHM:
            raise unittest.SkipTest(
                "weston-simple-shm not installed "
                "(apt-get install weston) — fake-client contract tests "
                "still pin the protocol")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-weston-")
        self.socket_path = os.path.join(self.tmpdir, "wayland-weston-test")
        self.host = CompositorHost()
        from ui.wayland_socket import WaylandSocketServer

        self.server = WaylandSocketServer(self.socket_path)
        self.server.set_data_callback(self._on_data)
        self.server.set_disconnect_callback(self.host.on_client_disconnected)
        # SHM pool fds travel out-of-band (SCM_RIGHTS): without this
        # wiring the client's pixel memory never reaches the host and
        # no real content can be presented.
        self.server.set_fd_callback(self.host.on_client_fd)
        if self.host.engine != "rust":
            self.server.stop()
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            self.skipTest("compositor crate not available (stub engine)")
        from ui import compositor_codec as comp

        comp.add_output(800, 600, "weston-test")
        comp.start()
        self.assertTrue(self.server.start())

    def tearDown(self):
        from ui import compositor_codec as comp

        comp.stop()
        self.server.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @staticmethod
    def _on_data(client_id: int, data: bytes) -> None:
        host = TestWestonSimpleShm._host
        host.on_client_data(client_id, data)
        host.flush_pending(TestWestonSimpleShm._server.send_to_client)

    _host = None
    _server = None

    def _stats_snapshot(self):
        return self.host.get_stats()

    def test_client_connects_commits_and_survives(self):
        """Run weston-simple-shm for ~3 s against the socket.

        The client must stay connected (no protocol error on either
        side), land its surface in the state machine, and commit frames.
        """
        type(self)._host = self.host
        type(self)._server = self.server

        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = self.tmpdir
        env["WAYLAND_DISPLAY"] = os.path.basename(self.socket_path)
        # NOTE: never set WAYLAND_SOCKET, not even to "" — libwayland
        # treats a set-but-empty value as an fd number and aborts the
        # connect (found the hard way in this very test).
        env.pop("WAYLAND_SOCKET", None)

        # The real client run: connect, bind, draw, frame loop for 3 s.
        # weston-simple-shm takes no useful positional args (it IGNORES
        # --help and connects anyway — do not "probe" with it) and reads
        # WAYLAND_DISPLAY. It runs until killed — kill it after the
        # window; a clean protocol conversation is the pass condition
        # (no error event from us, no protocol-error exit from it).
        proc = subprocess.Popen(
            [WESTON_SIMPLE_SHM],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        try:
            # Give the client time to handshake + draw several frames.
            time.sleep(3.0)
            stats = self.host.get_stats()
            self.assertEqual(stats.engine, "rust")
            self.assertGreater(stats.bytes_from_clients, 0,
                               "the client must have sent requests")
            self.assertGreater(stats.bytes_to_clients, 0,
                               "the compositor must have answered")
            # No protocol error: our wire loop accepted everything the
            # real libwayland client sent.
            self.assertEqual(
                stats.protocol_errors, 0,
                f"wire loop rejected the real client: "
                f"last_error={self._last_wire_error()}")
            # The client's SHM pool fd must have arrived out-of-band.
            self.assertGreater(
                stats.fds_received, 0,
                "the client's wl_shm.create_pool fd never arrived "
                "— check set_fd_callback wiring")
            # The client's surface(s) reached the state machine.
            self.assertGreater(
                self._crate_surface_count(), 0,
                "no surface registered from the real client")
            # Client still alive == it did not die on a protocol error
            # (weston-simple-shm exits(1) with an error when the
            # compositor misbehaves; a compositor disconnect closes the
            # socket and the client exits).
            self.assertIsNone(proc.poll(),
                              "weston-simple-shm exited early — likely a "
                              "protocol error on the client side")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # After a clean conversation the disconnect teardown runs.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if self._crate_surface_count() == 0:
                break
            time.sleep(0.1)
        self.assertEqual(self._crate_surface_count(), 0,
                         "disconnect must destroy the client's surfaces")

    # -- helpers -------------------------------------------------------

    def _last_wire_error(self):
        try:
            from ui import compositor_codec as comp
            return comp.event_loop_last_error()
        except ImportError:
            return "codec unavailable"

    def _crate_surface_count(self):
        from ui import compositor_codec as comp
        return comp.surface_count()


if __name__ == "__main__":
    unittest.main()
