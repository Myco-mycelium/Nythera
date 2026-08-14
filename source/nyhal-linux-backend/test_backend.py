#!/usr/bin/env python3
"""
Test Suite for the Nyrqis Linux Backend

Tests the implementation of NPS-017 §4 (Backend Requirements).
Covers container primitives, capability enforcement, IPC, storage, and boot.

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
"""

import ctypes
import errno
import hashlib
import json
import logging
import os
import random
import shutil
import signal
import stat as stat_module
import struct
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
from backend import rust_syscalls
from backend import container_codec
from ipc.core import (
    IPCManager, IPCMessage, IPCMessageType, IPCEndpoint, TokenBucket
)
from ipc.transport import (
    IPCTransportError, UnixDatagramEndpoint, IPCDatagramServer, IPCClient
)
from fuse.nyfs import (
    NyFSFilesystem, NyFSBlock, NyFSOperations, NyFSError, NyFSMount, _import_fusepy
)
from fuse import nyfs_codec
from ipc import ipc_codec
from ipc import transport_codec
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


class TestContainerFreezer(unittest.TestCase):
    """Test the cgroup v2 freezer integration for suspension
    (implementation_plan.md §4.1): suspend freezes the container's whole
    cgroup via ``cgroup.freeze`` when attached, thawing on resume;
    SIGSTOP/SIGCONT remains the universal fallback (v1 hosts, failed
    cgroup setup, failed freeze/thaw writes). The control-file decision
    and the write/fallback behavior are tested hermetically; the signal
    fallback is exercised against a real process.
    """

    def _v2_manager(self):
        # Force the v2 code path deterministically (no host dependence).
        manager = ContainerManager(use_cgroups_v2=False)
        manager.use_cgroups_v2 = True
        return manager

    def _running_container(self, manager, pid=4242, cgroup_path=None):
        container = manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = pid
        if cgroup_path is not None:
            container.cgroup_paths = [cgroup_path]
        return container

    def test_freeze_control_v2_returns_cgroup_freeze(self):
        manager = self._v2_manager()
        container = self._running_container(manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        control = manager._freeze_control(container)
        self.assertEqual(
            control,
            (Path("/sys/fs/cgroup/nyrqis/c1/cgroup.freeze"), "1", "0"),
        )

    def test_freeze_control_none_without_cgroup_paths(self):
        manager = self._v2_manager()
        container = self._running_container(manager, cgroup_path=None)
        self.assertIsNone(manager._freeze_control(container))

    def test_freeze_control_none_on_v1(self):
        # v1 has no unified freezer (the legacy controller is not
        # provisioned) — the signal path applies.
        manager = ContainerManager(use_cgroups_v2=False)
        container = self._running_container(manager, cgroup_path="/sys/fs/cgroup/memory/nyrqis/c1")
        self.assertIsNone(manager._freeze_control(container))

    def test_suspend_freezes_cgroup_when_attached(self):
        manager = self._v2_manager()
        container = self._running_container(
            manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        with mock.patch("backend.container.Path.write_text") as write, \
                mock.patch.object(manager, "_wait_frozen", return_value=True), \
                mock.patch("backend.container.os.kill") as kill:
            manager.suspend(container)
        write.assert_called_once_with("1\n")
        kill.assert_not_called()
        self.assertTrue(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.SUSPENDED)

    def test_suspend_falls_back_to_sigstop_on_freeze_error(self):
        manager = self._v2_manager()
        container = self._running_container(
            manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        with mock.patch(
            "backend.container.Path.write_text",
            side_effect=OSError("permission denied"),
        ), mock.patch("backend.container.os.kill") as kill:
            manager.suspend(container)
        kill.assert_called_once_with(4242, signal.SIGSTOP)
        self.assertFalse(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.SUSPENDED)

    def test_suspend_signal_fallback_without_cgroup(self):
        manager = self._v2_manager()
        container = self._running_container(manager, cgroup_path=None)
        with mock.patch("backend.container.os.kill") as kill:
            manager.suspend(container)
        kill.assert_called_once_with(4242, signal.SIGSTOP)
        self.assertFalse(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.SUSPENDED)

    def test_resume_thaws_frozen_container(self):
        manager = self._v2_manager()
        container = self._running_container(
            manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        container._frozen_via_cgroup = True
        container.transition_to(ContainerState.SUSPENDED)
        with mock.patch("backend.container.Path.write_text") as write, \
                mock.patch("backend.container.os.kill") as kill:
            manager.resume(container)
        write.assert_called_once_with("0\n")
        kill.assert_not_called()
        self.assertFalse(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.RUNNING)

    def test_resume_sigcont_after_signal_suspend(self):
        manager = self._v2_manager()
        container = self._running_container(manager, cgroup_path=None)
        container.transition_to(ContainerState.SUSPENDED)
        with mock.patch("backend.container.os.kill") as kill:
            manager.resume(container)
        kill.assert_called_once_with(4242, signal.SIGCONT)
        self.assertEqual(container.state, ContainerState.RUNNING)

    def test_resume_thaw_error_raises_and_keeps_suspended(self):
        # A thaw-write failure means the cgroup is still frozen — and a
        # frozen cgroup defers every signal except SIGKILL, so a SIGCONT
        # fallback would leave the container frozen while reporting
        # RUNNING. The OSError is raised instead (the caller retries or
        # escalates to terminate, whose SIGKILL still applies).
        manager = self._v2_manager()
        container = self._running_container(
            manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        container._frozen_via_cgroup = True
        container.transition_to(ContainerState.SUSPENDED)
        with mock.patch(
            "backend.container.Path.write_text",
            side_effect=OSError("cgroup removed"),
        ), mock.patch("backend.container.os.kill") as kill:
            with self.assertRaises(OSError):
                manager.resume(container)
        kill.assert_not_called()
        self.assertTrue(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.SUSPENDED)

    def test_suspend_state_guard(self):
        manager = self._v2_manager()
        container = manager.create(ContainerConfig())  # CREATED
        with self.assertRaises(ValueError):
            manager.suspend(container)
        with self.assertRaises(ValueError):
            manager.resume(container)

    def test_terminate_thaws_frozen_container_first(self):
        # A cgroup-frozen container defers non-SIGKILL signals; terminate
        # must thaw (best-effort) so SIGTERM gets its graceful window.
        manager = self._v2_manager()
        container = self._running_container(
            manager, cgroup_path="/sys/fs/cgroup/nyrqis/c1")
        container._frozen_via_cgroup = True
        with mock.patch("backend.container.Path.write_text") as write, \
                mock.patch.object(container, "is_running", return_value=False), \
                mock.patch("backend.container.os.kill") as kill:
            manager.terminate(container)
        write.assert_called_once_with("0\n")
        kill.assert_called_once_with(4242, signal.SIGTERM)
        self.assertFalse(container._frozen_via_cgroup)
        self.assertEqual(container.state, ContainerState.TERMINATED)

    def test_suspend_resume_real_process_signal_fallback(self):
        # End-to-end fallback: a real process (no cgroup) is SIGSTOPped
        # and SIGCONTinued, observable through /proc/<pid>/stat. The
        # stat field is polled to a deadline (not a fixed sleep) so the
        # test holds on loaded hosts.
        manager = ContainerManager(use_cgroups_v2=False)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            container = self._running_container(manager, pid=proc.pid)
            manager.suspend(container)
            state = "?"
            deadline = time.time() + 2.0
            while time.time() < deadline:
                state = open(f"/proc/{proc.pid}/stat").read().split()[2]
                if state in ("T", "t"):  # stopped (or traced)
                    break
                time.sleep(0.02)
            self.assertIn(state, ("T", "t"))
            manager.resume(container)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                state = open(f"/proc/{proc.pid}/stat").read().split()[2]
                if state not in ("T", "t"):
                    break
                time.sleep(0.02)
            self.assertNotIn(state, ("T", "t"))
        finally:
            proc.kill()
            proc.wait()


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


class TestIPCTransport(unittest.TestCase):
    """Test the Unix-domain datagram IPC transport (NPS-017 §4.3, plan
    §4.3): wire-codec framing, kernel SCM_CREDENTIALS sender
    authentication (unknown/forged senders dropped), capability
    enforcement, ADR-0009 rate limiting on the inbound path, and the
    CALL/REPLY pattern — including a real cross-process exchange.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _paths(self, name):
        return (
            os.path.join(self.tmp, f"{name}-svc.sock"),
            os.path.join(self.tmp, f"{name}-cli.sock"),
        )

    def _server(self, endpoint_id="ep-svc", pid_registry=None,
                capability_manager=None, on_call=None):
        svc_path, _ = self._paths("t")
        manager = IPCManager()
        manager.create_endpoint("container-svc", endpoint_id)
        server = IPCDatagramServer(
            manager, endpoint_id, svc_path,
            pid_registry=pid_registry, capability_manager=capability_manager,
            on_call=on_call,
        )
        return manager, server

    def test_send_receive_authenticated_same_process(self):
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"})
        server.bind()
        _, cli_path = self._paths("t")
        client = IPCClient("container-A", cli_path).bind()
        try:
            client.send(server.endpoint.path, b"hello")
            self.assertIsNotNone(server.serve_once(timeout=2.0))
            msg = manager.receive("ep-svc", timeout_s=1.0)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.payload, b"hello")
            self.assertEqual(msg.sender_id, "container-A")
        finally:
            client.close()
            server.close()

    def test_unknown_sender_dropped(self):
        manager, server = self._server(pid_registry={})  # no mapping
        server.bind()
        _, cli_path = self._paths("t")
        client = IPCClient("container-A", cli_path).bind()
        try:
            client.send(server.endpoint.path, b"anon")
            self.assertIsNone(server.serve_once(timeout=2.0))
            self.assertEqual(manager.receive("ep-svc", timeout_s=0.1), None)
        finally:
            client.close()
            server.close()

    def test_forged_sender_dropped(self):
        # The wire claims "container-evil" but SCM_CREDENTIALS
        # authenticates this process as "container-A".
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"})
        server.bind()
        _, cli_path = self._paths("t")
        client = IPCClient("container-evil", cli_path).bind()
        try:
            client.send(server.endpoint.path, b"spoof")
            self.assertIsNone(server.serve_once(timeout=2.0))
            self.assertEqual(manager.receive("ep-svc", timeout_s=0.1), None)
        finally:
            client.close()
            server.close()

    def test_malformed_wire_dropped(self):
        manager, server = self._server(pid_registry={os.getpid(): "container-A"})
        server.bind()
        try:
            server.endpoint.send(b"\x00\xff not an IPCMessage", server.endpoint.path)
            self.assertIsNone(server.serve_once(timeout=2.0))
            self.assertEqual(manager.receive("ep-svc", timeout_s=0.1), None)
        finally:
            server.close()

    def test_sender_without_cap_denied(self):
        caps = mock.Mock()
        caps.validate_operation.return_value = False  # no CAP_IPC_SEND
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"},
            capability_manager=caps,
        )
        server.bind()
        _, cli_path = self._paths("t")
        client = IPCClient("container-A", cli_path).bind()
        try:
            client.send(server.endpoint.path, b"denied")
            self.assertIsNone(server.serve_once(timeout=2.0))
            self.assertEqual(manager.receive("ep-svc", timeout_s=0.1), None)
        finally:
            client.close()
            server.close()

    def test_call_reply_in_process(self):
        import threading
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"})
        server.bind()

        def handler(msg, sender, sender_path):
            server.reply(sender_path, msg.message_id, b"pong")

        server.on_call = handler
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        _, cli_path = self._paths("t")
        client = IPCClient("container-A", cli_path).bind()
        try:
            reply = client.call(server.endpoint.path, b"ping", timeout_s=5.0)
            self.assertIsNotNone(reply)
            self.assertEqual(reply.payload, b"pong")
            self.assertEqual(reply.message_type, IPCMessageType.REPLY)
        finally:
            stop.set()
            client.close()
            server.close()

    def test_cross_process_call_authenticated(self):
        # A REAL second process (not a thread): its pid is registered as
        # container-cli, the kernel's SCM_CREDENTIALS authenticate it at
        # the server, and the CALL/REPLY round-trips over the socket.
        import threading
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        svc_path, cli_path = self._paths("xp")
        results = {}
        server = IPCDatagramServer(manager, "ep-svc", svc_path)
        server.bind()

        def handler(msg, sender, sender_path):
            results["sender"] = sender
            server.reply(sender_path, msg.message_id, b"pong")

        server.on_call = handler
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()

        backend_dir = str(Path(__file__).resolve().parent)
        ready_path = os.path.join(self.tmp, "xp-ready")
        script = (
            "import os, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "deadline = time.time() + 10\n"
            "while not os.path.exists(sys.argv[4]) and time.time() < deadline:\n"
            "    time.sleep(0.01)\n"
            "from ipc.transport import IPCClient\n"
            "c = IPCClient('container-cli', sys.argv[2]).bind()\n"
            "r = c.call(sys.argv[3], b'ping', timeout_s=10)\n"
            "print('REPLY:' + (r.payload.decode() if r else 'NONE'))\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, backend_dir,
             cli_path, svc_path, ready_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            # Register the child's pid BEFORE it may send (the child
            # waits on the ready marker, so there is no TOCTOU window).
            server.pid_registry = {proc.pid: "container-cli"}
            with open(ready_path, "w") as fh:
                fh.write("go")
            out, err = proc.communicate(timeout=20)
        finally:
            stop.set()
            server.close()
            try:
                os.unlink(ready_path)
            except OSError:
                pass
        self.assertIn("REPLY:pong", out, err)
        # The handler saw the authenticated container, not the wire claim.
        self.assertEqual(results.get("sender"), "container-cli")

    def test_inbound_rate_limited(self):
        # ADR-0009 applies to the transport inbound path: once the
        # endpoint's bucket is empty, further datagrams are dropped.
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"})
        server.bind()
        manager.get_endpoint("ep-svc").rate_limit = TokenBucket(
            bucket_size=3, tokens_per_second=0.1)
        _, cli_path = self._paths("t")
        client = IPCClient("container-A", cli_path).bind()
        try:
            for _ in range(6):
                client.send(server.endpoint.path, b"x")
            delivered = 0
            for _ in range(6):
                if server.serve_once(timeout=2.0) is not None:
                    delivered += 1
            self.assertEqual(delivered, 3)
        finally:
            client.close()
            server.close()

    def test_socket_path_length_guard(self):
        with self.assertRaises(IPCTransportError):
            UnixDatagramEndpoint("x" * 200)


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

    def test_syscall_tables_have_unique_numbers(self):
        # Syscall numbers are the FFI wire vocabulary (ADR-0020 seccomp
        # conformance): a collision makes the policy<->JSON round-trip
        # ambiguous and silently aliases two syscalls in a compiled
        # filter. Two real aarch64 collisions were found and fixed
        # 2026-08-12 (readlink/splice 76, access/faccessat 48); this
        # guard keeps the tables collision-free.
        for arch in seccomp.SyscallArch:
            nums = [n for n in seccomp._SYSCALLS[arch].values() if n >= 0]
            self.assertEqual(
                len(nums), len(set(nums)),
                f"duplicate syscall numbers in the {arch.value} table")

    def test_policy_json_roundtrip(self):
        # ADR-0020 seccomp conformance step 0: the policy <-> JSON wire
        # format (the FFI boundary's shared vocabulary) must round-trip
        # exactly for both postures and both architectures — including
        # producing byte-identical compiled programs.
        caps = {
            Capability.CAP_FILESYSTEM_READ.value,
            Capability.CAP_NETWORK_SOCKET.value,
            Capability.CAP_PROCESS_SPAWN.value,
        }
        for arch in (seccomp.SyscallArch.X86_64, seccomp.SyscallArch.AARCH64):
            for builder in (seccomp.build_policy, seccomp.build_allowlist_policy):
                policy = builder(caps, arch=arch)
                back = seccomp.policy_from_json(policy.to_json())
                self.assertEqual(back.arch, policy.arch)
                self.assertEqual(back.default_action, policy.default_action)
                self.assertEqual(back.deny_syscalls, policy.deny_syscalls)
                self.assertEqual(back.allow_syscalls, policy.allow_syscalls)
                self.assertEqual(back.deny_if_any_flags,
                                 policy.deny_if_any_flags)
                self.assertEqual(back.allow_if_no_flags,
                                 policy.allow_if_no_flags)
                self.assertEqual(seccomp.build_program(back),
                                 seccomp.build_program(policy))


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

    def test_network_default_off(self):
        # Network namespace isolation is opt-in: the default container
        # shares the host network namespace (behavior preserved).
        self.assertFalse(ContainerConfig().network)
        self.assertFalse(ContainerConfig(network=False).network)
        self.assertTrue(ContainerConfig(network=True).network)

    def test_legacy_launch_command_net_when_enabled(self):
        config = ContainerConfig(network=True, seccomp=False)
        container = self.manager.create(config)
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))
        self.assertIn("--net", cmd)
        self.assertLess(cmd.index("--net"), cmd.index("--"))

    def test_legacy_launch_command_no_net_by_default(self):
        config = ContainerConfig(seccomp=False)
        container = self.manager.create(config)
        cmd = self.manager._build_launch_command(container, Path("launcher.py"))
        self.assertNotIn("--net", cmd)

    def test_bring_loopback_up_sets_if_up(self):
        import backend.launcher as launcher
        fake_sock = mock.Mock()
        fake_sock.fileno.return_value = 7
        with mock.patch(
            "backend.launcher.socket.socket", return_value=fake_sock
        ), mock.patch("backend.launcher.fcntl.ioctl") as ioctl:
            # First call (SIOCGIFFLAGS): lo exists but is down.
            ioctl.return_value = struct.pack("16sH", b"lo", 0)
            self.assertTrue(launcher.bring_loopback_up())
        self.assertEqual(ioctl.call_count, 2)
        sioc, payload = ioctl.call_args_list[1].args[1:]
        self.assertEqual(sioc, 0x8914)  # SIOCSIFFLAGS
        self.assertTrue(struct.unpack("16sH", payload)[1] & 0x1)  # IFF_UP

    def test_bring_loopback_up_already_up(self):
        import backend.launcher as launcher
        fake_sock = mock.Mock()
        fake_sock.fileno.return_value = 7
        with mock.patch(
            "backend.launcher.socket.socket", return_value=fake_sock
        ), mock.patch("backend.launcher.fcntl.ioctl") as ioctl:
            ioctl.return_value = struct.pack("16sH", b"lo", 0x1)
            self.assertTrue(launcher.bring_loopback_up())
        # Only the read flags call — no write attempt.
        ioctl.assert_called_once()

    def test_bring_loopback_up_eperm_graceful(self):
        # Sharing the host netns (owned by the init user namespace) the
        # ioctl EPERMs — harmless (host lo already up), never fatal.
        import backend.launcher as launcher
        fake_sock = mock.Mock()
        fake_sock.fileno.return_value = 7
        with mock.patch(
            "backend.launcher.socket.socket", return_value=fake_sock
        ), mock.patch(
            "backend.launcher.fcntl.ioctl",
            side_effect=OSError(errno.EPERM, "not permitted"),
        ):
            self.assertFalse(launcher.bring_loopback_up())

    def test_bring_loopback_up_no_socket_graceful(self):
        import backend.launcher as launcher
        with mock.patch(
            "backend.launcher.socket.socket",
            side_effect=OSError(errno.EPERM, "not permitted"),
        ):
            self.assertFalse(launcher.bring_loopback_up())


class TestDirectSyscallLaunch(unittest.TestCase):
    """Test the direct-syscall launcher (implementation_plan.md §4.1,
    ADR-0020 priority #2): the manager forks a namespace-setup child
    which performs the unshare(2) dance, relays the container's PID
    through a pipe, and is reaped by wait() with Popen-compatible exit
    semantics. The fork/pipe/select boundaries are mocked here; the real
    syscalls are exercised by the host smoke test and CI.
    """

    def setUp(self):
        self.manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True
        )

    def _spawn_with_pipe(self, data=b"4242", fork_returns=777):
        """Drive _spawn_direct with a mocked pipe/fork/read."""
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.os.fork", return_value=fork_returns), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read", return_value=data) as read:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False,
            ))
            self.manager._spawn_direct(container)
        return container, read

    def test_direct_spawn_relays_container_pid(self):
        container, _ = self._spawn_with_pipe()
        # The manager records the container's PID-1 (the grandchild) as
        # container.pid and the setup child as the reaped launcher pid.
        self.assertEqual(container.pid, 4242)
        self.assertEqual(container._direct_launcher_pid, 777)
        self.assertIsNone(container._proc)

    def test_direct_spawn_err_marker_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            self._spawn_with_pipe(data=b"ERR:unshare(CLONE_NEWUSER): boom")
        self.assertIn("unshare(CLONE_NEWUSER)", str(cm.exception))

    def test_direct_spawn_empty_read_raises(self):
        with self.assertRaises(RuntimeError):
            self._spawn_with_pipe(data=b"")

    def test_direct_spawn_reaps_failed_child(self):
        # The manager must reap the setup child even on the failure path
        # (no zombie), then raise.
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.os.fork", return_value=777), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([], [], [])), \
                mock.patch("backend.container.os.read") as read, \
                mock.patch("backend.container.os.waitpid") as waitpid:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False,
            ))
            with self.assertRaises(RuntimeError):
                self.manager._spawn_direct(container)
        read.assert_not_called()
        waitpid.assert_called_once_with(777, 0)

    def test_launcher_args_no_shell_interpolation(self):
        # FIND-BACKEND-004 holds on the shared _launcher_args builder used
        # by the direct path (not just the legacy unshare(1) command).
        config = ContainerConfig(
            hostname="evil; rm -rf /",
            command=["/bin/sh", "-c", "echo hi"],
            capabilities=["CAP_FILESYSTEM_READ"],
        )
        container = self.manager.create(config)
        argv = self.manager._launcher_args(container, Path("launcher.py"))
        self.assertIn("launcher.py", argv)
        self.assertEqual(argv[argv.index("--hostname") + 1], config.hostname)
        launcher_part = argv[:argv.index("--")]
        self.assertNotIn("sh", launcher_part)
        self.assertNotIn("-c", launcher_part)
        self.assertEqual(argv[argv.index("--") + 1:], config.command)

    def test_launcher_args_matches_legacy_launcher_section(self):
        # Both launch paths must hand the container the exact same argv.
        config = ContainerConfig(
            command=["/bin/echo", "hi"], seccomp=False,
        )
        container = self.manager.create(config)
        legacy = self.manager._build_launch_command(container, Path("launcher.py"))
        direct = self.manager._launcher_args(container, Path("launcher.py"))
        first_sep = legacy.index("--")
        self.assertEqual(legacy[first_sep + 1:], direct)

    def test_wait_direct_reaps_and_maps_exit_code(self):
        container = self.manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 4242
        container._direct_launcher_pid = 777
        with mock.patch("backend.container.os.waitpid",
                        return_value=(777, 7 << 8)):
            code = self.manager.wait(container)
        self.assertEqual(code, 7)
        self.assertEqual(container.state, ContainerState.TERMINATED)
        self.assertEqual(container.exit_code, 7)

    def test_wait_direct_signal_maps_to_negative(self):
        # WIFSIGNALED(SIGTERM) -> -15, matching Popen semantics.
        container = self.manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 4242
        container._direct_launcher_pid = 777
        # Linux wait status encoding for "killed by SIGTERM": the low
        # bits carry the signal number.
        status = 15
        with mock.patch("backend.container.os.waitpid",
                        return_value=(777, status)):
            code = self.manager.wait(container)
        self.assertEqual(code, -15)
        self.assertEqual(container.state, ContainerState.TERMINATED)

    def test_wait_direct_timeout(self):
        container = self.manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 4242
        container._direct_launcher_pid = 777
        with mock.patch("backend.container.os.waitpid",
                        return_value=(0, 0)):
            with self.assertRaises(TimeoutError):
                self.manager.wait(container, timeout_s=0.1)
        self.assertEqual(container.state, ContainerState.RUNNING)

    def test_wait_legacy_still_uses_popen(self):
        # The unshare(1) opt-in path keeps Popen-based wait semantics.
        manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=False
        )
        container = manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 99
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 3
        container._proc = fake_proc
        self.assertEqual(manager.wait(container), 3)
        fake_proc.wait.assert_called_once_with(timeout=None)

    def test_direct_child_reports_errors_through_pipe(self):
        # The namespace-setup child reports failures as ERR: pipe messages
        # and exits without touching Python cleanup machinery. os._exit is
        # mocked so every fall-through point is patched too (the child
        # would otherwise continue into real syscalls).
        import backend.container as container_mod
        with mock.patch("backend.container.rust_syscalls.unshare",
                        side_effect=OSError(errno.EPERM, "denied")), \
                mock.patch("backend.container._write_root_maps"), \
                mock.patch("backend.container.os.write") as write, \
                mock.patch("backend.container.os.fork", return_value=1), \
                mock.patch("backend.container.os.waitpid",
                           return_value=(1, 0)), \
                mock.patch("backend.container.os._exit") as exit_: 
            container_mod._direct_launch_child(4, ["/bin/true"])
        self.assertTrue(
            any(c.args[1].startswith(b"ERR:") for c in write.call_args_list)
        )
        exit_.assert_called()

    def test_direct_child_setup_sequence_uses_clone_new_flags(self):
        # The setup child's unshare sequence: NEWUSER, then NS|UTS|IPC,
        # then NEWPID (which affects only the next fork). It relays the
        # grandchild PID, waits for it, and exits with its status.
        import backend.container as container_mod
        calls = []
        with mock.patch(
            "backend.container.rust_syscalls.unshare",
            side_effect=lambda flags: calls.append(flags) or None,
        ), mock.patch(
            "backend.container._write_root_maps"
        ) as maps, mock.patch(
            "backend.container.os.fork", return_value=4242
        ), mock.patch(
            "backend.container.os.write"
        ) as write, mock.patch(
            "backend.container.os.close"
        ), mock.patch(
            "backend.container.os.waitpid", return_value=(4242, 7 << 8)
        ), mock.patch(
            "backend.container.os._exit"
        ) as exit_:
            container_mod._direct_launch_child(4, ["/bin/true"])
        self.assertEqual(calls, [
            rust_syscalls.CLONE_NEWUSER,
            rust_syscalls.CLONE_NEWNS | rust_syscalls.CLONE_NEWUTS
            | rust_syscalls.CLONE_NEWIPC,
            rust_syscalls.CLONE_NEWPID,
        ])
        maps.assert_called_once()
        # The grandchild's PID is relayed, and the setup child exits
        # with the grandchild's exit status. (os._exit is mocked so the
        # fall-through final os._exit(1) is also observed — assert_any_call
        # pins the exit-status propagation.)
        write.assert_any_call(4, b"4242")
        exit_.assert_any_call(7)

    def test_direct_child_new_net_when_network_enabled(self):
        # network=True adds CLONE_NEWNET to the mount/UTS/IPC unshare.
        import backend.container as container_mod
        calls = []
        with mock.patch(
            "backend.container.rust_syscalls.unshare",
            side_effect=lambda flags: calls.append(flags) or None,
        ), mock.patch("backend.container._write_root_maps"), mock.patch(
            "backend.container.os.fork", return_value=4242
        ), mock.patch("backend.container.os.write"), mock.patch(
            "backend.container.os.close"
        ), mock.patch(
            "backend.container.os.waitpid", return_value=(4242, 7 << 8)
        ), mock.patch("backend.container.os._exit"):
            container_mod._direct_launch_child(4, ["/bin/true"], network=True)
        self.assertEqual(calls, [
            rust_syscalls.CLONE_NEWUSER,
            rust_syscalls.CLONE_NEWNS | rust_syscalls.CLONE_NEWUTS
            | rust_syscalls.CLONE_NEWIPC | rust_syscalls.CLONE_NEWNET,
            rust_syscalls.CLONE_NEWPID,
        ])

    def test_direct_spawn_forwards_network_flag_to_child(self):
        # The manager passes the container's network flag through to the
        # namespace-setup child (fork mocked to run the child branch so
        # the forwarding is observable).
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.os.fork", return_value=0), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read", return_value=b"4242"), \
                mock.patch(
                    "backend.container._direct_launch_child") as child:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False, network=True,
            ))
            self.manager._spawn_direct(container)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(child.call_args.args[0], 4)  # write_fd
        self.assertTrue(child.call_args.args[2])  # network=True

    def test_direct_child_grandchild_mounts_proc_and_execs(self):
        # The PID-1 grandchild (os.fork returns 0) hardens against
        # losing the setup child, mounts a fresh procfs, and execs the
        # launcher argv.
        import backend.container as container_mod
        launcher_argv = ["/usr/bin/python3", "launcher.py", "--"]
        with mock.patch(
            "backend.container.rust_syscalls.unshare"
        ), mock.patch(
            "backend.container._write_root_maps"
        ), mock.patch(
            "backend.container.os.fork", return_value=0
        ), mock.patch(
            "backend.container.os.close"
        ), mock.patch(
            "backend.container.rust_syscalls.prctl"
        ) as prctl, mock.patch(
            "backend.container.rust_syscalls.mount_proc", return_value=0
        ) as mount_proc, mock.patch(
            "backend.container.os.execv"
        ) as execv, mock.patch(
            "backend.container.os.waitpid", return_value=(0, 0)
        ), mock.patch(
            "backend.container.os.write"
        ), mock.patch(
            "backend.container.os._exit"
        ):
            container_mod._direct_launch_child(4, launcher_argv)
        prctl.assert_called_once_with(1, 9)  # PR_SET_PDEATHSIG, SIGKILL
        mount_proc.assert_called_once_with()
        execv.assert_called_once_with(launcher_argv[0], launcher_argv)

    def test_write_root_maps_contents(self):
        # --map-root-user equivalent: setgroups deny, then uid/gid maps
        # mapping the caller to root.
        import backend.container as container_mod
        written = {}

        def fake_open(path, flags):
            return path  # use the path as the fd handle

        def fake_write(fd, content):
            written[fd] = content
            return len(content)

        with mock.patch("backend.container.os.open",
                        side_effect=fake_open), \
                mock.patch("backend.container.os.write",
                           side_effect=fake_write), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.os.getuid", return_value=1000), \
                mock.patch("backend.container.os.getgid", return_value=1000):
            container_mod._write_root_maps()
        self.assertEqual(written["/proc/self/setgroups"], b"deny\n")
        self.assertEqual(written["/proc/self/uid_map"], b"0 1000 1\n")
        self.assertEqual(written["/proc/self/gid_map"], b"0 1000 1\n")


_NETNS_SUPPORTED = None  # cached real-launch probe result


def _launch_cleanup(manager, container) -> None:
    """Tear down a spawned container and reap its launcher child.

    ``terminate()`` already transitions the container to TERMINATED, so
    ``wait()`` must NOT be called afterwards (the state machine rejects
    terminated → terminated). The namespace-setup child is reaped
    directly instead, so no zombie survives the test.
    """
    if container.pid is not None and container.is_running():
        manager.terminate(container)
    launcher_pid = getattr(container, "_direct_launcher_pid", None)
    if launcher_pid is not None:
        try:
            os.waitpid(launcher_pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass


def _netns_launch_supported() -> bool:
    """True when a direct-syscall container with a network namespace
    can actually launch on this host. Probing with a real launch is the
    honest gate: it covers the unprivileged-userns knob AND the whole
    mount-proc/launcher chain in one check, so the isolation tests skip
    (not fail) on hosts that cannot run them. The result is cached
    (both skipUnless decorators would otherwise re-run the probe)."""
    global _NETNS_SUPPORTED
    if _NETNS_SUPPORTED is not None:
        return _NETNS_SUPPORTED
    supported = False
    if shutil.which("unshare") is not None:
        try:
            probe = subprocess.run(
                ["unshare", "--user", "--net", "true"],
                capture_output=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            probe = None
        if probe is not None and probe.returncode == 0:
            try:
                manager = ContainerManager(
                    use_cgroups_v2=False, use_direct_syscalls=True)
                container = manager.create(ContainerConfig(
                    command=["/bin/sleep", "5"], seccomp=False,
                    network=True))
                manager.spawn(container)
                try:
                    os.readlink(f"/proc/{container.pid}/ns/net")
                    supported = True
                finally:
                    _launch_cleanup(manager, container)
            except Exception:
                supported = False
    _NETNS_SUPPORTED = supported
    return supported


class TestNetworkNamespaceIsolation(unittest.TestCase):
    """Real-launch verification that ``network=True`` containers get
    their own network namespace (loopback only), while the default
    shares the host's — observed through the netns inode and the
    container's own procfs, no root required.
    """

    _NETNS = "host cannot launch network-namespace containers"

    @staticmethod
    def _net_dev_names(pid: int) -> list:
        """Interface names visible in a process's network namespace.

        ``/proc/<pid>/net/dev`` resolves within the process's netns via
        the HOST procfs (magic symlink) — unlike
        ``/proc/<pid>/root/proc/net/dev``, which is either the inherited
        host procfs before ``mount_proc`` or unreadable (ENOENT) once
        PID-1 mounts its own (a cross-PID-namespace restriction).
        Race-free: the netns is created before PID-1 is forked, so the
        moment the manager holds the PID the namespace is in effect.
        """
        text = Path(f"/proc/{pid}/net/dev").read_text()
        return [ln.split(":")[0].strip()
                for ln in text.splitlines()[2:] if ln.strip()]

    @unittest.skipUnless(_netns_launch_supported(), _NETNS)
    def test_network_container_gets_own_netns(self):
        manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True)
        host_net = os.readlink("/proc/self/ns/net")
        container = manager.create(ContainerConfig(
            command=["/bin/sleep", "30"], seccomp=False, network=True))
        manager.spawn(container)
        try:
            cont_net = os.readlink(f"/proc/{container.pid}/ns/net")
            self.assertNotEqual(cont_net, host_net)
            # A fresh network namespace contains exactly the loopback
            # device — the isolation boundary's observable content.
            self.assertEqual(self._net_dev_names(container.pid), ["lo"])
        finally:
            _launch_cleanup(manager, container)

    @unittest.skipUnless(_netns_launch_supported(), _NETNS)
    def test_default_container_shares_host_netns(self):
        manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True)
        host_net = os.readlink("/proc/self/ns/net")
        host_names = [
            ln.split(":")[0].strip()
            for ln in Path("/proc/self/net/dev").read_text().splitlines()[2:]
            if ln.strip()]
        container = manager.create(ContainerConfig(
            command=["/bin/sleep", "30"], seccomp=False, network=False))
        manager.spawn(container)
        try:
            cont_net = os.readlink(f"/proc/{container.pid}/ns/net")
            self.assertEqual(cont_net, host_net)
            self.assertEqual(self._net_dev_names(container.pid), host_names)
        finally:
            _launch_cleanup(manager, container)

    @unittest.skipUnless(_netns_launch_supported(), _NETNS)
    def test_container_ipc_call_service(self):
        # The whole stack end-to-end: a REAL container (direct-syscall
        # launch) runs an IPCClient that calls a backend service over
        # the datagram transport. The kernel's SCM_CREDENTIALS
        # authenticate the container (via its host-visible pid) at the
        # server, the seccomp filter permits the socket family (network
        # caps granted) and the marker write (filesystem cap), and the
        # CALL/REPLY round-trips through the wire codec.
        import threading
        base = tempfile.mkdtemp(prefix="nyrqis-ipc-e2e-")
        svc_path = os.path.join(base, "svc.sock")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")

        ipc_manager = IPCManager()
        ipc_manager.create_endpoint("container-svc", "ep-svc")
        results = {}
        server = IPCDatagramServer(ipc_manager, "ep-svc", svc_path)
        server.bind()

        def handler(msg, sender, sender_path):
            results["sender"] = sender
            server.reply(sender_path, msg.message_id, b"pong")

        server.on_call = handler
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()

        backend_dir = str(Path(__file__).resolve().parent)
        script = (
            "import os, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "deadline = time.time() + 10\n"
            "while not os.path.exists(sys.argv[4]) and time.time() < deadline:\n"
            "    time.sleep(0.01)\n"
            "from ipc.transport import IPCClient\n"
            "c = IPCClient('container-cli', sys.argv[2]).bind()\n"
            "r = c.call(sys.argv[3], b'ping', timeout_s=10)\n"
            "open(sys.argv[5], 'w').write("
            "(r.payload.decode() if r else 'NONE'))\n"
        )
        ctr_manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True)
        container = ctr_manager.create(ContainerConfig(
            command=[sys.executable, "-c", script, backend_dir,
                     cli_path, svc_path, ready_path, marker],
            seccomp=True,
            capabilities=[
                "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                "CAP_FILESYSTEM_WRITE",
            ],
        ))
        ctr_manager.spawn(container)
        try:
            # Register the container's host-visible pid BEFORE it may
            # send (it waits on the ready marker — no TOCTOU).
            server.pid_registry = {container.pid: "container-cli"}
            with open(ready_path, "w") as fh:
                fh.write("go")
            deadline = time.time() + 20.0
            while time.time() < deadline and not os.path.exists(marker):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(marker),
                "container never reached the IPC service",
            )
            with open(marker) as fh:
                self.assertEqual(fh.read(), "pong")
            # The handler saw the authenticated container, not a claim.
            self.assertEqual(results.get("sender"), "container-cli")
        finally:
            _launch_cleanup(ctr_manager, container)
            stop.set()
            server.close()
            shutil.rmtree(base, ignore_errors=True)

    @unittest.skipUnless(_netns_launch_supported(), _NETNS)
    def test_network_container_loopback_is_up(self):
        # The launcher brings lo up before exec (step 2b), so a netns
        # container can bind 127.0.0.1 — verified end-to-end: the
        # container writes a marker to the shared rootfs only after a
        # successful loopback bind (a down lo raises EADDRNOTAVAIL).
        # Network capabilities are granted so the seccomp filter allows
        # socket()/bind() — proving the path works WITH enforcement on.
        manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True)
        marker = f"/tmp/nyrqis-lo-up-{os.getpid()}.marker"
        try:
            os.unlink(marker)
        except OSError:
            pass
        snippet = (
            "import socket; "
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); "
            "s.bind(('127.0.0.1', 0)); s.close(); "
            f"open({marker!r}, 'w').write('ok')"
        )
        container = manager.create(ContainerConfig(
            command=[sys.executable, "-c", snippet],
            seccomp=True,
            network=True,
            # Network caps let the seccomp filter allow socket()/bind();
            # CAP_FILESYSTEM_WRITE lets it write the marker (without it
            # the data plane correctly EPERMs the open(O_CREAT)).
            capabilities=[
                "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                "CAP_FILESYSTEM_WRITE",
            ],
        ))
        manager.spawn(container)
        try:
            deadline = time.time() + 10.0
            while time.time() < deadline and not os.path.exists(marker):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(marker),
                "container could not bind 127.0.0.1 (loopback not up "
                "inside the network namespace)",
            )
        finally:
            _launch_cleanup(manager, container)
            try:
                os.unlink(marker)
            except OSError:
                pass


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
        # Interleaved save materializes .bin files, which is what this
        # on-disk tamper scenario exercises (journal mode keeps payloads
        # in the journal until compaction).
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/t.bin")
        fs.write(f, b"integrity-check")
        fs.save(use_journal=False)
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
        # filesystem must not rewrite anything (reviewer-flagged:
        # random-UUID block IDs could otherwise churn the block store).
        # Journal mode (the default) appends no records on an unchanged
        # re-save; the interleaved path leaves block-file mtimes alone.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/idem.bin")
        fs.write(f, b"data" * 5000)
        fs.save()  # journal mode (default)
        before = len(fs._scan_journal())
        self.assertGreater(before, 0)
        time.sleep(0.01)
        fs.save()
        self.assertEqual(len(fs._scan_journal()), before)

        # Interleaved path: materialize to .bin, then confirm no block
        # file is rewritten by a further interleaved re-save.
        fs.save(use_journal=False)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        first = {
            p: os.path.getmtime(os.path.join(blocks_dir, p))
            for p in os.listdir(blocks_dir)
        }
        time.sleep(0.01)
        fs.save(use_journal=False)
        second = os.listdir(blocks_dir)
        self.assertEqual(sorted(first), sorted(second))
        for name in second:
            self.assertEqual(
                os.path.getmtime(os.path.join(blocks_dir, name)),
                first[name],
            )

    def test_gc_removes_orphaned_blocks(self):
        # gc_blocks reclaims .bin files, so this scenario uses the
        # interleaved (materialized) path; journal garbage is reclaimed
        # by compaction instead.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/g.bin")
        fs.write(f, b"a" * 100)
        old_id = f.blocks[0].block_id
        snap = fs.create_snapshot()   # pins the 'a' block
        fs.write(f, b"b" * 100)       # CoW: new block for the live state
        fs.save(use_journal=False)
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
        # The grouped (interleaved, non-journal) path publishes every
        # temp via rename; none may be left behind, and the blocks dir
        # holds exactly the live blocks.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/t.bin")
        fs.write(f, b"data" * 3000)
        fs.save(batched_fsync=True, use_journal=False)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        names = os.listdir(blocks_dir)
        self.assertEqual([n for n in names if n.endswith(".tmp")], [])
        self.assertEqual(len(names), len(f.blocks))
        # Re-save with the grouped path is a no-op on block files too.
        before = sorted(names)
        fs.save(batched_fsync=True, use_journal=False)
        self.assertEqual(sorted(os.listdir(blocks_dir)), before)

    def test_batched_fsync_crash_mid_save_leaves_old_state(self):
        # Crash-atomicity on the interleaved batched (grouped-rename)
        # path: a failure mid rename-phase — some new block files
        # published, others still temps — must leave the previous
        # committed state loadable, because the old metadata references
        # only old, present blocks. Pinned to use_journal=False: the
        # grouped rename phase exists only on the interleaved path, and
        # journal mode is the default (2026-08-12).
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/c.sav")
        fs.write(f, b"A" * 20000)  # 5 blocks
        fs.save(use_journal=False)

        fs.write(f, b"B" * 20000)  # 5 new CoW blocks
        calls = {"n": 0}
        # mock.patch replaces os.replace on the SHARED os module, so the
        # side-effect function must call a pre-captured reference or it
        # would re-enter the mock (and the real rename would never run).
        real_replace = os.replace

        def _fail_after_two_renames(src, dst):
            # Real rename for the first two calls (block files 1-2 get
            # published), then crash: the rest stay temps and the
            # metadata is never swapped.
            calls["n"] += 1
            if calls["n"] > 2:
                raise OSError("simulated crash mid rename-phase")
            return real_replace(src, dst)

        with mock.patch("fuse.nyfs.os.replace",
                        side_effect=_fail_after_two_renames):
            with self.assertRaises(OSError):
                fs.save(batched_fsync=True, use_journal=False)

        # The crash must have been genuinely mid rename-phase, not a
        # failed metadata swap: two of the five new CoW blocks were
        # published as .bin, the other three remain stuck as temps, and
        # the commit-point rename never ran. (Self-guard: if the save
        # path were reordered to swap metadata first, this assertion and
        # the reload check below would both fail.)
        self.assertEqual(calls["n"], 3)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        temps = [n for n in os.listdir(blocks_dir) if n.endswith(".tmp")]
        self.assertEqual(len(temps), 3)

        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.sav")), b"A" * 20000)

    def test_journal_save_load_roundtrip(self):
        # Journal-mode commit (one fsync per transaction) must produce a
        # loadable state with no .bin block files — payloads live in the
        # journal until compaction.
        fs = NyFSFilesystem(self.base, block_size=4096)
        fs.mkdir("/games")
        f = fs.create_file("/games/save.sav")
        payload = b"journal-" * 5000  # 40000 bytes, 10 blocks
        fs.write(f, payload)
        snap = fs.create_snapshot()
        fs.write(f, b"v2", offset=100)
        fs.save(use_journal=True)
        del fs

        blocks_dir = os.path.join(self.base, "state", "blocks")
        self.assertFalse(os.path.exists(blocks_dir),
                         "journal mode must not write .bin files")
        journal = os.path.join(self.base, "state", "journal.bin")
        self.assertTrue(os.path.exists(journal))

        fs2 = NyFSFilesystem.load(self.base)
        restored = fs2.resolve("/games/save.sav")
        self.assertEqual(fs2.read(restored), payload[:100] + b"v2"
                         + payload[102:])
        self.assertIn(snap, fs2.list_snapshots())
        fs2.restore_snapshot(snap)
        self.assertEqual(fs2.read(fs2.resolve("/games/save.sav")), payload)

    def test_journal_crash_mid_save_leaves_old_state(self):
        # Journal mode keeps the same crash-atomicity: the metadata swap
        # is the commit point and happens only after the journal is
        # fsynced, so a failure before it leaves the old state loadable.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/c.sav")
        fs.write(f, b"journal-v1")
        fs.save(use_journal=True)

        fs.write(f, b"journal-v2")
        with mock.patch(
            "fuse.nyfs.os.replace",
            side_effect=OSError("simulated crash before metadata swap"),
        ):
            with self.assertRaises(OSError):
                fs.save(use_journal=True)

        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.sav")), b"journal-v1")

    def test_journal_torn_tail_is_ignored(self):
        # A crash mid-append can leave a torn tail; scanning must stop
        # at the first malformed record so load() never fabricates data
        # from garbage.
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/t.txt")
        fs.write(f, b"good data" * 100)
        fs.save(use_journal=True)
        journal = os.path.join(self.base, "state", "journal.bin")
        # Append garbage: a plausible header but a truncated payload.
        with open(journal, "ab") as fh:
            fh.write((1024 * 1024).to_bytes(4, "little"))
            fh.write(b"0" * 36)  # valid-looking id
            fh.write(b"short")    # payload cut short
        del fs

        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/t.txt")), b"good data" * 100)
        # The garbage record must not be indexed.
        self.assertNotIn("0" * 36, fs2._scan_journal())

    def test_journal_compaction_materializes_and_truncates(self):
        # Once the journal exceeds the threshold, a journal-mode save
        # materializes referenced blocks into .bin files and truncates
        # the journal; the state must still round-trip.
        fs = NyFSFilesystem(self.base, block_size=4096,
                            journal_compact_bytes=1)  # compact eagerly
        f = fs.create_file("/c.bin")
        fs.write(f, b"payload" * 3000)
        fs.save(use_journal=True)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        bins = [n for n in os.listdir(blocks_dir) if n.endswith(".bin")]
        self.assertEqual(len(bins), len(f.blocks))
        journal = os.path.join(self.base, "state", "journal.bin")
        self.assertEqual(os.path.getsize(journal), 0)
        # Re-save after compaction: blocks are on disk, journal stays
        # empty, and the state is still loadable.
        fs.save(use_journal=True)
        self.assertEqual(os.path.getsize(journal), 0)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.bin")), b"payload" * 3000)

    def test_journal_blocks_not_reappended_across_saves(self):
        # Immutable blocks already durable in the journal must not be
        # re-appended by a later journal-mode save (checked via the
        # scan index record count).
        fs = NyFSFilesystem(self.base, block_size=4096)
        f = fs.create_file("/a.bin")
        fs.write(f, b"A" * 100)  # single block
        fs.save(use_journal=True)
        first_count = len(fs._scan_journal())
        self.assertEqual(first_count, 1)

        fs.write(f, b"B" * 100)  # CoW: one new block
        fs.save(use_journal=True)
        self.assertEqual(len(fs._scan_journal()), first_count + 1)

        fs.save(use_journal=True)  # no changes: nothing appended
        self.assertEqual(len(fs._scan_journal()), first_count + 1)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/a.bin")), b"B" * 100)

    def test_dirty_flag_tracking(self):
        # DAEMON_LIFECYCLE dirty gate: True while in-memory state
        # differs from the last commit, False after save/load.
        fs = NyFSFilesystem(self.base, block_size=4096)
        self.assertFalse(fs.dirty)
        f = fs.create_file("/d.txt")
        self.assertTrue(fs.dirty)
        fs.write(f, b"x")
        self.assertTrue(fs.dirty)
        fs.save()
        self.assertFalse(fs.dirty)
        fs.write(f, b"y", offset=1)
        self.assertTrue(fs.dirty)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertFalse(fs2.dirty)

    def test_journal_public_compaction_api(self):
        # The daemon-facing compaction API (BENCHMARK_RESULTS §14):
        # journal_bytes() reports the journal size, maybe_compact() is a
        # no-op below its threshold and materializes + truncates above
        # it, compact_journal() forces compaction. The state must
        # round-trip after every step, and blocks on disk are never
        # re-journaled.
        fs = NyFSFilesystem(self.base, block_size=4096,
                            journal_compact_bytes=1 << 30)  # never auto-compact
        f = fs.create_file("/c.bin")
        fs.write(f, b"J" * 9000)  # 3 blocks at 4096
        fs.save(use_journal=True)
        self.assertGreater(fs.journal_bytes(), 0)

        # Below the (huge) default threshold: a no-op.
        self.assertEqual(fs.maybe_compact(), 0)
        self.assertGreater(fs.journal_bytes(), 0)

        # An explicit low threshold triggers compaction.
        moved = fs.maybe_compact(threshold=0)
        self.assertGreaterEqual(moved, 3)
        self.assertEqual(fs.journal_bytes(), 0)
        blocks_dir = os.path.join(self.base, "state", "blocks")
        bins = [n for n in os.listdir(blocks_dir) if n.endswith(".bin")]
        self.assertEqual(len(bins), 3)
        # Blocks are now on disk: a further journal save appends nothing
        # and the journal stays empty.
        fs.save(use_journal=True)
        self.assertEqual(fs.journal_bytes(), 0)

        # Forced compaction of a fresh journal (CoW orphans dropped).
        fs.write(f, b"K" * 9000)  # CoW: 3 new blocks, old 3 orphaned
        fs.save(use_journal=True)
        self.assertGreater(fs.journal_bytes(), 0)
        self.assertEqual(fs.compact_journal(), 3)
        self.assertEqual(fs.journal_bytes(), 0)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.bin")), b"K" * 9000)

    def test_compaction_crash_mid_materialize_leaves_journal_intact(self):
        # Compaction's crash contract: block renames happen BEFORE the
        # journal truncate, so a crash mid-compaction (one block
        # materialized, the rest still journal-only) must leave the
        # journal intact and the state loadable — the truncate is the
        # last, destructive step. (Same captured-real-replace pattern as
        # test_batched_fsync_crash_mid_save_leaves_old_state: patching
        # os.replace patches the SHARED os module.)
        fs = NyFSFilesystem(self.base, block_size=4096,
                            journal_compact_bytes=1 << 30)
        f = fs.create_file("/c.bin")
        fs.write(f, b"Z" * 12000)  # 3 blocks
        fs.save(use_journal=True)
        journal_path = os.path.join(self.base, "state", "journal.bin")
        before = os.path.getsize(journal_path)
        self.assertGreater(before, 0)

        calls = {"n": 0}
        real_replace = os.replace

        def _fail_after_one(src, dst):
            # First rename (one .bin materialized) succeeds; the next
            # crashes before the journal truncate ever runs.
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("simulated crash mid-compaction")
            return real_replace(src, dst)

        with mock.patch("fuse.nyfs.os.replace",
                        side_effect=_fail_after_one):
            with self.assertRaises(OSError):
                fs.compact_journal()

        # The journal survived (truncate is last) and load() still
        # reconstructs every block (one from .bin, the rest from the
        # journal fallback).
        self.assertEqual(os.path.getsize(journal_path), before)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.bin")), b"Z" * 12000)


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

    def test_shutdown_commits_dirty_state(self):
        # DAEMON_LIFECYCLE shutdown contract: an orderly shutdown
        # commits uncommitted state (dirty gate) before unmounting.
        mount = NyFSMount(self.fs, tempfile.mkdtemp())
        self.fs.create_file("/x.txt")
        self.assertTrue(self.fs.dirty)
        mount.shutdown()
        self.assertFalse(self.fs.dirty)
        meta = os.path.join(self.fs.base_path, "state", "metadata.json")
        self.assertTrue(os.path.exists(meta))

    def test_auto_compact_is_the_mount_default(self):
        # DAEMON_LIFECYCLE recommendation, now implemented: the
        # background compaction watcher runs without the caller passing
        # auto_compact (default True). attach() is mocked because these
        # tests exercise the watcher lifecycle, not the fusepy attach
        # path (which is environment-dependent — no fusepy on CI).
        mount = NyFSMount(self.fs, tempfile.mkdtemp())
        with mock.patch.object(mount, "attach", return_value=True), \
             mock.patch.object(mount, "_build_fuse", return_value=None):
            self.assertTrue(mount.mount(foreground=True, blocking=False))
        self.assertIsNotNone(mount._compact_stop,
                             "watcher should be running by default")
        mount.unmount()
        thread = mount._compact_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())

    def test_auto_compact_failed_mount_leaves_no_watcher(self):
        # A mount that fails must not leave the background compaction
        # watcher orphaned (reviewer-flagged lifecycle edge: the watcher
        # starts before the blocking FUSE loop, so the failure path must
        # stop it before propagating the error). attach() is mocked for
        # the same environment-independence reason as above.
        mount = NyFSMount(self.fs, tempfile.mkdtemp())
        with mock.patch.object(mount, "attach", return_value=True), \
             mock.patch.object(
                mount, "_build_fuse",
                side_effect=NyFSError(errno.ENODEV, "simulated mount failure")):
            with self.assertRaises(NyFSError):
                mount.mount(foreground=True, blocking=True, auto_compact=True)
        thread = mount._compact_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive(),
                             "failed mount left a watcher thread running")


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
        # Journal commit is the default: block payloads live in
        # state/journal.bin (compacted to state/blocks/ only past the
        # threshold), so the journal must be non-empty after fsync.
        journal = os.path.join(state, "journal.bin")
        self.assertTrue(os.path.exists(journal))
        self.assertGreater(os.path.getsize(journal), 0)

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

    def test_auto_compact_watcher_trims_journal_while_mounted(self):
        # The background compaction watcher (NyFSMount auto_compact,
        # BENCHMARK_RESULTS §14) trims the journal during idle periods,
        # below the save()-time threshold, so a transaction is never the
        # one that stalls on the materialize pass. Hard cap: 90 s.
        signal.alarm(90)
        try:
            fs = NyFSFilesystem(self.backing, journal_compact_bytes=8 << 20)
            m = NyFSMount(fs, self.mnt)
            self.assertTrue(m.mount(
                foreground=True, blocking=False, auto_compact=True,
                compact_interval=0.2, compact_interval_bytes=64 * 1024))
            self.mounts.append(m)
            self.assertTrue(m.wait_ready(timeout=5.0))

            # ~256 KiB of incompressible data + fsync: the journal grows
            # past the watcher's 64 KiB threshold but stays far below
            # the 8 MiB save()-time threshold, so only the watcher can
            # trim it.
            rng = __import__("random").Random(7)
            payload = bytes(rng.randrange(256) for _ in range(256 * 1024))
            path = os.path.join(self.mnt, "j.bin")
            with open(path, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())

            # Poll: the watcher must compact the journal to empty.
            journal = os.path.join(self.backing, "state", "journal.bin")
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                size = (os.path.getsize(journal)
                        if os.path.exists(journal) else 0)
                if size == 0:
                    break
                time.sleep(0.1)
            self.assertEqual(
                os.path.getsize(journal) if os.path.exists(journal) else 0, 0,
                "background watcher did not trim the journal")
            blocks_dir = os.path.join(self.backing, "state", "blocks")
            self.assertGreater(
                len([n for n in os.listdir(blocks_dir)
                     if n.endswith(".bin")]), 0)

            # Data intact through the mount and after reload.
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), payload)
            m.unmount()
            fs2 = NyFSFilesystem.load(self.backing)
            self.assertEqual(fs2.read(fs2.resolve("/j.bin")), payload)
        finally:
            signal.alarm(0)

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


class TestRustFfILoader(unittest.TestCase):
    """ADR-0020 FFI loader behavior (see rust/seccomp/README.md).

    These tests pin the loader's fallback contract and the wire format
    on hosts WITHOUT the Rust crate built. When the crate lands, the CI
    conformance job (NYRQIS_RUST_FORCE=1) becomes the real gate: every
    seccomp test then drives the Rust module through the FFI.
    """

    def setUp(self):
        self._env = dict(os.environ)
        seccomp._RUST_LIB = None
        seccomp._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        seccomp._RUST_LIB = None
        seccomp._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        """Point the loader at a guaranteed-absent library."""
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_seccomp.so"

    def test_wire_format_is_sock_filter_layout(self):
        # ld [4] -> code 0x20, jt 0, jf 0, k 4; u16/u8/u8/u32 LE = 8 bytes.
        packed = seccomp._program_to_rust_bytes([(0x20, 0, 0, 4)])
        self.assertEqual(packed, b"\x20\x00\x00\x00\x04\x00\x00\x00")
        self.assertEqual(len(packed), 8)

    def test_program_wire_roundtrip(self):
        for posture in (
            seccomp.build_policy(set()),
            seccomp.build_allowlist_policy(set()),
        ):
            program = seccomp.build_program(posture)
            self.assertTrue(program)
            self.assertEqual(
                seccomp._program_from_rust_bytes(
                    seccomp._program_to_rust_bytes(program)
                ),
                program,
            )

    def test_rust_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_seccomp.so"
        self.assertEqual(
            seccomp._rust_lib_candidates(), ["/custom/libnyrqis_seccomp.so"]
        )

    def test_absent_backend_falls_back_to_python(self):
        self._no_backend()
        # This test exercises the FALLBACK path, so it must not inherit
        # NYRQIS_RUST_FORCE from the CI conformance job env (where the
        # whole suite runs with force=1). tearDown restores the env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(seccomp._load_rust_backend())
        policy = seccomp.build_policy(set())  # no capabilities -> write-intent denied
        program = seccomp.build_program(policy)
        self.assertTrue(program)
        # Read-only openat is allowed; a write-intent openat is denied.
        arch = policy.arch
        nr = seccomp._SYSCALLS[arch]["openat"]
        self.assertEqual(
            seccomp.simulate(program, nr, arch.audit_arch, [0, 0, os.O_RDONLY]),
            seccomp.SECCOMP_RET_ALLOW,
        )
        self.assertEqual(
            seccomp.simulate(program, nr, arch.audit_arch, [0, 0, os.O_WRONLY]),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        policy = seccomp.build_policy(set())
        with self.assertRaises(seccomp.PolicyError):
            seccomp.build_program(policy)
        with self.assertRaises(seccomp.PolicyError):
            seccomp.validate_program([(0x06, 0, 0, 0x7FFF0000)])
        with self.assertRaises(seccomp.PolicyError):
            seccomp.simulate(
                [(0x06, 0, 0, 0x7FFF0000)], 0, seccomp.AUDIT_ARCH_X86_64
            )

    def test_rust_and_python_agree_differentially(self):
        """Seeded differential: the Rust module and the pure-Python
        compiler must produce byte-identical programs and identical
        verdicts for identical inputs.

        Runs only where the Rust cdylib is actually built (the CI
        conformance job builds it and sets NYRQIS_RUST_LIB); hosts
        without the crate skip it. This is the strongest "ported"
        signal short of the forced-mode gate itself.
        """
        lib = seccomp._load_rust_backend()
        if lib is None:
            self.skipTest("Rust seccomp backend not built on this host")
        rng = random.Random(20260813)
        cap_sets = [
            set(),
            {Capability.CAP_FILESYSTEM_WRITE.value},
            {
                Capability.CAP_NETWORK_SOCKET.value,
                Capability.CAP_NETWORK_BIND.value,
            },
            {Capability.CAP_PROCESS_SPAWN.value},
            {c.value for c in Capability},
        ]
        for arch in (seccomp.SyscallArch.X86_64, seccomp.SyscallArch.AARCH64):
            for build in (seccomp.build_policy, seccomp.build_allowlist_policy):
                for caps in cap_sets:
                    policy = build(caps, arch=arch)
                    label = f"{build.__name__} {arch.value} {sorted(caps)}"
                    rust_prog = seccomp._rust_build_program(lib, policy)
                    py_prog = seccomp._build_program_python(policy)
                    self.assertEqual(rust_prog, py_prog, f"program mismatch: {label}")
                    table = seccomp._SYSCALLS[arch]
                    names = [n for n, v in table.items() if v is not None and v >= 0]
                    for _ in range(15):
                        name = rng.choice(names)
                        nr = table[name]
                        args = [rng.getrandbits(64) for _ in range(6)]
                        rv = seccomp._rust_simulate(
                            lib, rust_prog, nr, arch.audit_arch, args
                        )
                        pv = seccomp._simulate_python(
                            py_prog, nr, arch.audit_arch, args
                        )
                        self.assertEqual(
                            rv, pv, f"verdict mismatch: {label} {name} nr={nr} args={args}"
                        )


class TestRustSyscallsLoader(unittest.TestCase):
    """ADR-0020 priority #2 FFI loader behavior (see
    rust/syscalls/README.md): the fallback contract and error mapping
    for the syscalls module. Like TestRustFfILoader, these pin the
    loader on hosts WITHOUT the crate built; when the crate lands, the
    CI conformance job (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        rust_syscalls._RUST_LIB = None
        rust_syscalls._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        rust_syscalls._RUST_LIB = None
        rust_syscalls._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        """Point the loader at a guaranteed-absent library."""
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_syscalls.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_syscalls.so"
        self.assertEqual(
            rust_syscalls._rust_lib_candidates(),
            ["/custom/libnyrqis_syscalls.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            rust_syscalls._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            rust_syscalls._raise_rust_error(-4096, "test")

    def test_absent_backend_falls_back_to_ctypes_hostname(self):
        self._no_backend()
        # This test exercises the FALLBACK path, so it must not inherit
        # NYRQIS_RUST_FORCE from the CI conformance job env. tearDown
        # restores the env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(rust_syscalls._load_rust_backend())
        fake_libc = mock.Mock()
        fake_libc.sethostname.return_value = 0
        with mock.patch(
            "backend.rust_syscalls.ctypes.CDLL", return_value=fake_libc
        ):
            self.assertTrue(rust_syscalls.set_hostname("test-host"))
        fake_libc.sethostname.assert_called_once()
        self.assertEqual(fake_libc.sethostname.call_args.args[0], b"test-host")

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            rust_syscalls.set_hostname("test-host")

    def test_ffi_routing_with_fake_lib(self):
        # With a lib loaded, set_hostname must drive the FFI entry point
        # and never touch ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_syscalls_version.return_value = rust_syscalls.MIN_RUST_ABI_VERSION
        fake.nyrqis_syscalls_sethostname.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ), mock.patch("backend.rust_syscalls.ctypes.CDLL") as cdll_mock:
            self.assertTrue(rust_syscalls.set_hostname("ffi-host"))
        fake.nyrqis_syscalls_sethostname.assert_called_once()
        cdll_mock.assert_not_called()

    def test_ctypes_prctl_fallback_after_sethostname_failure(self):
        # No crate: sethostname(2) fails, so set_hostname must reach the
        # prctl(PR_SET_HOSTNAME) fallback with a real buffer address
        # (regression pin: ctypes.cast on a raw bytes object raises).
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        # Prime the loader cache BEFORE mocking ctypes.CDLL, so the mock
        # (which fakes libc, not the Rust loader) is never seen by it.
        self.assertIsNone(rust_syscalls._load_rust_backend())
        fake_libc = mock.Mock()
        fake_libc.sethostname.return_value = -1  # syscall fails
        fake_libc.prctl.return_value = 0  # fallback succeeds
        with mock.patch(
            "backend.rust_syscalls.ctypes.CDLL", return_value=fake_libc
        ), mock.patch(
            "backend.rust_syscalls.ctypes.get_errno", return_value=errno.EPERM
        ):
            self.assertTrue(rust_syscalls.set_hostname("fallback-host"))
        self.assertEqual(fake_libc.prctl.call_args.args[0], rust_syscalls.PR_SET_HOSTNAME)
        self.assertNotEqual(fake_libc.prctl.call_args.args[1], 0)  # buffer address

    def test_ffi_prctl_fallback_when_sethostname_fails(self):
        # Crate loaded: FFI sethostname returns -EPERM (kernel answer),
        # so set_hostname falls through to prctl — also through the FFI.
        fake = mock.Mock()
        fake.nyrqis_syscalls_sethostname.return_value = -errno.EPERM
        fake.nyrqis_syscalls_prctl.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            self.assertTrue(rust_syscalls.set_hostname("ffi-fallback-host"))
        prctl_args = fake.nyrqis_syscalls_prctl.call_args.args
        self.assertEqual(prctl_args[0], rust_syscalls.PR_SET_HOSTNAME)
        self.assertNotEqual(prctl_args[1], 0)  # buffer address, not a stray cast

    def test_prctl_routes_through_ffi_when_loaded(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_prctl.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            self.assertEqual(rust_syscalls.prctl(10, 0x1234), 0)
        fake.nyrqis_syscalls_prctl.assert_called_once_with(10, 0x1234, 0, 0, 0)

    def test_unshare_routes_through_ffi_when_loaded(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_unshare.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            rust_syscalls.unshare(0x20000)  # CLONE_NEWNS — must not raise
        fake.nyrqis_syscalls_unshare.assert_called_once_with(0x20000)

    def test_unshare_negative_rc_becomes_oserror(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_unshare.return_value = -errno.EPERM
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            with self.assertRaises(OSError) as cm:
                rust_syscalls.unshare(0x20000)
        self.assertEqual(cm.exception.errno, errno.EPERM)

    def test_clone_new_flag_constants_are_stable_uapi(self):
        # The direct-syscall launcher's namespace mask is the Linux UAPI
        # contract (the Rust crate consumes the same bits via unshare(2));
        # pin them so a transcription slip is caught at the boundary.
        self.assertEqual(rust_syscalls.CLONE_NEWNS, 0x0002_0000)
        self.assertEqual(rust_syscalls.CLONE_NEWUTS, 0x0400_0000)
        self.assertEqual(rust_syscalls.CLONE_NEWIPC, 0x0800_0000)
        self.assertEqual(rust_syscalls.CLONE_NEWUSER, 0x1000_0000)
        self.assertEqual(rust_syscalls.CLONE_NEWPID, 0x2000_0000)

    def test_mount_proc_routes_through_ffi_when_loaded(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_mount_proc.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            self.assertEqual(rust_syscalls.mount_proc(), 0)
        fake.nyrqis_syscalls_mount_proc.assert_called_once_with()

    def test_mount_proc_negative_rc_returned(self):
        # mount_proc returns 0 or -errno (no OSError): the caller (the
        # container PID-1) inspects the rc and exits 125 on failure.
        fake = mock.Mock()
        fake.nyrqis_syscalls_mount_proc.return_value = -errno.EPERM
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            self.assertEqual(rust_syscalls.mount_proc(), -errno.EPERM)

    def test_mount_proc_ctypes_fallback_when_no_backend(self):
        # No crate: mount_proc must fall back to ctypes libc.mount with
        # the hardened flag set and proc paths.
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(rust_syscalls._load_rust_backend())
        fake_libc = mock.Mock()
        fake_libc.mount.return_value = 0
        with mock.patch(
            "backend.rust_syscalls.ctypes.CDLL", return_value=fake_libc
        ):
            self.assertEqual(rust_syscalls.mount_proc(), 0)
        args = fake_libc.mount.call_args.args
        self.assertEqual(args[:3], (b"proc", b"/proc", b"proc"))
        self.assertEqual(args[3], rust_syscalls.MS_PROC_MOUNT)

    def test_mount_routes_through_ffi_when_loaded(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_mount.return_value = 0
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            self.assertEqual(
                rust_syscalls.mount(b"src", b"/mnt", b"ext4", 0), 0
            )
        args = fake.nyrqis_syscalls_mount.call_args.args
        # The path args cross the boundary as buffer addresses (the FFI
        # takes caller-owned buffers); the flags and data must arrive as
        # the plain integers/pointers the loader passed.
        self.assertEqual(args[3], 0)  # flags
        self.assertIsNone(args[4])  # data defaults to NULL

    def test_mount_ctypes_fallback_when_no_backend(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(rust_syscalls._load_rust_backend())
        fake_libc = mock.Mock()
        fake_libc.mount.return_value = 0
        with mock.patch(
            "backend.rust_syscalls.ctypes.CDLL", return_value=fake_libc
        ):
            self.assertEqual(
                rust_syscalls.mount(b"proc", b"/proc", b"proc", 0), 0
            )
        self.assertEqual(fake_libc.mount.call_args.args[:3],
                         (b"proc", b"/proc", b"proc"))


def _codec_corpora():
    """The corpus set the codec differential tests run: empty, tiny,
    block-sized compressible, zeroes, incompressible random, and mixed."""
    return [
        b"",
        b"a",
        b"compressible-data;" * 4096,  # 64 KiB text-like
        b"\x00" * 65536,               # 64 KiB zeroes (very compressible)
        os.urandom(4096),               # incompressible
        b"mixed-data-" * 1000 + os.urandom(64),
    ]


class TestNyFSCodecLoader(unittest.TestCase):
    """ADR-0020 priority #3 FFI loader behavior (see
    rust/nyfs/README.md): the fallback contract and error mapping for
    the NyFS block codec module. Like TestRustSyscallsLoader, these pin
    the loader on hosts WITHOUT the crate built; when the crate lands,
    the CI conformance job (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        nyfs_codec._RUST_LIB = None
        nyfs_codec._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        nyfs_codec._RUST_LIB = None
        nyfs_codec._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        """Point the loader at a guaranteed-absent library."""
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_nyfs.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_nyfs.so"
        self.assertEqual(
            nyfs_codec._rust_lib_candidates(),
            ["/custom/libnyrqis_nyfs.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            nyfs_codec._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_checksum_is_valueerror(self):
        with self.assertRaises(ValueError) as cm:
            nyfs_codec._raise_rust_error(nyfs_codec.RUST_ERR_CHECKSUM, "test")
        self.assertIn("checksum", str(cm.exception).lower())

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            nyfs_codec._raise_rust_error(nyfs_codec.RUST_ERR_INTERNAL, "test")

    def test_absent_backend_falls_back_to_hashlib_checksum(self):
        self._no_backend()
        # This test exercises the FALLBACK path, so it must not inherit
        # NYRQIS_RUST_FORCE from the CI conformance job env. tearDown
        # restores the env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(nyfs_codec._load_rust_backend())
        self.assertEqual(
            nyfs_codec.checksum(b"abc"),
            hashlib.sha256(b"abc").hexdigest(),
        )

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            nyfs_codec.checksum(b"abc")

    def test_ffi_routing_with_fake_lib(self):
        # With a lib loaded, checksum must drive the FFI entry point and
        # never touch ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_nyfs_version.return_value = nyfs_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_nyfs_sha256.return_value = 0
        with mock.patch.object(
            nyfs_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("fuse.nyfs_codec.ctypes.CDLL") as cdll_mock:
            digest = nyfs_codec.checksum(b"ffi-data")
        fake.nyrqis_nyfs_sha256.assert_called_once()
        cdll_mock.assert_not_called()
        # A zero-filled digest buffer hex-encodes to 64 zero chars.
        self.assertEqual(digest, "00" * 32)

    def test_rust_call_failure_falls_back_in_normal_mode(self):
        # A Rust-side routing failure must NOT break non-force users: the
        # Python floor answers. This is the NORMAL-mode path, so it must
        # not inherit NYRQIS_RUST_FORCE from a CI conformance gate env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        fake = mock.Mock()
        fake.nyrqis_nyfs_version.return_value = nyfs_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_nyfs_sha256.side_effect = OSError("boom")
        with mock.patch.object(
            nyfs_codec, "_load_rust_backend", return_value=fake
        ):
            digest = nyfs_codec.checksum(b"abc")
        self.assertEqual(digest, hashlib.sha256(b"abc").hexdigest())

    def test_force_mode_raises_on_rust_call_failure(self):
        fake = mock.Mock()
        fake.nyrqis_nyfs_version.return_value = nyfs_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_nyfs_sha256.side_effect = OSError("boom")
        with mock.patch.object(
            nyfs_codec, "_load_rust_backend", return_value=fake
        ), mock.patch.dict(os.environ, {"NYRQIS_RUST_FORCE": "1"}):
            with self.assertRaises(OSError):
                nyfs_codec.checksum(b"abc")

    def test_compress_fallback_without_zstandard_stores_raw(self):
        # The floor stores data uncompressed when zstandard is absent
        # (identical to NyFSBlock.compress's ImportError path).
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(nyfs_codec._load_rust_backend())
        with mock.patch.dict(sys.modules, {"zstandard": None}):
            self.assertEqual(nyfs_codec.compress(b"raw-data"), b"raw-data")

    def test_decompress_verify_fallback_roundtrip(self):
        # The floor path (zstandard or uncompressed) must roundtrip and
        # verify, whatever the host's module availability.
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(nyfs_codec._load_rust_backend())
        data = b"roundtrip-data;" * 16
        compressed = nyfs_codec.compress(data)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(nyfs_codec.decompress_verify(compressed, digest), data)

    def test_decompress_verify_fallback_mismatch_raises_valueerror(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(nyfs_codec._load_rust_backend())
        data = b"real-data;" * 16
        compressed = nyfs_codec.compress(data)
        with self.assertRaises(ValueError) as cm:
            nyfs_codec.decompress_verify(compressed, "ab" * 32)
        self.assertIn("checksum", str(cm.exception).lower())


def _wire_message_cases():
    """The message set the IPC codec differential tests run: all five
    types, absent/present reply_to, empty and large payloads, empty and
    multi-capability transfers, plain and nested metadata."""
    return [
        # (type_index, message_id, sender, receiver, reply_to, payload, caps, metadata)
        (0, "m-empty", "c-a", "c-b", None, b"", [], {}),
        (0, "m-payload", "c-a", "c-b", None, b"hello wire", [], {}),
        (1, "m-recv", "c-a", "c-b", None, b"", [], {}),
        (2, "m-call", "c-a", "c-b", None, b"request", ["CAP_IPC_SEND"], {"k": 1}),
        (3, "m-reply", "c-a", "c-b", "m-call", b"response", [], {}),
        (4, "m-notify", "c-a", "c-b", None, b"", [], {"evt": "respawn"}),
        (0, "m-multi", "c-a", "c-b", "m-parent", b"x" * 4096,
         ["CAP_IPC_SEND", "CAP_IPC_RECEIVE", "CAP_IPC_CALL"],
         {"nested": {"deep": [1, 2, 3]}}),
        (2, "m-big", "container-alpha", "container-beta", None,
         b"payload-bytes;" * 4096, [], {"blob": "\u00e9\u00fc"}),
    ]


class TestIPCWireLoader(unittest.TestCase):
    """ADR-0020 priority #4 FFI loader behavior (see rust/ipc/README.md):
    the fallback contract and error mapping for the IPC wire codec
    module. Like the seccomp/syscalls/nyfs loader tests, these pin the
    loader on hosts WITHOUT the crate built; when the crate lands, the
    CI conformance job (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        ipc_codec._RUST_LIB = None
        ipc_codec._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        ipc_codec._RUST_LIB = None
        ipc_codec._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_ipc.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_ipc.so"
        self.assertEqual(
            ipc_codec._rust_lib_candidates(),
            ["/custom/libnyrqis_ipc.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            ipc_codec._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_invalid_wire_is_valueerror(self):
        with self.assertRaises(ValueError) as cm:
            ipc_codec._raise_rust_error(ipc_codec.RUST_ERR_INVALID_WIRE, "t")
        self.assertIn("wire format", str(cm.exception))

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            ipc_codec._raise_rust_error(ipc_codec.RUST_ERR_INTERNAL, "test")

    def test_floor_encode_is_byte_identical(self):
        # The canonical layout, pinned byte-for-byte (the Rust encoder
        # must match this exactly — see encode_layout_is_canonical in the
        # crate's tests).
        wire = ipc_codec._py_encode(
            2, 1234.5, b"id1", b"s1", b"r1", b"", b"hello", b"", b"{}"
        )
        self.assertEqual(wire[:4], b"NYRQ")
        self.assertEqual(wire[4], 1)
        self.assertEqual(wire[5], 2)
        self.assertEqual(wire[6:14], struct.pack("<d", 1234.5))
        self.assertEqual(wire[14:18], struct.pack("<I", 3))
        self.assertEqual(wire[18:21], b"id1")
        self.assertEqual(wire[21:25], struct.pack("<I", 2))
        self.assertEqual(wire[25:27], b"s1")
        self.assertEqual(wire[27:31], struct.pack("<I", 2))
        self.assertEqual(wire[31:33], b"r1")
        self.assertEqual(wire[33:37], struct.pack("<I", 0))
        self.assertEqual(wire[37:41], struct.pack("<I", 5))
        self.assertEqual(wire[41:46], b"hello")
        self.assertEqual(wire[46:50], struct.pack("<I", 0))
        self.assertEqual(wire[50:54], struct.pack("<I", 2))
        self.assertEqual(wire[54:56], b"{}")
        self.assertEqual(len(wire), 56)

    def test_absent_backend_falls_back_to_struct_floor(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(ipc_codec._load_rust_backend())
        wire = ipc_codec.encode(0, 1.5, "mid", "cs", "cr", None, b"p", [], b"{}")
        fields = ipc_codec.decode(wire)
        self.assertEqual(fields["message_id"], b"mid")
        self.assertEqual(fields["payload"], b"p")
        self.assertEqual(fields["timestamp"], 1.5)

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            ipc_codec.encode(0, 1.0, "m", "s", "r", None, b"", [], b"{}")

    def test_ffi_routing_with_fake_lib(self):
        # With a lib loaded, encode must drive the FFI entry point with
        # the expected arguments and never touch ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_ipc_version.return_value = ipc_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_ipc_encode.return_value = ipc_codec.RUST_ERR_INTERNAL
        # FORCE mode: a Rust failure must surface, not fall back (in
        # normal mode the -4096 would correctly fall back to the floor).
        with mock.patch.object(
            ipc_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.ipc_codec.ctypes.CDLL") as cdll_mock, mock.patch.dict(
            os.environ, {"NYRQIS_RUST_FORCE": "1"}
        ):
            with self.assertRaises(RuntimeError):
                ipc_codec.encode(2, 7.5, "mid", "s1", "r1", "call-id",
                                 b"payload", ["CAP_IPC_SEND"], b"{}")
        fake.nyrqis_ipc_encode.assert_called_once()
        cdll_mock.assert_not_called()
        args = fake.nyrqis_ipc_encode.call_args.args
        self.assertEqual(args[0], 2)  # message_type
        self.assertEqual(args[1], 7.5)  # timestamp

    def test_rust_call_failure_falls_back_in_normal_mode(self):
        # NORMAL-mode path: a Rust routing failure must not break
        # non-force users. Must not inherit NYRQIS_RUST_FORCE from a gate
        # env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        fake = mock.Mock()
        fake.nyrqis_ipc_version.return_value = ipc_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_ipc_encode.side_effect = OSError("boom")
        with mock.patch.object(
            ipc_codec, "_load_rust_backend", return_value=fake
        ):
            wire = ipc_codec.encode(0, 1.0, "m", "s", "r", None, b"p", [], b"{}")
        self.assertEqual(ipc_codec.decode(wire)["message_id"], b"m")

    def test_force_mode_raises_on_rust_call_failure(self):
        fake = mock.Mock()
        fake.nyrqis_ipc_version.return_value = ipc_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_ipc_encode.side_effect = OSError("boom")
        with mock.patch.object(
            ipc_codec, "_load_rust_backend", return_value=fake
        ), mock.patch.dict(os.environ, {"NYRQIS_RUST_FORCE": "1"}):
            with self.assertRaises(OSError):
                ipc_codec.encode(0, 1.0, "m", "s", "r", None, b"p", [], b"{}")

    def test_floor_decode_rejects_malformed(self):
        wire = ipc_codec._py_encode(0, 1.0, b"m", b"s", b"r", b"", b"p", b"", b"{}")
        for bad in (
            wire[:3],                                        # truncated header
            b"X" + wire[1:],                                 # bad magic
            wire[:4] + bytes([2]) + wire[5:],                 # wrong version
            wire[:-1],                                       # truncated tail
            wire + b"\x00",                                  # trailing garbage
        ):
            with self.assertRaises(ValueError):
                ipc_codec._py_decode(bad)

    def test_caps_flat_roundtrip(self):
        caps = ["CAP_IPC_SEND", "CAP_IPC_RECEIVE"]
        self.assertEqual(ipc_codec.split_caps_flat(ipc_codec.build_caps_flat(caps)), caps)
        self.assertEqual(ipc_codec.split_caps_flat(b""), [])

    def test_message_to_wire_from_wire_roundtrip(self):
        # The core wiring: a real IPCMessage through to_wire/from_wire on
        # the floor path (byte-identical to the Rust path — verified in
        # the conformance class).
        message = IPCMessage(
            message_type=IPCMessageType.CALL,
            sender_id="c-a", receiver_id="c-b",
            payload=b"roundtrip", capabilities=["CAP_IPC_SEND"],
            metadata={"n": 1}, reply_to=None,
        )
        restored = IPCMessage.from_wire(message.to_wire())
        self.assertEqual(restored.message_type, message.message_type)
        self.assertEqual(restored.sender_id, message.sender_id)
        self.assertEqual(restored.receiver_id, message.receiver_id)
        self.assertEqual(restored.payload, message.payload)
        self.assertEqual(restored.capabilities, message.capabilities)
        self.assertEqual(restored.metadata, message.metadata)


class TestRustSyscallsConformance(unittest.TestCase):
    """Real-FFI conformance for the syscalls module (the direct-syscall
    launcher's primitives). Skips on hosts without the crate; the CI
    ``rust-syscalls-conformance`` job builds it and sets
    ``NYRQIS_RUST_LIB`` so these drive the actual Rust module. This is
    the syscalls analogue of ``TestRustFfILoader``'s differential test:
    the pure-Python ctypes floor and the Rust module must agree on the
    -errno contract, ABI version, and flag vocabulary.
    """

    @classmethod
    def setUpClass(cls):
        cls.lib = rust_syscalls._load_rust_backend()

    def _skip_unless_lib(self):
        if self.lib is None:
            self.skipTest("Rust syscalls backend not built on this host")

    def test_abi_version_meets_contract(self):
        self._skip_unless_lib()
        version = self.lib.nyrqis_syscalls_version()
        self.assertGreaterEqual(version, rust_syscalls.MIN_RUST_ABI_VERSION)

    def test_unshare_invalid_flags_raises_einval(self):
        self._skip_unless_lib()
        # unshare(0xFFFFFFFF) -> EINVAL through the Rust module, surfaced
        # as OSError by the loader (the ctypes floor raises the same).
        with self.assertRaises(OSError) as cm:
            rust_syscalls.unshare(0xFFFF_FFFF)
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_prctl_get_name_roundtrip(self):
        self._skip_unless_lib()
        # PR_GET_NAME (16) writes the calling thread's name — a safe,
        # deterministic read that exercises the variadic prctl wrapper.
        # (15 is PR_SET_NAME: calling it with an empty buffer would
        # succeed and write nothing, which is exactly the failure this
        # test must not make.)
        buf = ctypes.create_string_buffer(16)
        rc = rust_syscalls.prctl(16, ctypes.addressof(buf), 0, 0, 0)
        self.assertEqual(rc, 0)
        self.assertGreater(len(buf.value), 0)

    def test_mount_missing_target_returns_negative_rc(self):
        self._skip_unless_lib()
        # A mount whose target does not exist fails with -ENOENT (or
        # -EPERM/-EACCES on some kernels) — proving the wrapper reaches
        # the kernel and returns the -errno convention without side
        # effects.
        rc = rust_syscalls.mount(
            b"proc", b"/nonexistent-nyrqis-conformance", b"proc", 0
        )
        self.assertLess(rc, 0)
        self.assertIn(-rc, (errno.ENOENT, errno.EPERM, errno.EACCES))

    def test_mount_proc_returns_negative_rc_when_unprivileged(self):
        self._skip_unless_lib()
        # mount_proc on a host where the caller lacks CAP_SYS_ADMIN fails
        # with a negative errno and never crashes (the CI runner is
        # unprivileged).
        rc = rust_syscalls.mount_proc()
        self.assertLess(rc, 0)


class TestNyFSCodecConformance(unittest.TestCase):
    """Real-FFI conformance for the NyFS block codec (ADR-0020
    priority #3). Skips on hosts without the crate; the CI
    ``rust-nyfs-conformance`` job builds it and sets ``NYRQIS_RUST_LIB``
    so these drive the actual Rust module. The differential core: the
    Rust module and the pure-Python floor (hashlib/zstandard) must
    agree on every corpus — checksums byte-identical, roundtrips
    byte-identical, and integrity failures surfaced as the same
    ``ValueError`` the floor raises.
    """

    @classmethod
    def setUpClass(cls):
        cls.lib = nyfs_codec._load_rust_backend()

    def _skip_unless_lib(self):
        if self.lib is None:
            self.skipTest("Rust NyFS codec backend not built on this host")

    def test_abi_version_meets_contract(self):
        self._skip_unless_lib()
        version = self.lib.nyrqis_nyfs_version()
        self.assertGreaterEqual(version, nyfs_codec.MIN_RUST_ABI_VERSION)

    def test_sha256_matches_python_hashlib(self):
        self._skip_unless_lib()
        for data in _codec_corpora():
            self.assertEqual(
                nyfs_codec.checksum(data),
                hashlib.sha256(data).hexdigest(),
                f"Rust checksum diverges for {len(data)}-byte corpus",
            )

    def test_compress_decompress_roundtrip_matches_python(self):
        self._skip_unless_lib()
        for data in _codec_corpora():
            compressed = nyfs_codec.compress(data, 3)
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(
                nyfs_codec.decompress_verify(compressed, digest),
                data,
                f"Rust roundtrip lost data for {len(data)}-byte corpus",
            )

    def test_decompress_verify_rejects_wrong_checksum(self):
        self._skip_unless_lib()
        data = b"integrity-check;" * 1024
        compressed = nyfs_codec.compress(data, 3)
        with self.assertRaises(ValueError) as cm:
            nyfs_codec.decompress_verify(compressed, "ab" * 32)
        self.assertIn("checksum", str(cm.exception).lower())

    def test_compress_level_3_shrinks_compressible(self):
        self._skip_unless_lib()
        data = b"compressible-data;" * 4096  # 64 KiB text-like
        compressed = nyfs_codec.compress(data, 3)
        self.assertLess(len(compressed), len(data))


class TestIPCCodecConformance(unittest.TestCase):
    """Real-FFI conformance for the IPC wire codec (ADR-0020 priority
    #4). Skips on hosts without the crate; the CI
    ``rust-ipc-conformance`` job builds it and sets ``NYRQIS_RUST_LIB``
    so these drive the actual Rust module. The differential core: the
    Rust module and the pure-Python floor must agree byte-for-byte on
    the wire format and field-for-field on decoding, across the message
    corpus, and must reject the same malformed inputs.
    """

    @classmethod
    def setUpClass(cls):
        cls.lib = ipc_codec._load_rust_backend()

    def _skip_unless_lib(self):
        if self.lib is None:
            self.skipTest("Rust IPC wire codec not built on this host")

    def test_abi_version_meets_contract(self):
        self._skip_unless_lib()
        self.assertGreaterEqual(
            self.lib.nyrqis_ipc_version(), ipc_codec.MIN_RUST_ABI_VERSION
        )

    def test_encode_matches_python_floor_byte_identical(self):
        self._skip_unless_lib()
        for case in _wire_message_cases():
            mtype, mid, sender, receiver, reply, payload, caps, meta = case
            ts = 1234.5
            kwargs = dict(
                message_type=mtype, timestamp=ts,
                message_id=mid, sender_id=sender, receiver_id=receiver,
                reply_to=reply, payload=payload, capabilities=caps,
                metadata_blob=json.dumps(meta, sort_keys=True).encode(),
            )
            rust_wire = ipc_codec.encode(**kwargs)  # drives the Rust module
            py_wire = ipc_codec._py_encode(
                mtype, ts,
                mid.encode(), sender.encode(), receiver.encode(),
                (reply or "").encode(), payload,
                ipc_codec.build_caps_flat(caps),
                json.dumps(meta, sort_keys=True).encode(),
            )
            self.assertEqual(
                rust_wire, py_wire,
                f"Rust wire diverges from floor for {mid}",
            )

    def test_decode_matches_python_floor(self):
        self._skip_unless_lib()
        for case in _wire_message_cases():
            mtype, mid, sender, receiver, reply, payload, caps, meta = case
            wire = ipc_codec._py_encode(
                mtype, 1234.5,
                mid.encode(), sender.encode(), receiver.encode(),
                (reply or "").encode(), payload,
                ipc_codec.build_caps_flat(caps),
                json.dumps(meta, sort_keys=True).encode(),
            )
            rust_fields = ipc_codec.decode(wire)   # drives the Rust module
            py_fields = ipc_codec._py_decode(wire)
            self.assertEqual(rust_fields, py_fields, f"decode diverges for {mid}")
            self.assertEqual(rust_fields["message_id"], mid.encode())
            self.assertEqual(rust_fields["timestamp"], 1234.5)

    def test_roundtrip_preserves_message_through_core(self):
        self._skip_unless_lib()
        for case in _wire_message_cases():
            mtype, mid, sender, receiver, reply, payload, caps, meta = case
            message = IPCMessage(
                message_type=list(IPCMessageType)[mtype],
                message_id=mid, sender_id=sender, receiver_id=receiver,
                reply_to=reply, payload=payload, capabilities=caps,
                metadata=meta,
            )
            restored = IPCMessage.from_wire(message.to_wire())
            self.assertEqual(restored.message_type, message.message_type)
            self.assertEqual(restored.payload, payload)
            self.assertEqual(restored.capabilities, caps)
            self.assertEqual(restored.metadata, meta)
            self.assertEqual(restored.reply_to, reply)

    def test_rust_decode_rejects_malformed_like_floor(self):
        self._skip_unless_lib()
        wire = ipc_codec._py_encode(0, 1.0, b"m", b"s", b"r", b"", b"p", b"", b"{}")
        for bad in (
            wire[:3],
            b"X" + wire[1:],
            wire[:4] + bytes([2]) + wire[5:],
            wire[:-1],
            wire + b"\x00",
        ):
            with self.assertRaises(ValueError):
                ipc_codec.decode(bad)


def _container_plan_cases():
    """The container-launch config set the codec differential tests
    run: seccomp on/off, default-deny on/off, hostile hostnames, empty
    and multi-entry commands, CPU quota on/off.

    Returns (hostname, command, policy_path, default_deny, memory_mb,
    pid_limit, cpu_quota_us, cpu_period_us).
    """
    return [
        ("ctr-1", ["/bin/sh"], "", False, 128, 16, None, 100000),
        ("evil; rm -rf /", ["/bin/sh", "-c", "echo hi"], "", False,
         256, 32, None, 100000),
        ("nyrqis-test", ["/bin/echo", "hi"], "/tmp/pol.json", True,
         512, 64, 50000, 100000),
        ("\u00fcnicode-h\u00f6st", [], "/tmp/p.json", False,
         64, 8, 100000, 100000),
        ("c5", ["a", "b c", ""], "", True, 1024, 128, None, 100000),
        ("c6", ["/bin/true"], "/tmp/x.json", False, 256, 16, 0, 100000),
    ]


class TestContainerPrimitivesLoader(unittest.TestCase):
    """ADR-0020 priority #5 FFI loader behavior (see
    rust/container/README.md): the fallback contract and error mapping
    for the container launch-plan primitives. Like the
    seccomp/syscalls/nyfs/ipc loader tests, these pin the loader on
    hosts WITHOUT the crate built; when the crate lands, the CI
    conformance job (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    PY = "/usr/bin/python3"
    LAUNCHER = "/opt/nyrqis/launcher.py"

    def setUp(self):
        self._env = dict(os.environ)
        container_codec._RUST_LIB = None
        container_codec._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        container_codec._RUST_LIB = None
        container_codec._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_container.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_container.so"
        self.assertEqual(
            container_codec._rust_lib_candidates(),
            ["/custom/libnyrqis_container.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            container_codec._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_invalid_wire_is_valueerror(self):
        with self.assertRaises(ValueError) as cm:
            container_codec._raise_rust_error(
                container_codec.RUST_ERR_INVALID_WIRE, "t")
        self.assertIn("flat buffer", str(cm.exception))

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            container_codec._raise_rust_error(
                container_codec.RUST_ERR_INTERNAL, "test")

    def test_floor_launcher_argv_is_byte_identical(self):
        # The canonical layout, pinned byte-for-byte (the Rust encoder
        # must match this exactly — see launcher_argv_layout_is_canonical
        # in the crate's tests).
        flat = container_codec.build_command_flat(["/bin/sh"])
        wire = container_codec._py_launcher_argv(
            self.PY.encode(), self.LAUNCHER.encode(), b"ctr-1",
            b"", 0, flat,
        )
        self.assertEqual(wire[:4], b"NYRQ")
        self.assertEqual(wire[4], 1)
        self.assertEqual(wire[5:9], struct.pack("<I", 6))
        pos = 9
        for expected in (self.PY.encode(), self.LAUNCHER.encode(),
                         b"--hostname", b"ctr-1", b"--", b"/bin/sh"):
            (length,) = struct.unpack_from("<I", wire, pos)
            pos += 4
            self.assertEqual(wire[pos:pos + length], expected)
            pos += length
        self.assertEqual(pos, len(wire))

    def test_floor_cgroup_plan_is_byte_identical(self):
        wire = container_codec._py_cgroup_plan(b"ctr-1", 128, 16, None, 100000)
        self.assertEqual(wire[:4], b"NYRQ")
        self.assertEqual(wire[4], 1)
        self.assertEqual(wire[5:9], struct.pack("<I", 2))
        pos = 9
        (path_len,) = struct.unpack_from("<I", wire, pos)
        pos += 4
        self.assertEqual(wire[pos:pos + path_len], b"/sys/fs/cgroup/memory/ctr-1")
        pos += path_len
        self.assertEqual(
            struct.unpack_from("<I", wire, pos)[0], 2)
        pos += 4
        (klen,) = struct.unpack_from("<I", wire, pos)
        pos += 4
        self.assertEqual(wire[pos:pos + klen], b"memory.limit_in_bytes")
        pos += klen
        (vlen,) = struct.unpack_from("<I", wire, pos)
        pos += 4
        self.assertEqual(wire[pos:pos + vlen], b"134217728")

    def test_floor_root_maps_is_byte_identical(self):
        wire = container_codec._py_root_maps(1000, 1000)
        self.assertEqual(wire[:4], b"NYRQ")
        pos = 5
        for expected in (b"deny\n", b"0 1000 1\n", b"0 1000 1\n"):
            (length,) = struct.unpack_from("<I", wire, pos)
            pos += 4
            self.assertEqual(wire[pos:pos + length], expected)
            pos += length
        self.assertEqual(pos, len(wire))

    def test_floor_transition_valid_matches_nps010(self):
        legal = [("created", "running"), ("running", "suspended"),
                 ("running", "terminated"), ("suspended", "running"),
                 ("suspended", "terminated")]
        for f, t in legal:
            self.assertTrue(
                container_codec._py_transition_valid(f, t), f"{f}->{t}")
        for f, t in [("running", "created"), ("created", "suspended"),
                     ("terminated", "created"), ("running", "running")]:
            self.assertFalse(
                container_codec._py_transition_valid(f, t), f"{f}->{t}")
        with self.assertRaises(ValueError):
            container_codec._py_transition_valid("nonexistent", "running")

    def test_absent_backend_falls_back_to_floor(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(container_codec._load_rust_backend())
        argv = container_codec.launcher_argv(
            self.PY, self.LAUNCHER, "ctr-1", "", False, ["/bin/sh"])
        self.assertEqual(
            argv,
            [self.PY, self.LAUNCHER, "--hostname", "ctr-1", "--", "/bin/sh"],
        )
        plan = container_codec.cgroup_plan("ctr-1", 128, 16)
        self.assertEqual(plan["v1"][0][0], "/sys/fs/cgroup/memory/ctr-1")
        self.assertEqual(plan["v2"], [("memory.max", "134217728"),
                                       ("pids.max", "16")])
        maps = container_codec.root_maps(1000, 1000)
        self.assertEqual(maps, (b"deny\n", b"0 1000 1\n", b"0 1000 1\n"))
        self.assertTrue(container_codec.transition_valid("created", "running"))
        self.assertFalse(container_codec.transition_valid("running", "created"))

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            container_codec.launcher_argv(
                self.PY, self.LAUNCHER, "h", "", False, [])
        with self.assertRaises(RuntimeError):
            container_codec.transition_valid("created", "running")

    def test_ffi_routing_with_fake_lib(self):
        # With a lib loaded, launcher_argv must drive the FFI entry
        # point with the expected arguments and never touch
        # ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_container_version.return_value = \
            container_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_container_launcher_argv.return_value = \
            container_codec.RUST_ERR_INTERNAL
        with mock.patch.object(
            container_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("backend.container_codec.ctypes.CDLL") as cdll_mock, \
                mock.patch.dict(os.environ, {"NYRQIS_RUST_FORCE": "1"}):
            with self.assertRaises(RuntimeError):
                container_codec.launcher_argv(
                    self.PY, self.LAUNCHER, "h", "/tmp/p.json", True,
                    ["/bin/sh"])
        fake.nyrqis_container_launcher_argv.assert_called_once()
        cdll_mock.assert_not_called()
        args = fake.nyrqis_container_launcher_argv.call_args.args
        self.assertEqual(args[8], 1)  # default_deny

    def test_ffi_transition_valid_with_fake_lib(self):
        fake = mock.Mock()
        fake.nyrqis_container_version.return_value = \
            container_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_container_transition_valid.return_value = \
            container_codec.RUST_ERR_INVALID_TRANSITION
        with mock.patch.object(
            container_codec, "_load_rust_backend", return_value=fake
        ):
            self.assertFalse(
                container_codec.transition_valid("created", "suspended"))
        fake.nyrqis_container_transition_valid.assert_called_once_with(0, 2)

    def test_rust_call_failure_falls_back_in_normal_mode(self):
        # NORMAL-mode path: a Rust routing failure must not break
        # non-force users. Must not inherit NYRQIS_RUST_FORCE from a gate
        # env.
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        fake = mock.Mock()
        fake.nyrqis_container_version.return_value = \
            container_codec.MIN_RUST_ABI_VERSION
        fake.nyrqis_container_launcher_argv.side_effect = OSError("boom")
        with mock.patch.object(
            container_codec, "_load_rust_backend", return_value=fake
        ):
            argv = container_codec.launcher_argv(
                self.PY, self.LAUNCHER, "h", "", False, [])
        self.assertEqual(
            argv, [self.PY, self.LAUNCHER, "--hostname", "h", "--"])


class TestContainerPrimitivesConformance(unittest.TestCase):
    """Real-FFI conformance for the container launch-plan primitives
    (ADR-0020 priority #5). Skips on hosts without the crate; the CI
    ``rust-container-conformance`` job builds it and sets
    ``NYRQIS_RUST_LIB`` so these drive the actual Rust module. The
    differential core: the Rust module and the pure-Python floor must
    agree byte-for-byte on the launcher argv, cgroup plan, and root map
    wires across the config corpus, and agree on the NPS-010 §4 state
    machine.
    """

    @classmethod
    def setUpClass(cls):
        cls.lib = container_codec._load_rust_backend()

    def _skip_unless_lib(self):
        if self.lib is None:
            self.skipTest("Rust container launch-plan not built on this host")

    def test_abi_version_meets_contract(self):
        self._skip_unless_lib()
        self.assertGreaterEqual(
            self.lib.nyrqis_container_version(),
            container_codec.MIN_RUST_ABI_VERSION,
        )

    def test_launcher_argv_matches_python_floor_byte_identical(self):
        self._skip_unless_lib()
        py = TestContainerPrimitivesLoader.PY
        launcher = TestContainerPrimitivesLoader.LAUNCHER
        for host, command, policy, deny, _, _, _, _ in _container_plan_cases():
            flat = container_codec.build_command_flat(command)
            rust_wire = container_codec._rust_launcher_argv(
                self.lib,
                py.encode(), launcher.encode(), host.encode(),
                policy.encode(), int(deny), flat,
            )
            py_wire = container_codec._py_launcher_argv(
                py.encode(), launcher.encode(), host.encode(),
                policy.encode(), int(deny), flat,
            )
            self.assertEqual(
                rust_wire, py_wire,
                f"launcher argv wire diverges from floor for {host}",
            )
            # And the public surface decodes both identically.
            self.assertEqual(
                container_codec.launcher_argv(
                    py, launcher, host, policy, deny, command),
                container_codec._decode_launcher_argv(py_wire),
            )

    def test_cgroup_plan_matches_python_floor_byte_identical(self):
        self._skip_unless_lib()
        for host, _, _, _, mem, pids, quota, period in _container_plan_cases():
            cid = f"id-{host[:6]}"
            rust_wire = container_codec._rust_cgroup_plan(
                self.lib, cid.encode(), mem, pids, quota, period)
            py_wire = container_codec._py_cgroup_plan(
                cid.encode(), mem, pids, quota, period)
            self.assertEqual(
                rust_wire, py_wire,
                f"cgroup plan wire diverges from floor for {host}",
            )
            self.assertEqual(
                container_codec.cgroup_plan(cid, mem, pids, quota, period),
                container_codec._decode_cgroup_plan(py_wire),
            )

    def test_root_maps_matches_python_floor_byte_identical(self):
        self._skip_unless_lib()
        for uid, gid in ((0, 0), (1000, 1000), (65534, 65534), (2 ** 32 - 1, 1)):
            rust_wire = container_codec._rust_root_maps(self.lib, uid, gid)
            py_wire = container_codec._py_root_maps(uid, gid)
            self.assertEqual(
                rust_wire, py_wire,
                f"root maps diverge from floor for uid={uid} gid={gid}",
            )
            self.assertEqual(
                container_codec.root_maps(uid, gid),
                container_codec._decode_root_maps(py_wire),
            )

    def test_transition_valid_matches_python_floor(self):
        self._skip_unless_lib()
        states = ["created", "running", "suspended", "terminated"]
        for f in states:
            for t in states:
                rust_ans = container_codec.transition_valid(f, t)
                py_ans = container_codec._py_transition_valid(f, t)
                self.assertEqual(
                    rust_ans, py_ans,
                    f"transition_valid diverges from floor for {f}->{t}",
                )

    def test_launcher_args_wiring_through_manager(self):
        self._skip_unless_lib()
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(
            hostname="evil; rm -rf /",
            command=["/bin/sh", "-c", "echo hi"],
            capabilities=["CAP_FILESYSTEM_READ"],
        )
        container = manager.create(config)
        argv = manager._launcher_args(container, Path("launcher.py"))
        self.assertEqual(argv[argv.index("--hostname") + 1],
                         config.hostname)
        launcher_part = argv[:argv.index("--")]
        self.assertNotIn("sh", launcher_part)
        self.assertNotIn("-c", launcher_part)
        self.assertEqual(argv[argv.index("--") + 1:], config.command)
        manager._cleanup_policy_files()

    def test_cgroup_v1_plan_wiring_through_manager(self):
        self._skip_unless_lib()
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(
            limits=ResourceLimits(memory_mb=128, pid_limit=16))
        container = manager.create(config)
        plan = manager._cgroup_v1_plan(container)
        memory_settings = None
        for cgroup_path, settings in plan:
            if "memory.limit_in_bytes" in settings:
                memory_settings = settings
        self.assertIsNotNone(memory_settings)
        self.assertEqual(memory_settings["memory.limit_in_bytes"],
                         "134217728")
        self.assertEqual(memory_settings["notify_on_release"], "0")


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


class TestTransportRustLoader(unittest.TestCase):
    """ADR-0020 priority #6 FFI loader behavior (see rust/transport/):
    the fallback contract and error mapping for the Rust IPC transport
    hot path. Like the other migration loader tests, these pin the
    loader on hosts WITHOUT the crate built; when the crate lands, the
    CI conformance job (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        transport_codec._RUST_LIB = None
        transport_codec._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        transport_codec._RUST_LIB = None
        transport_codec._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_transport.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_transport.so"
        self.assertEqual(
            transport_codec._rust_lib_candidates(),
            ["/custom/libnyrqis_transport.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            transport_codec._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            transport_codec._raise_rust_error(
                transport_codec.RUST_ERR_INTERNAL, "test")

    def test_absent_backend_raises_backend_unavailable(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(transport_codec._load_rust_backend())
        with self.assertRaises(transport_codec.BackendUnavailable):
            transport_codec.send(3, b"x", "/tmp/p.sock")
        with self.assertRaises(transport_codec.BackendUnavailable):
            transport_codec.recv(3, 100)

    def test_absent_backend_endpoint_falls_back_to_python_floor(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        base = tempfile.mkdtemp(prefix="nyrqis-floor-transport-")
        try:
            a = UnixDatagramEndpoint(os.path.join(base, "a.sock")).bind()
            b = UnixDatagramEndpoint(os.path.join(base, "b.sock")).bind()
            try:
                payload = b"floor-frame"
                a.send(payload, b.path)
                got = b.receive(timeout=2.0)
                self.assertIsNotNone(got)
                self.assertEqual(got[0], payload)
                self.assertEqual(got[1], os.getpid())
            finally:
                a.close()
                b.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            transport_codec.send(3, b"x", "/tmp/p.sock")
        with self.assertRaises(RuntimeError):
            transport_codec.recv(3, 100)

    def test_ffi_send_routing_with_fake_lib(self):
        # With a lib loaded, send must drive the FFI entry point with
        # the expected arguments and never touch ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_transport_send.return_value = 0
        with mock.patch.object(
            transport_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.transport_codec.ctypes.CDLL") as cdll_mock:
            transport_codec.send(7, b"frame", "/tmp/dst.sock")
        fake.nyrqis_transport_send.assert_called_once()
        cdll_mock.assert_not_called()
        args = fake.nyrqis_transport_send.call_args.args
        self.assertEqual(args[0], 7)  # fd
        self.assertEqual(args[2], 5)  # wire_len
        self.assertEqual(args[3], b"/tmp/dst.sock")  # peer path

    def test_ffi_recv_routing_with_fake_lib(self):
        # recv must drive the FFI entry point, translate the written
        # outputs into the floor's tuple, and free both buffers.
        fake = mock.Mock()
        payload = b"recv-frame"

        def fake_recv(fd, ms, ow, ol, op, ou, og, osp):
            # The byref'd outputs arrive as CArgObjects; write through
            # them with cast (verified: cast works on CArgObject).
            fake._buf = ctypes.create_string_buffer(payload, len(payload))
            ctypes.cast(ol, ctypes.POINTER(ctypes.c_size_t))[0] = len(payload)
            ctypes.cast(ow, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = (
                ctypes.cast(fake._buf, ctypes.POINTER(ctypes.c_ubyte)))
            ctypes.cast(op, ctypes.POINTER(ctypes.c_int))[0] = os.getpid()
            ctypes.cast(ou, ctypes.POINTER(ctypes.c_int))[0] = os.getuid()
            ctypes.cast(og, ctypes.POINTER(ctypes.c_int))[0] = os.getgid()
            ctypes.cast(osp, ctypes.POINTER(ctypes.c_char_p))[0] = (
                ctypes.c_char_p(b"/tmp/sender.sock"))
            return 0

        fake.nyrqis_transport_recv.side_effect = fake_recv
        with mock.patch.object(
            transport_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.transport_codec.ctypes.CDLL") as cdll_mock:
            result = transport_codec.recv(7, 250)
        self.assertEqual(
            result,
            (payload, os.getpid(), os.getuid(), os.getgid(), "/tmp/sender.sock"),
        )
        fake.nyrqis_transport_recv.assert_called_once()
        cdll_mock.assert_not_called()
        self.assertEqual(fake.nyrqis_transport_free.call_count, 2)

    def test_force_mode_raises_on_rust_call_failure(self):
        fake = mock.Mock()
        fake.nyrqis_transport_send.return_value = transport_codec.RUST_ERR_INTERNAL
        with mock.patch.object(
            transport_codec, "_load_rust_backend", return_value=fake
        ), mock.patch.dict(os.environ, {"NYRQIS_RUST_FORCE": "1"}):
            with self.assertRaises(RuntimeError):
                transport_codec.send(3, b"x", "/tmp/p.sock")


class TestTransportConformance(unittest.TestCase):
    """ADR-0020 priority #6 differential: the Rust transport (via the
    FFI) must reproduce the Python floor's contract exactly — payload
    round-trip, kernel-attached (pid, uid, gid), sender path, timeout
    semantics, and error surfacing. Runs when the crate is built (the
    CI gate builds it and forces the class; locally it runs when the
    crate is present). Uses raw wire bytes only — it must not depend on
    the ipc-codec loader, so the transport-only gate stays honest.
    """

    @classmethod
    def setUpClass(cls):
        # Fresh probe: don't inherit the loader state the loader tests
        # leave behind, and honor NYRQIS_RUST_LIB/FORCE from a gate env.
        transport_codec._RUST_LIB = None
        transport_codec._RUST_LIB_CHECKED = False
        cls.available = transport_codec.available()

    def setUp(self):
        if not self.available:
            self.skipTest(
                "Rust IPC transport crate not built (CI gate builds it)")

    def test_endpoint_roundtrip_payload_creds_and_sender_path(self):
        base = tempfile.mkdtemp(prefix="nyrqis-rust-transport-")
        try:
            a = UnixDatagramEndpoint(os.path.join(base, "a.sock")).bind()
            b = UnixDatagramEndpoint(os.path.join(base, "b.sock")).bind()
            try:
                payload = b"NYRQ\x01\x02rust-frame"
                a.send(payload, b.path)
                got = b.receive(timeout=2.0)
                self.assertIsNotNone(got)
                data, pid, uid, gid, sender_path = got
                self.assertEqual(data, payload)
                self.assertEqual(pid, os.getpid())
                self.assertEqual(uid, os.getuid())
                self.assertEqual(gid, os.getgid())
                self.assertEqual(sender_path, a.path)
            finally:
                a.close()
                b.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_rust_timeout_returns_none(self):
        base = tempfile.mkdtemp(prefix="nyrqis-rust-transport-")
        try:
            ep = UnixDatagramEndpoint(os.path.join(base, "t.sock")).bind()
            try:
                self.assertIsNone(ep.receive(timeout=0.02))
            finally:
                ep.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_send_to_missing_peer_raises(self):
        base = tempfile.mkdtemp(prefix="nyrqis-rust-transport-")
        try:
            a = UnixDatagramEndpoint(os.path.join(base, "a.sock")).bind()
            try:
                with self.assertRaises(IPCTransportError):
                    a.send(b"x", os.path.join(base, "missing.sock"))
            finally:
                a.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitives))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerFreezer))
    suite.addTests(loader.loadTestsFromTestCase(TestNetworkNamespaceIsolation))
    suite.addTests(loader.loadTestsFromTestCase(TestCapabilityEnforcement))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCSemantics))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCTransport))
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
    suite.addTests(loader.loadTestsFromTestCase(TestRustFfILoader))
    suite.addTests(loader.loadTestsFromTestCase(TestRustSyscallsLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestDirectSyscallLaunch))
    suite.addTests(loader.loadTestsFromTestCase(TestRustSyscallsConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSCodecLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSCodecConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCWireLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCCodecConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestTransportRustLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestTransportConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesConformance))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
