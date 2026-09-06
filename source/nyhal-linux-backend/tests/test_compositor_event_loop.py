"""Tests for compositor event loop."""

import os
import socket
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.compositor_event_loop import (
    CompositorEventLoop,
    WaylandClient,
    WaylandSurface,
    WaylandOutput,
)


class TestCompositorEventLoop(unittest.TestCase):
    """Test compositor event loop."""

    def setUp(self):
        """Create temp socket path."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-compositor-")
        self.socket_path = os.path.join(self.tmpdir, "wayland-test")

    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creation(self):
        """Event loop can be created."""
        loop = CompositorEventLoop(self.socket_path)
        self.assertFalse(loop.is_running)
        self.assertEqual(loop.client_count, 0)
        self.assertEqual(loop.surface_count, 0)

    def test_start_stop(self):
        """Event loop can be started and stopped."""
        loop = CompositorEventLoop(self.socket_path)
        self.assertTrue(loop.start())
        self.assertTrue(loop.is_running)
        loop.stop()
        self.assertFalse(loop.is_running)

    def test_add_output(self):
        """Outputs can be added."""
        loop = CompositorEventLoop(self.socket_path)
        output = loop.add_output(1920, 1080, "primary")
        self.assertEqual(output.width, 1920)
        self.assertEqual(output.height, 1080)
        self.assertEqual(loop.output_count, 1)

    def test_client_connection(self):
        """Clients can connect."""
        loop = CompositorEventLoop(self.socket_path)
        loop.start()
        
        try:
            # Connect a client
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(self.socket_path)
            
            time.sleep(0.1)  # Let event loop process
            
            self.assertEqual(loop.client_count, 1)
            
            client.close()
        finally:
            loop.stop()

    def test_surface_creation(self):
        """Surfaces can be created."""
        loop = CompositorEventLoop(self.socket_path)
        
        surfaces_created = []
        loop.on_surface_created(lambda s: surfaces_created.append(s))
        
        loop.start()
        
        try:
            # Connect and send create_surface message
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(self.socket_path)
            
            time.sleep(0.1)
            
            # Send wl_display.get_registry
            data = struct.pack("II", 1, (8 << 16) | 1)  # object_id=1, size=8, opcode=1
            client.sendall(data)
            
            time.sleep(0.1)
            
            client.close()
        finally:
            loop.stop()


class TestWaylandSurface(unittest.TestCase):
    """Test WaylandSurface dataclass."""

    def test_creation(self):
        """Surface can be created."""
        surface = WaylandSurface(id=1, client_id=0)
        self.assertEqual(surface.id, 1)
        self.assertTrue(surface.active)

    def test_default_values(self):
        """Surface has correct defaults."""
        surface = WaylandSurface(id=1, client_id=0)
        self.assertEqual(surface.width, 0)
        self.assertEqual(surface.height, 0)
        self.assertEqual(surface.scale, 1)


class TestWaylandOutput(unittest.TestCase):
    """Test WaylandOutput dataclass."""

    def test_creation(self):
        """Output can be created."""
        output = WaylandOutput(id=0, name="primary", width=1920, height=1080)
        self.assertEqual(output.width, 1920)
        self.assertTrue(output.active)


if __name__ == "__main__":
    # Need struct for message construction
    import struct
    unittest.main()
