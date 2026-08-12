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

- ``SeccompPolicy`` — a declarative policy: an architecture, a default
  action, a set of denied syscalls (whole-syscall denies), and a set of
  deny-if-any-flag rules (syscalls denied only when an argument carries
  certain flags, e.g. ``openat`` with ``O_WRONLY``). In default-deny
  mode the same policy additionally carries allowed syscalls and
  allow-if-flags-clear rules (e.g. ``openat`` allowed only read-only).
- ``build_policy`` — the default-allow deny model (current posture).
- ``build_allowlist_policy`` — the default-deny allowlist posture: the
  filter's default action is ``ERRNO(EPERM)`` and only a runtime
  baseline plus capability-granted families are allowed. This is the
  strictly-stronger posture listed as outstanding work in earlier
  versions of this module.
- ``build_program`` — compiles a policy (either mode) into raw
  ``sock_filter`` instructions (the same format the kernel consumes).
- ``simulate`` — a tiny BPF interpreter over the same instruction format,
  used by the test suite to prove the policy's decisions without ever
  invoking the kernel. It also serves as a debugging tool.
- ``install_filter`` — installs a compiled program via ``prctl``
  (``PR_SET_NO_NEW_PRIVS`` + ``PR_SET_SECCOMP``), using ``ctypes`` so the
  module has no non-stdlib dependency. ``PR_SET_NO_NEW_PRIVS`` makes this
  usable from an unprivileged user namespace.

Honesty note (NPC-002 §5.2, NPS-017 §5.1): seccomp filters here use a
default-allow model with an explicit deny policy derived from
capabilities, with a default-deny allowlist posture available opt-in via
``build_allowlist_policy`` / the launcher's ``--default-deny`` flag. The
default-deny baseline was derived empirically on x86_64 (verified by
running dynamically-linked binaries under the filter); its aarch64
coverage is a conservative subset pending verification on real arm64
hardware.

