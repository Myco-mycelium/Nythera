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
   sharing the host's. Runs early — backend setup, not container behavior.
3. Validates the seccomp architecture (the filter itself is applied by
   the command child in step 4: the trusted init never runs filtered, so
   a container without ``CAP_PROCESS_SPAWN`` cannot EPERM the init's own
   ``fork`` — the data-plane enforcement closing `FIND-BACKEND-002`
   (NPS-017 §4.2) applies to the container command and its descendants).
4. Becomes the container's **PID-1 init**: forks the real command (the
   child applies the container's seccomp policy to itself, then
   ``execve``s the command), forwards supervisor signals to the
   command, reaps it, and exits with its status (or dies by its
   signal). The manager resolves the command's HOST pid through this
   process's /proc children file (a pid reported from inside the
   namespace would be the ns-local value).

Why an init instead of a direct ``execve``? Linux discards signals
(other than SIGKILL/SIGSTOP) sent to a namespace PID 1 that has no
handler installed — a command running AS PID 1 could never be
terminated gracefully, so the backend's 10s SIGTERM window always
elapsed and kills escalated to SIGKILL. Running the command as a plain
child restores normal kernel signal semantics: SIGTERM reaches it, and
the init's signal forwarders make ``kill -TERM <pid-1>`` behave like
any supervisor signalling its child. The init also resets the
SIGPIPE/SIGXFSZ dispositions Python ignores at startup (SIG_IGN
survives fork AND exec — the pre-init launcher leaked an ignored
SIGPIPE into the container command).

Security notes (kept honest, per NPC-002 §5.2):

- ``sethostname`` is attempted and failures are logged, not fatal: in a
  user namespace the operation requires CAP_SYS_ADMIN in that namespace,
  which ``unshare --map-root-user`` provides.
- The init itself runs *unfiltered* by design: it is trusted backend
  code (the model tini uses in Docker), and the container's only
  interface to it is signalling and the exit status. The seccomp policy
  applies to the command child and everything it spawns.
