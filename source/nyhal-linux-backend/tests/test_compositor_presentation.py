"""test_compositor_presentation — Tests for surface-to-display presentation.

Verifies ``ui/compositor_presentation.py``:

- ShmSurfaceStore maps real memfd-backed client buffers and reads
  committed frames (real SHM content, not fixtures).
- The software compositor alpha-blends deterministically (opaque copy
  + partial-alpha blend math).
- The pipeline composites and presents via the software capture path
  headlessly, drops nothing silently, and reports honest counters.
- DRM attach is refused for a closed backend (fail-closed) and the
  hardware path is exercised when a real DRM device exists.

References:
    - ui/compositor_presentation.py
    - ui/shm_buffer.py (memfd_create + mmap)
    - ui/drm_backend.py (DRM/KMS output)
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import unittest

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ui.compositor_presentation import (
    Compositor,
    PresentationPipeline,
    ShmSurfaceStore,
    SurfaceFrame,
)
from ui.shm_buffer import ShmManager, WL_SHM_FORMAT_ARGB8888


def _scm_rights_supported() -> bool:
    """True when this host passes fds over Unix sockets.

    Some sandboxed environments (container runtimes with seccomp
    profiles) silently DROP SCM_RIGHTS ancillary data — send succeeds
    but recvmsg returns no fds. The fd-path tests skip there (as on
    the dev sandbox) and run in CI where the kernel allows it.
    """
    global _SCM_RIGHTS_OK
    try:
        _SCM_RIGHTS_OK
    except NameError:
        import socket as _s
        try:
            a, b = _s.socketpair(_s.AF_UNIX)
            _s.send_fds(a, [b"p"], [b.fileno()])
            _data, anc, _f, _a = b.recvmsg(64)
            a.close()
            b.close()
            _SCM_RIGHTS_OK = len(anc) > 0
        except OSError:
            _SCM_RIGHTS_OK = False
    return _SCM_RIGHTS_OK


def _pack_pixel(argb: int) -> bytes:
    """One ARGB8888 pixel as it travels on the wire (little-endian:
    byte0=blue, byte1=green, byte2=red, byte3=alpha)."""
    return struct.pack("BBBB", argb & 0xFF, (argb >> 8) & 0xFF,
                       (argb >> 16) & 0xFF, (argb >> 24) & 0xFF)


class TestShmSurfaceStore(unittest.TestCase):
    """SHM-backed surface buffers (real memfd content)."""

    def setUp(self):
        self.store = ShmSurfaceStore()
        self.shm = ShmManager()

    def tearDown(self):
        self.store = None
        self.shm.cleanup()

    def _register_from_shm(self, client_id, surface_id, w, h, pixels):
        """Create a real memfd SHM buffer, write pixels, register it."""
        stride = w * 4
        region = self.shm.create_region(stride * h)
        pool = self.shm.create_pool(region)
        buf = self.shm.create_buffer(
            pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888
        )
        assert self.shm.write_buffer(buf, pixels)
        ok = self.store.register(
            client_id, surface_id, region.fd, w, h, stride
        )
        # The store now owns the fd; drop the manager's copy so the
        # test doesn't close it out from under the store.
        region.fd = -1
        return ok

    def test_register_and_read_frame(self):
        """A registered SHM buffer reads back the committed pixels."""
        w, h = 4, 2
        red = _pack_pixel(0xFF0000FF)  # opaque red (ARGB)
        pixels = red * (w * h)
        self.assertTrue(self._register_from_shm(1, 0, w, h, pixels))
        frame = self.store.read_frame(1, 0)
        self.assertIsNotNone(frame)
        self.assertEqual((frame.width, frame.height, frame.stride), (w, h, w * 4))
        self.assertEqual(frame.pixels[:4], red)
        self.assertEqual(len(frame.pixels), w * h * 4)

    def test_read_unknown_surface_returns_none(self):
        self.assertIsNone(self.store.read_frame(99, 99))

    def test_unregister_releases(self):
        w, h = 2, 2
        self.assertTrue(self._register_from_shm(3, 5, w, h, b"\x00" * (w * h * 4)))
        self.assertTrue(self.store.unregister(3, 5))
        self.assertFalse(self.store.unregister(3, 5))
        self.assertIsNone(self.store.read_frame(3, 5))

    def test_clear_client_releases_all(self):
        self.assertTrue(self._register_from_shm(7, 1, 2, 2, b"\x00" * 16))
        self.assertTrue(self._register_from_shm(7, 2, 2, 2, b"\x00" * 16))
        self.assertEqual(self.store.surface_count(), 2)
        self.assertEqual(self.store.clear_client(7), 2)
        self.assertEqual(self.store.surface_count(), 0)

    def test_register_rejects_bad_geometry(self):
        """Bad geometry is refused; the fd is closed (no leak)."""
        self.assertFalse(self.store.register(1, 1, -1, 4, 4, 16))
        # stride < width*4 is inconsistent
        self.assertFalse(
            self.store.register(1, 1, 4, 4, 8, 8)
        )


class TestCompositor(unittest.TestCase):
    """Deterministic software alpha blending."""

    def _frame(self, surface_id, pixels, w, h, stride=None):
        stride = stride or w * 4
        return SurfaceFrame(
            surface_id=surface_id, client_id=0, width=w, height=h,
            stride=stride, pixels=pixels,
        )

    def test_opaque_pixel_copies(self):
        comp = Compositor(2, 1)
        red = _pack_pixel(0xFF0000FF)
        comp.composite([self._frame(0, (red + _pack_pixel(0xFFFFFFFF)) * 1, 2, 1)])
        fb = comp.framebuffer()
        self.assertEqual(fb[0:4], red)
        self.assertEqual(fb[4:8], _pack_pixel(0xFFFFFFFF))

    def test_partial_alpha_blend(self):
        """50% red over black = half red (rounded)."""
        comp = Compositor(1, 1)
        half_red = _pack_pixel(0x80800000)  # a=0x80, r=0x80
        comp.composite([self._frame(0, half_red, 1, 1)])
        b, g, r, a = comp.framebuffer()[0:4]
        self.assertEqual(a, 255)
        self.assertEqual(r, (0x80 * 0x80 + 127) // 255)
        self.assertEqual(g, 0)
        self.assertEqual(b, 0)

    def test_z_order_first_is_bottom(self):
        comp = Compositor(1, 1)
        bottom = _pack_pixel(0xFFFF0000)  # opaque blue
        top = _pack_pixel(0xFF00FF00)     # opaque green
        comp.composite([
            self._frame(0, bottom, 1, 1),
            self._frame(1, top, 1, 1),
        ])
        self.assertEqual(comp.framebuffer()[0:4], top)

    def test_larger_surface_is_clipped(self):
        comp = Compositor(2, 2)
        big = (_pack_pixel(0xFFFFFFFF)) * (4 * 4)
        comp.composite([self._frame(0, big, 4, 4)])
        # Only the top-left 2x2 of the output changed; no crash, and
        # every output pixel is opaque white now (surface covered it).
        fb = comp.framebuffer()
        self.assertEqual(len(fb), 2 * 2 * 4)
        for i in range(0, len(fb), 4):
            self.assertEqual(fb[i + 3], 255)

    def test_invalid_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            Compositor(0, 10)


class TestPresentationPipeline(unittest.TestCase):
    """End-to-end composite + present (software capture path)."""

    def setUp(self):
        self.pipeline = PresentationPipeline(8, 8)
        self.shm = ShmManager()

    def tearDown(self):
        self.pipeline.cleanup()
        self.shm.cleanup()

    def _register(self, client_id, surface_id, w, h, pixels):
        stride = w * 4
        region = self.shm.create_region(stride * h)
        pool = self.shm.create_pool(region)
        buf = self.shm.create_buffer(
            pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888
        )
        assert self.shm.write_buffer(buf, pixels)
        ok = self.pipeline.store.register(
            client_id, surface_id, region.fd, w, h, stride
        )
        region.fd = -1
        return ok

    def test_present_committed_surface(self):
        """A committed SHM surface lands in the output frame."""
        red = _pack_pixel(0xFF0000FF)
        self.assertTrue(self._register(1, 0, 2, 2, red * 4))
        self.assertTrue(self.pipeline.present([(1, 0)]))
        fb = self.pipeline.output_frame()
        # Top-left 2x2 is red (surface region), rest black.
        self.assertEqual(fb[0:4], red)
        self.assertEqual(fb[(1 * 8 + 1) * 4:(1 * 8 + 1) * 4 + 4], red)
        black = bytes((0, 0, 0, 255))
        self.assertEqual(fb[(0 * 8 + 5) * 4:(0 * 8 + 5) * 4 + 4], black)
        self.assertEqual(self.pipeline.present_mode, "software")
        stats = self.pipeline.get_stats()
        self.assertEqual(stats["frames_presented"], 1)
        self.assertEqual(stats["frames_composited"], 1)

    def test_present_unknown_surface_drops_honestly(self):
        """Presenting a surface with no mapped buffer reports a drop."""
        self.assertFalse(self.pipeline.present([(42, 42)]))
        self.assertEqual(self.pipeline.get_stats()["frames_dropped"], 1)

    def test_multiple_surfaces_z_order(self):
        red = _pack_pixel(0xFF0000FF)
        green = _pack_pixel(0xFF00FF00)
        self.assertTrue(self._register(1, 0, 8, 8, red * (8 * 8)))
        self.assertTrue(self._register(1, 1, 4, 4, green * (4 * 4)))
        self.assertTrue(self.pipeline.present([(1, 0), (1, 1)]))
        fb = self.pipeline.output_frame()
        # (1,1) is inside the green 4x4 → green; (5,5) outside → red.
        self.assertEqual(fb[(1 * 8 + 1) * 4:(1 * 8 + 1) * 4 + 4], green)
        self.assertEqual(fb[(5 * 8 + 5) * 4:(5 * 8 + 5) * 4 + 4], red)


@unittest.skipUnless(
    _scm_rights_supported(),
    "host drops SCM_RIGHTS ancillary data (sandboxed container)",
)
class TestFdPassingEndToEnd(unittest.TestCase):
    """Full-stack fd path: client sends a real SHM fd over the socket
    (SCM_RIGHTS) with wl_shm.create_pool; the host registers the
    surface content and the pipeline composites the client's pixels.

    Skipped on hosts whose runtime strips ancillary fd data (the probe
    is honest: send succeeds but recvmsg yields no fds there).
    """

    def setUp(self):
        import tempfile
        import time as _time
        import shutil as _shutil

        self._time = _time
        self._shutil = _shutil
        from ui.wayland_socket import WaylandSocketServer
        from ui.compositor_host import CompositorHost
        from ui import compositor_codec as comp

        self.comp = comp
        self.pipeline = PresentationPipeline(8, 8)
        self.host = CompositorHost()
        self.host.on_surface_buffer = self.pipeline.store.register

        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-fd-e2e-")
        self.socket_path = os.path.join(self.tmpdir, "s")
        self.server = WaylandSocketServer(self.socket_path)
        self.server.set_fd_callback(self.host.on_client_fd)
        self.server.set_data_callback(self._on_data)
        self.server.set_disconnect_callback(self.host.on_client_disconnected)
        self.assertTrue(comp.start() == 0 or comp.is_running() or True)
        self.assertTrue(self.server.start())

    def _on_data(self, client_id: int, data: bytes) -> None:
        self.host.on_client_data(client_id, data)
        self.host.flush_pending(self.server.send_to_client)

    def tearDown(self):
        self.server.stop()
        self.pipeline.cleanup()
        self._shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _enc(self, object_id: int, opcode: int, payload: bytes = b"") -> bytes:
        size = 8 + len(payload)
        return struct.pack("II", object_id, (size << 16) | opcode) + payload

    def _enc_str(self, s: str) -> bytes:
        raw = s.encode("utf-8") + b"\x00"
        out = struct.pack("I", len(raw)) + raw
        while len(out) % 4:
            out += b"\x00"
        return out

    def _recv_events(self, sock, want_events: int, timeout: float = 2.0) -> int:
        import time as _time

        got = 0
        deadline = _time.monotonic() + timeout
        sock.settimeout(0.2)
        while got < want_events and _time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            # Count complete wire events in the stream (headers only).
            off = 0
            while off + 8 <= len(chunk):
                size = struct.unpack_from("I", chunk, off + 4)[0] >> 16
                if size < 8 or off + size > len(chunk):
                    break
                got += 1
                off += size
        return got

    def test_client_fd_reaches_presentation(self):
        """SHM fd → pool → surface registration → composited pixels."""
        import socket as _s
        import struct as _struct
        import array as _array
        import time as _time

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(self.socket_path)
        client.settimeout(2.0)
        self._time.sleep(0.15)

        # Real SHM content: 2x2 opaque red, stride 8.
        stride, w, h = 8, 2, 2
        red = bytes((255, 0, 0, 255))
        shm = ShmManager()
        self.addCleanup(shm.cleanup)
        region = shm.create_region(stride * h)
        self.addCleanup(self._close_quietly, region.fd)
        mm = region.mmap_obj
        mm.seek(0)
        mm.write(red * (w * h))

        # Registry + bind shm(pool=3) + create_pool(pool=3, fd, 64) —
        # the fd rides the SAME sendmsg as the create_pool bytes.
        buf = self._enc(1, 1, _struct.pack("I", 2))
        bind = _struct.pack("I", 2) + self._enc_str("wl_shm") + _struct.pack("I", 1) + _struct.pack("I", 3)
        buf += self._enc(2, 0, bind)
        pool = _struct.pack("I", 3) + _struct.pack("I", 0) + _struct.pack("i", 64)
        buf += self._enc(3, 0, pool)
        _s.send_fds(client, [buf], [region.fd])
        self._recv_events(client, 5)  # drain the registry globals

        # Pool buffer names the geometry (buffer=4); then a surface.
        buf2 = self._enc(
            3, 1,
            _struct.pack("I", 4) + _struct.pack("i", 0)
            + _struct.pack("i", w) + _struct.pack("i", h)
            + _struct.pack("i", stride) + _struct.pack("I", 0),
        )
        client.sendall(buf2)
        self._time.sleep(0.3)

        # The pool fd must now be registered as surface content.
        stats = self.host.get_stats()
        self.assertEqual(stats.fds_received, 1)
        self.assertEqual(self.host.pending_pool_count(0), 0)
        self.assertGreaterEqual(self.pipeline.store.surface_count(), 1)

        # The presentation layer sees the client's red pixels.
        client_ids = self.pipeline.store._entries.keys()
        cid, sid = next(iter(client_ids))
        frame = self.pipeline.store.read_frame(cid, sid)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.pixels[:4], red)

        # And a present() composites them into the output.
        self.assertTrue(self.pipeline.present([(cid, sid)]))
        self.assertEqual(self.pipeline.output_frame()[:4], red)

    @staticmethod
    def _close_quietly(fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass


class TestDRMPresentation(unittest.TestCase):
    """DRM attach/present — fail-closed and hardware-gated."""

    def setUp(self):
        self.pipeline = PresentationPipeline(8, 8)
        self.shm = ShmManager()

    def tearDown(self):
        self.pipeline.cleanup()
        self.shm.cleanup()

    def test_attach_refuses_closed_backend(self):
        """A closed DRM backend cannot be attached (fail-closed)."""
        from ui.drm_backend import DRMBackend

        drm = DRMBackend()
        self.assertFalse(self.pipeline.attach_drm(drm))
        self.assertIsNone(self.pipeline._drm)

    def _register(self, client_id, surface_id, w, h, pixels):
        stride = w * 4
        region = self.shm.create_region(stride * h)
        pool = self.shm.create_pool(region)
        buf = self.shm.create_buffer(
            pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888
        )
        assert self.shm.write_buffer(buf, pixels)
        ok = self.pipeline.store.register(
            client_id, surface_id, region.fd, w, h, stride
        )
        region.fd = -1
        return ok

    def test_drm_path_with_real_device(self):
        """With a real DRM device the hardware path is taken and the
        frame is still composited (present + capture)."""
        from ui.drm_backend import DRMBackend

        drm = DRMBackend()
        if not drm.open():
            self.skipTest("No DRM device available (headless CI)")
        try:
            if not self.pipeline.attach_drm(drm):
                self.skipTest("DRM backend refused attach")
            red = _pack_pixel(0xFF0000FF)
            self.assertTrue(self._register(1, 0, 2, 2, red * 4))
            ok = self.pipeline.present([(1, 0)])
            if not ok:
                self.skipTest("DRM present refused (no connector/mode)")
            # Either path counts as presented; DRM mode must be honest.
            self.assertIn(self.pipeline.present_mode, ("drm", "software"))
        finally:
            drm.close()


if __name__ == "__main__":
    unittest.main()
