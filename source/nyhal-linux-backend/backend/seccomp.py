#!/usr/bin/env python3
"""
seccomp — Data-Plane Capability Enforcement for the Nythera Linux Backend

Implements the *data-plane* half of NPS-017 §4.2 (Capability Enforcement):

> Data-plane enforcement — a process running inside a container's own
> execution context is prevented, by an OS-level mechanism (seccomp, LSM,
> or equivalent), from directly performing an operation its container
> lacks the capability for, regardless of whether it goes through the
> backend's own API.

Threat-model finding `FIND-BACKEND-002` (NPS-022 §4) found that the Linux
Backend's capability enforcement previously covered exactly one operation
class (IPC send/call via the control plane), leaving direct syscalls
unmediated. This module closes that gap: it turns a container's granted
capability set (NPS-011) into a seccomp-BPF filter that is installed by
`launcher.py` inside the container's own execution context, before the
container's real command is exec'd.

Design (kept deliberately small and auditable):

- ``SeccompPolicy`` — a declarative policy: an architecture, a set of
  denied syscalls (whole-syscall denies), and a set of
  deny-if-any-flag rules (syscalls denied only when an argument carries
  certain flags, e.g. ``openat`` with ``O_WRONLY``).
- ``build_program`` — compiles a policy into raw ``sock_filter``
  instructions (the same format the kernel consumes).
- ``simulate`` — a tiny BPF interpreter over the same instruction format,
  used by the test suite to prove the policy's decisions without ever
  invoking the kernel. It also serves as a debugging tool.
- ``install_filter`` — installs a compiled program via ``prctl``
  (``PR_SET_NO_NEW_PRIVS`` + ``PR_SET_SECCOMP``), using ``ctypes`` so the
  module has no non-stdlib dependency. ``PR_SET_NO_NEW_PRIVS`` makes this
  usable from an unprivileged user namespace.

Honesty note (NPC-002 §5.2, NPS-017 §5.1): seccomp filters here use a
default-allow model with an explicit deny policy derived from
capabilities. A future default-deny allowlist (allow-list what the
container may do) is a strictly stronger posture and is listed as
outstanding work; the deny model already closes the Phase-4 finding's
specific hole (direct syscalls for operations the container lacks the
capability for are refused with EPERM).

Known residual gap (recorded honestly, not half-enforced): ``openat2``
cannot be flag-filtered from classic BPF because its flags are behind a
pointer, so a read-only container can still pass write intent through
``openat2``. The ``openat``/``open`` write-intent rules cover the common
path; ``openat2`` remains a documented limitation (IMPLEMENTATION_STATUS.md).

References:
- NPS-017 §4.2: Capability Enforcement (control-plane + data-plane)
- NPS-011: Capability Registry
- NPS-022 §4 / FIND-BACKEND-002: the finding this module closes
"""

import ctypes
import enum
import logging
import platform
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# seccomp return actions
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000  # | errno
SECCOMP_RET_ALLOW = 0x7FFF0000

EPERM = 1

# prctl(2) constants
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
PR_GET_SECCOMP = 21
SECCOMP_MODE_FILTER = 2

# audit architectures (the `arch` field of seccomp_data, offset 4)
AUDIT_ARCH_X86_64 = 0xC000003E
AUDIT_ARCH_AARCH64 = 0xC00000B7

# seccomp_data field offsets (both x86_64 and aarch64)
OFF_NR = 0
OFF_ARCH = 4
OFF_ARGS = 16  # args[i] at OFF_ARGS + 8*i

# open(2)/openat(2) flag bits used for the filesystem-write rule.
# Values are the real Linux <asm-generic/fcntl.h> constants (hex): the
# octal spellings of these values (0o40 = 32, etc.) do NOT overlap the
# kernel's bits and would let write-capable opens through.
O_ACCMODE = 0x3
O_CREAT = 0x40
O_TRUNC = 0x200
O_APPEND = 0x400
# O_TMPFILE (0x410000 = __O_TMPFILE|O_DIRECTORY) creates an unnamed
# writable file — a filesystem write regardless of the access mode bits,
# so it is denied outright for containers lacking CAP_FILESYSTEM_WRITE.
O_TMPFILE = 0x410000
OPEN_WRITE_MASK = O_ACCMODE | O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE  # 0x410643

