#!/usr/bin/env python3
"""
Test Suite for the Nyrqis Linux Backend

Tests the implementation of NPS-017 §4 (Backend Requirements).
Covers container primitives, capability enforcement, IPC, storage, and boot.

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
"""

import errno
import json
import logging
import os
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import (
    Container, ContainerManager, ContainerConfig, ContainerState, ResourceLimits
)
from backend.capability import (
    CapabilityManager, Capability, CapabilityGrant
)
from backend import seccomp
from ipc.core import (
    IPCManager, IPCMessage, IPCMessageType, IPCEndpoint, TokenBucket
)
from fuse.nyfs import (
    NyFSFilesystem, NyFSBlock, NyFSOperations, NyFSError, NyFSMount, _import_fusepy
)
from boot.lifecycle import BootSequence, BootPhase, SecureBootStatus


logging.basicConfig(level=logging.WARNING)


class TestContainerPrimitives(unittest.TestCase):
    """Test NPS-017 §4.1 (Container Primitives)."""
    
    def setUp(self):
        self.manager = ContainerManager(use_cgroups_v2=False)
    
    def test_container_creation(self):
        """Test creating a container."""
        config = ContainerConfig(hostname="test-container")
        container = self.manager.create(config)
        
        self.assertIsNotNone(container.id)
        self.assertEqual(container.state, ContainerState.CREATED)
        self.assertEqual(container.config.hostname, "test-container")
    
    def test_container_state_machine(self):
        """Test container state transitions."""
        config = ContainerConfig()
        container = self.manager.create(config)
        
        # CREATED -> RUNNING
        container.transition_to(ContainerState.RUNNING)
        self.assertEqual(container.state, ContainerState.RUNNING)
        
        # RUNNING -> SUSPENDED
        container.transition_to(ContainerState.SUSPENDED)
        self.assertEqual(container.state, ContainerState.SUSPENDED)
        
        # SUSPENDED -> RUNNING
        container.transition_to(ContainerState.RUNNING)
        self.assertEqual(container.state, ContainerState.RUNNING)
        
        # RUNNING -> TERMINATED
        container.transition_to(ContainerState.TERMINATED)
        self.assertEqual(container.state, ContainerState.TERMINATED)
    
    def test_invalid_state_transition(self):
        """Test that invalid state transitions are rejected."""
        config = ContainerConfig()
        container = self.manager.create(config)
        
        # CREATED -> SUSPENDED is invalid
        with self.assertRaises(ValueError):
            container.transition_to(ContainerState.SUSPENDED)
    
    def test_resource_limits(self):
        """Test resource limit configuration."""
        limits = ResourceLimits(memory_mb=512, pid_limit=128)
        config = ContainerConfig(limits=limits)
        container = self.manager.create(config)
        
        self.assertEqual(container.config.limits.memory_mb, 512)
        self.assertEqual(container.config.limits.pid_limit, 128)


class TestCapabilityEnforcement(unittest.TestCase):
    """Test NPS-017 §4.2 (Capability Enforcement)."""
    
    def setUp(self):
        self.manager = CapabilityManager()
        self.container_id = "test-container-001"
    
    def test_capability_grant(self):
        """Test granting a capability."""
        self.manager.grant_capability(self.container_id, Capability.CAP_FILESYSTEM_READ)
        
        self.assertTrue(
            self.manager.has_capability(self.container_id, Capability.CAP_FILESYSTEM_READ)
        )
    
    def test_capability_revoke(self):
        """Test revoking a capability."""
        self.manager.grant_capability(self.container_id, Capability.CAP_FILESYSTEM_WRITE)
        self.manager.revoke_capability(self.container_id, Capability.CAP_FILESYSTEM_WRITE)
        
        self.assertFalse(
            self.manager.has_capability(self.container_id, Capability.CAP_FILESYSTEM_WRITE)
        )
    
    def test_capability_validation(self):
        """Test capability validation."""
        self.manager.grant_capability(self.container_id, Capability.CAP_GRAPHICS_RENDER)
        
        # Should succeed
        result = self.manager.validate_operation(
            self.container_id, Capability.CAP_GRAPHICS_RENDER
        )
        self.assertTrue(result)
        
        # Should fail
        result = self.manager.validate_operation(
            self.container_id, Capability.CAP_NETWORK_SOCKET
        )
        self.assertFalse(result)
    
    def test_capability_attenuation(self):
        """Test capability transfer with attenuation."""
        source_container = "source-001"
        target_container = "target-001"
        
        self.manager.grant_capability(source_container, Capability.CAP_IPC_SEND)
        
        # Transfer should succeed
        result = self.manager.attenuate_capability(
            source_container, target_container, Capability.CAP_IPC_SEND
        )
        self.assertTrue(result)
        
        # Target should now have the capability
        self.assertTrue(
            self.manager.has_capability(target_container, Capability.CAP_IPC_SEND)
        )
    
    def test_default_capabilities(self):
        """Test default capability set."""
        self.manager.initialize_container(self.container_id)
        
        caps = self.manager.get_capabilities(self.container_id)
        self.assertGreater(len(caps), 0)
        self.assertIn(Capability.CAP_PROCESS_SPAWN, caps)
        self.assertIn(Capability.CAP_FILESYSTEM_READ, caps)


class TestIPCSemantics(unittest.TestCase):
    """Test NPS-017 §4.3 (IPC Semantics)."""
    
    def setUp(self):
        self.manager = IPCManager()
        self.container1 = "container-1"
        self.container2 = "container-2"
        self.ep1 = self.manager.create_endpoint(self.container1, "ep-1")
        self.ep2 = self.manager.create_endpoint(self.container2, "ep-2")
    
    def test_send_receive(self):
        """Test send/receive primitives."""
        payload = b"Hello from container 1"
        
        # Send message
        result = self.manager.send(self.container1, self.ep2.endpoint_id, payload)
        self.assertTrue(result)
        
        # Receive message
        msg = self.manager.receive(self.ep2.endpoint_id, timeout_s=1.0)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.payload, payload)
        self.assertEqual(msg.message_type, IPCMessageType.SEND)
    
    def test_call_reply(self):
        """Test call/reply pattern."""
        import threading
        
        # Start a responder thread
        def responder():
            msg = self.manager.receive(self.ep1.endpoint_id, timeout_s=5.0)
            if msg and msg.message_type == IPCMessageType.CALL:
                self.manager.reply(msg.message_id, b"Reply from service")
        
        thread = threading.Thread(target=responder, daemon=True)
        thread.start()
        
        # Make a call
        reply = self.manager.call(
            self.container2, self.ep1.endpoint_id, b"Request from client",
            timeout_s=2.0
        )
        
        self.assertIsNotNone(reply)
        self.assertEqual(reply.payload, b"Reply from service")
        self.assertEqual(reply.message_type, IPCMessageType.REPLY)
        
        thread.join(timeout=1.0)
    
    def test_notify(self):
        """Test notify primitive."""
        result = self.manager.notify(
            self.container1, self.ep2.endpoint_id, "process_exited"
        )
        self.assertTrue(result)
        
        msg = self.manager.receive(self.ep2.endpoint_id, timeout_s=1.0)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.message_type, IPCMessageType.NOTIFY)
    
    def test_rate_limiting(self):
        """Test token bucket rate limiting."""
        bucket = TokenBucket(bucket_size=5, tokens_per_second=2.0)
        
        # Should succeed initially
        for _ in range(5):
            self.assertTrue(bucket.try_consume())
        
        # Should fail when empty
        self.assertFalse(bucket.try_consume())
        
        # Wait for refill
        time.sleep(0.6)  # Should get at least 1 token
        self.assertTrue(bucket.try_consume())


