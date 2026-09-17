"""test_ffi_byref_contract — pin the FFI byref rule.

The DRM ``get_connector_info`` bug (0.29.22): ``ctypes.byref(struct.field)``
raises ``TypeError: byref() argument must be a ctypes instance`` because
field access on a Structure yields a plain Python int. It survived for the
crate's whole life because the tests skipped whenever the crate was
present.

The rule: an FFI argument wrapped in ``ctypes.byref(...)`` must be either

1. a standalone ctypes instance (``c_uint32(0)``, ``c_void_p()``, …), or
2. a whole Structure instance (``byref(info)``).

Anything else — most notably ``byref(struct.field)`` — is a latent
crash on the crate path. This test statically scans every FFI wrapper
module for ``byref(<expr> . <field>)`` patterns and fails listing the
offending call sites.
"""

import os
import re
import unittest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules that touch ctypes FFI (lib loading + calls). Static scan set —
# add a module here when it gains an FFI surface.
FFI_MODULES = [
    "ui/drm_codec.py",
    "ui/wayland_codec.py",
    "ui/gbm_codec.py",
    "ui/render_pipeline.py",
    "backend/rust_syscalls.py",
    "backend/nyruntime.py",
    "backend/container_codec.py",
    "backend/seccomp.py",
    "backend/keys.py",
    "fuse/nyfs_codec.py",
    "ipc/ipc_codec.py",
    "ipc/transport_codec.py",
]

# byref( <anything> . <identifier> ) — a byref over an attribute access.
# Whole-struct byref(info) and standalone byref(w) do NOT match (no dot).
BYREF_FIELD = re.compile(
    r"byref\(\s*[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\s*\)")


class TestByrefRule(unittest.TestCase):
    """No FFI wrapper may pass a struct field to ctypes.byref."""

    def test_no_byref_over_attribute_access(self):
        offenders = []
        for rel in FFI_MODULES:
            path = os.path.join(_BACKEND_DIR, rel)
            if not os.path.isfile(path):
                offenders.append(f"{rel}: MISSING — update FFI_MODULES")
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if BYREF_FIELD.search(line):
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "ctypes.byref(struct.field) is a latent crate-path crash "
            "(field access yields a plain int, not a ctypes instance). "
            "Pass a standalone ctypes instance or the whole struct: "
            + "; ".join(offenders))

    def test_drm_out_params_are_standalone_instances(self):
        # Regression pin for the exact 0.29.22 bug: the wrapper must
        # create standalone c_uint32 instances, never touch
        # info.<field> before the FFI call.
        path = os.path.join(_BACKEND_DIR, "ui", "drm_codec.py")
        src = open(path, encoding="utf-8").read()
        self.assertIn("width = ctypes.c_uint32(0)", src)
        self.assertNotIn("byref(info.width)", src)

    def test_ffi_modules_all_exist(self):
        missing = [rel for rel in FFI_MODULES
                   if not os.path.isfile(os.path.join(_BACKEND_DIR, rel))]
        self.assertEqual(
            missing, [],
            f"FFI_MODULES lists files that no longer exist: {missing}")


if __name__ == "__main__":
    unittest.main()
