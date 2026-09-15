"""test_weston_terminal — the GTK-class real client against the compositor.

The second real-client proof for Priority 8: run upstream
``weston-terminal`` (weston's VTE terminal — a GTK3 client with a real
event loop, clipboard, cursor surfaces, and a spawned shell) against
the compositor's Unix domain socket through the full stack
(``WaylandSocketServer`` → ``CompositorHost`` → Rust wire event loop).

Where weston-simple-shm exercises the minimal SHM drawing path,
weston-terminal exercises the GTK-class handshake:

- ``wl_compositor`` v3 / ``wl_output`` v2 binding (exercises the
  version-gated ``wl_output.done``/``.scale`` events — v2 binders get
  them, v1 binders must not)
- ``wl_seat`` v7 + ``wl_keyboard``/``wl_pointer`` device objects
- **Two** SHM pools (window buffer + cursor) — the fd path twice over
- VTE drawing with real content (the shell's title lands in
  ``xdg_toplevel.set_title``), frame callbacks driving redraws

Pass criteria: the client stays connected for the window, drives fds
through, registers its surface, and the wire loop records zero
protocol errors — any of those failing means the GTK-class handshake
broke. Skips honestly when ``weston-terminal`` is not installed.

References:
    - ui/compositor_host.py, ui/wayland_socket.py
    - rust/compositor/src/event_loop.rs (ABI 0x0000_0400)
    - tests/test_weston_simple_shm.py (the minimal-client proof)
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

WESTON_TERMINAL = shutil.which("weston-terminal")

# Long enough for VTE to spawn its shell and draw real content, short
# enough to keep the suite fast.
RUN_SECONDS = 5.0


class TestWestonTerminal(unittest.TestCase):
    """The real weston-terminal binary against our compositor."""

    @classmethod
    def setUpClass(cls):
        if not WESTON_TERMINAL:
            raise unittest.SkipTest(
                "weston-terminal not installed "
                "(apt-get install weston) — the fake-client contract "
                "tests still pin the protocol"
            )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-wterm-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.socket_path = os.path.join(self.tmpdir, "wayland-wterm")
        self.host = CompositorHost()
        if self.host.engine != "rust":
            self.skipTest("compositor crate not available (stub engine)")
        from ui.wayland_socket import WaylandSocketServer

        self.server = WaylandSocketServer(self.socket_path)
        self.addCleanup(self.server.stop)
        self.addCleanup(self._stop_codec)
        self.server.set_data_callback(self._on_data)
        self.server.set_disconnect_callback(self.host.on_client_disconnected)
        # SHM pool fds travel out-of-band (SCM_RIGHTS): weston-terminal
        # opens TWO pools (window buffer + cursor) — both must arrive.
        self.server.set_fd_callback(self.host.on_client_fd)
        from ui import compositor_codec as comp

        comp.add_output(1024, 768, "wterm-test")
        comp.start()
        self.assertTrue(self.server.start())

    @staticmethod
    def _stop_codec():
        from ui import compositor_codec as comp

        comp.stop()

    @staticmethod
    def _on_data(client_id: int, data: bytes) -> None:
        host = TestWestonTerminal._host
        host.on_client_data(client_id, data)
        host.flush_pending(TestWestonTerminal._server.send_to_client)

    _host = None
    _server = None

    def test_terminal_connects_draws_and_survives(self):
        """Run weston-terminal for ~5 s against the socket.

        The terminal must stay connected (no protocol error on either
        side), drive its SHM pool fds through, land its surface in the
        compositor's state machine, and draw with the wire loop's frame
        callbacks (bytes flowed both ways).
        """
        type(self)._host = self.host
        type(self)._server = self.server

        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = self.tmpdir
        env["WAYLAND_DISPLAY"] = os.path.basename(self.socket_path)
        # NOTE: never set WAYLAND_SOCKET, not even to "" — libwayland
        # parses a set-but-empty value as an fd number and aborts the
        # connect (see tests/test_weston.py).
        env.pop("WAYLAND_SOCKET", None)
        env.pop("WAYLAND_DEBUG", None)

        proc = subprocess.Popen(
            [WESTON_TERMINAL],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            time.sleep(RUN_SECONDS)
            self.assertIsNone(
                proc.poll(),
                "weston-terminal exited early — likely a protocol "
                "error on the client side",
            )
            stats = self.host.get_stats()
            self.assertEqual(stats.engine, "rust")
            self.assertGreater(
                stats.bytes_from_clients, 0,
                "the client must have sent requests")
            self.assertGreater(
                stats.bytes_to_clients, 0,
                "the compositor must have answered")
            # The window buffer AND cursor pools both travelled.
            self.assertGreaterEqual(
                stats.fds_received, 2,
                f"expected the window and cursor pool fds, got "
                f"{stats.fds_received}")
            # No protocol error: our wire loop accepted everything the
            # real GTK client sent.
            self.assertEqual(
                stats.protocol_errors, 0,
                f"wire loop rejected the real client: "
                f"last_error={self._last_wire_error()}")
            # The terminal's surface reached the state machine.
            self.assertGreater(
                self._crate_surface_count(), 0,
                "no surface registered from the real client")
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