class TestStorageGuarantees(unittest.TestCase):
    """Test NPS-017 §4.4 (Storage Guarantees)."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fs = NyFSFilesystem(self.temp_dir)
    
    def test_file_creation(self):
        """Test creating files."""
        file_inode = self.fs.create_file("/test.txt")
        
        self.assertIsNotNone(file_inode.inode_number)
        self.assertEqual(file_inode.name, "test.txt")
        self.assertFalse(file_inode.is_directory)
    
    def test_block_write_read(self):
        """Test writing and reading blocks."""
        file_inode = self.fs.create_file("/data.bin")
        test_data = b"Hello, NyFS!"
        
        # Write block
        block = self.fs.write_block(file_inode.inode_number, test_data)
        self.assertIsNotNone(block.checksum)
        
        # Read block back
        read_data = self.fs.read_block(file_inode.inode_number)
        self.assertEqual(read_data, test_data)
    
    def test_checksumming(self):
        """Test data checksumming."""
        file_inode = self.fs.create_file("/checksummed.txt")
        test_data = b"Verify integrity"
        
        block = self.fs.write_block(file_inode.inode_number, test_data)
        
        # Checksum should be computed
        self.assertIsNotNone(block.checksum)
        self.assertEqual(len(block.checksum), 64)  # SHA256 hex digest
    
    def test_snapshots(self):
        """Test snapshot creation and restoration."""
        # Create a file
        file1 = self.fs.create_file("/file1.txt")
        self.fs.write_block(file1.inode_number, b"Original content")
        
        # Create snapshot
        snap_id = self.fs.create_snapshot()
        self.assertIsNotNone(snap_id)
        
        # Modify filesystem
        file2 = self.fs.create_file("/file2.txt")
        self.fs.write_block(file2.inode_number, b"New file")
        
        # Restore snapshot
        self.fs.restore_snapshot(snap_id)
        
        # Should only have original file
        self.assertEqual(len(self.fs.inodes), 2)  # root + file1


class TestBootLifecycle(unittest.TestCase):
    """Test NPS-017 §4.5 (Boot and Lifecycle)."""
    
    def test_boot_phases(self):
        """Test boot sequence phases."""
        boot = BootSequence()
        
        # Initial phase
        self.assertEqual(boot.current_phase, BootPhase.UNINITIALIZED)
        
        # Transition through phases
        boot.transition_to_phase(BootPhase.HARDWARE_INIT, "Testing")
        self.assertEqual(boot.current_phase, BootPhase.HARDWARE_INIT)
        
        boot.transition_to_phase(BootPhase.FIRST_PROCESS, "Testing")
        self.assertEqual(boot.current_phase, BootPhase.FIRST_PROCESS)
    
    def test_milestone_recording(self):
        """Test milestone recording."""
        boot = BootSequence()
        
        boot.record_milestone(
            BootPhase.HARDWARE_INIT,
            "Test Milestone",
            "This is a test",
            success=True
        )
        
        self.assertEqual(len(boot.milestones), 1)
        self.assertTrue(boot.milestones[0].success)


class TestSeccompEnforcement(unittest.TestCase):
    """Test NPS-017 §4.2 data-plane enforcement (FIND-BACKEND-002).

    The policy compiler and BPF simulator are tested here without ever
    invoking the kernel; ``simulate`` evaluates the exact program that
    would be installed in a container's execution context.
    """

    ARCH = seccomp.SyscallArch.X86_64

    def _policy(self, *caps):
        return seccomp.build_policy({c.value for c in caps}, arch=self.ARCH)

    def _decision(self, policy, name, args=None):
        program = seccomp.build_program(policy)
        nr = seccomp._SYSCALLS[self.ARCH][name]
        return seccomp.simulate(program, nr, self.ARCH.audit_arch, args or [])

    def test_program_jumps_in_bounds(self):
        policy = self._policy()
        seccomp.validate_program(seccomp.build_program(policy))

    def test_oversized_jump_rejected(self):
        # A jump offset > 255 would be rejected by the kernel at prctl
        # time; validate_program must fail loudly at compile time instead.
        program = [(0, 0, 0, 0), (0, 300, 0, 0)]
        with self.assertRaises(ValueError):
            seccomp.validate_program(program)

    def test_wrong_arch_is_killed(self):
        policy = self._policy()
        program = seccomp.build_program(policy)
        decision = seccomp.simulate(
            program, 0, seccomp.AUDIT_ARCH_AARCH64
        )
        self.assertEqual(decision, seccomp.SECCOMP_RET_KILL_PROCESS)

    def test_default_container_is_read_only(self):
        # Default capabilities grant filesystem READ, not WRITE.
        default_caps = CapabilityManager().get_default_capabilities()
        policy = self._policy(*default_caps)

        # read-only open is fine... (openat(dirfd, path, flags, mode) —
        # flags live in arg 2; arg 1 is the pathname pointer)
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_RDONLY]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        # ...but any write-capable open is refused with EPERM.
        for flags in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_APPEND):
            decision = self._decision(policy, "openat", [0, 0, flags])
            self.assertEqual(decision, seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM)
        # A read-only open carrying O_CLOEXEC (what ld.so uses at startup)
        # must stay allowed.
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_RDONLY | os.O_CLOEXEC]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        # Read-only DIRECTORY opens (ls, opendir, stat on directories) must
        # stay allowed — O_DIRECTORY (0x10000) is a read-side flag and must
        # never be folded into the write mask (regression guard).
        self.assertEqual(
            self._decision(policy, "openat",
                           [0, 0, os.O_RDONLY | os.O_NONBLOCK |
                            os.O_CLOEXEC | os.O_DIRECTORY]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        # O_TMPFILE creates an unnamed file — a filesystem write whatever
        # the access-mode bits say, so it is denied outright (any mode).
        for flags in (os.O_TMPFILE, os.O_TMPFILE | os.O_RDONLY,
                      os.O_TMPFILE | os.O_WRONLY):
            self.assertEqual(
                self._decision(policy, "openat", [0, 0, flags]),
                seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
            )
        # Filesystem-mutating syscalls are denied outright (unlink's args
        # are irrelevant — the deny is whole-syscall).
        self.assertEqual(
            self._decision(policy, "unlink", [0, 0]),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )
        # openat2 is NOT flag-gated: its flags live behind a pointer that
        # classic BPF cannot dereference, and masking the pointer value is
        # nondeterministic (documented residual gap — IMPLEMENTATION_STATUS).
        self.assertEqual(
            self._decision(policy, "openat2", [0, 0x7F1234567890, 0]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        # Baseline runtime syscalls stay allowed.
        for name in ("read", "write", "close", "exit", "exit_group", "clock_gettime"):
            self.assertEqual(
                self._decision(policy, name), seccomp.SECCOMP_RET_ALLOW
            )

    def test_no_network_without_network_capability(self):
        default_caps = CapabilityManager().get_default_capabilities()
        policy = self._policy(*default_caps)
        self.assertEqual(
            self._decision(policy, "socket"),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )
        self.assertEqual(
            self._decision(policy, "connect"),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )

    def test_network_socket_without_bind(self):
        policy = self._policy(Capability.CAP_NETWORK_SOCKET)
        self.assertEqual(
            self._decision(policy, "socket"), seccomp.SECCOMP_RET_ALLOW
        )
        # Outbound connect allowed; inbound bind/listen still denied
        # without CAP_NETWORK_BIND.
        self.assertEqual(
            self._decision(policy, "connect"), seccomp.SECCOMP_RET_ALLOW
        )
        self.assertEqual(
            self._decision(policy, "bind"),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )

    def test_full_network_grants_bind(self):
        policy = self._policy(
            Capability.CAP_NETWORK_SOCKET, Capability.CAP_NETWORK_BIND
        )
        self.assertEqual(
            self._decision(policy, "bind"), seccomp.SECCOMP_RET_ALLOW
        )
        self.assertEqual(
            self._decision(policy, "listen"), seccomp.SECCOMP_RET_ALLOW
        )

    def test_filesystem_write_capability_grants_mutation(self):
        policy = self._policy(Capability.CAP_FILESYSTEM_WRITE)
        self.assertEqual(
            self._decision(policy, "unlink"), seccomp.SECCOMP_RET_ALLOW
        )
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_WRONLY]),
            seccomp.SECCOMP_RET_ALLOW,
        )

    def test_process_spawn_is_capability_gated(self):
        policy = self._policy(Capability.CAP_FILESYSTEM_READ)
        self.assertEqual(
            self._decision(policy, "clone"),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )
        policy = self._policy(Capability.CAP_PROCESS_SPAWN)
        self.assertEqual(
            self._decision(policy, "clone"), seccomp.SECCOMP_RET_ALLOW
        )

    def test_dangerous_syscalls_always_denied(self):
        # Even a fully-granted container cannot mount, load modules, or
        # ptrace its way out of the container boundary.
        all_caps = set(Capability)
        policy = self._policy(*all_caps)
        for name in ("mount", "init_module", "ptrace", "reboot", "setns"):
            self.assertEqual(
                self._decision(policy, name),
                seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
            )

    def test_aarch64_policy_builds(self):
        arch = seccomp.SyscallArch.AARCH64
        policy = seccomp.build_policy(
            {c.value for c in CapabilityManager().get_default_capabilities()},
            arch=arch,
        )
        program = seccomp.build_program(policy)
        seccomp.validate_program(program)
        self.assertEqual(
            seccomp.simulate(program, seccomp._SYSCALLS[arch]["openat"], arch.audit_arch, [0, 0, os.O_WRONLY]),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )


class TestDefaultDenyAllowlist(unittest.TestCase):
    """Test the default-deny allowlist posture (build_allowlist_policy).

    The filter's default action is EPERM: only the runtime baseline plus
    granted capabilities are allowed; unknown syscalls — including ones
    added to the kernel after compilation — are refused.
    """

    ARCH = seccomp.SyscallArch.X86_64

    def _policy(self, *caps):
        return seccomp.build_allowlist_policy(
            {c.value for c in caps}, arch=self.ARCH
        )

    def _decision(self, policy, name, args=None):
        program = seccomp.build_program(policy)
        nr = seccomp._SYSCALLS[self.ARCH][name]
        return seccomp.simulate(program, nr, self.ARCH.audit_arch, args or [])

    def test_program_jumps_in_bounds(self):
        policy = self._policy()
        seccomp.validate_program(seccomp.build_program(policy))

    def test_unknown_syscall_is_denied(self):
        # io_uring_setup (425) is in neither the baseline nor any grant —
        # the default action must refuse it.
        policy = self._policy(*CapabilityManager().get_default_capabilities())
        program = seccomp.build_program(policy)
        decision = seccomp.simulate(program, 425, self.ARCH.audit_arch)
        self.assertEqual(decision, seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM)

    def test_baseline_runtime_allowed(self):
        policy = self._policy()
        for name in ("read", "write", "close", "brk", "mmap", "mprotect",
                     "getrandom", "statx", "futex", "execve", "exit_group"):
            self.assertEqual(self._decision(policy, name),
                             seccomp.SECCOMP_RET_ALLOW, name)

    def test_readonly_open_allowed_write_denied(self):
        policy = self._policy()
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_RDONLY]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_RDONLY | os.O_CLOEXEC]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        # Read-only directory opens must stay allowed (regression guard).
        self.assertEqual(
            self._decision(policy, "openat",
                           [0, 0, os.O_RDONLY | os.O_NONBLOCK |
                            os.O_CLOEXEC | os.O_DIRECTORY]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        for flags in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_APPEND,
                      os.O_TMPFILE):
            self.assertEqual(
                self._decision(policy, "openat", [0, 0, flags]),
                seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
                hex(flags),
            )

    def test_capability_gated_families(self):
        # Without the grant, mutation/network/spawn are refused by default.
        policy = self._policy()
        for name in ("unlink", "mkdirat", "socket", "bind", "clone"):
            self.assertEqual(self._decision(policy, name),
                             seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM, name)

        # With the grants, they are allowed.
        policy = self._policy(
            Capability.CAP_FILESYSTEM_WRITE,
            Capability.CAP_NETWORK_SOCKET,
            Capability.CAP_NETWORK_BIND,
            Capability.CAP_PROCESS_SPAWN,
        )
        for name in ("unlink", "mkdirat", "socket", "bind", "clone"):
            self.assertEqual(self._decision(policy, name),
                             seccomp.SECCOMP_RET_ALLOW, name)
        # Write capability also unlocks write-capable openat.
        self.assertEqual(
            self._decision(policy, "openat", [0, 0, os.O_WRONLY]),
            seccomp.SECCOMP_RET_ALLOW,
        )

    def test_always_deny_names_excluded_from_baseline(self):
        # Deny-wins semantics: nothing on the always-deny list may also be
        # allowed by the baseline (a future edit could otherwise widen it).
        baseline = set(seccomp._BASELINE_ALLOW)
        self.assertTrue(baseline.isdisjoint(set(seccomp._ALWAYS_DENY)))

    def test_baseline_names_resolve_on_x86_64(self):
        # Every baseline entry must resolve to a real syscall number on
        # x86_64 — otherwise a typo'd or missing entry is silently skipped
        # by allow() and never caught (fail-safe, but dead intent).
        unresolved = [n for n in seccomp._BASELINE_ALLOW
                      if seccomp._SYSCALLS[self.ARCH].get(n) is None]
        self.assertEqual(unresolved, [])

    def test_openat2_documented_gap(self):
        # openat2 is allowed outright in allowlist mode (cBPF cannot
        # inspect open_how behind the pointer; glibc hard-fails on EPERM
        # instead of falling back). Documented residual gap.
        policy = self._policy()
        self.assertEqual(
            self._decision(policy, "openat2", [0, 0x7F1234567890, 0]),
            seccomp.SECCOMP_RET_ALLOW,
        )

    def test_aarch64_allowlist_builds(self):
        arch = seccomp.SyscallArch.AARCH64
        policy = seccomp.build_allowlist_policy(
            {c.value for c in CapabilityManager().get_default_capabilities()},
            arch=arch,
        )
        program = seccomp.build_program(policy)
        seccomp.validate_program(program)
        self.assertEqual(
            seccomp.simulate(program, seccomp._SYSCALLS[arch]["openat"],
                             arch.audit_arch, [0, 0, os.O_WRONLY]),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )
        self.assertEqual(
            seccomp.simulate(program, seccomp._SYSCALLS[arch]["read"],
                             arch.audit_arch),
            seccomp.SECCOMP_RET_ALLOW,
        )


class TestLauncherSecurity(unittest.TestCase):
    """Test container launch safety (FIND-BACKEND-004) and cgroup
    hardening (FIND-BACKEND-003).
    """

    def setUp(self):
        self.manager = ContainerManager(use_cgroups_v2=False)

    def test_launch_command_has_no_shell_interpolation(self):
        # The hostname and command are argv entries — never interpolated
        # into a shell string (FIND-BACKEND-004).
        config = ContainerConfig(
            hostname="evil; rm -rf /",
            command=["/bin/sh", "-c", "echo hi"],
            capabilities=["CAP_FILESYSTEM_READ"],
        )
        container = self.manager.create(config)
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))

        self.assertIn("launcher.py", cmd)
        self.assertIn("--hostname", cmd)
        self.assertEqual(cmd[cmd.index("--hostname") + 1], config.hostname)
        # The launcher invocation itself is argv-direct — no shell wrapper
        # that could interpret the hostname's metacharacters (the container's
        # own command legitimately contains 'sh -c' after the second '--').
        first_sep = cmd.index("--")
        second_sep = cmd.index("--", first_sep + 1)
        launcher_part = cmd[first_sep + 1:second_sep]
        self.assertNotIn("sh", launcher_part)
        self.assertNotIn("-c", launcher_part)
        # The container's real command survives verbatim as separate args.
        self.assertEqual(cmd[second_sep + 1:], config.command)

    def test_policy_file_written_for_seccomp(self):
        config = ContainerConfig(
            capabilities=["CAP_FILESYSTEM_WRITE", "CAP_GRAPHICS_RENDER"],
            seccomp=True,
        )
        container = self.manager.create(config)
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))

        idx = cmd.index("--policy-file")
        policy_path = Path(cmd[idx + 1])
        self.assertTrue(policy_path.exists())
        self.assertEqual(stat_module.S_IMODE(os.stat(policy_path).st_mode), 0o600)

        data = json.loads(policy_path.read_text())
        self.assertIn("CAP_FILESYSTEM_WRITE", data["capabilities"])
        self.assertIn("CAP_GRAPHICS_RENDER", data["capabilities"])
        self.manager._cleanup_policy_files()
        self.assertFalse(policy_path.exists())

    def test_no_policy_file_when_seccomp_disabled(self):
        config = ContainerConfig(seccomp=False)
        container = self.manager.create(config)
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))
        self.assertNotIn("--policy-file", cmd)

    def test_default_capabilities_used_when_unspecified(self):
        container = self.manager.create(ContainerConfig())
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))
        idx = cmd.index("--policy-file")
        data = json.loads(Path(cmd[idx + 1]).read_text())
        defaults = {c.value for c in CapabilityManager().get_default_capabilities()}
        self.assertEqual(set(data["capabilities"]), defaults)
        self.manager._cleanup_policy_files()

    def test_cgroup_v1_plan_hardens_release_agent(self):
        # FIND-BACKEND-003: the v1 plan must never leave the container
        # cgroup able to trigger the release_agent mechanism.
        config = ContainerConfig(limits=ResourceLimits(memory_mb=128, pid_limit=16))
        container = self.manager.create(config)
        plan = self.manager._cgroup_v1_plan(container)

        memory_settings = None
        for cgroup_path, settings in plan:
            if "memory.limit_in_bytes" in settings:
                memory_settings = settings
        self.assertIsNotNone(memory_settings)
        self.assertEqual(memory_settings["notify_on_release"], "0")

    def test_require_cgroups_v2_refuses_v1_fallback(self):
        with mock.patch.object(
            ContainerManager, "_detect_cgroups_v2", return_value=False
        ):
            with self.assertRaises(RuntimeError):
                ContainerManager(use_cgroups_v2=True, require_cgroups_v2=True)


class TestBootSecurity(unittest.TestCase):
    """Test NPS-001 §5 transition validation (FIND-BOOT-002) and NPS-017
    §4.5 Secure Boot status reporting (FIND-BOOT-001).
    """

    def test_legal_transition_chain(self):
        boot = BootSequence()
        for phase in (
            BootPhase.HARDWARE_INIT,
            BootPhase.FIRST_PROCESS,
            BootPhase.SERVICE_BRINGUP,
            BootPhase.USABLE_SESSION,
            BootPhase.SHUTDOWN,
        ):
            boot.transition_to_phase(phase)
        self.assertEqual(boot.current_phase, BootPhase.SHUTDOWN)

    def test_out_of_order_transition_rejected(self):
        boot = BootSequence()
        boot.transition_to_phase(BootPhase.HARDWARE_INIT)
        boot.transition_to_phase(BootPhase.FIRST_PROCESS)
        # Skipping Service Bring-up to Usable Session is out of order.
        with self.assertRaises(ValueError):
            boot.transition_to_phase(BootPhase.USABLE_SESSION)

    def test_restart_resets_sequence(self):
        boot = BootSequence()
        boot.transition_to_phase(BootPhase.HARDWARE_INIT)
        boot.restart()
        self.assertEqual(boot.current_phase, BootPhase.UNINITIALIZED)
        boot.transition_to_phase(BootPhase.HARDWARE_INIT)

    def test_secure_boot_status_from_efivars(self):
        boot = BootSequence()
        with mock.patch.object(
            BootSequence, "_probe_efi_vars", return_value=(True, "efivars")
        ), mock.patch.object(
            BootSequence, "_probe_mokutil", return_value=None
        ):
            status = boot.secure_boot_status()
        self.assertIsInstance(status, SecureBootStatus)
        self.assertTrue(status.detected)
        self.assertTrue(status.enabled)
        self.assertEqual(status.source, "efivars")

    def test_secure_boot_status_unknown(self):
        boot = BootSequence()
        with mock.patch.object(
            BootSequence, "_probe_efi_vars", return_value=None
        ), mock.patch.object(
            BootSequence, "_probe_mokutil", return_value=None
        ):
            status = boot.secure_boot_status()
        self.assertFalse(status.detected)
        self.assertIsNone(status.enabled)
        self.assertEqual(status.source, "none")

    def test_efi_var_parsing(self):
        # 4 bytes of EFI attributes + 1 byte value (1 = enabled).
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(
            Path, "read_bytes", return_value=b"\x07\x00\x00\x00\x01"
        ):
            result = BootSequence._probe_efi_vars()
        self.assertEqual(result, (True, "efivars"))


class TestNyFSPathAPI(unittest.TestCase):
    """Test the NyFS path-based storage API (NPS-004 §4, ADR-0016)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fs = NyFSFilesystem(self.temp_dir)

    def test_tree_and_resolution(self):
        self.fs.mkdir("/games")
        self.fs.mkdir("/games/saves")
        file_inode = self.fs.create_file("/games/saves/quick.sav")

        self.assertEqual(self.fs.resolve("/games/saves/quick.sav").inode_number,
                         file_inode.inode_number)
        self.assertTrue(self.fs.resolve("/games").is_directory)
        with self.assertRaises(NyFSError) as ctx:
            self.fs.resolve("/nope")
        self.assertEqual(ctx.exception.errno, errno.ENOENT)

    def test_readdir_entries(self):
        self.fs.mkdir("/a")
        self.fs.create_file("/a/f1.txt")
        self.fs.create_file("/a/f2.txt")
        entries = self.fs.readdir("/a")
        self.assertEqual(entries, [".", "..", "f1.txt", "f2.txt"])

    def test_getattr_shape(self):
        self.fs.create_file("/x.txt", mode=0o644)
        st = self.fs.getattr("/x.txt")
        for key in ("st_ino", "st_mode", "st_nlink", "st_size", "st_uid",
                    "st_gid", "st_atime", "st_mtime", "st_ctime"):
            self.assertIn(key, st)
        self.assertEqual(st["st_mode"] & 0o7777, 0o644)

    def test_offset_write_and_read(self):
        f = self.fs.create_file("/log.txt")
        self.fs.write(f, b"hello")
        self.fs.write(f, b" world", offset=5)
        self.assertEqual(self.fs.read(f), b"hello world")
        self.assertEqual(self.fs.read(f, size=5), b"hello")
        self.assertEqual(self.fs.read(f, size=100, offset=6), b"world")

    def test_truncate(self):
        f = self.fs.create_file("/t.txt")
        self.fs.write(f, b"0123456789")
        self.fs.truncate(f, 5)
        self.assertEqual(self.fs.read(f), b"01234")
        self.fs.truncate(f, 8)
        self.assertEqual(self.fs.read(f), b"01234\x00\x00\x00")

    def test_rename_unlink_rmdir(self):
        self.fs.mkdir("/d")
        self.fs.create_file("/d/f.txt")
        self.fs.rename("/d/f.txt", "/d/g.txt")
        self.assertEqual(self.fs.readdir("/d"), [".", "..", "g.txt"])

        self.fs.unlink("/d/g.txt")
        self.assertEqual(self.fs.readdir("/d"), [".", ".."])
        self.fs.rmdir("/d")

        self.fs.mkdir("/e")
        self.fs.create_file("/e/f.txt")
        with self.assertRaises(NyFSError) as ctx:
            self.fs.rmdir("/e")
        self.assertEqual(ctx.exception.errno, errno.ENOTEMPTY)

    def test_snapshot_immutability_with_cow_write(self):
        f = self.fs.create_file("/save.sav")
        self.fs.write(f, b"v1")
        snap = self.fs.create_snapshot()

        # A write must not mutate the snapshot (CoW).
        self.fs.write(f, b"v2")
        self.assertEqual(self.fs.read(f), b"v2")

        self.fs.restore_snapshot(snap)
        restored = self.fs.resolve("/save.sav")
        self.assertEqual(self.fs.read(restored), b"v1")

    def test_checksum_detects_corruption(self):
        f = self.fs.create_file("/c.txt")
        self.fs.write(f, b"integrity")
        block = f.blocks[0]
        block.checksum = "0" * 64  # tamper
        with self.assertRaises(ValueError):
            self.fs.read_block(f.inode_number)

    def test_checksum_detects_corruption_on_path_read(self):
        # NPS-004 4.3: silent corruption must be detected on read, not
        # only via the legacy read_block API.
        f = self.fs.create_file("/c2.txt")
        self.fs.write(f, b"integrity")
        f.blocks[0].checksum = "0" * 64  # tamper
        with self.assertRaises(ValueError):
            self.fs.read(f)

    def test_multi_block_write_splits_into_fixed_blocks(self):
        bs = self.fs.block_size
        f = self.fs.create_file("/big.bin")
        payload = b"x" * (2 * bs + 100)  # spans 3 blocks
        self.fs.write(f, payload)

        self.assertEqual(len(f.blocks), 3)
        self.assertEqual(f.size, len(payload))
        # Every block is exactly block_size bytes (no padding leaks).
        self.assertTrue(all(len(b.decompress()) == bs for b in f.blocks))
        self.assertEqual(self.fs.read(f), payload)

    def test_partial_write_rewrites_only_touched_blocks(self):
        # Per-block CoW: a write in the middle of a multi-block file must
        # carry untouched blocks over by reference (no whole-file
        # recompression) — the benchmark-identified cost driver.
        bs = self.fs.block_size
        f = self.fs.create_file("/cow.bin")
        self.fs.write(f, b"A" * (3 * bs))
        before = list(f.blocks)

        # Overwrite a single byte inside block 1.
        self.fs.write(f, b"X", offset=bs + 10)

        self.assertEqual(len(f.blocks), 3)
        # Blocks 0 and 2 are untouched, carried over by reference.
        self.assertIs(f.blocks[0], before[0])
        self.assertIs(f.blocks[2], before[2])
        # Block 1 was rebuilt (new object, new data).
        self.assertIsNot(f.blocks[1], before[1])
        data = self.fs.read(f)
        self.assertEqual(data[bs + 10:bs + 11], b"X")
        self.assertEqual(data[:bs], b"A" * bs)

    def test_boundary_spanning_writes_truncate_and_extend(self):
        # Regression: writes that straddle a block boundary, truncation
        # to a boundary, append at that boundary, and zero-extension must
        # all compose without losing or misplacing bytes.
        fs = NyFSFilesystem(self.temp_dir, block_size=4096)
        f = fs.create_file("/boundary.bin")

        fs.write(f, b"A" * 4090)
        fs.write(f, b"XY" * 10, offset=4088)  # spans blocks 0/1
        exp = b"A" * 4088 + b"XY" * 10
        self.assertEqual(fs.read(f), exp)

        fs.truncate(f, 4096)
        self.assertEqual(fs.read(f), exp[:4096])

        fs.write(f, b"TAIL", offset=4096)
        self.assertEqual(fs.read(f), exp[:4096] + b"TAIL")
        self.assertEqual(fs.read(f, 100, 4088), exp[4088:4096] + b"TAIL")

        fs.truncate(f, 5000)
        # exp[:4096] + TAIL is 4100 bytes; 5000 - 4100 = 900 zero bytes.
        self.assertEqual(fs.read(f), exp[:4096] + b"TAIL" + b"\x00" * 900)

    def test_legacy_write_block_mixes_with_path_api(self):
        # The legacy write_block appends arbitrary-size blocks; the path
        # API must re-block (normalize) before operating on such an inode
        # so block-indexed reads/writes stay aligned.
        f = self.fs.create_file("/legacy.bin")
        self.fs.write_block(f.inode_number, b"AB")   # 2-byte block
        self.fs.write_block(f.inode_number, b"CDE")  # 3-byte block
        self.assertEqual(self.fs.read(f), b"ABCDE")
        self.fs.write(f, b"XY", offset=2)
        self.assertEqual(self.fs.read(f), b"ABXYE")
        self.assertEqual(self.fs.read(f, 3, 2), b"XYE")

    def test_past_eof_write_zero_fills_gap_blocks(self):
        fs = NyFSFilesystem(self.temp_dir, block_size=4096)
        f = fs.create_file("/sparse.bin")
        fs.write(f, b"X" * 10, offset=5000)
        data = fs.read(f)
        self.assertEqual(len(data), 5010)
        self.assertEqual(data[:100], b"\x00" * 100)
        self.assertEqual(data[-10:], b"X" * 10)

    def test_snapshot_keeps_old_blocks_after_multi_block_write(self):
        bs = self.fs.block_size
        f = self.fs.create_file("/snap.bin")
        self.fs.write(f, b"B" * (2 * bs))
        snap = self.fs.create_snapshot()

        self.fs.write(f, b"C" * (2 * bs))
        self.assertEqual(self.fs.read(f), b"C" * (2 * bs))

        self.fs.restore_snapshot(snap)
        # Restore swaps the inode table, so re-resolve the path rather
        # than holding a pre-restore inode reference.
        restored = self.fs.resolve("/snap.bin")
        self.assertEqual(self.fs.read(restored), b"B" * (2 * bs))


