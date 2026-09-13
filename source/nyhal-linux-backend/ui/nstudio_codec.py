#!/usr/bin/env python3
"""nstudio_codec — FFI loader for the Rust NUI (.nstudio) parse/validate
hot path (ADR-0025; see rust/nyui/README.md).

The UI runtime's import gate: validate a ``.nstudio`` document against
the NUI contract tables before the shell trusts it. The pure-Python
reference floor is ``ui/nstudio.py``; this module is the crate's
``nyrqis_nyui_validate`` ctypes binding. The conformance gate
(``TestNstudioCodecConformance``) forces the floor's validation suite
through this FFI unchanged — the ADR-0020 migration contract.

Contract, mirroring the transport/ipcd/seccomp loaders:

- Search order for the Rust cdylib: ``$NYRQIS_RUST_LIB``, the crate's
  ``target/release/``, then a bare name (honors ``LD_LIBRARY_PATH``).
- ABI-version check against ``MIN_RUST_ABI_VERSION`` (1.0.0).
- On load failure, ``available()`` is False and ``validate()`` raises
  ``BackendUnavailable`` — the conformance gate skips when the crate
  isn't built, unless ``NYRQIS_RUST_FORCE=1``, which turns routing
  failures into errors (the gate's guarantee that every call drives the
  Rust module).
- The Rust module returns 0 on success or a negative status code (see
  rust/nyui/src/lib.rs): ``-1`` invalid UTF-8, ``-2`` malformed JSON,
  ``-3`` unsupported schema version, ``-4`` validation failed, ``-4096``
  internal. The loader maps ``-3`` → ``NstudioVersionError``, ``-4`` →
  ``NstudioValidationError`` (fetching the reason via
  ``nyrqis_nyui_last_error``), and ``-4096`` → ``RuntimeError``.
"""

import ctypes
import logging
import os
from typing import Optional

from ui import nstudio

logger = logging.getLogger(__name__)

MIN_RUST_ABI_VERSION = 0x0001_0000  # nyrqis-nyui 1.0.0

# Status codes (negative i32), mirroring rust/nyui/src/lib.rs.
RUST_ERR_INVALID_UTF8 = -1
RUST_ERR_MALFORMED_JSON = -2
RUST_ERR_VERSION = -3
RUST_ERR_VALIDATION = -4
RUST_ERR_INTERNAL = -4096

ERROR_BUF_SIZE = 4096

_RUST_LIB = None
_RUST_LIB_CHECKED = False


class BackendUnavailable(RuntimeError):
    """The Rust nyui crate could not be loaded."""


def _candidate_paths() -> list:
    env = os.environ.get("NYRQIS_RUST_LIB")
    if env:
        return [env]
    here = os.path.dirname(os.path.abspath(__file__))
    release = os.path.join(here, "..", "rust", "nyui", "target", "release")
    return [
        os.path.join(release, "libnyrqis_nyui.so"),
        os.path.join(release, "libnyrqis_nyui.dylib"),
        os.path.join(release, "nyrqis_nyui.dll"),
        "libnyrqis_nyui.so",
    ]


def _load() -> Optional[ctypes.CDLL]:
    global _RUST_LIB, _RUST_LIB_CHECKED
    if _RUST_LIB_CHECKED:
        return _RUST_LIB

    lib = None
    for path in _candidate_paths():
        try:
            lib = ctypes.CDLL(path)
            break
        except OSError:
            continue
    if lib is None:
        _RUST_LIB_CHECKED = True
        return None

    try:
        version = lib.nyrqis_nyui_version
        version.restype = ctypes.c_uint32
        if version() < MIN_RUST_ABI_VERSION:
            logger.warning(
                "nyrqis_nyui ABI version %#x < required %#x; disabling crate",
                version(), MIN_RUST_ABI_VERSION)
            lib = None
    except AttributeError:
        logger.warning("nyrqis_nyui missing nyrqis_nyui_version symbol")
        lib = None

    if lib is not None:
        lib = _check_registry_parity(lib)

    _RUST_LIB = lib
    _RUST_LIB_CHECKED = True
    return lib


def _check_registry_parity(lib: ctypes.CDLL) -> Optional[ctypes.CDLL]:
    """Disable the crate when its EMBEDDED registry does not match the
    tree's ``nui-api-v1.json``.

    The crate ``include_str!``-es the registry at compile time, so a
    stale cdylib validates documents against an older contract — the
    registry-1.1 ``cornerRadius`` was rejected exactly this way by an
    Aug-build cdylib while the Python floor accepted it. Parity is
    enforced in either direction: an old crate next to a new registry,
    or a new crate next to a downgraded registry. A crate without the
    version symbol predates the check and is disabled too (it cannot
    prove its currency).
    """
    import json as _json

    try:
        symbols = [
            lib.nyrqis_nyui_registry_version,
            lib.nyrqis_nyui_registry_version_len,
        ]
        symbols[0].restype = ctypes.c_void_p
        symbols[1].restype = ctypes.c_size_t
    except AttributeError:
        logger.warning(
            "nyrqis_nyui lacks the registry-version symbols; disabling "
            "crate (cannot prove its embedded registry is current)")
        return None

    try:
        ptr = symbols[0]()
        length = symbols[1]()
        if not ptr or not length:
            raise ValueError("empty registry version from the crate")
        buf = ctypes.string_at(ptr, length)
        crate_registry = buf.decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("nyrqis_nyui registry version unreadable (%s); "
                       "disabling crate", exc)
        return None

    tree_registry = _tree_registry_version()
    if tree_registry is None:
        logger.warning(
            "cannot read the tree's registry version; disabling crate "
            "(parity unverifiable)")
        return None
    if crate_registry != tree_registry:
        logger.warning(
            "nyrqis_nyui embedded registry %s != tree registry %s; "
            "disabling crate (rebuild rust/nyui) — stale cdylibs must "
            "not validate documents against an older contract",
            crate_registry, tree_registry)
        return None
    return lib