# BPF instruction encoding helpers (subset used here)
BPF_LD = 0x00
BPF_W = 0x00
BPF_DW = 0x18
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_JSET = 0x40
BPF_RET = 0x06
BPF_K = 0x00
BPF_ALU = 0x04
BPF_AND = 0x50
BPF_OR = 0x40
BPF_ADD = 0x00


class BpfInsn(ctypes.Structure):
    """struct sock_filter — one BPF instruction, as the kernel sees it."""

    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class BpfProgram(ctypes.Structure):
    """struct sock_fprog — the program container passed to prctl."""

    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(BpfInsn)),
    ]


class SyscallArch(enum.Enum):
    """The architectures the policy compiler understands."""

    X86_64 = "x86_64"
    AARCH64 = "aarch64"

    @property
    def audit_arch(self) -> int:
        return AUDIT_ARCH_X86_64 if self is SyscallArch.X86_64 else AUDIT_ARCH_AARCH64

    @classmethod
    def from_machine(cls, machine: Optional[str] = None) -> "SyscallArch":
        machine = machine or platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            return cls.X86_64
        if machine in ("aarch64", "arm64"):
            return cls.AARCH64
        raise ValueError(
            f"Unsupported architecture {machine!r} — seccomp policies are "
            f"defined for x86_64 and aarch64 only"
        )