class TestNyFSPersistence(unittest.TestCase):
    """Test NyFS durability (NPS-004 §7): save/load, crash atomicity,
    and corruption handling."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.base = os.path.join(self.temp_dir, "fs")

    def test_save_load_roundtrip(self):
        fs = NyFSFilesystem(self.base, block_size=4096)
        fs.mkdir("/games")
        f = fs.create_file("/games/save.sav")
        payload = b"hello " * 2000  # 12000 bytes
        fs.write(f, payload)
        fs.write(f, b"world", offset=10000)  # overwrite 5 bytes in place
        fs.save()
        del fs

        fs2 = NyFSFilesystem.load(self.base)
        restored = fs2.resolve("/games/save.sav")
        data = fs2.read(restored)
        self.assertEqual(len(data), len(payload))
        self.assertEqual(data[:10000], payload[:10000])
        self.assertEqual(data[10000:10005], b"world")
        self.assertEqual(data[10005:], payload[10005:])
        self.assertTrue(fs2.resolve("/games").is_directory)

    def test_snapshots_survive_roundtrip(self):
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/s.sav")
        fs.write(f, b"v1")
        snap = fs.create_snapshot()
        fs.write(f, b"v2")
        fs.save()
        del fs

        fs2 = NyFSFilesystem.load(self.base)
        self.assertIn(snap, fs2.list_snapshots())
        fs2.restore_snapshot(snap)
        restored = fs2.resolve("/s.sav")
        self.assertEqual(fs2.read(restored), b"v1")

    def test_load_missing_metadata_raises(self):
        with self.assertRaises(NyFSError):
            NyFSFilesystem.load(self.base)

    def test_corrupt_metadata_raises(self):
        fs = NyFSFilesystem(self.base)
        f = fs.create_file("/x.txt")
        fs.write(f, b"data")
        fs.save()
        del fs
        meta = os.path.join(self.base, "state", "metadata.json")
        with open(meta, "w") as fh:
            fh.write("{ not valid json")
        with self.assertRaises(NyFSError):
            NyFSFilesystem.load(self.base)

    def test_crash_mid_save_leaves_last_committed_state(self):
        # Crash atomicity: a failure between block writes and the
        # metadata swap must leave the PREVIOUS committed state loadable
        # (never a mixed one). Simulated by making the metadata rename
        # fail inside the save path.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/c.sav")
        fs.write(f, b"committed-v1")
        fs.save()

        fs.write(f, b"committed-v2")
        with mock.patch(
            "fuse.nyfs.os.replace",
            side_effect=OSError("simulated crash mid-commit"),
        ):
            with self.assertRaises(OSError):
                fs.save()

        # The on-disk state is still v1 — the failed save must not have
        # corrupted it.
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.sav")), b"committed-v1")

    def test_tampered_block_file_detected_on_read(self):
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/t.bin")
        fs.write(f, b"integrity-check")
        fs.save()
        block_id = f.blocks[0].block_id
        del fs

        block_path = os.path.join(
            self.base, "state", "blocks", f"{block_id}.bin")
        with open(block_path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x00\x00\x00\x00")

        fs2 = NyFSFilesystem.load(self.base)
        with self.assertRaises(ValueError):
            fs2.read(fs2.resolve("/t.bin"))

    def test_resave_is_idempotent(self):
        # Blocks are immutable (CoW), so re-saving an unchanged
        # filesystem must not rewrite any block file (reviewer-flagged:
        # random-UUID block IDs could otherwise churn the block store).
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/idem.bin")
        fs.write(f, b"data" * 5000)
        fs.save()
        blocks_dir = os.path.join(self.base, "state", "blocks")
        first = {
            p: os.path.getmtime(os.path.join(blocks_dir, p))
            for p in os.listdir(blocks_dir)
        }
        time.sleep(0.01)
        fs.save()
        second = os.listdir(blocks_dir)
        self.assertEqual(sorted(first), sorted(second))
        for name in second:
            self.assertEqual(
                os.path.getmtime(os.path.join(blocks_dir, name)),
                first[name],
            )

    def test_gc_removes_orphaned_blocks(self):
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/g.bin")
        fs.write(f, b"a" * 100)
        old_id = f.blocks[0].block_id
        snap = fs.create_snapshot()   # pins the 'a' block
        fs.write(f, b"b" * 100)       # CoW: new block for the live state
        fs.save()
        # With the snapshot holding the old block, gc removes nothing yet.
        self.assertEqual(fs.gc_blocks(), 0)
        # Dropping the snapshot orphans the old block; gc reclaims it.
        del fs.snapshots[snap]
        self.assertGreaterEqual(fs.gc_blocks(), 1)
        self.assertFalse(
            os.path.exists(os.path.join(
                self.base, "state", "blocks", f"{old_id}.bin")))

    def test_batched_fsync_save_roundtrip(self):
        # Grouped-fsync save (all temps written, then all fsynced, then
        # all renamed) must produce the same loadable state as the
        # default interleaved path — content, size, and snapshots.
        fs = NyFSFilesystem(self.base, block_size=4096)
        fs.mkdir("/games")
        f = fs.create_file("/games/save.sav")
        payload = b"batched-" * 4000  # 32000 bytes, 8 blocks
        fs.write(f, payload)
        snap = fs.create_snapshot()
        fs.write(f, b"v2-tail", offset=1000)
        fs.save(batched_fsync=True)
        del fs

        fs2 = NyFSFilesystem.load(self.base)
        restored = fs2.resolve("/games/save.sav")
        # v2-tail is 7 bytes, overwriting payload[1000:1007].
        self.assertEqual(fs2.read(restored), payload[:1000] + b"v2-tail"
                         + payload[1007:])
        self.assertIn(snap, fs2.list_snapshots())
        fs2.restore_snapshot(snap)
        self.assertEqual(fs2.read(fs2.resolve("/games/save.sav")), payload)

    def test_batched_fsync_leaves_no_temp_files(self):
        # The grouped path publishes every temp via rename; none may be
        # left behind, and the blocks dir holds exactly the live blocks.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/t.bin")
        fs.write(f, b"data" * 3000)
        fs.save(batched_fsync=True)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        names = os.listdir(blocks_dir)
        self.assertEqual([n for n in names if n.endswith(".tmp")], [])
        self.assertEqual(len(names), len(f.blocks))
        # Re-save with the grouped path is a no-op on block files too.
        before = sorted(names)
        fs.save(batched_fsync=True)
        self.assertEqual(sorted(os.listdir(blocks_dir)), before)

    def test_batched_fsync_crash_mid_save_leaves_old_state(self):
        # Same crash-atomicity contract as the default path: a failure
        # before the metadata swap leaves the previous committed state
        # loadable, even though the grouped path delays renames. The
        # crash is injected mid rename-phase (some new block files
        # renamed, others still temps) — the strongest ordering claim:
        # partially-published blocks must stay invisible because the old
        # metadata references only old, present blocks.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/c.sav")
        fs.write(f, b"A" * 20000)  # 5 blocks
        fs.save()

        fs.write(f, b"B" * 20000)  # 5 new CoW blocks
        calls = {"n": 0}

        def _fail_after_two_renames(src, dst):
            # Real os.replace for the first two (fuse.nyfs.os.replace is
            # the mocked one), then crash: blocks 1-2 published, the rest
            # stuck as temps, metadata never swapped.
            calls["n"] += 1
            if calls["n"] > 2:
                raise OSError("simulated crash mid rename-phase")
            return os.replace(src, dst)

        with mock.patch("fuse.nyfs.os.replace",
                        side_effect=_fail_after_two_renames):
            with self.assertRaises(OSError):
                fs.save(batched_fsync=True)

        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.sav")), b"A" * 20000)


class TestNyFSOperations(unittest.TestCase):
    """Test the FUSE operation handlers (ADR-0016) without a kernel mount."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.fs = NyFSFilesystem(self.temp_dir)
        self.ops = NyFSOperations(self.fs)

    def test_full_operation_flow(self):
        self.assertEqual(self.ops.mkdir("/app", 0o755), 0)
        self.assertEqual(self.ops.mknod("/app/data.bin", 0o644, 0), 0)
        fh = self.ops.open("/app/data.bin", os.O_WRONLY)
        written = self.ops.write("/app/data.bin", b"payload", 0, fh)
        self.assertEqual(written, len(b"payload"))
        self.ops.release("/app/data.bin", fh)

        st = self.ops.getattr("/app/data.bin")
        self.assertEqual(st["st_size"], len(b"payload"))

        self.assertEqual(self.ops.readdir("/app"), [".", "..", "data.bin"])
        self.assertEqual(self.ops.read("/app/data.bin", 100, 0), b"payload")

        self.assertEqual(self.ops.truncate("/app/data.bin", 3), 0)
        self.assertEqual(self.ops.read("/app/data.bin", 100, 0), b"pay")

        self.assertEqual(self.ops.rename("/app/data.bin", "/app/renamed.bin"), 0)
        self.assertEqual(self.ops.unlink("/app/renamed.bin"), 0)
        self.assertEqual(self.ops.rmdir("/app"), 0)

    def test_missing_path_raises_errno(self):
        with self.assertRaises(NyFSError) as ctx:
            self.ops.getattr("/missing")
        self.assertEqual(ctx.exception.errno, errno.ENOENT)

    def test_statfs_shape(self):
        st = self.ops.statfs("/")
        for key in ("f_bsize", "f_blocks", "f_bfree", "f_files", "f_ffree"):
            self.assertIn(key, st)

    def test_fuse_attach_graceful_without_fusepy(self):
        mount = NyFSMount(self.fs, tempfile.mkdtemp())
        with mock.patch("fuse.nyfs._import_fusepy", return_value=None):
            self.assertFalse(mount.attach())


