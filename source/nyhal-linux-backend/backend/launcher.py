#!/usr/bin/env python3
"""
launcher — In-Namespace Container Launcher for the Nyrqis Linux Backend

This process is the *first thing that runs inside a container's new
namespaces*. ``backend/container.py`` spawns it via ``unshare(1)`` and it:

1. Sets the container hostname (``sethostname`` via ``ctypes`` — no shell,
   closing the shell-interpolation hygiene finding `FIND-BACKEND-004`,
   NPS-022 §4).
2. Hardens the mount namespace against the cgroup v1 ``release_agent``
   escape (best-effort unmount of any cgroup filesystems that expose it,
   closing `FIND-BACKEND-003`, NPS-022 §4 / NPS-017 §4.1).
2b. Brings the loopback interface up (best-effort ``SIOCSIFFLAGS``):
   succeeds in a container with its own network namespace (owned by this
   user namespace, where the process is root), harmlessly EPERMs when
   sharing the host's. Runs BEFORE the seccomp install — backend setup,
   not container behavior.
3. Installs the container's seccomp policy in *its own execution context*
   — the data-plane capability enforcement closing `FIND-BACKEND-002`
   (NPS-017 §4.2).
4. ``execve``s the container's real command.

Because step 3 runs here — inside the container, before any untrusted code
executes — a container process can never bypass the capability policy by
making syscalls directly; the filter is already active.

Security notes (kept honest, per NPC-002 §5.2):

- ``sethostname`` is attempted and failures are logged, not fatal: in a
  user namespace the operation requires CAP_SYS_ADMIN in that namespace,
  which ``unshare --map-root-user`` provides.
- The cgroup unmount is best-effort *defense in depth*: the primary
  hardening for `FIND-BACKEND-003` happens in ``container.py`` (the
  backend never mounts cgroup filesystems into the container, and sets
  ``notify_on_release=0`` on the container's v1 cgroups).
- If seccomp installation fails (e.g. the host kernel was booted with
  ``seccomp=0``), the launcher logs loudly. By default it continues — the
  container still runs, but the backend records that data-plane
  enforcement is not in effect (the conformance statement in
  ``IMPLEMENTATION_STATUS.md`` reflects this). ``--strict-seccomp`` turns
  the failure into a hard error, for hosts where enforcement is mandatory.

Usage (invoked by container.py — not meant for humans):

    python3 launcher.py --hostname NAME --policy-file PATH [--strict-seccomp] [--default-deny] -- CMD [ARGS...]

References:
- NPS-017 §4.1 (Container Primitives), §4.2 (Capability Enforcement)
- NPS-022 §4: FIND-BACKEND-002/003/004
- NPS-001 §5: boot stages (this launcher is the backend equivalent of the
  trusted first process handing control to a container's real command)
"""

import argparse
import ctypes
import fcntl
import json
import logging
import os
import socket
import struct
import sys
from pathlib import Path
from typing import List, Optional

# The launcher always runs from the backend directory (container.py passes
# its absolute path), so the sibling modules are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.seccomp import (  # noqa: E402
    SeccompPolicy,
    SyscallArch,
    build_policy,
    build_allowlist_policy,
    build_program,
    install_filter,
)
from backend import rust_syscalls  # noqa: E402 - ADR-0020 priority #2 FFI loader

logger = logging.getLogger("nyrqis.launcher")


def set_hostname(hostname: str) -> bool:
    """Set the UTS hostname without any shell involvement.

    Routed through the Rust syscalls module (ADR-0020 priority #2) when
    the FFI library is loaded; falls back to the ``ctypes``
    ``sethostname(2)`` / ``prctl(PR_SET_HOSTNAME)`` path otherwise
    (equivalent effect within a UTS namespace). FIND-BACKEND-004: the
    hostname is an argv entry, never shell-interpolated.
    """
    return rust_syscalls.set_hostname(hostname)


def harden_cgroup_mounts() -> int:
    """Best-effort unmount of cgroup filesystems inside the mount namespace.

    Defense in depth for `FIND-BACKEND-003` (NPS-022 §4): a process able to
    write the cgroup v1 ``release_agent`` / ``notify_on_release`` files can
    achieve host-level code execution when its cgroup empties. The backend
    never mounts cgroups into containers; this handles the case where a
    pre-existing mount leaks into a new mount namespace (e.g. a host mount
    shared via CLONE_NEWNS inheritance).

    Returns the number of mounts unmounted.
    """
    mounts = []
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and parts[2].startswith("cgroup"):
                    mounts.append(parts[1])
    except OSError as e:
        logger.warning("could not read /proc/self/mounts: %s", e)
        return 0

    libc = ctypes.CDLL(None, use_errno=True)
    libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.umount2.restype = ctypes.c_int
    # MNT_DETACH = 2: unmount even if busy; we are inside a private mount
    # namespace so this cannot affect the host.
    unmounted = 0
    for mnt in mounts:
        if libc.umount2(mnt.encode(), 2) == 0:
            unmounted += 1
            logger.info("unmounted cgroup mount %s (defense in depth)", mnt)
        else:
            err = ctypes.get_errno()
            logger.debug("could not unmount %s: errno=%d", mnt, err)
    return unmounted