# Syscall numbers by name for the two supported architectures.
# Only the syscalls this policy model needs are listed.
_SYSCALLS: Dict[SyscallArch, Dict[str, int]] = {
    SyscallArch.X86_64: {
        # baseline runtime (referenced in tests; not denied)
        "read": 0, "write": 1, "open": 2, "close": 3, "mmap": 9,
        "mprotect": 10, "munmap": 11, "brk": 12, "ioctl": 16,
        "execve": 59, "exit": 60, "wait4": 61, "kill": 62, "uname": 63,
        "getcwd": 79, "chdir": 80, "getpid": 39, "gettid": 186,
        "futex": 202, "clock_gettime": 228, "exit_group": 231,
        "getdents64": 217, "readlink": 89, "readlinkat": 267, "statfs": 137,
        # capability-gated: process spawn
        "clone": 56, "fork": 57, "vfork": 58, "clone3": 435,
        # capability-gated: filesystem mutation
        "unlink": 87, "unlinkat": 263, "rmdir": 84, "rename": 82,
        "renameat": 264, "renameat2": 316, "mkdir": 83, "mkdirat": 258,
        "creat": 85, "truncate": 76, "ftruncate": 77, "chmod": 90,
        "fchmod": 91, "fchmodat": 268, "chown": 92, "fchown": 93,
        "fchownat": 260, "lchown": 94, "utimensat": 280, "link": 86,
        "linkat": 265, "symlink": 88, "symlinkat": 266, "mknod": 133,
        "mknodat": 259, "setxattr": 188, "fsetxattr": 190,
        "removexattr": 197, "fremovexattr": 199, "fallocate": 285,
        # capability-gated: network sockets (outbound / general)
        "socket": 41, "connect": 42, "sendto": 44, "sendmsg": 46,
        "sendmmsg": 307, "recvfrom": 45, "recvmsg": 47, "recvmmsg": 299,
        "shutdown": 48, "setsockopt": 54, "getsockopt": 55,
        "socketpair": 53,
        # capability-gated: network bind/listen (inbound)
        "bind": 49, "listen": 50, "accept": 43, "accept4": 288,
        "getsockname": 51, "getpeername": 52,
        # flag-gated: open/openat write access
        "openat": 257, "openat2": 437,
        # always denied (dangerous regardless of capabilities)
        "mount": 165, "umount2": 166, "pivot_root": 155, "kexec_load": 246,
        "init_module": 175, "finit_module": 313, "delete_module": 176,
        "reboot": 169, "swapon": 167, "swapoff": 168, "sethostname": 170,
        "setdomainname": 171, "ptrace": 101, "process_vm_readv": 310,
        "process_vm_writev": 311, "setns": 308, "open_by_handle_at": 304,
        "name_to_handle_at": 303, "perf_event_open": 298, "bpf": 321,
        "userfaultfd": 323, "kcmp": 312, "chroot": 161,
    },
    SyscallArch.AARCH64: {
        # baseline runtime (referenced in tests; not denied)
        "read": 63, "write": 64, "close": 57, "mmap": 222,
        "mprotect": 226, "munmap": 215, "brk": 214, "ioctl": 29,
        "execve": 221, "exit": 93, "kill": 129, "uname": 160,
        "getcwd": 17, "chdir": 49, "getpid": 172, "gettid": 178,
        "futex": 98, "clock_gettime": 113, "exit_group": 94,
        "getdents64": 61, "readlinkat": 78, "statfs": 43,
        # capability-gated: process spawn
        "clone": 220, "clone3": 435,  # arm64 has no fork/vfork
        # capability-gated: filesystem mutation (*at forms only)
        "unlinkat": 35, "renameat": 38, "renameat2": 276, "mkdirat": 34,
        "truncate": 45, "ftruncate": 46, "fchmod": 52, "fchmodat": 53,
        "fchown": 55, "fchownat": 54, "utimensat": 88, "linkat": 37,
        "symlinkat": 36, "mknodat": 33, "setxattr": 5, "fsetxattr": 7,
        "removexattr": 14, "fremovexattr": 16, "fallocate": 47,
        # capability-gated: network sockets (outbound / general)
        "socket": 198, "connect": 203, "sendto": 206, "sendmsg": 211,
        "sendmmsg": 269, "recvfrom": 207, "recvmsg": 212, "recvmmsg": 243,
        "shutdown": 210, "setsockopt": 208, "getsockopt": 209,
        "socketpair": 199,
        # capability-gated: network bind/listen (inbound)
        "bind": 200, "listen": 201, "accept": 202, "accept4": 242,
        "getsockname": 204, "getpeername": 205,
        # flag-gated: open/openat write access
        "openat": 56, "openat2": 437,
        # always denied (dangerous regardless of capabilities)
        "mount": 40, "umount2": 39, "pivot_root": 41, "kexec_load": 104,
        "init_module": 105, "finit_module": 273, "delete_module": 106,
        "reboot": 142, "swapon": 224, "swapoff": 225, "sethostname": 161,
        "setdomainname": 162, "process_vm_readv": 270,
        "process_vm_writev": 271, "setns": 268, "open_by_handle_at": 265,
        "name_to_handle_at": 264, "perf_event_open": 241, "bpf": 280,
        "userfaultfd": 282, "kcmp": 272, "ptrace": -1,  # not a syscall on arm64
        "chroot": 51,
    },
}


# Syscalls denied regardless of what capabilities a container holds.
# Rationale per NPS-017 §4.2 / NPS-022 §4: these cross or bypass the
# container boundary entirely (host mounts, module loading, reboot,
# process inspection of other containers, audit evasion).
_ALWAYS_DENY = [
    "mount", "umount2", "pivot_root", "kexec_load", "init_module",
    "finit_module", "delete_module", "reboot", "swapon", "swapoff",
    "sethostname", "setdomainname", "ptrace", "process_vm_readv",
    "process_vm_writev", "setns", "open_by_handle_at", "name_to_handle_at",
    "perf_event_open", "bpf", "userfaultfd", "kcmp", "chroot",
]

# Capability -> syscall families. A container that lacks the capability
# has the listed syscalls denied. Maps the backend's internal capability
# names (backend/capability.py) to the syscall vocabulary.
_FS_WRITE_SYSCALLS = [
    "unlink", "unlinkat", "rmdir", "rename", "renameat", "renameat2",
    "mkdir", "mkdirat", "creat", "truncate", "ftruncate", "chmod",
    "fchmod", "fchmodat", "chown", "fchown", "fchownat", "lchown",
    "utimensat", "link", "linkat", "symlink", "symlinkat", "mknod",
    "mknodat", "setxattr", "fsetxattr", "removexattr", "fremovexattr",
    "fallocate",
]

