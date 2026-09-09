#!/usr/bin/env python3
"""verify_presentation.py — Hardware verification for the compositor
presentation path (Nyrqis 0.28.0).

Checks, on the real machine:

1. DRM device probe (READ-ONLY ioctls only — never modesets; the
   display is left untouched). Reports connector/CRTC/mode info the
   same way DRMBackend.detect_connectors() sees it.
2. Real composited output: registers genuine memfd SHM surfaces in a
   PresentationPipeline, presents them, and asserts the output
   framebuffer bytes match the blend math (opaque copy + rounded
   over-operator alpha).
3. DRM-path posture: with a real DRM backend attached but without
   DRM master, SETCRTC must fail honestly — the pipeline reports
   software mode and still counts the frame as presented
   (fail-closed with fallback, never a silent drop).

Usage:
    python3 verify_presentation.py            # run all checks
    python3 verify_presentation.py --json     # machine-readable summary

Exit code 0 = all checks passed; 1 = any check failed.

References:
    - ui/compositor_presentation.py (pipeline under test)
    - ui/shm_buffer.py (memfd + mmap surface content)
    - ui/drm_backend.py (DRM/KMS output)
    - IMPLEMENTATION_STATUS.md § Compositor Presentation (0.28.0)
"""

from __future__ import annotations

import argparse
import fcntl
import json
import struct
import sys

from ui.compositor_presentation import (
    Compositor,
    PresentationPipeline,
    ShmSurfaceStore,
)
from ui.drm_backend import DRMBackend, DRM_IOCTL_MODE_GETRESOURCES
from ui.shm_buffer import ShmManager, WL_SHM_FORMAT_ARGB8888


def _px(argb: int) -> bytes:
    """One ARGB8888 pixel on the wire (LE: b, g, r, a)."""
    return struct.pack("BBBB", argb & 0xFF, (argb >> 8) & 0xFF,
                       (argb >> 16) & 0xFF, (argb >> 24) & 0xFF)


# ---------------------------------------------------------------------------
# 1. DRM device probe (read-only)
# ---------------------------------------------------------------------------

def probe_drm_devices() -> list:
    """Open each candidate DRM device read-only and enumerate
    connectors/CRTCs via GETRESOURCES. Never issues a modeset."""
    import os
    results = []
    for path in ["/dev/dri/card0", "/dev/dri/card1",
                 "/dev/dri/renderD128", "/dev/dri/renderD129"]:
        if not os.path.exists(path):
            continue
        info = {"path": path, "open": False}
        try:
            # O_RDWR is required for DRM ioctls; this performs no modeset.
            fd = os.open(path, os.O_RDWR)
            info["open"] = True
            try:
                # struct drm_mode_card_res (64 bytes): counts @32..48,
                # min/max width/height @48..64.
                buf = bytearray(64)
                fcntl.ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, buf, True)
                count_fbs, count_crtcs, count_conns, count_encs = \
                    struct.unpack_from("<4I", buf, 32)
                min_w, max_w, min_h, max_h = struct.unpack_from("<4I", buf, 48)
                info.update({
                    "framebuffers": count_fbs,
                    "crtcs": count_crtcs,
                    "connectors": count_conns,
                    "encoders": count_encs,
                    "max_resolution": f"{max_w}x{max_h}",
                })
            finally:
                os.close(fd)
        except OSError as exc:
            info["error"] = str(exc)
        results.append(info)
    return results


def drm_backends_visible() -> list:
    """Connectors as DRMBackend.detect_connectors() reports them."""
    out = []
    for path in ["/dev/dri/card0", "/dev/dri/card1"]:
        drm = DRMBackend(device_path=path)
        if not drm.open():
            continue
        try:
            out.append({
                "path": path,
                "connectors": [
                    {
                        "connector_id": c.id,
                        "crtc_id": c.crtc_id,
                        "connected": bool(getattr(c, "connected", True)),
                        "modes": len(getattr(c, "modes", []) or []),
                    }
                    for c in drm.detect_connectors()
                ],
            })
        finally:
            drm.close()
    return out


# ---------------------------------------------------------------------------
# 2. Real compositing through the pipeline
# ---------------------------------------------------------------------------

