"""test_gpu_vendor_conformance — M15 Phase 2 vendor-agnostic GPU conformance.

M15 Phase 2 calls for AMD Radeon and NVIDIA (Nouveau) validation. This
dev host has Intel i915 only, so vendor-specific validation cannot be
faked — NPC-002 §5.2: no results without measurement. What CAN be built
and verified here is the vendor-agnostic conformance suite: every
M15-Phase-2-relevant pipeline behavior (driver identification, GBM
allocation, EGL context, Vulkan device/swapchain, DRM discovery and
framebuffer lifecycle) exercised through the shipped codecs and asserted
IDENTICALLY no matter which vendor's driver answers — plus a driver-
matrix report that records exactly what was tested, so the eventual
AMD/NVIDIA runs reuse this suite unchanged and their results are
comparable.

Run (per vendor, on the machine with that hardware):
    NYRQIS_GPU_DEVICE=/dev/dri/card1 python3 -m pytest \\
        tests/test_gpu_vendor_conformance.py -v

References:
    - M15_PLAN.md Phase 2 (Hardware Compatibility)
    - ADR-0010 (Vulkan foundation), ADR-0026 (Wayland/DRM)
    - BENCHMARK_RESULTS §(this session): i915 baseline numbers
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

DEVICE = os.environ.get("NYRQIS_GPU_DEVICE", "/dev/dri/card1")


def _have_device() -> bool:
    return os.path.exists(DEVICE)


class TestDriverIdentification(unittest.TestCase):
    """The vendor matrix starts with knowing which driver is under test."""

    def test_query_driver_identifies_vendor(self):
        from ui.drm_backend import DRMBackend, query_driver
        if not _have_device():
            self.skipTest(f"{DEVICE} not present")
        backend = DRMBackend()
        self.assertTrue(backend.open(DEVICE), f"cannot open {DEVICE}")
        try:
            info = query_driver(backend.get_fd())
            self.assertIsNotNone(info, "DRM_IOCTL_VERSION failed")
            self.assertTrue(info["name"], "driver name empty")
            # The name is the vendor anchor: i915/xe = Intel, amdgpu =
            # AMD, nouveau = NVIDIA. Whatever it is, it must be one of
            # the known mainline KMS drivers for the matrix to mean
            # anything — unknown names are recorded, not failed, so a
            # new driver (e.g. a future Asahi/Apple or vestige driver)
            # still yields a usable conformance run.
            known = {"i915", "xe", "amdgpu", "nouveau", "vmwgfx",
                     "vc4", "v3d", "panel-splash", "simpledrm",
                     "msm", "rockchip", "mediatek", "virtio_gpu",
                     "vkms", "bochs", "cirrus", "mgag200", "ast"}
            print(f"\n[driver matrix] {DEVICE}: {info['name']} "
                  f"{info['major']}.{info['minor']}.{info['patchlevel']} "
                  f"({info['desc']}) — vendor-agnostic conformance")
            if info["name"] not in known:
                print(f"[driver matrix] WARNING: {info['name']} not in the "
                      f"known-driver list; results still valid but the "
                      f"vendor column must be filled manually")
        finally:
            backend.close()

    def test_query_driver_rejects_bad_fd(self):
        from ui.drm_backend import query_driver
        self.assertIsNone(query_driver(-1))


class TestGBMConformance(unittest.TestCase):
    """GBM behaviors every M15-Phase-2 vendor must satisfy."""

    def test_buffer_allocation_roundtrip(self):
        from ui import gbm_codec
        if not (_have_device() and gbm_codec.is_available()):
            self.skipTest("GBM unavailable")
        render = DEVICE.replace("card1", "renderD128").replace("card0", "renderD128")
        if not os.path.exists(render):
            self.skipTest("render node not present")
        dev = gbm_codec.open_device(render)
        if dev < 0:
            self.skipTest("cannot open render node")
        try:
            surf = gbm_codec.create_surface(dev, 64, 64)
            self.assertGreaterEqual(surf, 0, "surface creation failed")
            buf = gbm_codec.lock_buffer(surf)
            self.assertGreaterEqual(buf, 0, "buffer lock failed")
            info = gbm_codec.get_buffer_info(buf)
            self.assertIsNotNone(info)
            self.assertEqual(info[0], 64)        # width
            self.assertEqual(info[1], 64)        # height
            self.assertGreaterEqual(info[2], 64 * 4)  # stride >= w·4 (ARGB8888)
            self.assertTrue(gbm_codec.release_buffer(buf))
            self.assertTrue(gbm_codec.destroy_surface(surf))
        finally:
            gbm_codec.close_device(dev)

    def test_multiple_surfaces_independent(self):
        from ui import gbm_codec
        if not (_have_device() and gbm_codec.is_available()):
            self.skipTest("GBM unavailable")
        render = DEVICE.replace("card1", "renderD128").replace("card0", "renderD128")
        if not os.path.exists(render):
            self.skipTest("render node not present")
        dev = gbm_codec.open_device(render)
        if dev < 0:
            self.skipTest("cannot open render node")
        try:
            surfaces = []
            try:
                for size in (32, 64, 128):
                    s = gbm_codec.create_surface(dev, size, size)
                    self.assertGreaterEqual(s, 0)
                    surfaces.append(s)
            finally:
                for s in surfaces:
                    gbm_codec.destroy_surface(s)
        finally:
            gbm_codec.close_device(dev)


class TestEGLConformance(unittest.TestCase):
    """EGL context creation through the shipped codec."""

    def test_context_lifecycle(self):
        from ui import egl_codec
        if not (_have_device() and egl_codec.is_available()):
            self.skipTest("EGL unavailable")
        display = egl_codec.get_display()
        if display < 0:
            self.skipTest("no EGL display")
        try:
            self.assertTrue(egl_codec.initialize(display), "eglInitialize failed")
            config = egl_codec.choose_config(display)
            self.assertGreaterEqual(config, 0, "no acceptable EGLConfig")
            surface = egl_codec.create_window_surface(display, config, 64, 64)
            context = egl_codec.create_context(display, config)
            self.assertGreaterEqual(context, 0, "context creation failed")
            self.assertTrue(egl_codec.make_current(display, surface, context),
                            "makeCurrent failed")
            # NOTE: swap_buffers is deliberately not asserted here — the
            # established EGL test convention (test_gpu_pipeline.py) ends
            # the context contract at make_current; swap behavior belongs
            # to the full render-pipeline tests where the surface is
            # created exactly as the compositor creates it.
            self.assertTrue(egl_codec.destroy_context(context))
            self.assertTrue(egl_codec.destroy_surface(surface))
        finally:
            egl_codec.terminate(display)


class TestVulkanConformance(unittest.TestCase):
    """Vulkan instance/device/swapchain through the shipped codec."""

    def test_device_and_swapchain(self):
        from ui import vulkan_codec
        if not (_have_device() and vulkan_codec.is_available()):
            self.skipTest("Vulkan unavailable")
        inst = vulkan_codec.create_instance()
        self.assertGreaterEqual(
            inst, 0,
            f"instance creation failed: {vulkan_codec.last_error()}")
        try:
            dev = vulkan_codec.create_device(inst)
            self.assertGreaterEqual(
                dev, 0,
                f"device creation failed: {vulkan_codec.last_error()}")
            try:
                sc = vulkan_codec.create_swapchain(dev, 64, 64, image_count=3)
                self.assertGreaterEqual(
                    sc, 0,
                    f"swapchain creation failed: {vulkan_codec.last_error()}")
                vulkan_codec.destroy_swapchain(sc)
            finally:
                vulkan_codec.destroy_device(dev)
        finally:
            vulkan_codec.destroy_instance(inst)


class TestDRMConformance(unittest.TestCase):
    """DRM discovery and framebuffer lifecycle (non-master safe)."""

    def test_connector_discovery(self):
        from ui.drm_backend import DRMBackend
        if not _have_device():
            self.skipTest(f"{DEVICE} not present")
        backend = DRMBackend()
        self.assertTrue(backend.open(DEVICE))
        try:
            connectors = backend.detect_connectors()
            self.assertIsInstance(connectors, list)
            for c in connectors:
                self.assertGreater(c.id, 0)
                self.assertIsInstance(c.connected, bool)
        finally:
            backend.close()

    def test_framebuffer_lifecycle_non_master(self):
        """Dumb-buffer + ADDFB2 works (render node) or fails closed.

        Without DRM master the kernel refuses some operations (EPERM);
        the contract under test is that the backend NEVER lies: it
        either returns a usable framebuffer or reports failure —
        identical behavior on any vendor's driver.
        """
        from ui.drm_backend import DRMBackend
        if not _have_device():
            self.skipTest(f"{DEVICE} not present")
        backend = DRMBackend()
        self.assertTrue(backend.open(DEVICE))
        try:
            fb = backend.create_framebuffer(64, 64)
            if fb is None:
                self.skipTest("framebuffer creation refused (non-master)")
            try:
                self.assertGreater(fb.fb_id, 0)
                self.assertGreater(fb.pitch, 0)
                # write must either succeed or report False — never throw
                # a raw OSError to callers.
                result = backend.write_framebuffer(
                    fb, b"\x20\x30\x40\xff" * 64 * 64)
                self.assertIsInstance(result, bool)
            finally:
                backend._destroy_dumb(fb.handle)
        finally:
            backend.close()


class TestVendorMatrixReport(unittest.TestCase):
    """Emits the M15 Phase 2 matrix row for THIS host in one place."""

    def test_report(self):
        from ui.drm_backend import DRMBackend, query_driver
        from ui import gbm_codec, egl_codec, vulkan_codec
        row = {"device": DEVICE if _have_device() else "(absent)"}
        if _have_device():
            backend = DRMBackend()
            if backend.open(DEVICE):
                info = query_driver(backend.get_fd())
                if info:
                    row["driver"] = (f"{info['name']} "
                                     f"{info['major']}.{info['minor']}."
                                     f"{info['patchlevel']}")
                row["connectors"] = len(backend.detect_connectors())
                backend.close()
        row["gbm"] = bool(gbm_codec.is_available()) if _have_device() else False
        row["egl"] = bool(egl_codec.is_available())
        row["vulkan"] = bool(vulkan_codec.is_available())
        print("\n[M15 Phase 2 matrix row] " + ", ".join(
            f"{k}={v}" for k, v in row.items()))


if __name__ == "__main__":
    unittest.main()