_NETWORK_GENERAL_SYSCALLS = [
    "socket", "connect", "sendto", "sendmsg", "sendmmsg", "recvfrom",
    "recvmsg", "recvmmsg", "shutdown", "setsockopt", "getsockopt",
    "socketpair",
]

_NETWORK_INBOUND_SYSCALLS = [
    "bind", "listen", "accept", "accept4", "getsockname", "getpeername",
]

_PROCESS_SPAWN_SYSCALLS = ["clone", "fork", "vfork", "clone3"]


class PolicyError(ValueError):
    """Raised for invalid or unbuildable policies."""


@dataclass
class SeccompPolicy:
    """A declarative seccomp policy derived from a capability set.

    ``deny_syscalls`` — syscall names denied unconditionally.
    ``deny_if_any_flags`` — mapping of syscall name -> (arg_index, mask);
    the syscall is denied only when ``(args[arg_index] & mask) != 0``.
    """

    arch: SyscallArch = SyscallArch.X86_64
    deny_syscalls: Set[str] = field(default_factory=set)
    deny_if_any_flags: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    def deny(self, *names: str) -> "SeccompPolicy":
        self.deny_syscalls.update(n for n in names if self._nr(n) is not None)
        return self

    def deny_on_flags(self, name: str, arg_index: int, mask: int) -> "SeccompPolicy":
        if self._nr(name) is None:
            raise PolicyError(f"{name!r} has no syscall number for {self.arch.value}")
        self.deny_if_any_flags[name] = (arg_index, mask)
        return self

    def _nr(self, name: str) -> Optional[int]:
        nr = _SYSCALLS[self.arch].get(name)
        return None if nr is None or nr < 0 else nr

    @property
    def denied_numbers(self) -> List[int]:
        """Resolved syscall numbers for whole-syscall denies."""
        return [self._nr(n) for n in sorted(self.deny_syscalls) if self._nr(n) is not None]

    @property
    def flag_rule_numbers(self) -> List[Tuple[int, int, int]]:
        """Resolved (nr, arg_index, mask) tuples for flag-based denies."""
        rules = []
        for name, (arg_index, mask) in self.deny_if_any_flags.items():
            nr = self._nr(name)
            if nr is not None:
                rules.append((nr, arg_index, mask))
        return rules

    def validate(self) -> None:
        """Ensure the policy is self-consistent before compilation."""
        for name in self.deny_syscalls:
            if self._nr(name) is None:
                raise PolicyError(
                    f"syscall {name!r} is not defined for {self.arch.value}"
                )
        for name, (arg_index, mask) in self.deny_if_any_flags.items():
            if self._nr(name) is None:
                raise PolicyError(
                    f"syscall {name!r} is not defined for {self.arch.value}"
                )
            if not 0 <= arg_index <= 5:
                raise PolicyError(f"arg_index {arg_index} out of range (0..5)")
            if not 0 <= mask <= 0xFFFFFFFF:
                raise PolicyError(f"mask 0x{mask:x} out of 32-bit range")


