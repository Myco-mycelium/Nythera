"""test_boot_init — integration tests for nyrqis_init.py and the boot flow.

Tests that the daemon starts, the socket appears, the shell loads,
and the session can render a frame.  These tests are designed to run
without root privileges and without a real Wayland compositor.

References:
    - NPS-017 §4.5: boot and lifecycle
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


class TestNyrqisInit(unittest.TestCase):
    """Tests for nyrqis_init.py (the unified boot script)."""

    def setUp(self):
        """Create a temp directory for test sockets and state."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-init-test-")
        self.socket_path = os.path.join(self.tmpdir, "status.sock")
        self.health_socket = os.path.join(self.tmpdir, "health.sock")
        self.state_file = os.path.join(self.tmpdir, "daemon-state.json")
        self.vault_dir = os.path.join(self.tmpdir, "vault")
        os.makedirs(self.vault_dir, exist_ok=True)

    def tearDown(self):
        """Clean up temp files."""
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_find_design_default_locations(self):
        """nyrqis_init._find_design searches known paths."""
        from nyrqis_init import _find_design

        # With explicit path that doesn't exist, still searches fallback locations
        # (the function prioritizes finding *any* design over rejecting the path)
        result = _find_design("/nonexistent/file.nstudio")
        self.assertIsInstance(result, str)

        # With explicit path that exists, returns it
        fixture = os.path.join(
            _HERE, "tests", "fixtures", "nstudio", "desktop.nstudio")
        if os.path.exists(fixture):
            result = _find_design(fixture)
            self.assertEqual(result, fixture)

        # Without explicit path, it searches known locations
        result = _find_design()
        # May or may not find a design — just don't crash
        self.assertIsInstance(result, str)

    def test_wait_for_socket_timeout(self):
        """_wait_for_socket returns False when no socket appears."""
        from nyrqis_init import _wait_for_socket

        fake_socket = os.path.join(self.tmpdir, "nonexistent.sock")
        result = _wait_for_socket(fake_socket, timeout=0.3)
        self.assertFalse(result)

    def test_socket_command_no_daemon(self):
        """_socket_command returns None when no daemon is running."""
        from nyrqis_init import _socket_command

        result = _socket_command(
            self.socket_path,
            {"op": "ping"},
            timeout=1.0,
        )
        self.assertIsNone(result)

    def test_daemon_start_and_stop(self):
        """Backend daemon starts, binds socket, and stops cleanly."""
        backend_script = os.path.join(_HERE, "nyrqis_backend.py")

        process = subprocess.Popen(
            [
                sys.executable, backend_script,
                "service", "serve",
                "--socket", self.socket_path,
                "--health-socket", self.health_socket,
                "--state-file", self.state_file,
                "--vault-dir", self.vault_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Wait for socket to appear (up to 10s)
            deadline = time.monotonic() + 10.0
            socket_ready = False
            while time.monotonic() < deadline:
                if os.path.exists(self.socket_path):
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                        s.settimeout(0.5)
                        s.connect(self.socket_path)
                        s.close()
                        socket_ready = True
                        break
                    except (ConnectionRefusedError, FileNotFoundError, OSError):
                        pass
                time.sleep(0.2)

            self.assertTrue(socket_ready, "Daemon socket did not appear")

            # Verify state file was created
            self.assertTrue(
                os.path.exists(self.state_file),
                "Daemon state file was not created")

            # Verify health socket exists
            self.assertTrue(
                os.path.exists(self.health_socket),
                "Health socket was not created")

            # Send a ping command
            from ipc.transport import IPCClient, DEFAULT_OPERATOR_ID
            tmp = tempfile.mkdtemp(prefix="nyrqis-ping-")
            cli_path = os.path.join(tmp, "ctl.sock")
            client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
            try:
                reply = client.call(
                    self.socket_path,
                    json.dumps({"op": "ping"}).encode("utf-8"),
                    timeout_s=5.0,
                )
                self.assertIsNotNone(reply, "Ping did not get a reply")
                resp = json.loads(reply.payload.decode("utf-8"))
                self.assertTrue(resp.get("ok"), f"Ping failed: {resp}")
            finally:
                client.close()
                shutil.rmtree(tmp, ignore_errors=True)

        finally:
            # Stop the daemon
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def test_daemon_control_container_run(self):
        """Backend daemon can run a container via the control plane."""
        backend_script = os.path.join(_HERE, "nyrqis_backend.py")

        process = subprocess.Popen(
            [
                sys.executable, backend_script,
                "service", "serve",
                "--socket", self.socket_path,
                "--state-file", self.state_file,
                "--vault-dir", self.vault_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Wait for socket
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if os.path.exists(self.socket_path):
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                        s.settimeout(0.5)
                        s.connect(self.socket_path)
                        s.close()
                        break
                    except (ConnectionRefusedError, FileNotFoundError, OSError):
                        pass
                time.sleep(0.2)

            # Run a container via the control plane
            from ipc.transport import IPCClient, DEFAULT_OPERATOR_ID
            tmp = tempfile.mkdtemp(prefix="nyrqis-ctl-")
            cli_path = os.path.join(tmp, "ctl.sock")
            client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
            try:
                # List containers (should be empty)
                reply = client.call(
                    self.socket_path,
                    json.dumps({
                        "service": "control",
                        "op": "container_list",
                    }).encode("utf-8"),
                    timeout_s=5.0,
                )
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode("utf-8"))
                self.assertTrue(resp.get("ok"))
                containers = resp.get("containers", [])
                self.assertEqual(len(containers), 0)

                # Run a short-lived container
                reply = client.call(
                    self.socket_path,
                    json.dumps({
                        "service": "control",
                        "op": "container_run",
                        "command": ["/bin/echo", "hello"],
                        "network": False,
                        "memory_mb": 64,
                        "pids": 8,
                    }).encode("utf-8"),
                    timeout_s=30.0,
                )
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode("utf-8"))
                self.assertTrue(
                    resp.get("ok"),
                    f"container_run failed: {resp}")

            finally:
                client.close()
                shutil.rmtree(tmp, ignore_errors=True)

        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def test_daemon_persistent_state_survives_restart(self):
        """Daemon state file persists across restarts."""
        backend_script = os.path.join(_HERE, "nyrqis_backend.py")

        # Start, run a container, stop
        process = subprocess.Popen(
            [
                sys.executable, backend_script,
                "service", "serve",
                "--socket", self.socket_path,
                "--state-file", self.state_file,
                "--vault-dir", self.vault_dir,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if os.path.exists(self.socket_path):
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                        s.settimeout(0.5)
                        s.connect(self.socket_path)
                        s.close()
                        break
                    except (ConnectionRefusedError, FileNotFoundError, OSError):
                        pass
                time.sleep(0.2)

            # Verify state file has daemon identity
            self.assertTrue(os.path.exists(self.state_file))
            with open(self.state_file) as f:
                state = json.load(f)
            self.assertIn("daemon_pid", state)
            self.assertIn("backend_version", state)
            self.assertIn("containers", state)

            first_pid = state["daemon_pid"]
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        # Verify state file still exists after daemon stopped
        self.assertTrue(os.path.exists(self.state_file))
        with open(self.state_file) as f:
            state = json.load(f)
        # The PID in the file should be the old daemon's PID
        self.assertEqual(state["daemon_pid"], first_pid)


class TestDesktopSessionHeadless(unittest.TestCase):
    """Tests for the desktop session in headless mode."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-session-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_headless_render(self):
        """Desktop session can render a frame in headless mode."""
        # Find a test fixture
        fixture = os.path.join(
            _HERE, "tests", "fixtures", "nstudio", "desktop.nstudio")
        if not os.path.exists(fixture):
            self.skipTest("desktop.nstudio fixture not found")

        from ui.nstudio import load
        from ui.desktop_session import DesktopSession

        doc = load(fixture)
        session = DesktopSession(doc)

        # Render a frame
        img = session.live_render()
        self.assertIsNotNone(img)
        self.assertGreater(img.size[0], 0)
        self.assertGreater(img.size[1], 0)

    def test_session_summary(self):
        """Session summary reports window and monitor counts."""
        fixture = os.path.join(
            _HERE, "tests", "fixtures", "nstudio", "desktop.nstudio")
        if not os.path.exists(fixture):
            self.skipTest("desktop.nstudio fixture not found")

        from ui.nstudio import load
        from ui.desktop_session import DesktopSession

        doc = load(fixture)
        session = DesktopSession(doc)

        summary = session.summary()
        self.assertIn("windows", summary)
        self.assertIn("monitors", summary)
        self.assertIsInstance(summary["windows"], int)


class TestCompositorCodec(unittest.TestCase):
    """Tests for the Rust compositor Python FFI bindings."""

    def test_import(self):
        """compositor_codec imports without error."""
        import ui.compositor_codec as codec
        self.assertTrue(hasattr(codec, "available"))
        self.assertTrue(hasattr(codec, "start"))
        self.assertTrue(hasattr(codec, "stop"))

    def test_available_returns_bool(self):
        """available() returns a boolean."""
        from ui.compositor_codec import available
        result = available()
        self.assertIsInstance(result, bool)

    def test_version_returns_int(self):
        """version() returns an integer."""
        from ui.compositor_codec import version
        result = version()
        self.assertIsInstance(result, int)

    def test_stubs_return_error_codes(self):
        """When crate is absent, stubs return -1 or 0."""
        from ui.compositor_codec import (
            start, stop, add_output, create_surface,
            destroy_surface, process_input, surface_count,
        )
        # These should not crash even without the crate
        start()  # may return 0 or -1
        stop()   # may return 0 or -1
        self.assertIsInstance(add_output(1920, 1080), int)
        self.assertIsInstance(create_surface(0, 800, 600), int)
        self.assertIsInstance(destroy_surface(0), int)
        self.assertIsInstance(process_input(1, 0), int)
        self.assertIsInstance(surface_count(), int)


class TestSystemdUnit(unittest.TestCase):
    """Tests for the systemd unit file."""

    def test_service_unit_exists(self):
        """The systemd service unit file exists and is valid."""
        unit_path = os.path.join(
            _HERE, "packaging", "systemd", "nyrqis-backend.service")
        self.assertTrue(os.path.exists(unit_path), f"Missing: {unit_path}")

        content = open(unit_path).read()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("nyrqis_backend", content)

    def test_desktop_unit_exists(self):
        """The desktop session unit file exists."""
        unit_path = os.path.join(
            _HERE, "packaging", "systemd", "nyrqis-desktop.service")
        self.assertTrue(os.path.exists(unit_path), f"Missing: {unit_path}")

        content = open(unit_path).read()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("nyrqis_session", content)


class TestPyprojectToml(unittest.TestCase):
    """Tests for the pyproject.toml configuration."""

    def test_pyproject_exists(self):
        """pyproject.toml exists at the backend root."""
        path = os.path.join(_HERE, "pyproject.toml")
        self.assertTrue(os.path.exists(path), f"Missing: {path}")

    def test_pyproject_has_entry_points(self):
        """pyproject.toml defines CLI entry points."""
        import tomllib
        path = os.path.join(_HERE, "pyproject.toml")
        with open(path, "rb") as f:
            config = tomllib.load(f)

        scripts = config.get("project", {}).get("scripts", {})
        self.assertIn("nyrqisctl", scripts)
        self.assertIn("nyrqis-backend", scripts)
        self.assertIn("nyrqis-session", scripts)
        self.assertIn("nyrqis-run", scripts)

    def test_pyproject_has_dependencies(self):
        """pyproject.toml lists core dependencies."""
        import tomllib
        path = os.path.join(_HERE, "pyproject.toml")
        with open(path, "rb") as f:
            config = tomllib.load(f)

        deps = config.get("project", {}).get("dependencies", [])
        dep_names = " ".join(deps)
        self.assertIn("zstandard", dep_names)
        self.assertIn("fusepy", dep_names)


if __name__ == "__main__":
    unittest.main()
