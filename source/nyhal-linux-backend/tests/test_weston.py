"""test_weston — Tests with weston-simple-shm (requires weston).

These tests verify the Nyrqis compositor works with real Wayland clients.
They only run when weston-simple-shm is installed.

Both tests drive the FULL stack (WaylandSocketServer → CompositorHost →
Rust wire event loop) exactly as a production session would, with the
environment libwayland actually requires:

- ``XDG_RUNTIME_DIR`` must be set: libwayland resolves
  ``$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY`` and refuses absolute socket
  paths in ``WAYLAND_DISPLAY``.
- ``WAYLAND_SOCKET`` must be UNSET, not empty: a set-but-empty value
  is parsed as an fd number and aborts the connect.

The richer client-compat coverage (protocol surface assertions) lives
in tests/test_wayland_client_compat.py (fake client, exact bytes) and
tests/test_weston_simple_shm.py (this client, state-machine assertions).

References:
    - ADR-0026: Wayland display-server integration
    - Priority 8: Wayland client compatibility
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    os.sys.path.insert(0, _HERE)


WESTON_AVAILABLE = shutil.which("weston-simple-shm") is not None


@unittest.skipUnless(WESTON_AVAILABLE, "weston-simple-shm not installed")
class TestWestonSimpleShm(unittest.TestCase):
    """Tests with weston-simple-shm client."""

    def setUp(self):
        """Set up test socket and the full compositor stack."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.compositor_host import CompositorHost
        from ui import compositor_codec as comp

        self._tmpdir = tempfile.mkdtemp(prefix="nyrqis-weston-test-")
        self._host = CompositorHost()
        if self._host.engine != "rust":
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self.skipTest("compositor crate not available (stub engine)")
        self._socket_path = os.path.join(self._tmpdir, "wayland-test")
        self._server = WaylandSocketServer(self._socket_path)
        self._server.set_data_callback(
            lambda cid, data: (
                self._host.on_client_data(cid, data),
                self._host.flush_pending(self._server.send_to_client),
            )
        )
        self._server.set_disconnect_callback(self._host.on_client_disconnected)
        self._server.set_fd_callback(self._host.on_client_fd)
        comp.add_output(800, 600, "weston-test")
        comp.start()
        self.assertTrue(self._server.start())

    def tearDown(self):
        """Tear down the stack and clean up."""
        from ui import compositor_codec as comp

        comp.stop()
        self._server.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _client_env(self):
        """The env libwayland needs to find our socket (see module docstring)."""
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = self._tmpdir
        env["WAYLAND_DISPLAY"] = os.path.basename(self._socket_path)
        env.pop("WAYLAND_SOCKET", None)
        return env

    def test_weston_connects(self):
        """weston-simple-shm can connect to our compositor."""
        # The client runs its frame loop until killed. TimeoutExpired
        # means it stayed connected and drew for the whole window; an
        # early exit (rc 1) would mean a protocol error on either side.
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(
                ["weston-simple-shm"],
                env=self._client_env(),
                timeout=2,
                capture_output=True,
            )
        stats = self._host.get_stats()
        self.assertGreater(
            stats.bytes_from_clients, 0, "the client sent no requests"
        )
        self.assertGreater(
            stats.bytes_to_clients, 0, "the compositor sent no events"
        )
        self.assertEqual(
            stats.protocol_errors, 0, "wire loop rejected the real client"
        )


@unittest.skipUnless(WESTON_AVAILABLE, "weston-simple-shm not installed")
class TestWestonIntegration(unittest.TestCase):
    """Integration tests with weston-simple-shm."""

    def test_weston_renders_frame(self):
        """weston-simple-shm renders a frame through our compositor."""
        from ui.wayland_socket import WaylandSocketServer
        from ui.compositor_host import CompositorHost
        from ui import compositor_codec as comp

        tmpdir = tempfile.mkdtemp(prefix="nyrqis-weston-frame-")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        host = CompositorHost()
        if host.engine != "rust":
            self.skipTest("compositor crate not available (stub engine)")
        socket_path = os.path.join(tmpdir, "wayland-test")
        server = WaylandSocketServer(socket_path)
        self.addCleanup(server.stop)
        self.addCleanup(comp.stop)
        server.set_data_callback(
            lambda cid, data: (
                host.on_client_data(cid, data),
                host.flush_pending(server.send_to_client),
            )
        )
        server.set_disconnect_callback(host.on_client_disconnected)
        server.set_fd_callback(host.on_client_fd)
        comp.add_output(800, 600, "weston-frame")
        comp.start()
        self.assertTrue(server.start())

        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = tmpdir
        env["WAYLAND_DISPLAY"] = "wayland-test"
        env.pop("WAYLAND_SOCKET", None)

        # The client runs its frame loop until killed. Assert the
        # content path WHILE it is alive: after the kill, the disconnect
        # teardown destroys its surfaces (and that teardown races any
        # post-mortem assertion — found under the full suite).
        proc = subprocess.Popen(
            ["weston-simple-shm"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(2.0)
            self.assertIsNone(
                proc.poll(),
                "weston-simple-shm exited early — likely a protocol error",
            )
            stats = host.get_stats()
            self.assertGreater(stats.bytes_from_clients, 0)
            self.assertGreater(stats.bytes_to_clients, 0)
            self.assertGreater(
                stats.fds_received,
                0,
                "no wl_shm pool fd arrived (content path)",
            )
            self.assertEqual(stats.protocol_errors, 0)
            self.assertGreater(
                comp.surface_count(),
                0,
                "no surface registered from the client",
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # Disconnect teardown: the client's surfaces must be gone.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and comp.surface_count() > 0:
            time.sleep(0.1)
        self.assertEqual(comp.surface_count(), 0)


if __name__ == "__main__":
    unittest.main()