def build_policy(
    capabilities: Set[str],
    arch: Optional[SyscallArch] = None,
) -> SeccompPolicy:
    """Build a seccomp policy from a set of granted capabilities.

    Args:
        capabilities: The container's granted capability *names* (the
            ``Capability`` enum values from ``backend/capability.py``).
        arch: Target architecture; defaults to the running machine.

    Returns:
        A ``SeccompPolicy`` encoding what the capability set permits.
    """
    policy = SeccompPolicy(arch=arch or SyscallArch.from_machine())
    policy.deny(*_ALWAYS_DENY)

    caps = set(capabilities)

    # Import lazily to avoid a hard dependency between the two modules.
    from backend.capability import Capability

    if Capability.CAP_FILESYSTEM_WRITE.value not in caps:
        policy.deny(*_FS_WRITE_SYSCALLS)
        # openat/open with write intent is denied via flags (read-only open
        # must keep working for a read-only container). Argument indices are
        # the real syscall signatures: open(path, flags, mode) has flags in
        # arg 1; openat(dirfd, path, flags, mode) has flags in arg 2 (arg 1
        # is the pathname pointer, which must never be masked).
        policy.deny_on_flags("openat", 2, OPEN_WRITE_MASK)
        if policy._nr("open") is not None:
            policy.deny_on_flags("open", 1, OPEN_WRITE_MASK)
        # openat2 is deliberately NOT flag-gated: its flags live inside a
        # ``struct open_how`` behind a pointer, and classic BPF cannot
        # dereference memory. Masking the pointer value is nondeterministic
        # (it depends on heap-address bits) and must not be relied on, so
        # the rule is omitted and openat2 is left to the control plane. A
        # write-capable openat2 in a read-only container is a documented
        # residual gap (see IMPLEMENTATION_STATUS.md).

    if Capability.CAP_NETWORK_SOCKET.value not in caps:
        policy.deny(*_NETWORK_GENERAL_SYSCALLS)

    if Capability.CAP_NETWORK_BIND.value not in caps:
        policy.deny(*_NETWORK_INBOUND_SYSCALLS)

    if Capability.CAP_PROCESS_SPAWN.value not in caps:
        policy.deny(*_PROCESS_SPAWN_SYSCALLS)

    return policy


# ---------------------------------------------------------------------------
# Compiler: policy -> raw BPF
# ---------------------------------------------------------------------------

class _Assembler:
    """Tiny assembler with deferred jump targets (label fixups)."""

    def __init__(self) -> None:
        self._ins: List[Tuple[int, int, int, int]] = []
        self._fixups: List[Tuple[int, int, int]] = []  # (idx, target, which)

    def emit(self, code: int, k: int, jt: int = 0, jf: int = 0) -> int:
        idx = len(self._ins)
        self._ins.append((code, jt, jf, k))
        return idx

    def ld_abs(self, offset: int, size: int = BPF_W) -> int:
        return self.emit(BPF_LD | size | BPF_ABS, offset)

    def jeq_k(self, k: int, jt: int = 0, jf: int = 0) -> int:
        return self.emit(BPF_JMP | BPF_JEQ | BPF_K, k, jt, jf)

    def jset_k(self, k: int, jt: int = 0, jf: int = 0) -> int:
        return self.emit(BPF_JMP | BPF_JSET | BPF_K, k, jt, jf)

    def ret_k(self, k: int) -> int:
        return self.emit(BPF_RET | BPF_K, k)

    def alu(self, op: int, k: int) -> int:
        return self.emit(BPF_ALU | op | BPF_K, k)

    def jump_to(self, target_index: int) -> None:
        """Emit a placeholder jt/jf that will be fixed up to target_index."""
        idx = len(self._ins)
        self._ins.append((0, 0, 0, 0))
        self._fixups.append((idx, target_index, 0))

    def result(self) -> List[Tuple[int, int, int, int]]:
        for idx, target, which in self._fixups:
            code, jt, jf, k = self._ins[idx]
            delta = target - idx - 1
            if not 0 <= delta <= 0xFF:
                raise PolicyError(f"jump too far: {delta} (program exceeds BPF limits)")
            if which == 0:
                self._ins[idx] = (code, delta, jf, k)
            else:
                self._ins[idx] = (code, jt, delta, k)
        return self._ins


