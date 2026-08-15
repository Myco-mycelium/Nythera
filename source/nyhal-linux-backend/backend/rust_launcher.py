#!/usr/bin/env python3
"""
Rust launcher-init loader (ADR-0020 — the compiled container PID-1).

Locates the ``nyrqis-launcher`` binary (``rust/launcher/`` — the
compiled launcher-init: hostname, cgroup hardening, loopback, seccomp
install, signal forwarding, reaping) and reports availability. Unlike
the cdylib loaders (``rust_syscalls``, ``container_codec``) there is no
ABI gate to speak of: the binary's argv interface is the contract, and
the Python launcher (``launcher.py``) stays as the fallback when the
binary is absent.

Search order (mirroring the cdylib loaders): ``$NYRQIS_LAUNCHER``
override, the crate's ``target/release/``, then a bare name (honors
``PATH``). ``NYRQIS_LAUNCHER_FORCE=1`` makes a missing binary an error
(``RuntimeError``) instead of a silent fallback — the conformance gate
uses it so a regression in the compiled launcher fails the build.

NOTE: deliberately NOT cached. The cdylib loaders cache because dlopen
is expensive; for a binary path a stat is cheap, and a cached path that
later disappears (a rebuilt tree, a cleaned temp dir) would make every
spawn exec a dead binary (exit 126). ``launcher_path()`` re-stats on
every call — it runs once per spawn at most.
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger("backend.rust_launcher")


def _launcher_candidates() -> List[str]:
    override = os.environ.get("NYRQIS_LAUNCHER")
    if override:
        return [override]
    here = os.path.dirname(os.path.abspath(__file__))
    crate_target = os.path.join(
        here, "..", "rust", "launcher", "target", "release",
        "nyrqis-launcher",
    )
    return [crate_target, "nyrqis-launcher"]


def _force_enabled() -> bool:
    return os.environ.get("NYRQIS_LAUNCHER_FORCE") in ("1", "true", "yes")


def _force_error() -> str:
    return (
        "NYRQIS_LAUNCHER_FORCE=1 but the Rust launcher-init binary is not "
        "available (searched: " + ", ".join(_launcher_candidates()) + ")"
    )


def launcher_path() -> Optional[str]:
    """The Rust launcher-init binary path, or ``None`` when it is not
    available (never raises; a miss means \"use the Python launcher\")."""
    for path in _launcher_candidates():
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    if _force_enabled():
        raise RuntimeError(_force_error())
    return None


def available() -> bool:
    """True when the compiled launcher-init can be used."""
    try:
        return launcher_path() is not None
    except RuntimeError:
        return False


__all__ = ["launcher_path", "available"]
