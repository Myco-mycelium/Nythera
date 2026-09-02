"""test_compositor_integration — Integration tests for the full compositor pipeline.

Tests the Wayland socket, protocol encoding/decoding, and compositor integration.

References:
    - ADR-0026: Wayland display-server integration
    - ui/nyrqis_compositor.py
    - ui/wayland_socket.py
    - ui/wayland_protocol.py
"""

from __future__ import annotations

import os
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


class TestWaylandProtocol(unittest.TestCase):
    """Tests for Wayland protocol encoding/decoding."""

    def test_encode_message(self):
        """Can encode a Wayland message."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_message(1, 0, 42)
        self.assertEqual(len(data), 12)  # 8 header + 4 arg
        self.assertEqual(data[0:4], struct.pack("I", 1))  # object_id

    def test_encode_string(self):
        """Can encode a string argument."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_message(1, 0, "hello")
        # String: 4 bytes length + 6 bytes ("hello\0") + 2 bytes padding = 12
        self.assertGreater(len(data), 8)

    def test_decode_message(self):
        """Can decode a Wayland message."""
        from ui.wayland_protocol import WaylandEncoder, WaylandDecoder
        original = WaylandEncoder.encode_message(1, 0, 42)
        decoded = WaylandDecoder.decode_message(original)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.object_id, 1)
        self.assertEqual(decoded.opcode, 0)
        self.assertEqual(decoded.args[0], 42)

    def test_encode_registry_global(self):
        """Can encode wl_registry.global event."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_registry_global(
            2, 1, "wl_compositor", 4
        )
        self.assertGreater(len(data), 8)

    def test_encode_callback_done(self):
        """Can encode wl_callback.done event."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_callback_done(3, 0, 1234567890)
        self.assertGreater(len(data), 8)

    def test_encode_output_geometry(self):
        """Can encode wl_output.geometry event."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_output_geometry(
            1, 0, 0, 1920, 1080, 0, "DP", "Monitor", 0, 0
        )
        self.assertGreater(len(data), 8)

    def test_encode_xdg_wm_base_ping(self):
        """Can encode xdg_wm_base.ping event."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_xdg_wm_base_ping(1, 1)
        self.assertGreater(len(data), 8)

    def test_encode_xdg_toplevel_configure(self):
        """Can encode xdg_toplevel.configure event."""
        from ui.wayland_protocol import WaylandEncoder
        data = WaylandEncoder.encode_xdg_toplevel_configure(
            1, 800, 600, b"\x00"
        )
        self.assertGreater(len(data), 8)

    def test_decode_string_arg(self):
        """Can decode a string argument."""
        from ui.wayland_protocol import WaylandDecoder
        # Create string data: length (4) + "hello\0" (6) + padding (2)
        data = struct.pack("I", 6) + b"hello\x00\x00\x00"
        string, offset = WaylandDecoder.decode_string_arg(data, 0)
        self.assertEqual(string, "hello")
        self.assertEqual(offset, 12)

    def test_decode_array_arg(self):
        """Can decode an array argument."""
        from ui.wayland_protocol import WaylandDecoder
        # Create array data: length (4) + [1, 2, 3, 4] (16)
        data = struct.pack("I", 16) + struct.pack("IIII", 1, 2, 3, 4)
        array, offset = WaylandDecoder.decode_array_arg(data, 0)
        self.assertEqual(len(array), 16)

    def test_message_roundtrip(self):
        """Encoded messages can be decoded back."""
        from ui.wayland_protocol import WaylandEncoder, WaylandDecoder
        # Encode a complex message
        data = WaylandEncoder.encode_message(
            1, 5,
            42, "test", 3.14, b"\x01\x02\x03"
        )
        # Decode it
        decoded = WaylandDecoder.decode_message(data)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.object_id, 1)
        self.assertEqual(decoded.opcode, 5)

    def test_get_message_name(self):
        """Can get human-readable name for opcode."""
        from ui.wayland_protocol import WaylandDecoder
        self.assertEqual(WaylandDecoder.get_message_name(0), "error")
        self.assertEqual(WaylandDecoder.get_message_name(1), "create_surface")