def build_program(policy: SeccompPolicy) -> List[Tuple[int, int, int, int]]:
    """Compile a ``SeccompPolicy`` into a list of (code, jt, jf, k) tuples.

    Program layout (each flag rule is exactly 4 instructions, so the
    "skip the check block on non-match" jump is a constant offset):

        0:  ld  [4]                     ; arch
        1:  jeq ARCH, jt=1, jf=0        ; match -> skip RET KILL below
        2:  ret KILL_PROCESS            ; wrong arch
        3:  ld  [0]                     ; nr
        4..: jeq nr_i, jt=deny, jf=0    ; whole-syscall denies
             jeq openat, jt=0, jf=4     ; flag rule: match -> check block
             ld  [args[1]]              ;   check: load flags
             and MASK                   ;   mask
             jeq 0, jt=0, jf=deny       ;   masked==0 -> allowed, else deny
             ld  [0]                    ;   restore nr for the remaining chain
             ...
             ret ALLOW                  ; default allow
             ret ERRNO(EPERM)           ; deny target
    """
    policy.validate()
    a = _Assembler()

    a.ld_abs(OFF_ARCH)
    # On arch match skip the kill (jt=1); on mismatch fall into it (jf=0).
    a.jeq_k(policy.arch.audit_arch, jt=1, jf=0)
    a.ret_k(SECCOMP_RET_KILL_PROCESS)
    a.ld_abs(OFF_NR)

    denied = policy.denied_numbers
    flag_rules = policy.flag_rule_numbers

    # Whole-syscall denies: on match jump to the deny target (patched later).
    deny_jeq_indices = []
    for nr in denied:
        deny_jeq_indices.append(a.jeq_k(nr, jt=0, jf=0))

    # Flag rules. Each rule is exactly 4 instructions, so a non-matching nr
    # skips the whole block with jf=4.
    flag_deny_jf_indices = []
    for nr, arg_index, mask in flag_rules:
        a.jeq_k(nr, jt=0, jf=4)
        a.ld_abs(OFF_ARGS + 8 * arg_index)
        a.alu(BPF_AND, mask)
        flag_deny_jf_indices.append(a.jeq_k(0, jt=0, jf=0))  # masked==0 -> allowed
        a.ld_abs(OFF_NR)  # restore nr for the remaining chain

    allow_idx = a.ret_k(SECCOMP_RET_ALLOW)
    deny_idx = a.ret_k(SECCOMP_RET_ERRNO | EPERM)

    # Patch whole-syscall denies: jt -> deny target.
    for idx in deny_jeq_indices:
        code, jt, jf, k = a._ins[idx]
        a._ins[idx] = (code, deny_idx - idx - 1, jf, k)
    # Patch flag-rule jeq(0) instructions: jf -> deny target.
    for idx in flag_deny_jf_indices:
        code, jt, jf, k = a._ins[idx]
        a._ins[idx] = (code, jt, deny_idx - idx - 1, k)

    program = a.result()
    validate_program(program)
    return program


def validate_program(program: List[Tuple[int, int, int, int]]) -> None:
    """Sanity-check that all jump targets stay in bounds (test helper).

    Also enforces the kernel's 8-bit jt/jf field width: a program whose
    jumps exceed 255 instructions would be rejected by ``prctl`` with
    ``EINVAL`` at install time, silently degrading to "no enforcement"
    when the launcher is not running ``--strict-seccomp``. Fail loudly
    here at compile time instead.
    """
    n = len(program)
    for i, (code, jt, jf, k) in enumerate(program):
        if jt > 0xFF or jf > 0xFF:
            raise ValueError(
                f"jump offset exceeds 8-bit BPF limit at instruction {i}: "
                f"jt={jt} jf={jf}"
            )
        for offset in (jt, jf):
            target = i + 1 + offset
            if not 0 <= target <= n:
                raise ValueError(f"jump out of bounds at instruction {i}: {offset}")


# ---------------------------------------------------------------------------
# Simulator (test + debugging; mirrors the kernel's evaluation semantics)
# ---------------------------------------------------------------------------