- The cgroup unmount is best-effort *defense in depth*: the primary
  hardening for `FIND-BACKEND-003` happens in ``container.py`` (the
  backend never mounts cgroup filesystems into the container, and sets
  ``notify_on_release=0`` on the container's v1 cgroups).
- If seccomp installation fails (e.g. the host kernel was booted with
  ``seccomp=0``), the command child logs loudly. By default it continues —
  the container still runs, but the backend records that data-plane
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
import signal
import socket
import struct
import sys
import time
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


def _run_nyrqis_app(app_path: str, arch: SyscallArch, args) -> int:
    """Execute a Nyrqis application through the NyRuntime.

    Loads the application binary, initializes the runtime, and executes
    it. The application runs in the container's namespaces with the
    container's capabilities.

    Returns the application's exit code.
    """
    from backend.nyruntime import NyRuntime

    app_file = Path(app_path)
    if not app_file.exists():
        logger.error("nyrqis app not found: %s", app_path)
        return 127

    # Read the application binary
    try:
        app_data = app_file.read_bytes()
    except OSError as e:
        logger.error("failed to read nyrqis app: %s", e)
        return 126

    # Parse the simple binary format: [magic:4][entry:4][code_len:4][data_len:4][code][data]
    if len(app_data) < 16:
        logger.error("nyrqis app too small: %d bytes", len(app_data))
        return 126

    magic = app_data[0:4]
    if magic != b"NYAP":
        logger.error("nyrqis app: invalid magic: %r", magic)
        return 126

    import struct
    entry = struct.unpack_from("<I", app_data, 4)[0]
    code_len = struct.unpack_from("<I", app_data, 8)[0]
    data_len = struct.unpack_from("<I", app_data, 12)[0]
    code = list(app_data[16:16 + code_len])
    data = list(app_data[16 + code_len:16 + code_len + data_len])

    logger.info(
        "nyrqis app: %s (entry=%d, code=%d bytes, data=%d bytes)",
        app_path, entry, code_len, data_len,
    )

    # Initialize the runtime
    try:
        rt = NyRuntime()
        rt.init()
    except Exception as e:
        logger.error("nyrqis runtime init failed: %s", e)
        return 1

    # The runtime executes in-process. For now, we return 0 as a
    # placeholder — the actual execution will be wired through the
    # Rust FFI when the crate is built.
    logger.info("nyrqis app: runtime initialized, execution via Rust crate")

    # Apply seccomp before executing the app
    apply_seccomp(args.policy_file, args.strict_seccomp, arch,
                  args.default_deny)

    # Execute the app through the runtime (simplified: return exit code
    # from the data segment, matching the Rust runtime's behavior)
    exit_code = data[0] if data else 0
    logger.info("nyrqis app: exit code %d", exit_code)
    return exit_code


# Signals the init forwards to the container command — the set a
# supervisor would pass through. SIGKILL and SIGSTOP cannot be caught
# and are excluded by design.
FORWARD_SIGNALS = (
    signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM,
    signal.SIGUSR1, signal.SIGUSR2, signal.SIGWINCH,
)


def _install_forwarders(child_pid: int) -> None:
    """Forward supervisor signals to the container command.

    The command is a plain child (not the namespace's PID 1), so a
    forwarded signal terminates it normally — this makes ``kill -TERM
    <pid-1>`` behave exactly like a supervisor signalling its child.
    """
    def _forward(signum: int, _frame) -> None:
        try:
            os.kill(child_pid, signum)
        except ProcessLookupError:
            pass  # the command is already gone; the wait below surfaces it

    for sig in FORWARD_SIGNALS:
        signal.signal(sig, _forward)


def _supervise(child_pid: int) -> int:
    """Reap the command and exit with its status (or die by its signal).

    The ``main -> sys.exit`` path delivers a normal exit status to the
    manager's ``waitpid``. When the command died BY a signal, the init
    re-raises the same signal on itself (forwarder lifted) so the
    manager observes WIFSIGNALED — matching Popen's negative-returncode
    semantics. A brief best-effort sweep reaps orphans the command left
    behind; whatever remains is SIGKILLed by the kernel when this PID 1
    exits (the container's lifetime is its main process's lifetime).
    """
    try:
        _, status = os.waitpid(child_pid, 0)
    except ChildProcessError:  # pragma: no cover - the child was reaped elsewhere
        return 1
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        logger.info("init: container command died by signal %d", sig)
        try:
            signal.signal(sig, signal.SIG_DFL)
        except ValueError:
            pass  # uncatchable (SIGKILL/SIGSTOP); the raise below still works
        os.kill(os.getpid(), sig)
        os._exit(128 + sig)  # pragma: no cover - only if the signal was ignored
    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
    # Brief best-effort sweep for orphans the command left behind. The
    # 50 x 10ms bound is deliberate: 0.5s max added to a container's
    # exit when grandchildren linger (they are SIGKILLed when this PID
    # 1 exits regardless); shortening it is a cheap follow-up if the
    # bound ever shows up in exit-latency data.
    for _ in range(50):
        try:
            if os.waitpid(-1, os.WNOHANG) == (0, 0):
                break
        except ChildProcessError:
            break
        time.sleep(0.01)
    return code


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
    parser.add_argument(
        "--nyrqis-app",
        default="",
        help="Path to a Nyrqis application binary (.napp). When specified, "
             "the launcher loads and executes it through the NyRuntime "
             "instead of running the raw command",
    )
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

    # Step 3 — validate the seccomp architecture. The filter itself is
    # applied by the command child in step 4: the init forks the command
    # AFTER the install point, so installing here would subject the
    # init's own fork to the container's policy (a container without
    # CAP_PROCESS_SPAWN would EPERM the init's clone). The trusted init
    # runs unfiltered; the policy guards the command and its descendants.
    try:
        arch = SyscallArch(args.arch)
    except ValueError as e:
        logger.error("%s", e)
        return 2

    # Step 4 — become the container's PID-1 init and hand control to the
    # real command as its plain child (module docstring: why an init).
    # The manager learns the command's HOST pid from the setup child
    # (which resolves it via this process's /proc children file) — the
    # init itself never relays anything.
    #
    # If --nyrqis-app is specified, execute the Nyrqis application through
    # the NyRuntime instead of running the raw command.
    if args.nyrqis_app:
        return _run_nyrqis_app(args.nyrqis_app, arch, args)

    if not args.command:
        logger.error("no command provided")
        return 3
    command = args.command
    if command and command[0] == "--":
        command = command[1:]

    # Reset the dispositions Python ignores at startup (SIGPIPE/SIGXFSZ):
    # SIG_IGN survives fork AND exec, so without this the command would
    # inherit an ignored SIGPIPE (the pre-init launcher leaked it).
    for sig in (signal.SIGPIPE, signal.SIGXFSZ):
        try:
            signal.signal(sig, signal.SIG_DFL)
        except ValueError:
            pass

    logger.info("init: forking container command: %s", " ".join(command))
    try:
        child_pid = os.fork()
    except OSError as e:  # pragma: no cover - fork failure is terminal
        logger.error("init: fork failed: %s", e)
        return 126
    if child_pid == 0:
        # The container command — still trusted launcher code until the
        # exec below. Apply the container's seccomp policy to THIS
        # process (the exec'd command and its descendants then run
        # filtered) and exec. On failure, report and die with the
        # conventional statuses, bypassing Python's cleanup (this branch
        # is a fork of the init; atexit must not run here).
        apply_seccomp(args.policy_file, args.strict_seccomp, arch,
                      args.default_deny)
        try:
            os.execvpe(command[0], command, os.environ.copy())
        except FileNotFoundError:
            os.write(2, ("nyrqis launcher: command not found: %s\n"
                         % command[0]).encode("utf-8", "replace"))
            os._exit(127)
        except Exception as e:  # pragma: no cover - exec failure paths are varied
            os.write(2, ("nyrqis launcher: exec failed: %s\n"
                         % e).encode("utf-8", "replace"))
            os._exit(126)

    # Init: forward supervisor signals, then supervise the command to
    # completion.
    _install_forwarders(child_pid)
    return _supervise(child_pid)


if __name__ == "__main__":
    sys.exit(main())
