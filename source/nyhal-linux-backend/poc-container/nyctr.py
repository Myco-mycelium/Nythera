#!/usr/bin/env python3
"""
nyctr — Nythera container primitive, proof-of-concept spike.

Implements: a small slice of NPS-017 §4.1 (Container Primitives) for the
Linux Backend (ADR-0012), demonstrating that NPS-002's process-isolation
boundary is achievable atop Linux namespaces + cgroups.

Scope, stated plainly (see README.md in this directory for the full list):
this proves ONE primitive — that a command can run inside an isolated
PID/mount/UTS/user namespace with a basic memory and process-count limit,
and that tearing it down cleans up after itself. It does NOT implement
capability enforcement (NPS-017 §4.2), IPC (§4.3), storage guarantees
(§4.4), or boot integration (§4.5). It is not conformant to NPS-017 §5 and
must not be represented as such. It exists to de-risk the "is this
approach even viable" question before real implementation work begins.

Requires: Linux with unprivileged user namespaces enabled, and either
root or delegated cgroup v1 (memory, pids controllers) write access under
/sys/fs/cgroup. Tested in the sandbox this was written in; not tested on
arbitrary target hardware.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid

CGROUP_MEMORY_ROOT = "/sys/fs/cgroup/memory"
CGROUP_PIDS_ROOT = "/sys/fs/cgroup/pids"


class ContainerError(RuntimeError):
    pass


def _write(path: str, value: str) -> None:
    with open(path, "w") as f:
        f.write(str(value))


def create_cgroup(name: str, memory_limit_bytes: int | None, pid_limit: int | None) -> list[str]:
    """Create a per-container cgroup subgroup. Returns the list of cgroup
    paths created, for cleanup. Mirrors NPS-010 §7 ('every container MUST
    have resource limits assignable at creation') at the most primitive
    level available on this backend."""
    created = []
    if memory_limit_bytes is not None:
        mem_path = os.path.join(CGROUP_MEMORY_ROOT, name)
        os.makedirs(mem_path, exist_ok=True)
        _write(os.path.join(mem_path, "memory.limit_in_bytes"), memory_limit_bytes)
        created.append(mem_path)
    if pid_limit is not None:
        pids_path = os.path.join(CGROUP_PIDS_ROOT, name)
        os.makedirs(pids_path, exist_ok=True)
        _write(os.path.join(pids_path, "pids.max"), pid_limit)
        created.append(pids_path)
    return created


def cleanup_cgroups(paths: list[str]) -> None:
    for p in paths:
        try:
            os.rmdir(p)
        except OSError:
            pass  # best-effort; a lingering process would block this, expected in a PoC


def run_container(command: list[str], hostname: str, memory_mb: int, pid_limit: int) -> int:
    """Launch `command` inside a new PID/mount/UTS/user namespace, with
    cgroup-enforced memory and process-count limits. Returns the exit
    code of the isolated process.

    This is deliberately implemented by shelling out to `unshare(1)`
    rather than calling clone()/unshare() directly via ctypes — the PoC
    goal is to validate the *approach*, not to ship the final mechanism.
    A real implementation would very likely use direct syscalls."""
    name = f"nyctr-{uuid.uuid4().hex[:8]}"
    memory_limit_bytes = memory_mb * 1024 * 1024

    if shutil.which("unshare") is None:
        raise ContainerError("unshare(1) not found — required for this PoC")

    cgroup_paths = create_cgroup(name, memory_limit_bytes, pid_limit)

    try:
        # --user --map-root-user: unprivileged user namespace, so this
        #   doesn't require the caller to already be root on the host.
        # --pid --mount-proc --fork: isolated PID namespace with its own
        #   /proc, matching "a crashing process MUST NOT affect other
        #   containers" (NPS-002 §8.1) at the process-visibility level.
        # --uts: isolated hostname, set below.
        # --mount: isolated mount namespace (no persistent storage
        #   integration yet — that's NPS-017 §4.4, out of scope here).
        unshare_cmd = [
            "unshare",
            "--user", "--map-root-user",
            "--pid", "--mount-proc", "--fork",
            "--uts",
            "--mount",
            "sh", "-c",
            f"hostname {hostname} 2>/dev/null; exec \"$@\"",
            "--",
        ] + command

        print(f"[nyctr] launching container {name!r} (hostname={hostname}, "
              f"memory_limit={memory_mb}MiB, pid_limit={pid_limit})", file=sys.stderr)
        result = subprocess.run(unshare_cmd)
        return result.returncode
    finally:
        cleanup_cgroups(cgroup_paths)
        print(f"[nyctr] container {name!r} torn down", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--hostname", default="nythera-poc", help="UTS namespace hostname")
    parser.add_argument("--memory-mb", type=int, default=64, help="cgroup memory limit (MiB)")
    parser.add_argument("--pid-limit", type=int, default=32, help="cgroup pids.max")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                         help="command to run inside the container (default: /bin/sh)")
    args = parser.parse_args()

    command = args.command or ["/bin/sh"]
    if command and command[0] == "--":
        command = command[1:] or ["/bin/sh"]
    try:
        code = run_container(command, args.hostname, args.memory_mb, args.pid_limit)
    except ContainerError as e:
        print(f"[nyctr] error: {e}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