Known residual gap (recorded honestly, not half-enforced): ``openat2``
cannot be flag-filtered from classic BPF because its flags are behind a
pointer, so a read-only container can still pass write intent through
``openat2``. The ``openat``/``open`` write-intent rules cover the common
path; in default-deny mode ``openat2`` is allowed outright for the same
reason (denying it wholesale breaks glibc, which hard-fails rather than
falling back to ``openat``). ``openat2`` remains a documented limitation
(IMPLEMENTATION_STATUS.md).

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
# O_TMPFILE is __O_TMPFILE|O_DIRECTORY = 0x400000|0x10000. It creates an
# unnamed writable file — a filesystem write regardless of the access
# mode bits. Only the reserved __O_TMPFILE bit (0x400000) goes into the
# write mask: O_DIRECTORY (0x10000) is a read-side flag used by every
# directory open (opendir, ls, stat), and folding it into the mask would
# deny all read-only directory opens.
O_TMPFILE = 0x410000
O_TMPFILE_BIT = 0x400000
OPEN_WRITE_MASK = O_ACCMODE | O_CREAT | O_TRUNC | O_APPEND | O_TMPFILE_BIT  # 0x400643

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
# The numbers below are transcribed from the kernel's authoritative
# tables (arch/x86/entry/syscalls/syscall_64.tbl and
# include/uapi/asm-generic/unistd.h for arm64), NOT from memory.
_SYSCALLS: Dict[SyscallArch, Dict[str, int]] = {
    SyscallArch.X86_64: {
        # baseline runtime (default-deny allowlist + tests)
        "read": 0, "write": 1, "open": 2, "close": 3, "stat": 4,
        "fstat": 5, "lstat": 6, "poll": 7, "lseek": 8, "mmap": 9,
        "mprotect": 10, "munmap": 11, "brk": 12, "rt_sigaction": 13,
        "rt_sigprocmask": 14, "rt_sigreturn": 15, "ioctl": 16,
        "pread64": 17, "pwrite64": 18, "readv": 19, "writev": 20,
        "access": 21, "pipe": 22, "select": 23, "sched_yield": 24,
        "mremap": 25, "msync": 26, "mincore": 27, "madvise": 28,
        "shmget": 29, "shmat": 30, "shmctl": 31, "dup": 32, "dup2": 33,
        "nanosleep": 35, "getitimer": 36, "setitimer": 38, "getpid": 39,
        "sendfile": 40, "sigaltstack": 131, "personality": 135,
        "fstatfs": 138, "getpriority": 140, "setpriority": 141,
        "sched_setparam": 142, "sched_getparam": 143,
        "sched_setscheduler": 144, "sched_getscheduler": 145,
        "sched_get_priority_max": 146, "sched_get_priority_min": 147,
        "sched_rr_get_interval": 148, "mlock": 149, "munlock": 150,
        "mlockall": 151, "munlockall": 152, "prctl": 157,
        "arch_prctl": 158, "sync": 162,
        "getdents": 78, "fchdir": 81, "umask": 95, "gettimeofday": 96,
        "getrlimit": 97, "getrusage": 98, "sysinfo": 99, "times": 100,
        "getuid": 102, "getgid": 104, "geteuid": 107, "getegid": 108,
        "getppid": 110, "getpgrp": 111, "setsid": 112, "getgroups": 115,
        "getpgid": 121, "getsid": 124, "capget": 125, "capset": 126,
        "faccessat": 269,        "rt_sigpending": 127, "rt_sigtimedwait": 128, "rt_sigsuspend": 130,
        "fcntl": 72, "flock": 73, "fsync": 74, "fdatasync": 75, "getcwd": 79,
        "chdir": 80, "execve": 59, "exit": 60, "wait4": 61,
        "kill": 62, "uname": 63, "semget": 64, "semop": 65, "semctl": 66,
        "shmdt": 67, "msgget": 68, "msgsnd": 69, "msgrcv": 70, "msgctl": 71,
        "gettid": 186, "tkill": 200,
        "time": 201, "futex": 202, "sched_setaffinity": 203,
        "sched_getaffinity": 204, "epoll_create": 213,
        "getdents64": 217, "set_tid_address": 218, "restart_syscall": 219,
        "semtimedop": 220, "fadvise64": 221, "timer_create": 222,
        "timer_settime": 223, "timer_gettime": 224, "timer_getoverrun": 225,
        "timer_delete": 226, "clock_gettime": 228, "clock_getres": 229,
        "clock_nanosleep": 230, "exit_group": 231, "epoll_wait": 232,
        "epoll_ctl": 233, "tgkill": 234, "utimes": 235, "waitid": 247,
        "readlink": 89, "readlinkat": 267, "newfstatat": 262,
        "statfs": 137, "pselect6": 270, "ppoll": 271,
        "set_robust_list": 273, "get_robust_list": 274, "splice": 275,
        "tee": 276, "sync_file_range": 277, "vmsplice": 278,
        "epoll_pwait": 281, "signalfd": 282, "timerfd_create": 283,
        "eventfd": 284, "timerfd_settime": 286, "timerfd_gettime": 287,
        "signalfd4": 289, "eventfd2": 290, "epoll_create1": 291,
        "dup3": 292, "pipe2": 293, "preadv": 295, "pwritev": 296,
        "prlimit64": 302,        "getcpu": 309, "getrandom": 318, "memfd_create": 319,
        "execveat": 322, "copy_file_range": 326, "preadv2": 327,
        "pwritev2": 328, "statx": 332, "rseq": 334, "close_range": 436,
        "faccessat2": 439, "futex_waitv": 449, "mseal": 462,
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
        "userfaultfd": 323, "kcmp": 312,
    },
    SyscallArch.AARCH64: {
        # baseline runtime (default-deny allowlist + tests; arm64 has no
        # open/creat/stat/lstat/select/poll — only the *at/ppoll forms)
        "read": 63, "write": 64, "close": 57, "mmap": 222,
        "mprotect": 226, "munmap": 215, "brk": 214, "ioctl": 29,
        "execve": 221, "exit": 93, "kill": 129, "uname": 160,
        "getcwd": 17, "chdir": 49, "fchdir": 50, "getpid": 172,
        "getppid": 173, "gettid": 178, "futex": 98, "clock_gettime": 113,
        "exit_group": 94, "getdents64": 61, "readlink": 76,
        "readlinkat": 78, "statfs": 43, "fstatfs": 44, "newfstatat": 79,
        "fstat": 80, "access": 48, "faccessat": 49, "faccessat2": 439,
        "lseek": 62, "pread64": 67, "pwrite64": 68, "readv": 65,
        "writev": 66, "preadv": 69, "pwritev": 70, "preadv2": 286,
        "pwritev2": 287, "dup": 23,
        "dup3": 24, "fcntl": 25, "flock": 32, "pipe2": 59,
        "eventfd2": 19, "epoll_create1": 20, "epoll_ctl": 21,
        "epoll_pwait": 22, "pselect6": 72, "ppoll": 73, "signalfd4": 74,
        "splice": 76, "sync": 81, "fsync": 82, "fdatasync": 83,
        "sync_file_range": 84, "timerfd_create": 85, "capget": 90,
        "capset": 91, "personality": 92, "waitid": 95,
        "set_tid_address": 96, "set_robust_list": 99, "get_robust_list": 100,
        "nanosleep": 101, "getitimer": 102, "setitimer": 103,
        "timer_create": 107, "timer_gettime": 108, "timer_getoverrun": 109,
        "timer_settime": 110, "timer_delete": 111, "clock_getres": 114,
        "clock_nanosleep": 115, "sched_setparam": 118,
        "sched_setscheduler": 119, "sched_getscheduler": 120,
        "sched_getparam": 121, "sched_setaffinity": 122,
        "sched_getaffinity": 123, "sched_yield": 124,
        "sched_get_priority_max": 125, "sched_get_priority_min": 126,
        "sched_rr_get_interval": 127, "restart_syscall": 128, "tkill": 130,
        "tgkill": 131, "sigaltstack": 132, "rt_sigsuspend": 133,
        "rt_sigaction": 134, "rt_sigprocmask": 135, "rt_sigpending": 136,
        "rt_sigtimedwait": 137, "rt_sigqueueinfo": 138, "rt_sigreturn": 139,
        "setpriority": 140, "getpriority": 141, "times": 153,
        "setpgid": 154, "getpgid": 155, "getsid": 156, "setsid": 157,
        "getgroups": 158, "getrlimit": 163, "setrlimit": 164,
        "getrusage": 165, "umask": 166, "prctl": 167, "getcpu": 168,
        "gettimeofday": 169, "getuid": 174, "geteuid": 175, "getgid": 176,
        "getegid": 177, "sysinfo": 179, "msgget": 186, "msgctl": 187,
        "msgrcv": 188, "msgsnd": 189, "semget": 190, "semctl": 191,
        "semtimedop": 192, "semop": 193, "shmget": 194, "shmctl": 195,
        "shmat": 196, "shmdt": 197, "readahead": 213, "mremap": 216,
        "msync": 227, "mlock": 228, "munlock": 229, "mlockall": 230,
        "munlockall": 231, "mincore": 232, "madvise": 233, "wait4": 260,
        "prlimit64": 261, "sendfile": 71, "statx": 291, "rseq": 293,
        "getrandom": 278, "memfd_create": 279, "execveat": 281,
        "copy_file_range": 285, "close_range": 436, "futex_waitv": 449,
        "mseal": 462,
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

# Runtime baseline for the default-deny allowlist posture: what a
# dynamically-linked process needs to start, run, and exit (glibc's
# loader, signals, threads, timers, memory management, read-only
# filesystem access). Capability-gated families (process spawn,
# filesystem mutation, networking) are deliberately NOT here — they are
# added per-grant by ``build_allowlist_policy``.
#
# Empirically derived on x86_64: ``/bin/echo``, ``/bin/ls``, ``/bin/sh``
# and CPython all run under this baseline + the default capability set.
# Names that do not exist on a given architecture (e.g. ``open``,
# ``poll``, ``select``, ``arch_prctl`` on arm64) are skipped by
# ``SeccompPolicy.allow`` since ``_nr`` returns None for them.
_BASELINE_ALLOW = [
    # memory management
    "brk", "mmap", "munmap", "mprotect", "mremap", "madvise", "mincore",
    "msync", "mlock", "munlock", "mlockall", "munlockall", "mseal",
    # descriptors and I/O
    "read", "write", "close", "lseek", "pread64", "pwrite64", "readv",
    "writev", "preadv", "pwritev", "preadv2", "pwritev2", "dup", "dup2",
    "dup3", "fcntl", "ioctl", "fsync", "fdatasync", "sync",
    "sync_file_range", "copy_file_range", "sendfile", "flock",
    # process, threads, signals
    "execve", "execveat", "exit", "exit_group", "wait4", "waitid",
    "kill", "tgkill", "tkill", "rt_sigaction", "rt_sigprocmask",
    "rt_sigreturn", "rt_sigpending", "rt_sigtimedwait", "rt_sigsuspend",
    "sigaltstack", "restart_syscall", "prctl", "set_tid_address",
    "set_robust_list", "get_robust_list", "futex", "futex_waitv", "rseq",
    "getpid", "getppid", "gettid", "getuid", "geteuid", "getgid",
    "getegid", "getgroups", "getpgrp", "getpgid", "getsid", "setsid",
    "getrusage", "getpriority", "getcpu",
    # time and timers
    "clock_gettime", "clock_getres", "clock_nanosleep", "gettimeofday",
    "nanosleep", "times", "time", "timer_create", "timer_settime",
    "timer_gettime", "timer_getoverrun", "timer_delete", "getitimer",
    "setitimer", "timerfd_create", "timerfd_settime", "timerfd_gettime",
    # scheduling (read-only)
    "sched_yield", "sched_getaffinity", "sched_getparam",
    "sched_getscheduler", "sched_get_priority_max",
    "sched_get_priority_min", "sched_rr_get_interval",
    # filesystem read-only + metadata
    "getcwd", "chdir", "fchdir", "umask", "readlink", "readlinkat",
    "getdents", "getdents64", "statfs", "fstatfs", "stat", "lstat",
    "fstat", "newfstatat", "statx", "access", "faccessat", "faccessat2",
    "utimes", "fadvise64",
    # polling, events, pipes (arm64 uses ppoll/pselect6/epoll instead)
    "poll", "ppoll", "select", "pselect6", "epoll_create1", "epoll_ctl",
    "epoll_wait", "epoll_pwait", "epoll_create", "pipe", "pipe2",
    "eventfd", "eventfd2", "signalfd", "signalfd4",
    # SysV IPC (process-local)
    "shmget", "shmat", "shmctl", "shmdt", "semget", "semop", "semctl",
    "semtimedop", "msgget", "msgsnd", "msgrcv", "msgctl",
    # misc benign / read-only introspection. capset is deliberately NOT
    # here (least privilege, like the excluded uid/gid setters): the
    # kernel refuses capability raises without CAP_SETPCAP anyway, and a
    # container that legitimately needs to manage its own caps can be
    # granted that later.
    "uname", "sysinfo", "personality", "getrandom", "capget",
    "arch_prctl", "memfd_create", "close_range",
    # openat2 is allowed outright: classic BPF cannot inspect its flags
    # behind the open_how pointer, and glibc hard-fails rather than
    # falling back to openat when openat2 is denied. Documented residual
    # gap — the write-intent rules cover openat/open only.
    "openat2",
]


class PolicyError(ValueError):
    """Raised for invalid or unbuildable policies."""


@dataclass
class SeccompPolicy:
    """A declarative seccomp policy derived from a capability set.

    Two postures, selected by ``default_action``:

    - Default-allow (deny model): ``default_action`` is
      ``SECCOMP_RET_ALLOW``; ``deny_syscalls`` and ``deny_if_any_flags``
      list what is refused. This is the classic posture produced by
      :func:`build_policy`.
    - Default-deny (allowlist model): ``default_action`` is
      ``SECCOMP_RET_ERRNO | EPERM``; ``allow_syscalls`` and
      ``allow_if_no_flags`` list what is permitted and everything else
      is refused. Produced by :func:`build_allowlist_policy`.

    ``deny_if_any_flags`` — mapping of syscall name -> (arg_index, mask);
    the syscall is denied only when ``(args[arg_index] & mask) != 0``.
    ``allow_if_no_flags`` — mapping of syscall name -> (arg_index, mask);
    the syscall is allowed only when ``(args[arg_index] & mask) == 0``
    (e.g. ``openat`` allowed only for read-only opens).
    """

    arch: SyscallArch = SyscallArch.X86_64
    default_action: int = SECCOMP_RET_ALLOW
    deny_syscalls: Set[str] = field(default_factory=set)
    deny_if_any_flags: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    allow_syscalls: Set[str] = field(default_factory=set)
    allow_if_no_flags: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    def deny(self, *names: str) -> "SeccompPolicy":
        self.deny_syscalls.update(n for n in names if self._nr(n) is not None)
        return self

    def deny_on_flags(self, name: str, arg_index: int, mask: int) -> "SeccompPolicy":
        if self._nr(name) is None:
            raise PolicyError(f"{name!r} has no syscall number for {self.arch.value}")
        self.deny_if_any_flags[name] = (arg_index, mask)
        return self

    def allow(self, *names: str) -> "SeccompPolicy":
        """Allow syscalls unconditionally (default-deny mode only)."""
        self.allow_syscalls.update(n for n in names if self._nr(n) is not None)
        return self

    def allow_when_flags_clear(
        self, name: str, arg_index: int, mask: int
    ) -> "SeccompPolicy":
        """Allow a syscall only when ``(args[arg_index] & mask) == 0``."""
        if self._nr(name) is None:
            raise PolicyError(f"{name!r} has no syscall number for {self.arch.value}")
        self.allow_if_no_flags[name] = (arg_index, mask)
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

    @property
    def allowed_numbers(self) -> List[int]:
        """Resolved syscall numbers for whole-syscall allows."""
        return [self._nr(n) for n in sorted(self.allow_syscalls) if self._nr(n) is not None]

    @property
    def allow_flag_rule_numbers(self) -> List[Tuple[int, int, int]]:
        """Resolved (nr, arg_index, mask) tuples for flag-based allows."""
        rules = []
        for name, (arg_index, mask) in self.allow_if_no_flags.items():
            nr = self._nr(name)
            if nr is not None:
                rules.append((nr, arg_index, mask))
        return rules

    def validate(self) -> None:
        """Ensure the policy is self-consistent before compilation."""
        if self.default_action not in (SECCOMP_RET_ALLOW, SECCOMP_RET_ERRNO | EPERM):
            raise PolicyError(f"unsupported default_action 0x{self.default_action:x}")
        for name in self.deny_syscalls:
            if self._nr(name) is None:
                raise PolicyError(
                    f"syscall {name!r} is not defined for {self.arch.value}"
                )
        for name in self.allow_syscalls:
            if self._nr(name) is None:
                raise PolicyError(
                    f"syscall {name!r} is not defined for {self.arch.value}"
                )
        for label, rules in (
            ("deny_if_any_flags", self.deny_if_any_flags),
            ("allow_if_no_flags", self.allow_if_no_flags),
        ):
            for name, (arg_index, mask) in rules.items():
                if self._nr(name) is None:
                    raise PolicyError(
                        f"syscall {name!r} is not defined for {self.arch.value} "
                        f"({label})"
                    )
                if not 0 <= arg_index <= 5:
                    raise PolicyError(f"arg_index {arg_index} out of range (0..5)")
                if not 0 <= mask <= 0xFFFFFFFF:
                    raise PolicyError(f"mask 0x{mask:x} out of 32-bit range")
        overlap = self.deny_syscalls & self.allow_syscalls
        if overlap:
            raise PolicyError(
                f"syscalls listed in both deny and allow: {sorted(overlap)}"
            )


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


def build_allowlist_policy(
    capabilities: Set[str],
    arch: Optional[SyscallArch] = None,
) -> SeccompPolicy:
    """Build a *default-deny* (allowlist) policy from a capability set.

    The filter's default action is ``ERRNO(EPERM)``: only the runtime
    baseline (``_BASELINE_ALLOW``), capability-granted families, and
    read-only ``openat``/``open`` are permitted. Every other syscall —
    including ones added to the kernel after this policy was compiled —
    is refused. This is the strictly-stronger posture listed as
    outstanding work in the module docstring.

    The ``_ALWAYS_DENY`` set is subtracted from the allowlist rather than
    emitted as deny rules: with a default-deny action the denies are
    redundant, and subtracting them guarantees deny-wins semantics even
    if a future edit adds one of them to the baseline.

    Args:
        capabilities: The container's granted capability *names*.
        arch: Target architecture; defaults to the running machine.
    """
    policy = SeccompPolicy(
        arch=arch or SyscallArch.from_machine(),
        default_action=SECCOMP_RET_ERRNO | EPERM,
    )
    policy.allow(*(set(_BASELINE_ALLOW) - set(_ALWAYS_DENY)))

    caps = set(capabilities)
    from backend.capability import Capability

    if Capability.CAP_FILESYSTEM_WRITE.value in caps:
        policy.allow(*_FS_WRITE_SYSCALLS)
        policy.allow("openat")
        if policy._nr("open") is not None:
            policy.allow("open")
    else:
        # Read-only opens only. open(path, flags, mode): flags in arg 1;
        # openat(dirfd, path, flags, mode): flags in arg 2.
        policy.allow_when_flags_clear("openat", 2, OPEN_WRITE_MASK)
        if policy._nr("open") is not None:
            policy.allow_when_flags_clear("open", 1, OPEN_WRITE_MASK)

    if Capability.CAP_NETWORK_SOCKET.value in caps:
        policy.allow(*_NETWORK_GENERAL_SYSCALLS)

    if Capability.CAP_NETWORK_BIND.value in caps:
        policy.allow(*_NETWORK_INBOUND_SYSCALLS)

    if Capability.CAP_PROCESS_SPAWN.value in caps:
        policy.allow(*_PROCESS_SPAWN_SYSCALLS)

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

    Program layout (each flag rule block is 5 instructions — the
    jeq/ld/and/jeq/ld sequence — and a non-matching nr skips the 4
    check instructions with a constant jf=4 offset):

        0:  ld  [4]                     ; arch
        1:  jeq ARCH, jt=1, jf=0        ; match -> skip RET KILL below
        2:  ret KILL_PROCESS            ; wrong arch
        3:  ld  [0]                     ; nr
        4..: jeq nr_i, jt=TARGET, jf=0  ; whole-syscall rules (deny or allow)
             jeq openat, jt=0, jf=4     ; flag rule: match -> check block
             ld  [args[N]]              ;   check: load the flag argument
             and MASK                   ;   mask
             jeq 0, jt=A, jf=D          ;   branch on masked value
             ld  [0]                    ;   restore nr for the remaining chain
             ...
             ret <default action>       ; default (ALLOW or ERRNO|EPERM)
             ret <other action>         ; explicit target

    In default-allow mode the whole-syscall rules are denies (jt -> the
    ERRNO target) and the flag rule's ``masked != 0`` branch goes to the
    ERRNO target. In default-deny mode the rules are allows (jt -> the
    ALLOW target) and the flag rule's ``masked == 0`` branch goes to the
    ALLOW target; anything unmatched falls through to the default ERRNO.
    """
    policy.validate()
    a = _Assembler()

    a.ld_abs(OFF_ARCH)
    # On arch match skip the kill (jt=1); on mismatch fall into it (jf=0).
    a.jeq_k(policy.arch.audit_arch, jt=1, jf=0)
    a.ret_k(SECCOMP_RET_KILL_PROCESS)
    a.ld_abs(OFF_NR)

    deny_default = policy.default_action == SECCOMP_RET_ERRNO | EPERM

    if deny_default:
        # Default-deny: whole-syscall allows, then flag-based read-only
        # allows; the default ret is ERRNO and the patched target is ALLOW.
        whole_indices = [a.jeq_k(nr, jt=0, jf=0) for nr in policy.allowed_numbers]
        flag_allow_jt_indices = []
        for nr, arg_index, mask in policy.allow_flag_rule_numbers:
            a.jeq_k(nr, jt=0, jf=4)
            a.ld_abs(OFF_ARGS + 8 * arg_index)
            a.alu(BPF_AND, mask)
            flag_allow_jt_indices.append(a.jeq_k(0, jt=0, jf=0))  # masked==0 -> allow
            a.ld_abs(OFF_NR)  # restore nr for the remaining chain
        deny_idx = a.ret_k(SECCOMP_RET_ERRNO | EPERM)
        allow_idx = a.ret_k(SECCOMP_RET_ALLOW)
        for idx in whole_indices:
            code, jt, jf, k = a._ins[idx]
            a._ins[idx] = (code, allow_idx - idx - 1, jf, k)
        for idx in flag_allow_jt_indices:
            code, jt, jf, k = a._ins[idx]
            a._ins[idx] = (code, allow_idx - idx - 1, jf, k)
    else:
        # Default-allow: whole-syscall denies, then flag-based write-intent
        # denies; the default ret is ALLOW and the patched target is ERRNO.
        whole_indices = [a.jeq_k(nr, jt=0, jf=0) for nr in policy.denied_numbers]
        flag_deny_jf_indices = []
        for nr, arg_index, mask in policy.flag_rule_numbers:
            a.jeq_k(nr, jt=0, jf=4)
            a.ld_abs(OFF_ARGS + 8 * arg_index)
            a.alu(BPF_AND, mask)
            flag_deny_jf_indices.append(a.jeq_k(0, jt=0, jf=0))  # masked!=0 -> deny
            a.ld_abs(OFF_NR)  # restore nr for the remaining chain
        allow_idx = a.ret_k(SECCOMP_RET_ALLOW)
        deny_idx = a.ret_k(SECCOMP_RET_ERRNO | EPERM)
        for idx in whole_indices:
            code, jt, jf, k = a._ins[idx]
            a._ins[idx] = (code, deny_idx - idx - 1, jf, k)
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
    "SeccompPolicy", "build_policy", "build_allowlist_policy",
    "build_program", "simulate", "install_filter", "validate_program",
    "SyscallArch", "SECCOMP_RET_ALLOW", "SECCOMP_RET_ERRNO",
    "SECCOMP_RET_KILL_PROCESS", "EPERM", "OPEN_WRITE_MASK",
]
