"""Python loader for the NyRuntime Rust crate.

The NyRuntime provides the minimal execution environment for Nyrqis
programs. This module loads the compiled crate via ctypes and exposes
the runtime operations to Python.

ABI 1.0.0 — the FFI surface is versioned and checked at load time.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Optional


_ABI_VERSION: int = (1 << 16) | 0  # 1.0.0
_LIB_NAME = "libnyrqis_nyruntime.so"


def _find_library() -> Optional[Path]:
    """Search for the compiled NyRuntime crate."""
    # 1. Environment override
    override = os.environ.get("NYRQIS_RUNTIME_LIB")
    if override:
        p = Path(override)
        if p.exists():
            return p

    # 2. Crate target directory (relative to this file)
    here = Path(__file__).resolve().parent
    crate_dir = here.parent / "rust" / "nyruntime"
    target = crate_dir / "target" / "release" / _LIB_NAME
    if target.exists():
        return target
    target = crate_dir / "target" / "debug" / _LIB_NAME
    if target.exists():
        return target

    # 3. PATH search
    import shutil
    found = shutil.which(_LIB_NAME)
    if found:
        return Path(found)

    return None


def _load_library():
    """Load the NyRuntime library and configure function signatures."""
    path = _find_library()
    if path is None:
        raise ImportError(
            f"NyRuntime crate not found. Build it with: "
            f"cd {Path(__file__).resolve().parent.parent / 'rust' / 'nyruntime'} && "
            f"cargo build --release"
        )

    lib = ctypes.CDLL(str(path))

    # Version check
    lib.nyrqis_nyruntime_version.restype = ctypes.c_uint32
    lib.nyrqis_nyruntime_version.argtypes = []
    version = lib.nyrqis_nyruntime_version()
    if version != _ABI_VERSION:
        raise ImportError(
            f"NyRuntime ABI mismatch: expected {_ABI_VERSION:#x}, got {version:#x}"
        )

    # nyrqis_nyruntime_create -> *Runtime
    lib.nyrqis_nyruntime_create.restype = ctypes.c_void_p
    lib.nyrqis_nyruntime_create.argtypes = []

    # nyrqis_nyruntime_destroy(*Runtime) -> void
    lib.nyrqis_nyruntime_destroy.restype = None
    lib.nyrqis_nyruntime_destroy.argtypes = [ctypes.c_void_p]

    # nyrqis_nyruntime_init(*Runtime) -> i32
    lib.nyrqis_nyruntime_init.restype = ctypes.c_int32
    lib.nyrqis_nyruntime_init.argtypes = [ctypes.c_void_p]

    # nyrqis_nyruntime_state(*Runtime) -> i32
    lib.nyrqis_nyruntime_state.restype = ctypes.c_int32
    lib.nyrqis_nyruntime_state.argtypes = [ctypes.c_void_p]

    return lib


# Module-level library handle (loaded on first use)
_lib = None


def _get_lib():
    global _lib
    if _lib is None:
        _lib = _load_library()
    return _lib


class NyRuntime:
    """Python wrapper around the NyRuntime Rust crate.

    Usage::

        rt = NyRuntime()
        rt.init()
        # ... load and execute programs ...
        rt.destroy()
    """

    def __init__(self):
        lib = _get_lib()
        self._ptr = lib.nyrqis_nyruntime_create()
        self._destroyed = False

    def init(self) -> None:
        """Initialize the runtime. Must be called before loading programs."""
        lib = _get_lib()
        result = lib.nyrqis_nyruntime_init(self._ptr)
        if result != 0:
            raise RuntimeError(f"NyRuntime init failed: errno {result}")

    @property
    def state(self) -> int:
        """Get the current runtime state (0=Uninitialized, 1=Ready, 2=Loaded, 3=Running, 4=Failed)."""
        lib = _get_lib()
        return lib.nyrqis_nyruntime_state(self._ptr)

    def destroy(self) -> None:
        """Destroy the runtime instance and free resources."""
        if not self._destroyed:
            lib = _get_lib()
            lib.nyrqis_nyruntime_destroy(self._ptr)
            self._destroyed = True

    def __del__(self):
        self.destroy()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.destroy()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Quick smoke test
    print("Testing NyRuntime Python loader...")
    try:
        with NyRuntime() as rt:
            rt.init()
            print(f"  State: {rt.state}")
            print("  NyRuntime: OK")
    except ImportError as e:
        print(f"  Skipped (crate not built): {e}")
        sys.exit(0)
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)