def _fuse_mount_available() -> bool:
    """True when a live FUSE mount can be attempted on this host."""
    try:
        if not os.path.exists("/dev/fuse"):
            return False
        if shutil.which("fusermount3") is None and shutil.which("fusermount") is None:
            return False
        return _import_fusepy() is not None
    except Exception:
        return False


@unittest.skipUnless(
    _fuse_mount_available(),
    "live FUSE mount unavailable (needs fusepy, /dev/fuse, and fusermount)",
)
class TestNyFSLiveMount(unittest.TestCase):
    """End-to-end NyFS through a real kernel FUSE mount.

    Requires a host with fusepy + /dev/fuse + fusermount (present on
    this dev host; skipped elsewhere). Exercises the full stack — kernel
    FUSE path -> NyFSOperations -> NyFSFilesystem — including the fsync
    durability hook (NPS-004 §7) and CoW snapshots across
    unmount/reload/re-mount (verified on 2026-08-12; see
    BENCHMARK_RESULTS.md §6).
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.backing = os.path.join(self.temp_dir, "fs")
        self.mnt = os.path.join(self.temp_dir, "mnt")
        self.mounts = []

    def tearDown(self):
        for m in self.mounts:
            try:
                m.unmount()
            except Exception:
                pass
        try:
            subprocess.run(
                ["fusermount3", "-u", self.mnt], capture_output=True, timeout=5)
        except Exception:
            pass

    def _mount(self, fs):
        m = NyFSMount(fs, self.mnt)
        self.assertTrue(m.mount(foreground=True, blocking=False))
        self.mounts.append(m)
        self.assertTrue(m.wait_ready(timeout=5.0), "mount never became live")
        return m

    def test_fsync_durability_and_snapshot_roundtrip_through_mount(self):
        # Hard cap: a hung FUSE request must fail loudly, not hang CI.
        signal.alarm(90)
        try:
            self._run_mount_e2e()
        finally:
            signal.alarm(0)

    def _run_mount_e2e(self):
        fs = NyFSFilesystem(self.backing)
        m = self._mount(fs)

        # Multi-block write through the kernel path, then fsync -> save().
        os.makedirs(os.path.join(self.mnt, "games"))
        payload = b"level1-data" * 30000  # 330000 bytes, several 64 KiB blocks
        path = os.path.join(self.mnt, "games", "save.sav")
        with open(path, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())

        # The FUSE fsync handler must have committed on-disk state.
        state = os.path.join(self.backing, "state")
        self.assertTrue(os.path.exists(os.path.join(state, "metadata.json")))
        self.assertGreater(len(os.listdir(os.path.join(state, "blocks"))), 0)

        # CoW snapshot, overwrite through the mount, commit again.
        snap = fs.create_snapshot()
        with open(path, "r+b") as fh:
            fh.seek(0)
            fh.write(b"LEVEL2!")
            fh.flush()
            os.fsync(fh.fileno())
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(len(b"LEVEL2!")), b"LEVEL2!")
        # Timestamps must be float seconds, not 1970 (regression: fusepy's
        # use_ns flag would misinterpret them as nanoseconds).
        st = os.stat(path)
        self.assertGreater(st.st_mtime, 1700000000, "mount timestamps broken")

        # Unmount, reload from disk: committed content + snapshot present.
        m.unmount()
        fs2 = NyFSFilesystem.load(self.backing)
        f = fs2.resolve("/games/save.sav")
        d = fs2.read(f)
        self.assertEqual(d[:7], b"LEVEL2!")
        self.assertEqual(len(d), len(payload))
        self.assertIn(snap, fs2.list_snapshots())
        fs2.restore_snapshot(snap)
        self.assertEqual(fs2.read(fs2.resolve("/games/save.sav")), payload)

        # Re-mount the reloaded state and read through the kernel path.
        m2 = self._mount(fs2)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), payload)
        m2.unmount()

    def test_random_overwrites_through_mount_with_writeback_cache(self):
        # Writeback caching lets the kernel batch and reorder dirty-page
        # writes; overlapping random writes must still land correctly
        # after fsync (deterministic seed, ~600 KiB of writes).
        fs = NyFSFilesystem(self.backing)
        self._mount(fs)
        path = os.path.join(self.mnt, "rand.bin")
        rng = __import__("random").Random(20260812)
        expected = bytearray(4 * 1024 * 1024)
        max_written = 0
        with open(path, "wb") as fh:
            for _ in range(150):
                off = rng.randrange(0, len(expected) - 4096, 4096)
                size = rng.randrange(1, 4096)
                chunk = bytes(rng.randrange(256) for _ in range(size))
                fh.seek(off)
                fh.write(chunk)
                expected[off:off + size] = chunk
                max_written = max(max_written, off + size)
            fh.flush()
            os.fsync(fh.fileno())
        # The file is as large as the furthest write end (never-written
        # zero regions beyond it are not part of the file).
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), bytes(expected[:max_written]))
        # The on-disk committed state matches after reload too.
        fs.save()
        fs2 = NyFSFilesystem.load(self.backing)
        self.assertEqual(
            fs2.read(fs2.resolve("/rand.bin")), bytes(expected[:max_written]))

    def test_truncate_and_write_ordering_under_writeback_cache(self):
        # Writeback caching's classic hazard: truncate (shrink + extend)
        # interleaved with writes around dirty pages. Both orderings
        # must survive fsync and reload.
        fs = NyFSFilesystem(self.backing)
        self._mount(fs)
        path = os.path.join(self.mnt, "trunc.bin")

        # Shrink, then write straddling the new EOF boundary.
        with open(path, "wb") as fh:
            fh.write(b"A" * (128 * 1024))
        os.truncate(path, 4096)
        with open(path, "r+b") as fh:
            fh.seek(4090)
            fh.write(b"XY" * 5)  # ends 10 bytes past the truncated size
            fh.flush()
            os.fsync(fh.fileno())
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), b"A" * 4090 + b"XY" * 5)

        # Extend via truncate, then write into the zero gap.
        os.truncate(path, 8 * 1024)
        with open(path, "r+b") as fh:
            fh.seek(6000)
            fh.write(b"GAP")
            fh.flush()
            os.fsync(fh.fileno())
        with open(path, "rb") as fh:
            d = fh.read()
        self.assertEqual(d[:4100], b"A" * 4090 + b"XY" * 5)
        self.assertEqual(d[6000:6003], b"GAP")
        self.assertEqual(len(d), 8 * 1024)

        # Committed state matches after reload.
        fs.save()
        fs2 = NyFSFilesystem.load(self.backing)
        self.assertEqual(fs2.read(fs2.resolve("/trunc.bin")), d)


class TestNyFSSnapshotDiff(unittest.TestCase):
    """Test snapshot diffing: added/removed/modified detection."""

    def setUp(self):
        self.fs = NyFSFilesystem(tempfile.mkdtemp())

    def _diff_map(self, changes):
        return {c["path"]: c for c in changes}

    def test_diff_of_snapshot_against_itself_is_empty(self):
        f = self.fs.create_file("/a.txt")
        self.fs.write(f, b"data")
        snap = self.fs.create_snapshot()
        self.assertEqual(self.fs.diff_snapshots(snap, snap), [])
        self.assertEqual(self.fs.diff_live(snap), [])

    def test_detects_add_remove_and_modify(self):
        a = self.fs.create_file("/a.txt")
        b = self.fs.create_file("/b.txt")
        self.fs.write(a, b"version-one")
        self.fs.write(b, b"keep")
        self.fs.mkdir("/d")
        s1 = self.fs.create_snapshot()

        # Modify a.txt (different content), drop b.txt, add c.txt.
        self.fs.write(a, b"version-two")
        self.fs.unlink("/b.txt")
        c = self.fs.create_file("/c.txt")
        self.fs.write(c, b"new")
        s2 = self.fs.create_snapshot()

        diff = self._diff_map(self.fs.diff_snapshots(s1, s2))
        self.assertEqual(diff["/a.txt"]["change"], "modified")
        self.assertEqual(diff["/a.txt"]["size_before"], len(b"version-one"))
        self.assertEqual(diff["/a.txt"]["size_after"], len(b"version-two"))
        self.assertEqual(diff["/b.txt"]["change"], "removed")
        self.assertEqual(diff["/c.txt"]["change"], "added")
        self.assertNotIn("/d", diff)  # unchanged directory: not reported
        # Direction is from A to B: reversing swaps added/removed.
        rev = self._diff_map(self.fs.diff_snapshots(s2, s1))
        self.assertEqual(rev["/b.txt"]["change"], "added")
        self.assertEqual(rev["/c.txt"]["change"], "removed")

    def test_identical_content_is_not_reported_modified(self):
        f = self.fs.create_file("/same.txt")
        self.fs.write(f, b"payload" * 5000)
        s1 = self.fs.create_snapshot()
        # Rewriting identical bytes creates NEW blocks (different UUIDs);
        # the diff must still see no change via checksum lists.
        self.fs.write(f, b"payload" * 5000)
        s2 = self.fs.create_snapshot()
        self.assertEqual(self.fs.diff_snapshots(s1, s2), [])

    def test_diff_live_reports_uncommitted_changes(self):
        f = self.fs.create_file("/live.txt")
        self.fs.write(f, b"before")
        snap = self.fs.create_snapshot()
        self.fs.write(f, b"after")
        diff = self._diff_map(self.fs.diff_live(snap))
        self.assertEqual(diff["/live.txt"]["change"], "modified")
        # b"after" overwrites the first 5 of 6 bytes: size stays 6.
        self.assertEqual(diff["/live.txt"]["size_after"], len(b"before"))

    def test_diff_missing_snapshot_raises(self):
        with self.assertRaises(ValueError):
            self.fs.diff_snapshots("nope", "also-nope")
        with self.assertRaises(ValueError):
            self.fs.diff_live("nope")

    def test_diff_detects_added_directory(self):
        self.fs.create_snapshot()
        self.fs.mkdir("/newdir")
        snap = self.fs.create_snapshot()
        changes = self.fs.diff_snapshots(self.fs.list_snapshots()[0], snap)
        added = [c for c in changes if c["path"] == "/newdir"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["kind"], "directory")
        self.assertEqual(added[0]["change"], "added")

    def test_partial_vs_padded_final_block_not_reported_modified(self):
        # Reviewer-flagged: truncate-shrink produces an unpadded partial
        # final block; identical content across padded/partial
        # representations must not report a change.
        f = self.fs.create_file("/t.txt")
        self.fs.write(f, b"hello")     # padded 64 KiB block, size 5
        s1 = self.fs.create_snapshot()
        self.fs.truncate(f, 5000)       # padded re-block
        self.fs.truncate(f, 5)          # partial 5-byte block
        s2 = self.fs.create_snapshot()
        self.assertEqual(self.fs.diff_snapshots(s1, s2), [])

    def test_legacy_block_boundaries_not_reported_modified(self):
        # Reviewer-flagged: legacy write_block appends arbitrary-size
        # blocks; re-blocking to one padded block with identical bytes
        # must not report a change.
        f = self.fs.create_file("/leg.bin")
        payload = b"y" * 100
        self.fs.write_block(f.inode_number, payload[:40])
        self.fs.write_block(f.inode_number, payload[40:])
        s1 = self.fs.create_snapshot()   # two blocks (40 + 60 bytes)
        self.fs.write(f, payload)        # path API re-blocks to padded
        self.fs.truncate(f, 100)         # partial 100-byte block
        s2 = self.fs.create_snapshot()
        self.assertEqual(self.fs.diff_snapshots(s1, s2), [])

    def test_nested_paths_are_reported(self):
        self.fs.mkdir("/games")
        f = self.fs.create_file("/games/save.sav")
        self.fs.write(f, b"v1")
        s1 = self.fs.create_snapshot()
        self.fs.write(f, b"v2")
        s2 = self.fs.create_snapshot()
        diff = self._diff_map(self.fs.diff_snapshots(s1, s2))
        self.assertEqual(diff["/games/save.sav"]["change"], "modified")
        self.assertNotIn("/games", diff)  # unchanged dir: not reported


class TestConformance(unittest.TestCase):
    """Test overall conformance to NPS-017 §5."""
    
    def test_backend_contract_coverage(self):
        """Verify all backend contract requirements are addressed."""
        # Container Primitives (§4.1)
        manager = ContainerManager()
        config = ContainerConfig()
        container = manager.create(config)
        self.assertIsNotNone(container)
        
        # Capability Enforcement (§4.2)
        cap_mgr = CapabilityManager()
        cap_mgr.grant_capability("test", Capability.CAP_FILESYSTEM_READ)
        self.assertTrue(cap_mgr.has_capability("test", Capability.CAP_FILESYSTEM_READ))
        
        # IPC Semantics (§4.3)
        ipc_mgr = IPCManager()
        ep = ipc_mgr.create_endpoint("test")
        self.assertIsNotNone(ep)
        
        # Storage Guarantees (§4.4)
        fs = NyFSFilesystem(tempfile.mkdtemp())
        file_inode = fs.create_file("/test.txt")
        self.assertIsNotNone(file_inode)
        
        # Boot and Lifecycle (§4.5)
        boot = BootSequence()
        self.assertEqual(boot.current_phase, BootPhase.UNINITIALIZED)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitives))
    suite.addTests(loader.loadTestsFromTestCase(TestCapabilityEnforcement))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCSemantics))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageGuarantees))
    suite.addTests(loader.loadTestsFromTestCase(TestBootLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestSeccompEnforcement))
    suite.addTests(loader.loadTestsFromTestCase(TestDefaultDenyAllowlist))
    suite.addTests(loader.loadTestsFromTestCase(TestLauncherSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestBootSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSPathAPI))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSPersistence))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSOperations))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSLiveMount))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSSnapshotDiff))
    suite.addTests(loader.loadTestsFromTestCase(TestConformance))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