def _load_word(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        return 0
    return int.from_bytes(data[offset:offset + 4], "little", signed=False)


def simulate(
    program: List[Tuple[int, int, int, int]],
    nr: int,
    arch: int,
    args: Optional[List[int]] = None,
) -> int:
    """Evaluate a compiled program against synthetic ``seccomp_data``.

    Args:
        program: Output of :func:`build_program`.
        nr: Syscall number being evaluated.
        arch: Audit architecture constant.
        args: Up to six argument values.

    Returns:
        The seccomp return action (one of ``SECCOMP_RET_*``).
    """
    args = (args or []) + [0] * 6
    data = bytearray(64)
    data[OFF_NR:OFF_NR + 4] = (nr & 0xFFFFFFFF).to_bytes(4, "little")
    data[OFF_ARCH:OFF_ARCH + 4] = (arch & 0xFFFFFFFF).to_bytes(4, "little")
    for i in range(6):
        off = OFF_ARGS + 8 * i
        data[off:off + 8] = (args[i] & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")

    pc = 0
    reg = 0
    while pc < len(program):
        code, jt, jf, k = program[pc]
        pc += 1
        op = code & 0x07
        if op == BPF_LD:
            src = code & 0x18
            if src == BPF_W:
                reg = _load_word(data, k)
            elif src == BPF_DW:
                reg = int.from_bytes(data[k:k + 8], "little", signed=False)
            else:
                raise ValueError(f"unsupported load mode 0x{src:x}")
        elif op == BPF_JMP:
            # jmp class: K source is bit 3 (0x08); the sub-op lives in
            # bits 6-4 (JEQ=0x10, JSET=0x40).
            if code & 0x08:
                raise ValueError(f"register-source jump unsupported: 0x{code:x}")
            sub = code & 0x70
            if sub == 0x10:  # JEQ
                pc += jt if reg == k else jf
            elif sub == 0x40:  # JSET
                pc += jt if (reg & k) else jf
            else:
                raise ValueError(f"unsupported jump 0x{code:x}")
        elif op == BPF_RET:
            return k
        elif op == BPF_ALU:
            if (code & 0xF0) == BPF_AND:
                reg &= k
            elif (code & 0xF0) == BPF_OR:
                reg |= k
            elif (code & 0xF0) == BPF_ADD:
                reg = (reg + k) & 0xFFFFFFFFFFFFFFFF
            else:
                raise ValueError(f"unsupported alu 0x{code:x}")
        else:
            raise ValueError(f"unsupported instruction 0x{code:x}")
    raise ValueError("program terminated without a RET")


# ---------------------------------------------------------------------------
# Installer: program -> running process
# ---------------------------------------------------------------------------

def install_filter(program: List[Tuple[int, int, int, int]]) -> Tuple[bool, int]:
    """Install a compiled seccomp filter on the *calling thread*.

    Runs ``prctl(PR_SET_NO_NEW_PRIVS, 1)`` then
    ``prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)`` via ``ctypes``.

    Returns:
        ``(True, 0)`` on success, or ``(False, errno)`` if the kernel
        refuses (e.g. seccomp disabled at boot, or already in filter mode
        with a different policy).
    """
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int

    # If we are already in seccomp filter mode, installing another filter
    # is a no-op that would fail; report the current state instead.
    if libc.prctl(PR_GET_SECCOMP) == SECCOMP_MODE_FILTER:
        logger.warning("seccomp filter already active on this thread")
        return True, 0

    # PR_SET_NO_NEW_PRIVS must precede PR_SET_SECCOMP, and must be applied
    # before any execve would otherwise drop privileges.
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        logger.error("PR_SET_NO_NEW_PRIVS failed: errno=%d", err)
        return False, err

    if not program:
        return True, 0

    arr = (BpfInsn * len(program))()
    for i, (code, jt, jf, k) in enumerate(program):
        arr[i] = BpfInsn(code, jt, jf, k)
    prog = BpfProgram(len(program), ctypes.cast(arr, ctypes.POINTER(BpfInsn)))

    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog)) != 0:
        err = ctypes.get_errno()
        logger.error("PR_SET_SECCOMP failed: errno=%d (%s)", err, os_strerror(err))
        return False, err

    logger.info("seccomp filter installed (%d instructions)", len(program))
    return True, 0


def os_strerror(err: int) -> str:
    """errno -> message without importing os at module top (keeps module pure)."""
    import os
    return os.strerror(err) if err else ""


__all__ = [
    "SeccompPolicy", "build_policy", "build_program", "simulate",
    "install_filter", "validate_program", "SyscallArch",
    "SECCOMP_RET_ALLOW", "SECCOMP_RET_ERRNO", "SECCOMP_RET_KILL_PROCESS",
    "EPERM", "OPEN_WRITE_MASK",
]