class TestWaylandSocket(unittest.TestCase):
    """Tests for the Wayland socket server."""

    def test_server_create(self):
        """Can create a socket server."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            self.assertFalse(server.is_running)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_server_start_stop(self):
        """Can start and stop the server."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            self.assertTrue(server.start())
            self.assertTrue(server.is_running)
            server.stop()
            self.assertFalse(server.is_running)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_server_add_output(self):
        """Can add outputs to the server."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            output = server.add_output(1920, 1080, "test", 60000)
            self.assertEqual(output.width, 1920)
            self.assertEqual(output.height, 1080)
            self.assertEqual(server.get_output_count(), 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_server_client_count(self):
        """Client count starts at zero."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            self.assertEqual(server.get_client_count(), 0)
            self.assertEqual(server.get_surface_count(), 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_server_surface_count(self):
        """Surface count starts at zero."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            self.assertEqual(server.get_surface_count(), 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_server_double_start(self):
        """Double start returns False."""
        from ui.wayland_socket import WaylandSocketServer
        with tempfile.NamedTemporaryFile(suffix=".sock", delete=False) as f:
            path = f.name
        os.unlink(path)
        
        try:
            server = WaylandSocketServer(path)
            self.assertTrue(server.start())
            self.assertFalse(server.start())  # second start fails
            server.stop()
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestNyrqisCompositor(unittest.TestCase):
    """Tests for the integrated Nyrqis compositor."""

    def test_compositor_config_defaults(self):
        """CompositorConfig has sensible defaults."""
        from ui.nyrqis_compositor import CompositorConfig
        config = CompositorConfig()
        self.assertEqual(config.width, 1920)
        self.assertEqual(config.height, 1080)
        self.assertEqual(config.refresh_rate, 60000)

    def test_compositor_create(self):
        """Can create a compositor."""
        from ui.nyrqis_compositor import NyrqisCompositor, CompositorConfig
        config = CompositorConfig(headless=True)
        compositor = NyrqisCompositor(config)
        self.assertFalse(compositor._running)

    def test_compositor_stats(self):
        """Compositor provides stats."""
        from ui.nyrqis_compositor import NyrqisCompositor, CompositorConfig
        config = CompositorConfig(headless=True)
        compositor = NyrqisCompositor(config)
        stats = compositor.get_stats()
        self.assertIn("running", stats)
        self.assertIn("frame_count", stats)
        self.assertIn("config", stats)

    def test_compositor_context_manager(self):
        """Compositor works as context manager."""
        from ui.nyrqis_compositor import NyrqisCompositor, CompositorConfig
        config = CompositorConfig(headless=True)
        with NyrqisCompositor(config) as compositor:
            self.assertIsNotNone(compositor)

    def test_compositor_render_frame(self):
        """Can render frames in headless mode."""
        from ui.nyrqis_compositor import NyrqisCompositor, CompositorConfig
        config = CompositorConfig(headless=True)
        with NyrqisCompositor(config) as compositor:
            # Render a few frames
            for _ in range(5):
                compositor.render_frame()
            
            stats = compositor.get_stats()
            self.assertEqual(stats["frame_count"], 5)


class TestDRMBackend(unittest.TestCase):
    """Tests for the DRM backend."""

    def test_drm_backend_create(self):
        """Can create a DRM backend."""
        from ui.drm_backend import DRMBackend
        backend = DRMBackend()
        self.assertFalse(backend.is_open)

    def test_drm_backend_open_auto_detect(self):
        """DRM backend can auto-detect device."""
        from ui.drm_backend import DRMBackend
        backend = DRMBackend()
        if not os.path.exists("/dev/dri/card0") and not os.path.exists("/dev/dri/card1"):
            self.skipTest("No DRM device available")
        result = backend.open()
        if result:
            backend.close()

    def test_drm_backend_close(self):
        """Closing DRM backend is idempotent."""
        from ui.drm_backend import DRMBackend
        backend = DRMBackend()
        backend.close()  # should not raise
        backend.close()  # second call should be safe


class TestRenderPipeline(unittest.TestCase):
    """Tests for the render pipeline."""

    def test_render_pipeline_lifecycle(self):
        """Render pipeline initializes and cleans up."""
        from ui.render_pipeline import RenderPipeline, RenderConfig
        config = RenderConfig(width=640, height=480)
        pipeline = RenderPipeline(config)
        self.assertFalse(pipeline.state.initialized)
        pipeline.cleanup()  # should not raise


if __name__ == "__main__":
    unittest.main()