def _tree_registry_version() -> Optional[str]:
    """The registryVersion of the tree's ``nui-api-v1.json`` (None if
    unreadable — fail-closed, the same posture as the loader)."""
    import json as _json

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "contracts", "nui-api-v1.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return str(_json.load(fh).get("registryVersion"))
    except (OSError, ValueError) as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return None


def available() -> bool:
    return _load() is not None


def _last_error(lib) -> str:
    try:
        fn = lib.nyrqis_nyui_last_error
        fn.restype = ctypes.c_int32
        fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        buf = ctypes.create_string_buffer(ERROR_BUF_SIZE)
        fn(buf, ERROR_BUF_SIZE)
        return buf.value.decode("utf-8", "replace")
    except (AttributeError, ctypes.ArgumentError):
        return "(unavailable)"


def validate(text: str) -> None:
    """Validate a .nstudio document through the Rust crate. Raises
    ``NstudioVersionError`` / ``NstudioValidationError`` (same hierarchy
    as the floor) or ``BackendUnavailable`` when the crate isn't
    loadable."""
    lib = _load()
    if lib is None:
        if os.environ.get("NYRQIS_RUST_FORCE") == "1":
            raise BackendUnavailable(
                "NYRQIS_RUST_FORCE=1 but the Rust nyui crate is not "
                "available (searched: " + ", ".join(_candidate_paths()) + ")")
        raise BackendUnavailable("Rust nyui crate not available")

    validate_fn = lib.nyrqis_nyui_validate
    validate_fn.restype = ctypes.c_int32
    validate_fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

    payload = text.encode("utf-8")
    status = validate_fn(payload, len(payload))

    if status == 0:
        return
    if status in (RUST_ERR_MALFORMED_JSON, RUST_ERR_VALIDATION):
        # Malformed JSON is a validation-class failure, matching the
        # floor's NstudioValidationError for unparseable input.
        raise nstudio.NstudioValidationError(_last_error(lib) or "validation failed")
    if status == RUST_ERR_VERSION:
        raise nstudio.NstudioVersionError(_last_error(lib) or "unsupported schema version")
    if status == RUST_ERR_INTERNAL:
        raise RuntimeError(f"nyrqis_nyui internal error: {_last_error(lib)}")
    raise RuntimeError(f"nyrqis_nyui validate returned {status}: {_last_error(lib)}")


def inspect_version_rust(text: str) -> dict:
    """Run the Rust half of ``inspect_version`` over a document and
    return the report dict.

    Differential counterpart to ``NyforgeBridge.inspect_version``: the
    classification logic lives in both engines and must agree (the
    stale-cdylib incident is why — engines that silently disagree are
    worse than either alone). Raises ``BackendUnavailable`` when the
    crate isn't loadable; ``NstudioValidationError`` on malformed JSON.
    The report mirrors the Python one minus ``changesSinceOldestRequirement``
    (the change-span entries come from the same validated log on the
    Python side).
    """
    import json as _json

    lib = _load()
    if lib is None:
        raise BackendUnavailable("Rust nyui crate not available")

    try:
        inspect_fn = lib.nyrqis_nyui_inspect_version
    except AttributeError:
        raise BackendUnavailable(
            "nyrqis_nyui lacks nyrqis_nyui_inspect_version — rebuild "
            "rust/nyui")
    inspect_fn.restype = ctypes.c_int32
    inspect_fn.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]

    payload = text.encode("utf-8")
    # Call 1: size the report; call 2: fetch it. Negative statuses map
    # like validate() (malformed JSON = validation class, same floor
    # hierarchy); a positive value from the sizing call is the needed
    # capacity, not an error.
    needed = inspect_fn(payload, len(payload), None, 0)
    if needed == RUST_ERR_MALFORMED_JSON:
        raise nstudio.NstudioValidationError(
            _last_error(lib) or "malformed JSON")
    if needed < 0:
        raise RuntimeError(
            "nyrqis_nyui inspect_version sizing failed: "
            + (_last_error(lib) or str(needed)))
    buf = ctypes.create_string_buffer(needed)
    written = inspect_fn(payload, len(payload), buf, needed)
    if written == RUST_ERR_MALFORMED_JSON:
        raise nstudio.NstudioValidationError(
            _last_error(lib) or "malformed JSON")
    if written < 0:
        raise RuntimeError(
            "nyrqis_nyui inspect_version failed: "
            + (_last_error(lib) or str(written)))
    try:
        return _json.loads(buf.value.decode("utf-8"))
    except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
        raise RuntimeError(f"unparseable inspect report from the crate: {exc}")


def force_reload() -> None:
    """Forget the cached handle (tests that shuffle NYRQIS_RUST_LIB)."""
    global _RUST_LIB, _RUST_LIB_CHECKED
    _RUST_LIB = None
    _RUST_LIB_CHECKED = False


__all__ = [
    "MIN_RUST_ABI_VERSION", "BackendUnavailable",
    "available", "validate", "inspect_version_rust", "force_reload",
]