def check_composite_output() -> dict:
    """Register real memfd SHM surfaces, present, and verify the output
    framebuffer byte-for-byte against the blend math."""
    w, h = 8, 4
    pipeline = PresentationPipeline(w, h)
    shm = ShmManager()

    black = _px(0xFF000000)                     # opaque black
    semi_white = struct.pack("BBBB", 0xFF, 0xFF, 0xFF, 0x80)  # 50% white
    white = _px(0xFFFFFFFF)                     # opaque white
    stride = w * 4

    # Bottom: full opaque black.
    region = shm.create_region(stride * h)
    pool = shm.create_pool(region)
    buf = shm.create_buffer(pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888)
    assert shm.write_buffer(buf, black * (w * h))
    fd_bottom = region.fd
    region.fd = -1

    # Top: left half 50% white (partial-alpha path), right half opaque
    # white (opaque-copy path).
    top = (semi_white * (w // 2) + white * (w // 2)) * h
    region2 = shm.create_region(stride * h)
    pool2 = shm.create_pool(region2)
    buf2 = shm.create_buffer(pool2, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888)
    assert shm.write_buffer(buf2, top)
    fd_top = region2.fd
    region2.fd = -1

    assert pipeline.store.register(1, 0, fd_bottom, w, h, stride)
    assert pipeline.store.register(1, 1, fd_top, w, h, stride)

    assert pipeline.present([(1, 0), (1, 1)])  # z-order: red below
    fb = pipeline.output_frame()

    # Expected: left = 50% white over black (over operator, rounded to
    # nearest: (255*128 + 0*127 + 127)//255 = 128), right = pure white.
    blended = struct.pack("BBBB", 128, 128, 128, 255)

    ok = True
    mismatch = None
    for i in range(w * h):
        expect = blended if i % w < w // 2 else white
        actual = fb[i * 4:(i + 1) * 4]
        if actual != expect:
            ok = False
            mismatch = (i, expect.hex(), actual.hex())
            break

    stats = pipeline.get_stats()
    pipeline.cleanup()
    shm.cleanup()

    return {
        "check": "composite_output_bytes",
        "passed": ok,
        "first_mismatch_pixel": None if ok else mismatch[0],
        "expected": None if ok else mismatch[1],
        "actual": None if ok else mismatch[2],
        "frames_composited": stats["frames_composited"],
        "frames_presented": stats["frames_presented"],
        "present_mode": stats["present_mode"],
    }


# ---------------------------------------------------------------------------
# 3. DRM-path posture on real hardware (no modeset expected)
# ---------------------------------------------------------------------------

def check_drm_path_posture() -> dict:
    """Attach a real DRM backend (opened as a non-master, plain fd) and
    present. The hardware SETCRTC should honestly fail without DRM
    master; the pipeline must fall back to software and still present
    the frame. If the kernel does grant us a modeset (rare), that is
    reported as 'drm' — also acceptable and still honest."""
    drm = DRMBackend()
    if not drm.open():
        return {"check": "drm_path_posture", "passed": True,
                "skipped": "no DRM device available"}

    pipeline = None
    try:
        if not pipeline_attached(drm):
            return {"check": "drm_path_posture", "passed": True,
                    "skipped": "DRM backend refused attach"}

        w, h = 4, 2
        pipeline = PresentationPipeline(w, h)
        pipeline.attach_drm(drm)
        shm = ShmManager()
        stride = w * 4
        region = shm.create_region(stride * h)
        pool = shm.create_pool(region)
        buf = shm.create_buffer(pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888)
        assert shm.write_buffer(buf, _px(0xFF0000FF) * (w * h))
        fd = region.fd
        region.fd = -1
        assert pipeline.store.register(7, 9, fd, w, h, stride)

        ok = pipeline.present([(7, 9)])
        stats = pipeline.get_stats()
        shm.cleanup()
        return {
            "check": "drm_path_posture",
            "passed": bool(ok and stats["frames_presented"] >= 1),
            "presented": bool(ok),
            "present_mode": stats["present_mode"],  # "drm" or "software"
            "note": ("hardware modeset granted (drm master?)"
                     if stats["present_mode"] == "drm" else
                     "SETCRTC refused without DRM master — honest software fallback"),
        }
    finally:
        if pipeline is not None:
            pipeline.cleanup()
        drm.close()


def pipeline_attached(drm) -> bool:
    """attach_drm helper that also works when DRMBackend lacks is_open."""
    return getattr(drm, "is_open", False) or drm._fd >= 0


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable summary only")
    args = parser.parse_args()

    devices = probe_drm_devices()
    visible = drm_backends_visible()
    composite = check_composite_output()
    posture = check_drm_path_posture()

    report = {
        "drm_devices": devices,
        "drm_backends": visible,
        "checks": [composite, posture],
        "all_passed": all(c["passed"] for c in [composite, posture]),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Nyrqis compositor presentation — hardware verification")
        print("=" * 62)
        print("\n[1] DRM devices (read-only probe):")
        for d in devices:
            if d.get("open"):
                print(f"    {d['path']}: crtcs={d.get('crtcs')} "
                      f"connectors={d.get('connectors')} "
                      f"encoders={d.get('encoders', '?')} "
                      f"max={d.get('max_resolution', '?')}")
            else:
                print(f"    {d['path']}: unavailable ({d.get('error', 'n/a')})")
        print("\n[2] Connectors via DRMBackend.detect_connectors():")
        for b in visible:
            print(f"    {b['path']}: {b['connectors'] or 'none'}")
        print("\n[3] Composite output vs blend math:")
        print(f"    {'PASS' if composite['passed'] else 'FAIL'} "
              f"({composite['frames_composited']} composited, "
              f"{composite['frames_presented']} presented, "
              f"mode={composite['present_mode']})")
        print("\n[4] DRM-path posture:")
        print(f"    {'PASS' if posture['passed'] else 'FAIL'} "
              f"({posture.get('note', posture.get('skipped', ''))})")
        print(f"\nResult: {'ALL CHECKS PASSED' if report['all_passed'] else 'FAILURES PRESENT'}")

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