def bring_loopback_up() -> bool:
    """Best-effort: bring the loopback interface up (``SIOCSIFFLAGS``).

    In a container with its own network namespace (``network=True``) the
    netns is owned by the container's user namespace, where this process
    is root — CAP_NET_ADMIN applies and the ioctl succeeds, giving the
    container a usable 127.0.0.1. When the container shares the host
    netns (``network=False``, owned by the init user namespace) the
    ioctl fails with EPERM, which is harmless — the host's lo is already
    up. Never fatal: a container that cannot set the flag simply has no
    loopback. Runs before the seccomp install because it is backend
    setup, not container behavior (a container without network
    capabilities should still get a usable localhost).
    """
    IFF_UP = 0x1
    SIOCGIFFLAGS = 0x8913
    SIOCSIFFLAGS = 0x8914
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as e:
        logger.debug("loopback: cannot open control socket: %s", e)
        return False
    try:
        try:
            flags = struct.unpack(
                "16sH", fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS,
                                     struct.pack("16sH", b"lo", 0))
            )[1]
        except OSError as e:
            logger.debug("loopback: SIOCGIFFLAGS failed: %s", e)
            return False
        if flags & IFF_UP:
            logger.info("loopback already up")
            return True
        fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS,
                    struct.pack("16sH", b"lo", flags | IFF_UP))
        logger.info("loopback brought up (own network namespace)")
        return True
    except OSError as e:
        logger.debug(
            "loopback: could not bring lo up (shared host netns?): %s", e
        )
        return False
    finally:
        sock.close()


def load_capabilities(policy_file: str) -> Optional[list]:
    """Load the capability set from the policy file."""
    try:
        raw = Path(policy_file).read_text(encoding="utf-8")
        data = json.loads(raw)
        return list(data.get("capabilities", []))
    except (OSError, ValueError) as e:
        logger.error("failed to load policy file %s: %s", policy_file, e)
        return None


def apply_seccomp(
    policy_file: str, strict: bool, arch: SyscallArch, default_deny: bool
) -> bool:
    """Install the container's seccomp filter in this execution context."""
    if not policy_file:
        logger.warning("no policy file provided — data-plane enforcement OFF")
        return False

    caps = load_capabilities(policy_file)
    if caps is None:
        if strict:
            sys.exit(4)
        return False

    try:
        if default_deny:
            policy = build_allowlist_policy(caps, arch=arch)
        else:
            policy = build_policy(caps, arch=arch)
    except Exception as e:  # ValueError from the policy builders
        logger.error("failed to build seccomp policy: %s", e)
        if strict:
            sys.exit(4)
        return False

    try:
        program = build_program(policy)
    except Exception as e:
        logger.error("failed to compile seccomp policy: %s", e)
        if strict:
            sys.exit(4)
        return False

    ok, err = install_filter(program)
    if not ok:
        logger.error(
            "data-plane enforcement NOT in effect: seccomp install failed "
            "(errno=%d %s). The backend records this as non-conformant "
            "per NPS-017 §5.1.",
            err,
            os.strerror(err) if err else "",
        )
        if strict:
            sys.exit(4)
        return False

    denied = len(policy.denied_numbers) + len(policy.flag_rule_numbers)
    logger.info("seccomp filter active: %d deny rule(s) for this container", denied)
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Nyrqis container launcher (internal)")
    parser.add_argument("--hostname", default="nyrqis-container")
    parser.add_argument("--policy-file", default="")
    parser.add_argument("--strict-seccomp", action="store_true")
    parser.add_argument(
        "--default-deny",
        action="store_true",
        help="Use the default-deny allowlist posture: only the runtime "
        "baseline plus granted capabilities are allowed; everything else "
        "is refused with EPERM (strictly stronger than the default "
        "default-allow deny model)",
    )
    parser.add_argument("--arch", default=SyscallArch.from_machine().value)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("NYRQIS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Step 1 — hostname, no shell (FIND-BACKEND-004).
    set_hostname(args.hostname)

    # Step 2 — cgroup mount hardening (FIND-BACKEND-003, defense in depth).
    harden_cgroup_mounts()

    # Step 2b — usable loopback (own network namespace, best-effort).
    # Before the seccomp install: backend setup, not container behavior.
    bring_loopback_up()

    # Step 3 — data-plane capability enforcement (FIND-BACKEND-002).
    try:
        arch = SyscallArch(args.arch)
    except ValueError as e:
        logger.error("%s", e)
        return 2
    apply_seccomp(args.policy_file, args.strict_seccomp, arch, args.default_deny)

    # Step 4 — hand control to the container's real command.
    if not args.command:
        logger.error("no command provided")
        return 3
    command = args.command
    if command and command[0] == "--":
        command = command[1:]

    logger.info("exec: %s", " ".join(command))
    try:
        os.execvpe(command[0], command, os.environ.copy())
    except FileNotFoundError:
        logger.error("command not found: %s", command[0])
        return 127
    except Exception as e:  # pragma: no cover - exec failure paths are varied
        logger.error("exec failed: %s", e)
        return 126


if __name__ == "__main__":
    sys.exit(main())
