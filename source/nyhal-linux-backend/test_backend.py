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
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import (
    Container, ContainerManager, ContainerConfig, ContainerState,
    ResourceLimits, _DIRECT_LAUNCH_TIMEOUT_S,
)
from backend.capability import (
    CapabilityManager, Capability, CapabilityGrant
)
from backend import seccomp
from backend import rust_syscalls
from backend import container_codec
from backend import rust_launcher  # compiled launcher-init locator (ADR-0020)
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
from ipc import loop as ipc_loop
from ipc.dispatch import IpcdLoopDispatcher
from ipc.registry import ContainerIpcRegistry
from ipc.service import BackendStatusService, ServiceRouter
from ipc.control import ControlService, DEFAULT_OPERATOR_ID
from boot.lifecycle import BootSequence, BootPhase, SecureBootStatus
import nyrqis_backend
import nyrqisctl


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


class TestContainerCapabilityLifecycle(unittest.TestCase):
    """Control-plane capability lifecycle (NPS-010 §5): each spawned
    container is initialized with its default grants (so it can
    authenticate at the transport server with CAP_IPC_SEND and call
    capability-gated services), and its grants are revoked when it
    terminates — mirroring the ipc registry hooks, keyed by container
    id (not pid), so both launch paths initialize.
    """

    def _manager(self, caps):
        return ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True,
            capability_manager=caps)

    def test_spawn_initializes_default_grants(self):
        caps = CapabilityManager()
        manager = self._manager(caps)
        container = manager.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        with mock.patch.object(manager, "_spawn") as spawn, \
                mock.patch.object(manager, "_setup_cgroups"), \
                mock.patch.object(manager, "_attach_to_cgroups"):
            manager.spawn(container)
        spawn.assert_called_once()
        self.assertTrue(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))
        self.assertTrue(caps.has_capability(
            container.id, Capability.CAP_SYSTEM_INFO))

    def test_spawn_failure_revokes_grants(self):
        caps = CapabilityManager()
        manager = self._manager(caps)
        container = manager.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        with mock.patch.object(
            manager, "_spawn", side_effect=RuntimeError("boom")
        ), mock.patch.object(manager, "_setup_cgroups"), \
                mock.patch.object(manager, "_attach_to_cgroups"):
            with self.assertRaises(RuntimeError):
                manager.spawn(container)
        self.assertFalse(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))

    def test_terminate_revokes_grants(self):
        caps = CapabilityManager()
        manager = self._manager(caps)
        container = manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 4242
        manager._cap_initialize(container)
        self.assertTrue(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))
        with mock.patch("backend.container.os.kill"), \
                mock.patch.object(container, "is_running", return_value=False):
            manager.terminate(container)
        self.assertFalse(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))

    def test_wait_legacy_path_revokes_grants(self):
        caps = CapabilityManager()
        manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=False,
            capability_manager=caps)
        container = manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 99
        manager._cap_initialize(container)
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 3
        container._proc = fake_proc
        self.assertEqual(manager.wait(container), 3)
        self.assertFalse(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))

    def test_wait_direct_path_revokes_grants(self):
        caps = CapabilityManager()
        manager = self._manager(caps)
        container = manager.create(ContainerConfig())
        container.transition_to(ContainerState.RUNNING)
        container.pid = 4242
        container._direct_launcher_pid = 777
        manager._cap_initialize(container)
        with mock.patch("backend.container.os.waitpid",
                        return_value=(777, 0)):
            manager.wait(container)
        self.assertFalse(caps.has_capability(
            container.id, Capability.CAP_IPC_SEND))

    def test_no_capability_manager_is_noop(self):
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        manager._cap_initialize(container)  # must not raise
        manager._cap_reset(container)
        with mock.patch.object(manager, "_spawn"), \
                mock.patch.object(manager, "_setup_cgroups"), \
                mock.patch.object(manager, "_attach_to_cgroups"):
            manager.spawn(container)


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


class TestBackendStatusService(unittest.TestCase):
    """Test the first real container-facing service on the transport
    (implementation_plan.md §4.3): CALL/REPLY over the datagram
    transport with kernel identity, per-operation capability
    enforcement (status requires CAP_SYSTEM_INFO; denied when the
    manager is absent — fail closed), and the reply payloads.
    """

    VERSION = "9.9.9"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    def _serve(self, capability_manager=None):
        import threading
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", self.svc_path,
            pid_registry={os.getpid(): "container-A"},
            capability_manager=capability_manager,
        )
        service = BackendStatusService(
            capability_manager=capability_manager, backend_version=self.VERSION)
        service.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient("container-A", self.cli_path).bind()
        return server, client, stop

    def _call(self, client, payload):
        reply = client.call(self.svc_path, payload, timeout_s=5.0)
        self.assertIsNotNone(reply, "no reply from the status service")
        return json.loads(reply.payload.decode("utf-8"))

    def _default_caps(self):
        caps = CapabilityManager()
        caps.initialize_container("container-A")  # defaults incl. CAP_SYSTEM_INFO
        return caps

    def test_ping_roundtrip(self):
        server, client, stop = self._serve()
        try:
            resp = self._call(client, b'{"op": "ping"}')
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["service"], "nyrqis.backend.status")
            self.assertEqual(resp["echo"], "pong")
            # The handler saw the authenticated container, not a claim.
            self.assertEqual(resp["container"], "container-A")
        finally:
            stop.set()
            client.close()
            server.close()

    def test_status_reports_identity_and_capabilities(self):
        server, client, stop = self._serve(capability_manager=self._default_caps())
        try:
            resp = self._call(client, b'{"op": "status"}')
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["backend_version"], self.VERSION)
            self.assertEqual(resp["container"], "container-A")
            self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
            self.assertGreaterEqual(resp["uptime_s"], 0.0)
        finally:
            stop.set()
            client.close()
            server.close()

    def test_status_denied_without_capability(self):
        caps = self._default_caps()
        caps.revoke_capability("container-A", Capability.CAP_SYSTEM_INFO)
        server, client, stop = self._serve(capability_manager=caps)
        try:
            resp = self._call(client, b'{"op": "status"}')
            self.assertFalse(resp["ok"])
            self.assertIn("CAP_SYSTEM_INFO", resp["error"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_status_fails_closed_without_manager(self):
        # No capability manager attached: the service cannot verify the
        # CAP_SYSTEM_INFO grant it needs, so status is denied — never
        # granted by default.
        server, client, stop = self._serve(capability_manager=None)
        try:
            resp = self._call(client, b'{"op": "status"}')
            self.assertFalse(resp["ok"])
            self.assertIn("forbidden", resp["error"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_unknown_operation(self):
        server, client, stop = self._serve()
        try:
            resp = self._call(client, b'{"op": "self_destruct"}')
            self.assertFalse(resp["ok"])
            self.assertIn("unknown operation", resp["error"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_malformed_request(self):
        server, client, stop = self._serve()
        try:
            resp = self._call(client, b"not json")
            self.assertFalse(resp["ok"])
            self.assertIn("bad request", resp["error"])
            resp = self._call(client, b'["not", "an", "object"]')
            self.assertFalse(resp["ok"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_service_bug_replies_internal_error_not_crash(self):
        # A bug inside the service (here: a capability manager that
        # raises) becomes an "internal error" REPLY — the client gets an
        # answer and the serve loop keeps serving the next call.
        # Authentication still works (validate_operation returns True —
        # including the server's CAP_IPC_SEND check); the bug is inside
        # the status path only (get_capabilities raises).
        caps = self._default_caps()
        broken = mock.Mock()
        broken.validate_operation.return_value = True
        broken.get_capabilities.side_effect = RuntimeError("caps bug")
        caps.validate_operation = broken.validate_operation
        caps.get_capabilities = broken.get_capabilities
        server, client, stop = self._serve(capability_manager=caps)
        try:
            resp = self._call(client, b'{"op": "status"}')
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"], "internal error")
            # The serve loop survived: a normal ping still works.
            resp = self._call(client, b'{"op": "ping"}')
            self.assertTrue(resp["ok"])
        finally:
            stop.set()
            client.close()
            server.close()


class TestStatusServiceHost(unittest.TestCase):
    """The runnable status-service daemon (`nyrqis_backend.py`
    `StatusServiceHost`): serves ping/status over a real socket with the
    trust chain wired, stops cleanly (socket released), a container
    spawned through the host's manager is automatically granted, the
    CLI `service serve` subcommand wires the host, and a REAL
    subprocess binds the socket and exits 0 on SIGTERM.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sock = os.path.join(self.tmp, "status.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    def _host(self):
        return nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9")

    def test_host_serves_status_service(self):
        host = self._host()
        host.start()
        try:
            # The operator's control-plane bookkeeping: the caller must
            # be resolvable (registered pid) and hold CAP_IPC_SEND
            # (server) + CAP_SYSTEM_INFO (status) — the same grants a
            # container spawned through the host's manager gets
            # automatically.
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            client = IPCClient("cli", self.cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "status"}', timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["container"], "cli")
                self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
                self.assertEqual(resp["backend_version"], "9.9.9")
            finally:
                client.close()
        finally:
            host.stop()
        self.assertFalse(os.path.exists(self.sock))

    def test_host_main_socket_served_by_loop_when_crate_present(self):
        # ADR-0021 main-socket move: the daemon's PRIMARY service
        # socket (status + control) is served by the Rust serving loop
        # when the crate is present — the floor server only otherwise.
        # The router is driven by the dispatch handoff either way, so a
        # caller cannot tell which backend answered.
        host = self._host()
        host.start()
        try:
            self.assertTrue(os.path.exists(self.sock))
            self.assertEqual(
                host.main_loop is not None, ipc_loop.available(),
                "Rust loop should serve the main socket when built",
            )
            self.assertEqual(
                host.main_driver is not None, ipc_loop.available(),
                "the dispatch driver follows the loop",
            )
            # Backend-agnostic: the operator's status call is answered
            # identically through whichever backend is active.
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            client = IPCClient("cli", os.path.join(self.tmp, "ms.sock")).bind()
            try:
                reply = client.call(
                    self.sock, b'{"op": "status"}', timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["container"], "cli")
            finally:
                client.close()
        finally:
            host.stop()
        self.assertFalse(os.path.exists(self.sock))

    def test_host_main_socket_serves_control_ops(self):
        # The full router (status + control) is exposed on the main
        # socket through the loop's dispatch handoff: the operator's
        # container_list reaches the ControlService and gets a real
        # reply (not a drop).
        host = self._host()
        host.start()
        cli_path = os.path.join(self.tmp, "ctl2.sock")
        op_client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            reply = op_client.call(
                self.sock,
                json.dumps({
                    "service": "control", "op": "container_list"
                }).encode(),
                timeout_s=5.0,
            )
            self.assertIsNotNone(reply, "no reply from the control plane")
            resp = json.loads(reply.payload.decode("utf-8"))
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["containers"], [])
        finally:
            op_client.close()
            host.stop()

    def test_host_main_socket_denies_container_control(self):
        # A registered container reaches the router on the main socket
        # (its pid resolves first) but the control service refuses any
        # non-operator sender — identically through the loop's dispatch
        # handoff and the floor.
        host = self._host()
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "container-A")
            host.capability_manager.initialize_container("container-A")
            client = IPCClient(
                "container-A", os.path.join(self.tmp, "deny.sock")).bind()
            try:
                reply = client.call(
                    self.sock,
                    json.dumps({
                        "service": "control", "op": "container_kill",
                        "container_id": "ctr-1",
                    }).encode(),
                    timeout_s=5.0,
                )
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode("utf-8"))
                self.assertFalse(resp["ok"])
                self.assertIn("operator-only", resp["error"])
            finally:
                client.close()
        finally:
            host.stop()

    def test_host_manager_auto_wires_grants(self):
        # A container spawned through the host's manager is granted its
        # default capabilities automatically (NPS-010 §5) — the pieces
        # the daemon must own share state.
        host = self._host()
        container = host.container_manager.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        with mock.patch.object(host.container_manager, "_spawn"), \
                mock.patch.object(host.container_manager, "_setup_cgroups"), \
                mock.patch.object(host.container_manager, "_attach_to_cgroups"):
            host.container_manager.spawn(container)
        self.assertTrue(host.capability_manager.has_capability(
            container.id, Capability.CAP_IPC_SEND))
        self.assertTrue(host.capability_manager.has_capability(
            container.id, Capability.CAP_SYSTEM_INFO))
        host.stop()

    def test_cli_service_serve_wires_host(self):
        with mock.patch.object(nyrqis_backend, "StatusServiceHost") as Host, \
                mock.patch.object(
                    nyrqis_backend.sys, "argv",
                    ["nyrqis_backend.py", "service", "serve",
                     "--socket", self.sock],
                ):
            rc = nyrqis_backend.main()
        self.assertEqual(rc, 0)
        Host.assert_called_once_with(
            socket_path=self.sock, backend_version=None,
            state_file="/run/nyrqis/daemon-state.json",
            health_socket_path=None)
        Host.return_value.serve_until_signal.assert_called_once()

    def test_cli_service_serve_wires_health_socket(self):
        # --health-socket (ADR-0021) is passed to the host: the
        # dedicated ping path served by the Rust loop (or the floor).
        health = os.path.join(self.tmp, "health.sock")
        with mock.patch.object(nyrqis_backend, "StatusServiceHost") as Host, \
                mock.patch.object(
                    nyrqis_backend.sys, "argv",
                    ["nyrqis_backend.py", "service", "serve",
                     "--socket", self.sock,
                     "--health-socket", health],
                ):
            rc = nyrqis_backend.main()
        self.assertEqual(rc, 0)
        Host.assert_called_once_with(
            socket_path=self.sock, backend_version=None,
            state_file="/run/nyrqis/daemon-state.json",
            health_socket_path=health)
        Host.return_value.serve_until_signal.assert_called_once()

    def test_host_health_socket_serves_ping(self):
        # The dedicated health-probe socket (ADR-0021): the daemon
        # serves ping on it — via the Rust serving loop when the crate
        # is present, the floor otherwise — and the operator's probe
        # gets the byte-identical reply. The MAIN service socket keeps
        # working (regression).
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            self.assertTrue(
                os.path.exists(health_path),
                "health socket was never bound",
            )
            # The loop path is used exactly when the crate is present.
            self.assertEqual(
                host.health_loop is not None,
                ipc_loop.available(),
                "Rust loop should serve the health socket when built",
            )
            op_client = IPCClient(
                DEFAULT_OPERATOR_ID,
                os.path.join(self.tmp, "health-cli.sock"),
            ).bind()
            try:
                reply = op_client.call(
                    health_path, b'{"op": "ping"}', timeout_s=5.0)
                self.assertIsNotNone(reply, "health socket must answer")
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["echo"], "pong")
                self.assertEqual(resp["container"], DEFAULT_OPERATOR_ID)
            finally:
                op_client.close()
            # The main socket is unaffected: a registered+granted
            # caller still gets the full status reply.
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            client = IPCClient("cli", self.cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "status"}',
                                    timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["container"], "cli")
            finally:
                client.close()
        finally:
            host.stop()
        self.assertFalse(os.path.exists(health_path),
                         "health socket must be unlinked on stop")
        self.assertFalse(os.path.exists(self.sock))

    def test_host_health_socket_refreshes_container_policy(self):
        # ADR-0021's per-container pid-table refresh, end-to-end: a
        # container whose pid enters the registry AFTER the health
        # socket starts can probe it — the registry's change hook
        # pushes the policy to the Rust loop (the floor reads the
        # registry live). Removing the pid flips the sender back to the
        # trusted-uid operator fallback: a container-id ping is then
        # dropped. Both backends behave identically, so the assertions
        # are backend-agnostic (the loop path exercises the refresh;
        # the floor path pins the same observable contract).
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            # A "container" spawns after the health socket is up: the
            # manager would register + grant it; mirror that here.
            host.ipc_registry.register(os.getpid(), "ctr")
            host.capability_manager.initialize_container("ctr")
            client = IPCClient(
                "ctr", os.path.join(self.tmp, "health-cli.sock")).bind()
            try:
                reply = client.call(
                    health_path, b'{"op": "ping"}', timeout_s=5.0)
                self.assertIsNotNone(
                    reply, "a registered container must be answered")
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(
                    resp["container"], "ctr",
                    "the reply must carry the container's own identity")
            finally:
                client.close()
            # The container terminates: its pid leaves the registry, so
            # the policy no longer authorizes it (loop: refresh pushed;
            # floor: registry read live). A container-id ping is dropped
            # — the caller now falls to the trusted-uid operator path.
            host.ipc_registry.unregister(os.getpid())
            client = IPCClient(
                "ctr", os.path.join(self.tmp, "health-cli2.sock")).bind()
            try:
                self.assertIsNone(
                    client.call(health_path, b'{"op": "ping"}',
                                timeout_s=1.0),
                    "a pid removed from the registry must not be "
                    "answered as its old container")
            finally:
                client.close()
        finally:
            host.stop()

    def test_host_health_socket_serves_status_via_dispatch(self):
        # ADR-0021 decision point 1 — the non-ping dispatch handoff,
        # end-to-end through the real host: the health socket is no
        # longer ping-only. A registered+granted container's `status`
        # CALL goes through the loop's queue (loop path) or the floor
        # (fallback) and returns the FULL status reply — the same
        # service instance data either way.
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "ctr")
            host.capability_manager.initialize_container("ctr")
            client = IPCClient(
                "ctr", os.path.join(self.tmp, "h-status.sock")).bind()
            try:
                reply = client.call(
                    health_path, b'{"op": "status"}', timeout_s=5.0)
                self.assertIsNotNone(
                    reply, "the health socket must serve status")
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["container"], "ctr")
                self.assertEqual(resp["backend_version"], "9.9.9")
                self.assertEqual(resp["service"], "nyrqis.backend.status")
                self.assertIn(
                    "CAP_SYSTEM_INFO", resp["capabilities"])
            finally:
                client.close()
        finally:
            host.stop()

    def test_host_health_socket_denies_control_ops(self):
        # The health socket is a status endpoint, NOT the control
        # plane: a `control` request is never served. The exact error
        # differs by backend — the loop path's router replies
        # "unknown service: 'control'" (only the status service is
        # registered), the floor path's status service replies "unknown
        # operation" (it handles the payload directly) — both deny it.
        # The operator must use the MAIN socket for control.
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "ctr")
            host.capability_manager.initialize_container("ctr")
            client = IPCClient(
                "ctr", os.path.join(self.tmp, "h-ctl.sock")).bind()
            try:
                reply = client.call(
                    health_path,
                    b'{"service": "control", "op": "container_list"}',
                    timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertFalse(resp["ok"])
                self.assertIn("unknown", resp["error"])
            finally:
                client.close()
        finally:
            host.stop()

    def test_container_probes_health_socket(self):
        # Gate at run time: the _netns_* helpers (and the _NETNS skip
        # message, a class attribute) are defined later in this module,
        # so a decorator would fail at import time (same pattern as
        # test_host_container_completes_status_call).
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        # The full chain, real container, real daemon: a container
        # spawned through the host's OWN ContainerManager (auto-
        # registered in the registry → the change hook refreshes the
        # loop's policy) runs an IPCClient that calls `status` on the
        # HEALTH socket. The kernel attaches the container's host pid,
        # the loop (or floor) resolves it to the container, and the
        # reply comes back with the container's own identity + granted
        # capabilities — proving the refresh end-to-end with zero
        # manual bookkeeping.
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        base = tempfile.mkdtemp(prefix="nyrqis-health-e2e-")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")
        backend_dir = str(Path(__file__).resolve().parent)
        script = (
            "import json, os, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "deadline = time.time() + 10\n"
            "while not os.path.exists(sys.argv[4]) and time.time() < deadline:\n"
            "    time.sleep(0.01)\n"
            "from ipc.transport import IPCClient\n"
            "c = IPCClient('container-cli', sys.argv[2]).bind()\n"
            "r = c.call(sys.argv[3], b'{\\\"op\\\": \\\"status\\\"}', "
            "timeout_s=10)\n"
            "open(sys.argv[5], 'w').write("
            "(r.payload.decode() if r else 'NONE'))\n"
        )
        container = host.container_manager.create(ContainerConfig(
            name="container-cli",
            command=[sys.executable, "-c", script, backend_dir,
                     cli_path, health_path, ready_path, marker],
            seccomp=True,
            capabilities=[
                "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                "CAP_FILESYSTEM_WRITE",
            ],
        ))
        try:
            host.container_manager.spawn(container)
            with open(ready_path, "w") as fh:
                fh.write("go")
            deadline = time.time() + 20.0
            while time.time() < deadline and not os.path.exists(marker):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(marker),
                "container never reached the health socket",
            )
            with open(marker) as fh:
                body = fh.read()
            self.assertNotEqual(body, "NONE", "health socket never replied")
            resp = json.loads(body)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["container"], "container-cli")
            self.assertEqual(resp["backend_version"], "9.9.9")
            self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
        finally:
            _launch_cleanup(host.container_manager, container)
            host.stop()
            shutil.rmtree(base, ignore_errors=True)

    def test_host_health_op(self):
        # Plan §4.5 health check: serve-loop liveness, container load,
        # registry size — readable over the wire by any granted caller.
        host = self._host()
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            client = IPCClient("cli", self.cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "health"}',
                                    timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertTrue(resp["serve_loop_alive"])
                self.assertEqual(resp["backend_version"], "9.9.9")
                self.assertGreaterEqual(resp["uptime_s"], 0)
                self.assertEqual(resp["containers"],
                                 {"known": 0, "running": 0})
                self.assertEqual(resp["ipc_registry_entries"], 1)
                self.assertFalse(resp["state_persisted"])
                self.assertIsNone(resp["recovery"])
                self.assertEqual(resp["container"], "cli")
            finally:
                client.close()
        finally:
            host.stop()

    def test_host_health_denied_without_capability(self):
        # Fail closed: a caller holding CAP_IPC_SEND (so the transport
        # lets the CALL through) but WITHOUT CAP_SYSTEM_INFO is denied
        # at the service gate.
        host = self._host()
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "cli-ungranted")
            host.capability_manager.grant_capability(
                "cli-ungranted", Capability.CAP_IPC_SEND)
            client = IPCClient("cli-ungranted", self.cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "health"}',
                                    timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertFalse(resp["ok"])
                self.assertEqual(
                    resp["error"],
                    "forbidden: CAP_SYSTEM_INFO required")
            finally:
                client.close()
        finally:
            host.stop()

    def test_host_health_reports_state_persisted(self):
        # With a state file configured, the health op reports the
        # daemon's persistence is active.
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            state_file=os.path.join(self.tmp, "daemon-state.json"))
        host.start()
        try:
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            client = IPCClient("cli", self.cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "health"}',
                                    timeout_s=5.0)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertTrue(resp["state_persisted"])
            finally:
                client.close()
        finally:
            host.stop()

    def test_cli_service_serve_real_subprocess(self):
        # A REAL `nyrqis_backend.py service serve` process binds the
        # socket (0700, like the endpoint primitive) and shuts down
        # cleanly (exit 0) on SIGTERM.
        backend = str(Path(nyrqis_backend.__file__).resolve())
        proc = subprocess.Popen(
            [sys.executable, backend, "service", "serve",
             "--socket", self.sock],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.time() + 10.0
            while time.time() < deadline and not os.path.exists(self.sock):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(self.sock),
                "service daemon never bound the socket",
            )
            self.assertEqual(
                stat_module.S_IMODE(os.stat(self.sock).st_mode), 0o700)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, out + err)
            self.assertIn("Status service listening", out)
            self.assertIn("Status service stopped", out)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.assertFalse(os.path.exists(self.sock))

    def test_daemon_restart_recovers_stale_state(self):
        # Plan §4.5 crash-recovery END-TO-END through the REAL daemon
        # process: a state file left by a previous (now dead) daemon —
        # carrying an orphaned container manifest — is recovered at
        # boot. The daemon logs the orphan record, atomically replaces
        # the state with its OWN identity, and carries the recovery
        # summary forward (reporting only — never resumption).
        from backend.daemon_state import DaemonStateFile
        # A definitely-dead pid: spawn and reap a child, then reuse its
        # pid (kill(0) on it must raise ProcessLookupError).
        reaper = subprocess.Popen([sys.executable, "-c", "pass"])
        reaper.wait()
        dead_pid = reaper.pid
        try:
            os.kill(dead_pid, 0)
            self.skipTest("could not obtain a dead pid")
        except ProcessLookupError:
            pass
        state_path = os.path.join(self.tmp, "daemon-state.json")
        self.assertTrue(DaemonStateFile(state_path).save({
            "daemon_pid": dead_pid,
            "backend_version": "9.9.8",
            "socket_path": "/run/nyrqis/old.sock",
            "containers": [{
                "id": "ctr-orphan-1",
                "command": ["/bin/sleep", "5"],
                "state": "running",
                "pid": dead_pid,
            }],
        }))
        backend = str(Path(nyrqis_backend.__file__).resolve())
        proc = subprocess.Popen(
            [sys.executable, backend, "service", "serve",
             "--socket", self.sock, "--state-file", state_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.time() + 10.0
            while time.time() < deadline and not os.path.exists(self.sock):
                time.sleep(0.05)
            self.assertTrue(os.path.exists(self.sock),
                            "service daemon never bound the socket")
            # The state file was atomically replaced by the new
            # daemon's identity (poll: start() rewrites right after
            # recover, which is after bind).
            state = None
            deadline = time.time() + 10.0
            while time.time() < deadline:
                state = DaemonStateFile(state_path).load()
                if state and state.get("daemon_pid") == proc.pid:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(state)
            self.assertEqual(state["daemon_pid"], proc.pid)
            self.assertEqual(state["containers"], [],
                             "no live containers to re-persist")
            self.assertEqual(state["recovery"]["previous_pid"], dead_pid)
            self.assertEqual(
                len(state["recovery"]["containers_left"]), 1)
            self.assertEqual(
                state["recovery"]["containers_left"][0]["id"],
                "ctr-orphan-1")
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, out + err)
            self.assertIn("Status service listening", out)
            # The recovery was LOGGED at boot (reporting, not
            # resumption — nothing was auto-killed or re-spawned).
            self.assertIn("recovered 1 container record(s)", out + err)
            self.assertIn("ctr-orphan-1", out + err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_host_container_completes_status_call(self):
        # Gate at run time: the _netns_* helpers (and the _NETNS skip
        # message, a class attribute) are defined later in this module,
        # so a decorator would fail at import time.
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        # The full platform path through the RUNNABLE daemon: a REAL
        # container spawned via the host's own ContainerManager is
        # registered (pid) AND granted (defaults) automatically, and
        # completes a status CALL against the host's own server — the
        # operator flow with zero manual bookkeeping. The host's socket
        # path is the peer the container calls.
        host = self._host()
        host.start()
        base = tempfile.mkdtemp(prefix="nyrqis-host-e2e-")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")
        container = None
        try:
            backend_dir = str(Path(__file__).resolve().parent)
            script = (
                "import os, sys, time\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "deadline = time.time() + 10\n"
                "while not os.path.exists(sys.argv[4]) and time.time() < deadline:\n"
                "    time.sleep(0.01)\n"
                "from ipc.transport import IPCClient\n"
                "c = IPCClient('container-cli', sys.argv[2]).bind()\n"
                "r = c.call(sys.argv[3], b'{\"op\": \"status\"}', timeout_s=10)\n"
                "open(sys.argv[5], 'w').write(r.payload.decode() if r else 'NONE')\n"
            )
            container = host.container_manager.create(ContainerConfig(
                name="container-cli",
                command=[sys.executable, "-c", script, backend_dir,
                         cli_path, host.socket_path, ready_path, marker],
                seccomp=True,
                network=True,
                # Data-plane (seccomp) grants: the socket family + the
                # marker write. Control-plane grants (CAP_IPC_SEND,
                # CAP_SYSTEM_INFO) come automatically from the host's
                # capability manager at spawn.
                capabilities=[
                    "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                    "CAP_FILESYSTEM_WRITE",
                ],
            ))
            host.container_manager.spawn(container)
            with open(ready_path, "w") as fh:
                fh.write("go")
            deadline = time.time() + 20.0
            while time.time() < deadline and not os.path.exists(marker):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(marker),
                "container never reached the daemon's status service",
            )
            with open(marker) as fh:
                body = fh.read()
            self.assertNotEqual(body, "NONE",
                                "container got no status reply")
            resp = json.loads(body)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["container"], "container-cli")
            self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
        finally:
            if container is not None:
                _launch_cleanup(host.container_manager, container)
            shutil.rmtree(base, ignore_errors=True)
            host.stop()
        self.assertFalse(os.path.exists(self.sock))

    def test_host_control_plane_runs_and_kills_container(self):
        # The control plane end-to-end through the RUNNABLE daemon: the
        # operator (same uid — kernel-authenticated) CALLs container_run
        # over the wire, the daemon spawns a REAL container
        # (auto-registered + auto-granted), container_list sees it, and
        # container_kill terminates it.
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        host = self._host()
        host.start()
        cli_path = os.path.join(self.tmp, "ctl.sock")
        op_client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            def ctl(payload):
                reply = op_client.call(
                    host.socket_path, json.dumps(payload).encode(),
                    timeout_s=15,
                )
                self.assertIsNotNone(reply, "no reply from the control plane")
                return json.loads(reply.payload.decode("utf-8"))

            resp = ctl({"service": "control", "op": "container_run",
                        "command": ["/bin/sleep", "30"], "network": True})
            self.assertTrue(resp["ok"], resp)
            cid = resp["container_id"]
            self.assertIsNotNone(resp["pid"])
            # The spawned container was auto-granted (control plane).
            self.assertTrue(host.capability_manager.has_capability(
                cid, Capability.CAP_IPC_SEND))

            resp = ctl({"service": "control", "op": "container_list"})
            self.assertTrue(resp["ok"])
            found = [c for c in resp["containers"] if c["id"] == cid]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["state"], "running")

            resp = ctl({"service": "control", "op": "container_kill",
                        "container_id": cid})
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(
                host.container_manager.containers[cid].state.value,
                "terminated",
            )
        finally:
            for container in list(host.container_manager.containers.values()):
                _launch_cleanup(host.container_manager, container)
            op_client.close()
            host.stop()

    def test_cli_control_wires_operator_client(self):
        # `nyrqis_backend.py control container-list` builds the control
        # payload (hyphenated subcommand -> underscored op), claims the
        # operator identity, and prints the daemon's reply.
        fake_reply = mock.Mock()
        fake_reply.payload = b'{"ok": true, "containers": []}'
        fake_client = mock.Mock()
        fake_client.call.return_value = fake_reply
        fake_client.bind.return_value = fake_client
        with mock.patch.object(nyrqis_backend, "IPCClient",
                               return_value=fake_client), \
                mock.patch.object(
                    nyrqis_backend.sys, "argv",
                    ["nyrqis_backend.py", "control", "--socket",
                     "/tmp/x.sock", "container-list"],
                ):
            rc = nyrqis_backend.main()
        self.assertEqual(rc, 0)
        fake_client.bind.assert_called_once()
        fake_client.call.assert_called_once()
        payload = json.loads(
            fake_client.call.call_args.args[1].decode("utf-8"))
        self.assertEqual(
            payload, {"service": "control", "op": "container_list"})


class TestOperatorCli(unittest.TestCase):
    """The operator CLI (`nyrqisctl.py`): payload construction and
    human formatting are hermetic; the real commands run against a
    REAL daemon (status + control over the wire) — including the
    operator carve-out that answers `status`/`health` for the daemon's
    own user.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sock = os.path.join(self.tmp, "status.sock")
        self.backend_dir = os.path.dirname(os.path.abspath(__file__))

    def _host(self):
        return nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9")

    def _cli(self, *argv, socket=None):
        """Run nyrqisctl as a subprocess; returns (exit, stdout, stderr)."""
        args = [sys.executable, "-B",
                os.path.join(self.backend_dir, "nyrqisctl.py"),
                "--socket", socket or self.sock]
        args += list(argv)
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout, proc.stderr

    # -- hermetic -----------------------------------------------------

    def test_cli_builds_status_payloads(self):
        self.assertEqual(
            nyrqisctl.build_payload("ping", mock.Mock()), {"op": "ping"})
        self.assertEqual(
            nyrqisctl.build_payload("status", mock.Mock()),
            {"op": "status"})
        self.assertEqual(
            nyrqisctl.build_payload("health", mock.Mock()),
            {"op": "health"})

    def test_cli_builds_control_payloads(self):
        # ``name`` is a reserved Mock kwarg — set the attributes
        # explicitly so ``args.name`` is the string, not a child mock.
        args = mock.Mock()
        args.run_command = ["/bin/sleep", "30"]
        args.name = "ctr"
        args.capabilities = "CAP_IPC_SEND, CAP_SYSTEM_INFO"
        args.network = True
        args.memory = 512
        args.pids = 32
        payload = nyrqisctl.build_payload("containers-run", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_run")
        self.assertEqual(payload["command"], ["/bin/sleep", "30"])
        self.assertEqual(payload["capabilities"],
                         ["CAP_IPC_SEND", "CAP_SYSTEM_INFO"])
        self.assertTrue(payload["network"])
        self.assertEqual(payload["memory_mb"], 512)
        self.assertEqual(payload["pids"], 32)
        self.assertEqual(payload["name"], "ctr")
        kill = nyrqisctl.build_payload(
            "containers-kill", mock.Mock(container_id="abc"))
        self.assertEqual(kill["container_id"], "abc")
        self.assertEqual(nyrqisctl.build_payload(
            "containers-list", mock.Mock())["op"], "container_list")

    def test_cli_formats_human_output(self):
        status = {"ok": True, "backend_version": "9.9.9",
                  "service": "nyrqis.backend.status",
                  "service_version": "1.0", "uptime_s": 1.5,
                  "container": "host-operator",
                  "capabilities": ["CAP_SYSTEM_INFO", "CAP_IPC_SEND"]}
        out = nyrqisctl.format_human("status", status)
        self.assertIn("backend:      9.9.9", out)
        self.assertIn("caller:       host-operator", out)
        self.assertIn("CAP_SYSTEM_INFO, CAP_IPC_SEND", out)
        health = {"ok": True, "backend_version": "9.9.9",
                  "serve_loop_alive": True,
                  "containers": {"known": 1, "running": 1},
                  "ipc_registry_entries": 1, "state_persisted": True,
                  "recovery": {"previous_pid": 42,
                                "containers_left": 1}}
        out = nyrqisctl.format_human("health", health)
        self.assertIn("serve loop:     alive", out)
        self.assertIn("containers:     1 known, 1 running", out)
        self.assertIn("previous pid 42 with 1 containers left", out)
        listed = nyrqisctl.format_human("containers-list", {
            "ok": True, "containers": [
                {"id": "a", "state": "running", "pid": 1}]})
        self.assertIn("a\trunning\t1", listed)
        self.assertEqual(nyrqisctl.format_human(
            "containers-list", {"ok": True, "containers": []}),
            "no containers")
        self.assertEqual(nyrqisctl.format_human("containers-run", {
            "ok": True, "container_id": "x", "pid": 7}),
            "container x started (pid 7)")

    def test_cli_run_subcommand_keeps_command(self):
        # Regression: a positional named ``command`` on the `run`
        # subparser would clobber the subcommand value installed by
        # set_defaults — the positional is ``run_command`` instead.
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(
            ["containers", "run", "--name", "x", "/bin/sleep", "30"])
        self.assertEqual(args.command, "containers-run")
        self.assertEqual(args.run_command, ["/bin/sleep", "30"])

    def test_cli_call_daemon_missing_socket_returns_none(self):
        self.assertIsNone(nyrqisctl.call_daemon(
            os.path.join(self.tmp, "none.sock"), {"op": "ping"},
            timeout_s=2.0))

    # -- end to end ---------------------------------------------------

    def test_cli_operator_status_against_daemon(self):
        # The operator's ping/status/health CALLs are answered (the
        # status service's operator carve-out — a trusted-uid process
        # has full control of the daemon anyway, so the container
        # capability model does not apply to it).
        host = self._host()
        host.start()
        try:
            rc, out, err = self._cli("ping")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("pong", out)
            self.assertIn("host-operator", out)
            rc, out, err = self._cli("status")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("caller:       host-operator", out)
            self.assertIn("backend:      9.9.9", out)
            rc, out, err = self._cli("health")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("serve loop:     alive", out)
        finally:
            host.stop()
        self.assertFalse(os.path.exists(self.sock))

    def test_cli_containers_list_against_daemon(self):
        host = self._host()
        host.start()
        try:
            rc, out, err = self._cli("containers", "list")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("no containers", out)
        finally:
            host.stop()

    def test_cli_containers_run_list_kill_e2e(self):
        # The full operator loop through the CLI against a REAL daemon
        # and a REAL container (userns-gated like the other netns e2e).
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        host = self._host()
        host.start()
        try:
            rc, out, err = self._cli(
                "containers", "run", "--name", "cli-e2e", "--network",
                "/bin/sleep", "30")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("started", out)
            rc, out, err = self._cli("containers", "list")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("cli-e2e", out)
            self.assertIn("running", out)
            rc, out, err = self._cli("containers", "kill", "cli-e2e")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("terminated", out)
            self.assertEqual(
                host.container_manager.containers["cli-e2e"].state.value,
                "terminated")
        finally:
            for container in list(host.container_manager.containers.values()):
                _launch_cleanup(host.container_manager, container)
            host.stop()

    def test_cli_no_daemon_clean_error(self):
        # A missing daemon socket must fail cleanly (exit 1, no
        # traceback) on both the floor and the Rust client half.
        rc, out, err = self._cli(
            "ping", socket=os.path.join(self.tmp, "gone.sock"))
        self.assertEqual(rc, 1)
        self.assertIn("no reply from the daemon", err)
        self.assertNotIn("Traceback", err)

    def test_cli_json_flag(self):
        host = self._host()
        host.start()
        try:
            rc, out, err = self._cli("--json", "ping")
            self.assertEqual(rc, 0, (out, err))
            resp = json.loads(out)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["echo"], "pong")
        finally:
            host.stop()

    def test_cli_health_socket_routes_status_ops(self):
        # ADR-0021: the dedicated health socket serves the status
        # service (ping/status/health) without contending with
        # container traffic on the main socket — nyrqisctl routes the
        # status commands there via --health-socket, and refuses to
        # send control commands to it (they use the main --socket).
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            for cmd in ("ping", "status", "health"):
                rc, out, err = self._cli(
                    "--health-socket", health_path, cmd)
                self.assertEqual(rc, 0, (cmd, out, err))
                if cmd == "ping":
                    self.assertIn("pong", out)
            # The main socket still answers status (no --health-socket).
            rc, out, err = self._cli("status")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("backend:      9.9.9", out)
            # Control never rides the health socket.
            rc, out, err = self._cli(
                "--health-socket", health_path, "containers", "list")
            self.assertEqual(rc, 2)
            self.assertIn("health socket serves status/health only", err)
        finally:
            host.stop()


class TestServiceRouter(unittest.TestCase):
    """Multi-service dispatch on one server socket: the router routes on
    the payload's ``service`` field (default ``status`` for back-compat),
    replies to unknown services, and never lets a service bug kill the
    serve loop.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    def _serve(self, services=()):
        import threading
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", self.svc_path,
            pid_registry={os.getpid(): "container-A"})
        router = ServiceRouter()
        for name, svc in services:
            router.register(name, svc)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient("container-A", self.cli_path).bind()
        return server, client, stop

    def _call(self, client, payload):
        reply = client.call(self.svc_path, payload, timeout_s=5.0)
        self.assertIsNotNone(reply, "no reply")
        return json.loads(reply.payload.decode("utf-8"))

    def test_default_routes_to_status(self):
        status = BackendStatusService(backend_version="9.9.9")
        server, client, stop = self._serve([("status", status)])
        try:
            resp = self._call(client, b'{"op": "ping"}')  # no service field
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["service"], "nyrqis.backend.status")
        finally:
            stop.set()
            client.close()
            server.close()

    def test_explicit_service_field_routes(self):
        status = BackendStatusService(backend_version="9.9.9")
        calls = []

        class Dummy:
            _server = None

            def _on_call(self, msg, sender, sender_path):
                calls.append(sender)
                self._server.reply(
                    sender_path, msg.message_id,
                    b'{"ok": true, "service": "dummy"}')

        server, client, stop = self._serve(
            [("status", status), ("dummy", Dummy())])
        try:
            resp = self._call(client, b'{"service": "dummy"}')
            self.assertTrue(resp["ok"])
            self.assertEqual(calls, ["container-A"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_unknown_service_reply(self):
        server, client, stop = self._serve()  # nothing registered
        try:
            resp = self._call(client, b'{"service": "nope"}')
            self.assertFalse(resp["ok"])
            self.assertIn("unknown service", resp["error"])
        finally:
            stop.set()
            client.close()
            server.close()

    def test_service_bug_replies_internal_error_and_survives(self):
        class Buggy:
            _server = None

            def _on_call(self, msg, sender, sender_path):
                raise RuntimeError("bug")

        server, client, stop = self._serve([("buggy", Buggy())])
        try:
            resp = self._call(client, b'{"service": "buggy"}')
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"], "internal error")
            # The serve loop survived: a second call still gets served.
            resp = self._call(client, b'{"service": "buggy"}')
            self.assertEqual(resp["error"], "internal error")
        finally:
            stop.set()
            client.close()
            server.close()


class TestControlService(unittest.TestCase):
    """The operator control plane: authenticated by the kernel-attached
    uid at the server (``trusted_uids``, container-FIRST resolution),
    the control service's own operator-only gate, and the operations
    themselves (against a stub ContainerManager; the real-container
    control e2e lives in TestStatusServiceHost).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    class _FakeManager:
        def __init__(self):
            self.containers = {}
            self.created = []

        def create(self, config):
            c = mock.Mock()
            c.id = "ctr-1"
            c.pid = 4242
            c.state.value = "CREATED"
            self.containers["ctr-1"] = c
            self.created.append(config)
            return c

        def spawn(self, container):
            container.pid = 4242
            container.state.value = "RUNNING"

        def terminate(self, container):
            container.state.value = "TERMINATED"

    def _serve(self, container_manager, pid_registry=None,
               trusted_uids=None, state_saver=None):
        import threading
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", self.svc_path,
            pid_registry=pid_registry or {},
            trusted_uids=trusted_uids,
        )
        control = ControlService(container_manager, state_saver=state_saver)
        router = ServiceRouter()
        router.register("control", control)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        return server, stop

    def _call(self, client, payload, timeout=5.0):
        reply = client.call(self.svc_path, payload, timeout_s=timeout)
        if reply is None:
            return None
        return json.loads(reply.payload.decode("utf-8"))

    def test_operator_container_run(self):
        fake = self._FakeManager()
        server, stop = self._serve(fake, trusted_uids={os.getuid()})
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_run",
                "command": ["/bin/sleep", "30"],
                "capabilities": ["CAP_FILESYSTEM_WRITE"],
                "memory_mb": 128, "pids": 16,
            }).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["container_id"], "ctr-1")
            self.assertEqual(resp["pid"], 4242)
            cfg = fake.created[0]
            self.assertEqual(cfg.command, ["/bin/sleep", "30"])
            self.assertEqual(cfg.capabilities, ["CAP_FILESYSTEM_WRITE"])
            self.assertEqual(cfg.limits.memory_mb, 128)
            self.assertEqual(cfg.limits.pid_limit, 16)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_container_list_and_kill(self):
        fake = self._FakeManager()
        fake.create(mock.Mock())  # pre-populate the manager with ctr-1
        server, stop = self._serve(fake, trusted_uids={os.getuid()})
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_list"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["containers"],
                             [{"id": "ctr-1", "state": "CREATED",
                               "pid": 4242}])
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_kill",
                "container_id": "ctr-1"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(fake.containers["ctr-1"].state.value,
                             "TERMINATED")
        finally:
            client.close()
            stop.set()
            server.close()

    def test_container_cannot_drive_control(self):
        # A registered container reaches the router (its pid resolves
        # first) but the control service refuses any non-operator sender.
        fake = self._FakeManager()
        server, stop = self._serve(
            fake, pid_registry={os.getpid(): "container-A"},
            trusted_uids={os.getuid()})
        client = IPCClient("container-A", self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_kill",
                "container_id": "ctr-1"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("operator-only", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_untrusted_uid_operator_claim_dropped(self):
        # No trusted_uids configured: even a "host-operator" claim from
        # an unknown pid is dropped before the router — no reply at all.
        fake = self._FakeManager()
        server, stop = self._serve(fake)  # trusted_uids=None
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_list"}).encode(),
                timeout=1.0)
            self.assertIsNone(resp)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_bad_command_rejected(self):
        fake = self._FakeManager()
        server, stop = self._serve(fake, trusted_uids={os.getuid()})
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_run",
                "command": "not-a-list"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("command", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_control_saves_state_after_mutations(self):
        # Plan §4.5 persistent state: mutating ops (container_run,
        # container_kill) trigger the daemon's state saver; the
        # read-only container_list does not; a saver failure never
        # breaks the reply.
        fake = self._FakeManager()
        saved = []
        def saver():
            saved.append(True)
            if len(saved) == 2:
                raise OSError("state dir unwritable")
        server, stop = self._serve(fake, trusted_uids={os.getuid()},
                                   state_saver=saver)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_run",
                "command": ["/bin/sleep", "30"]}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(saved, [True])
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_list"}).encode())
            self.assertTrue(resp["ok"], resp)
            # read-only op: no additional save
            self.assertEqual(len(saved), 1)
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_kill",
                "container_id": "ctr-1"}).encode())
            # the saver raised on this call — the reply still arrives
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(len(saved), 2)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_unknown_op_and_container(self):
        fake = self._FakeManager()
        server, stop = self._serve(fake, trusted_uids={os.getuid()})
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "control",
                "op": "self_destruct"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown operation", resp["error"])
            resp = self._call(client, json.dumps({
                "service": "control", "op": "container_kill",
                "container_id": "missing"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown container", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()


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


class TestDaemonState(unittest.TestCase):
    """Plan §4.5 Persistent state management: the versioned,
    atomically-written state file the daemon persists and recovers
    from, and the host's recovery reporting.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp, "daemon-state.json")
        self.sock = os.path.join(self.tmp, "status.sock")

    def _state_file(self):
        from backend.daemon_state import DaemonStateFile
        return DaemonStateFile(self.state_path)

    def test_save_load_round_trip(self):
        sf = self._state_file()
        self.assertTrue(sf.save({"daemon_pid": 1234, "containers": []}))
        data = sf.load()
        self.assertIsNotNone(data)
        self.assertEqual(data["daemon_pid"], 1234)
        self.assertEqual(data["schema"], 1)
        self.assertIn("saved_at", data)

    def test_save_is_atomic_no_partial_file(self):
        # A failed os.replace must leave no partial state behind and
        # must not lose the previous good copy.
        sf = self._state_file()
        self.assertTrue(sf.save({"daemon_pid": 1}))
        with mock.patch.object(nyrqis_backend.os, "replace",
                               side_effect=OSError("disk full")):
            self.assertFalse(sf.save({"daemon_pid": 2}))
        self.assertEqual(sf.load()["daemon_pid"], 1)

    def test_load_missing_returns_none(self):
        self.assertIsNone(self._state_file().load())

    def test_load_corrupt_returns_none(self):
        Path(self.state_path).write_text("{not json")
        self.assertIsNone(self._state_file().load())

    def test_load_unsupported_schema_returns_none(self):
        with open(self.state_path, "w") as fh:
            json.dump({"schema": 99}, fh)
        self.assertIsNone(self._state_file().load())

    def test_is_stale_detects_dead_previous_daemon(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.2)"])
        proc.wait()
        self.assertTrue(self._state_file().is_stale(
            {"daemon_pid": proc.pid}))

    def test_is_stale_false_for_live_or_self(self):
        from backend.daemon_state import DaemonStateFile
        self.assertFalse(DaemonStateFile.is_stale(
            {"daemon_pid": os.getpid()}, current_pid=os.getpid()))
        self.assertFalse(DaemonStateFile.is_stale(None))
        self.assertFalse(DaemonStateFile.is_stale({"daemon_pid": None}))

    def test_manifest_extracts_container_fields(self):
        from backend.daemon_state import DaemonStateFile
        class _C:
            id = "ctr-x"
            pid = 42
            created_at = 1.0
            class _S:
                value = "running"
            state = _S()
            config = type("CFG", (), {"command": ["/bin/sleep", "1"]})()
        self.assertEqual(DaemonStateFile.manifest([_C()]), [{
            "id": "ctr-x", "command": ["/bin/sleep", "1"],
            "state": "running", "pid": 42, "created_at": 1.0,
        }])

    def test_host_recovers_from_stale_state(self):
        # A state file left by a crashed previous daemon (dead pid + one
        # container record) is detected at start and reported; the new
        # daemon rewrites the file with its own identity.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.2)"])
        proc.wait()
        with open(self.state_path, "w") as fh:
            json.dump({
                "schema": 1,
                "daemon_pid": proc.pid,
                "backend_version": "0.0.0",
                "socket_path": "/run/nyrqis/status.sock",
                "containers": [{"id": "ctr-orphan",
                                 "state": "running",
                                 "command": ["/bin/sleep"],
                                 "pid": 12345}],
            }, fh)
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            state_file=self.state_path)
        host.start()
        try:
            self.assertIsNotNone(host._recovery)
            self.assertEqual(host._recovery["previous_pid"], proc.pid)
            self.assertEqual(len(host._recovery["containers_left"]), 1)
            self.assertEqual(
                host._recovery["containers_left"][0]["id"],
                "ctr-orphan")
            # The health op reports a SUMMARY (previous pid + count) —
            # never the per-container manifest (CAP_SYSTEM_INFO is a
            # default grant).
            host.ipc_registry.register(os.getpid(), "cli")
            host.capability_manager.initialize_container("cli")
            cli_path = os.path.join(self.tmp, "cli.sock")
            client = IPCClient("cli", cli_path).bind()
            try:
                reply = client.call(self.sock, b'{"op": "health"}',
                                    timeout_s=5.0)
                self.assertIsNotNone(reply)
                resp = json.loads(reply.payload.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["recovery"], {
                    "previous_pid": proc.pid, "containers_left": 1,
                })
                self.assertNotIn("containers_left_manifest", resp)
                self.assertNotIn("ctr-orphan", reply.payload.decode())
            finally:
                client.close()
        finally:
            host.stop()
        data = json.loads(Path(self.state_path).read_text())
        self.assertEqual(data["daemon_pid"], os.getpid())
        self.assertEqual(data["backend_version"], "9.9.9")

    def test_host_state_saved_on_start_and_stop(self):
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            state_file=self.state_path)
        host.start()
        data = json.loads(Path(self.state_path).read_text())
        self.assertEqual(data["daemon_pid"], os.getpid())
        host.stop()
        data = json.loads(Path(self.state_path).read_text())
        self.assertIn("saved_at", data)

    def test_host_without_state_file_skips_persistence(self):
        host = self._plain_host()
        host.start()
        try:
            self.assertIsNone(host._recovery)
            self.assertIsNone(host.state)
        finally:
            host.stop()

    def _plain_host(self):
        return nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9")


class TestLoggingConfig(unittest.TestCase):
    """Plan §4.5 logging to syslog: `setup_logging(syslog=True)`
    mirrors records through `/dev/log` (with a UDP fallback), and the
    failure path degrades to stderr without raising.
    """

    def tearDown(self):
        # Remove any handler the tests added to the root logger.
        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_nyrqis_test", False):
                root.removeHandler(h)

    class _Recorder(logging.Handler):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.recorded_address = kwargs.get("address")
            self._nyrqis_test = True
            if self.recorded_address == "/dev/log":
                raise OSError("no /dev/log")

    def test_syslog_attaches_dev_log_handler(self):
        with mock.patch("logging.handlers.SysLogHandler") as Slh:
            nyrqis_backend.setup_logging(verbose=False, syslog=True)
        Slh.assert_called_once()
        self.assertEqual(
            Slh.call_args.kwargs.get("address"), "/dev/log")

    def test_syslog_falls_back_to_udp(self):
        with mock.patch("logging.handlers.SysLogHandler",
                        self._Recorder):
            nyrqis_backend.setup_logging(verbose=False, syslog=True)
        recorder = [h for h in logging.getLogger().handlers
                    if getattr(h, "_nyrqis_test", False)]
        self.assertEqual(len(recorder), 1)
        self.assertEqual(recorder[0].recorded_address,
                         ("localhost", 514))

    def test_syslog_failure_degrades_gracefully(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("syslog daemon gone")
        with mock.patch("logging.handlers.SysLogHandler", _boom):
            # must not raise
            nyrqis_backend.setup_logging(verbose=False, syslog=True)


class TestSystemdUnit(unittest.TestCase):
    """Plan §4.5 Host Integration (packaging/systemd/): the systemd unit
    must exist, run the actual daemon, pass ``systemd-analyze verify``
    (when systemd is present), and run unprivileged — the daemon
    launches containers through unprivileged user namespaces.
    """

    UNIT = (Path(__file__).resolve().parent.parent.parent
            / "packaging" / "systemd" / "nyrqis-backend.service")

    def test_systemd_unit_exists_and_wires_daemon(self):
        """The unit runs the backend daemon at boot (not just any
        command)."""
        self.assertTrue(self.UNIT.is_file(),
                        f"missing {self.UNIT}")
        text = self.UNIT.read_text()
        self.assertIn("service serve", text)  # the daemon subcommand
        self.assertIn("nyrqis_backend.py", text)
        self.assertIn("status.sock", text)
        self.assertIn("[Install]", text)
        self.assertIn("WantedBy=multi-user.target", text)
        # Plan §4.5: the unit enables journal logging and the
        # crash-recovery state file (inside the RuntimeDirectory).
        self.assertIn("--syslog", text)
        self.assertIn("--state-file /run/nyrqis/daemon-state.json",
                      text)
        # ADR-0021: the unit also serves the dedicated health-probe
        # socket (Rust serving loop when the crate is present).
        self.assertIn("--health-socket /run/nyrqis/health.sock", text)

    def test_systemd_unit_analyze_verify(self):
        """The unit must pass ``systemd-analyze verify`` when systemd is
        available (skipped on non-systemd hosts)."""
        if shutil.which("systemd-analyze") is None:
            self.skipTest("systemd-analyze not available")
        proc = subprocess.run(
            ["systemd-analyze", "verify", str(self.UNIT)],
            capture_output=True, text=True, timeout=60,
        )
        # systemd-analyze verify returns 0 with a clean unit; warnings
        # about unset Unit= targets are acceptable, hard errors are not.
        self.assertEqual(proc.returncode, 0,
                         msg=f"systemd-analyze verify failed:\n{proc.stderr}")

    def test_systemd_unit_runs_unprivileged(self):
        """The daemon launches containers via unprivileged user
        namespaces, so the unit must NOT run as root (DynamicUser or an
        explicit User, and NoNewPrivileges)."""
        text = self.UNIT.read_text()
        self.assertIn("NoNewPrivileges=true", text)
        self.assertTrue(
            "DynamicUser=true" in text or "User=" in text,
            "unit must not run as root",
        )
        self.assertIn("Restart=on-failure", text)


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

    def _spawn_with_pipe(self, data=b"4242", cmd_pid=9876,
                         fork_returns=777):
        """Drive _spawn_direct's FORK child (the crate-less fallback)
        with a mocked pipe/fork/read (pipe1 = setup child -> manager:
        PID-1 / ERR; the command's HOST pid comes from the host-side
        ``_resolve_command_pid`` poll). ``available()`` is forced False
        so these tests pin the fallback path; the clone path has its
        own tests below."""
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=False), \
                mock.patch("backend.container.os.fork", return_value=fork_returns), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read", return_value=data) as read, \
                mock.patch("backend.container._resolve_command_pid",
                           return_value=cmd_pid) as resolve:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False,
            ))
            self.manager._spawn_direct(container)
        return container, read, resolve

    def test_direct_spawn_relays_container_pid(self):
        container, read, resolve = self._spawn_with_pipe()
        # The manager records the launcher-init (PID-1, the grandchild)
        # as container._init_pid and the HOST-side-resolved command pid
        # as container.pid; the setup child is the reaped launcher pid.
        self.assertEqual(container.pid, 9876)  # the command (host pid)
        self.assertEqual(container._init_pid, 4242)  # the PID-1 init
        self.assertEqual(container._direct_launcher_pid, 777)
        self.assertIsNone(container._proc)
        resolve.assert_called_once_with(4242, _DIRECT_LAUNCH_TIMEOUT_S)

    def test_direct_spawn_err_marker_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            self._spawn_with_pipe(data=b"ERR:unshare(CLONE_NEWUSER): boom")
        self.assertIn("unshare(CLONE_NEWUSER)", str(cm.exception))

    def test_direct_spawn_empty_read_raises(self):
        with self.assertRaises(RuntimeError):
            self._spawn_with_pipe(data=b"")

    def test_direct_spawn_reaps_failed_child(self):
        # The manager must reap the setup child even on the failure path
        # (no zombie), then raise (the fork fallback's timeout path).
        with mock.patch("backend.container.os.pipe",
                        return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=False), \
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

    # -- the Rust-native clone child (syscalls ABI 1.2.0) ------------

    def _clone_spawn(self, data=b"", cmd_pid=9876, clone_returns=4242,
                     network=False):
        """Drive _spawn_direct's CLONE child (the Rust-native path)
        with a mocked clone/pipe/read. ``available()`` is forced True
        (the real crate would take this path anyway); ``os.read``
        returns ``data`` (EOF = the entry closed the pipe and exec'd;
        ERR: = setup failure)."""
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=True), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read",
                           return_value=data) as read, \
                mock.patch("backend.container.rust_syscalls.clone",
                           return_value=clone_returns) as clone, \
                mock.patch("backend.container._resolve_command_pid",
                           return_value=cmd_pid) as resolve:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False, network=network,
            ))
            self.manager._spawn_direct(container)
        return container, clone, read, resolve

    def test_clone_spawn_uses_rust_child_and_records_pids(self):
        # The clone child IS the launcher-init: one clone(2) FFI call
        # with ALL the namespace flags + SIGCHLD, the init pid comes
        # from clone's return (not the pipe), and wait() reaps the init
        # directly (_direct_launcher_pid == _init_pid).
        container, clone, read, resolve = self._clone_spawn()
        self.assertEqual(container.pid, 9876)  # the command (host pid)
        self.assertEqual(container._init_pid, 4242)  # the clone child
        self.assertEqual(container._direct_launcher_pid, 4242)
        self.assertIsNone(container._proc)
        resolve.assert_called_once_with(4242, _DIRECT_LAUNCH_TIMEOUT_S)
        self.assertEqual(clone.call_count, 1)
        flags = clone.call_args.args[0]
        self.assertEqual(
            flags,
            rust_syscalls.CLONE_NEWUSER | rust_syscalls.CLONE_NEWNS
            | rust_syscalls.CLONE_NEWUTS | rust_syscalls.CLONE_NEWIPC
            | rust_syscalls.CLONE_NEWPID | rust_syscalls.CLONE_SIGCHLD,
        )

    def test_clone_spawn_network_adds_newnet(self):
        _, clone, _, _ = self._clone_spawn(network=True)
        flags = clone.call_args.args[0]
        self.assertEqual(flags & rust_syscalls.CLONE_NEWNET,
                         rust_syscalls.CLONE_NEWNET)

    def test_clone_spawn_captures_real_uid_gid_before_clone(self):
        # Inside the new user namespace getuid() reports 65534, so the
        # MANAGER must capture its real uid/gid into the LaunchArgs the
        # Rust entry maps. Pin the build arguments.
        captured = {}
        real_build = rust_syscalls.LaunchArgs.build

        def spy_build(write_fd, uid, gid, argv):
            captured["write_fd"] = write_fd
            captured["uid"] = uid
            captured["gid"] = gid
            captured["argv"] = argv
            return real_build(write_fd, uid, gid, argv)

        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=True), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read", return_value=b""), \
                mock.patch("backend.container.rust_syscalls.clone",
                           return_value=4242), \
                mock.patch.object(rust_syscalls.LaunchArgs, "build",
                                  side_effect=spy_build) as build, \
                mock.patch("backend.container._resolve_command_pid",
                           return_value=9876):
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False,
            ))
            self.manager._spawn_direct(container)
        build.assert_called_once()
        self.assertEqual(captured["write_fd"], 4)
        self.assertEqual(captured["uid"], os.getuid())
        self.assertEqual(captured["gid"], os.getgid())
        # The launcher is the Python launcher.py OR the compiled
        # launcher-init (ADR-0020) — the argv carries whichever the
        # manager resolved.
        self.assertTrue(any(
            os.path.basename(a) in ("launcher.py",)
            or os.path.basename(a).startswith("nyrqis-launcher")
            for a in captured["argv"]
        ))

    def test_clone_spawn_err_marker_raises_and_kills_child(self):
        # An ERR: report from the Rust entry kills the clone child (the
        # manager owns its pid) and surfaces the detail.
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=True), \
                mock.patch("backend.container.os.close"), \
                mock.patch("backend.container.select.select",
                           return_value=([3], [], [])), \
                mock.patch("backend.container.os.read",
                           return_value=b"ERR:proc mount failed"), \
                mock.patch("backend.container.rust_syscalls.clone",
                           return_value=4242), \
                mock.patch("backend.container.os.kill") as kill, \
                mock.patch("backend.container.os.waitpid") as waitpid:
            container = self.manager.create(ContainerConfig(
                command=["/bin/true"], seccomp=False,
            ))
            with self.assertRaises(RuntimeError) as cm:
                self.manager._spawn_direct(container)
        self.assertIn("proc mount failed", str(cm.exception))
        kill.assert_called_once_with(4242, 9)
        waitpid.assert_called_once_with(4242, 0)

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
            container_mod._direct_launch_child(4, ["/bin/true"],
                                               network=True)
        self.assertEqual(calls, [
            rust_syscalls.CLONE_NEWUSER,
            rust_syscalls.CLONE_NEWNS | rust_syscalls.CLONE_NEWUTS
            | rust_syscalls.CLONE_NEWIPC | rust_syscalls.CLONE_NEWNET,
            rust_syscalls.CLONE_NEWPID,
        ])

    def test_direct_spawn_forwards_network_flag_to_child(self):
        # The manager passes the container's network flag through to the
        # namespace-setup child (fork mocked to run the child branch so
        # the forwarding is observable) — the crate-less fallback path.
        with mock.patch("backend.container.os.pipe", return_value=(3, 4)), \
                mock.patch("backend.container.rust_syscalls.available",
                           return_value=False), \
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
_DIRECT_LAUNCH_SUPPORTED = None  # cached real-launch probe result


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


def _direct_launch_supported() -> bool:
    """True when a direct-syscall container (no network namespace) can
    actually launch on this host. Probing with a real launch is the
    honest gate: it covers the unprivileged-userns knob (the uid_map
    write) and the whole mount-proc/launcher chain in one check, so the
    PID-1-init tests skip (not fail) on hosts that cannot run them —
    e.g. GitHub's runners, where the kernel blocks the uid_map write
    even though ``unshare(CLONE_NEWUSER)`` itself succeeds. The result
    is cached (the skipUnless decorator would otherwise re-run the
    probe per class)."""
    global _DIRECT_LAUNCH_SUPPORTED
    if _DIRECT_LAUNCH_SUPPORTED is not None:
        return _DIRECT_LAUNCH_SUPPORTED
    supported = False
    try:
        probe = subprocess.run(
            ["unshare", "--user", "true"],
            capture_output=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        probe = None
    if probe is not None and probe.returncode == 0:
        try:
            manager = ContainerManager(
                use_cgroups_v2=False, use_direct_syscalls=True)
            container = manager.create(ContainerConfig(
                command=["/bin/sleep", "5"], seccomp=False))
            manager.spawn(container)
            try:
                supported = (container.pid is not None
                             and container.is_running())
            finally:
                _launch_cleanup(manager, container)
        except Exception:
            supported = False
    _DIRECT_LAUNCH_SUPPORTED = supported
    return supported


@unittest.skipUnless(
    _direct_launch_supported(),
    "host cannot launch direct-syscall containers",
)
class TestPid1Init(unittest.TestCase):
    """The launcher-init: the container command runs as a plain child
    of the namespace's PID-1 — not as PID 1 itself — so kernel signal
    semantics apply to it and SIGTERM terminates it promptly (Linux
    discards signals sent to a namespace PID 1 without a handler; the
    pre-init design always burned the full 10s terminate window). The
    init relays the command pid, forwards supervisor signals, and
    propagates the exit status.
    """

    def _manager(self):
        return ContainerManager(use_cgroups_v2=False, use_direct_syscalls=True)

    def _spawn(self, manager, command, **kw):
        c = manager.create(ContainerConfig(command=command, seccomp=False, **kw))
        manager.spawn(c)
        self.addCleanup(_launch_cleanup, manager, c)
        return c

    def _ns_pid(self, host_pid):
        """The pid the process sees inside its own PID namespace (the
        last NSpid value in /proc/<pid>/status)."""
        try:
            with open(f"/proc/{host_pid}/status") as fh:
                for line in fh:
                    if line.startswith("NSpid:"):
                        return int(line.split()[-1])
        except (OSError, ValueError):
            return None
        return None

    def test_command_runs_as_child_of_pid1_init(self):
        m = self._manager()
        c = self._spawn(m, ["/bin/sleep", "60"])
        self.assertIsNotNone(c._init_pid)
        self.assertNotEqual(c.pid, c._init_pid)
        # The init is the namespace's PID 1; the command is a plain
        # child (pid 2 inside the namespace).
        self.assertEqual(self._ns_pid(c._init_pid), 1)
        self.assertEqual(self._ns_pid(c.pid), 2)
        # The command's parent is the init (field 4 of /proc/pid/stat).
        try:
            with open(f"/proc/{c.pid}/stat") as fh:
                fields = fh.read().split()
        except OSError as e:
            self.fail(f"could not read /proc/{c.pid}/stat: {e}")
        self.assertEqual(int(fields[3]), c._init_pid)
        # The init is the launcher: launcher.py (Python) or the
        # compiled launcher-init (ADR-0020) — accept either.
        try:
            with open(f"/proc/{c._init_pid}/cmdline", "rb") as fh:
                cmd = fh.read().decode(errors="replace")
        except OSError:
            cmd = ""
        self.assertTrue(
            "launcher.py" in cmd or "nyrqis-launcher" in cmd, cmd)

    def test_sigterm_terminates_promptly(self):
        m = self._manager()
        c = self._spawn(m, ["/bin/sleep", "60"])
        t0 = time.time()
        m.terminate(c)
        elapsed = time.time() - t0
        self.assertEqual(c.state, ContainerState.TERMINATED)
        self.assertLess(elapsed, 3.0,
                        f"terminate took {elapsed:.1f}s — PID-1 init broken?")
        self.assertFalse(c.is_running())

    def _init_caught_sigterm(self, init_pid: int) -> bool:
        """True once the launcher-init has installed its SIGTERM
        forwarder (the SIGTERM bit in /proc/<pid>/status SigCgt).
        Without this, a SIGTERM sent in the init's fork→install window
        is DISCARDED by kernel PID-1 semantics (no handler yet), the
        forwarder never fires, and the command keeps running."""
        try:
            with open(f"/proc/{init_pid}/status") as fh:
                for line in fh:
                    if line.startswith("SigCgt:"):
                        mask = int(line.split()[1], 16)
                        return bool(mask & (1 << (signal.SIGTERM - 1)))
        except (OSError, ValueError, IndexError):
            pass
        return False

    def test_init_forwards_sigterm_to_command(self):
        m = self._manager()
        c = self._spawn(m, ["/bin/sleep", "60"])
        # Wait for the init's forwarders: PID-1 semantics DISCARD a
        # signal the init has no handler for, so signaling in the
        # fork→install window would silently drop the SIGTERM.
        deadline = time.time() + 5.0
        while (time.time() < deadline
               and not self._init_caught_sigterm(c._init_pid)):
            time.sleep(0.02)
        self.assertTrue(
            self._init_caught_sigterm(c._init_pid),
            "the launcher-init never installed its SIGTERM forwarder",
        )
        os.kill(c._init_pid, signal.SIGTERM)  # signal the PID-1 INIT
        deadline = time.time() + 5.0
        while time.time() < deadline and c.is_running():
            time.sleep(0.05)
        self.assertFalse(
            c.is_running(),
            "the init did not forward SIGTERM to the command",
        )

    def test_exit_status_propagates_through_init(self):
        m = self._manager()
        c = self._spawn(m, [sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertEqual(m.wait(c, timeout_s=30), 7)

    def test_spawn_leaves_environment_untouched(self):
        # The command-pid relay is carried on a dedicated pipe (resolved
        # by the setup child), never through the process environment.
        before = dict(os.environ)
        m = self._manager()
        self._spawn(m, ["/bin/sleep", "1"])
        self.assertEqual(os.environ, before)

    def test_fast_exit_command_spawns_and_reports_status(self):
        # A command that exits within ~1ms of forking may never become
        # an observable process (pid=None — nothing to signal/attach).
        # Either way the spawn succeeds and wait() reports the status
        # with the setup child reaped (no zombie left behind).
        m = self._manager()
        c = self._spawn(m, ["/bin/true"])
        self.assertEqual(m.wait(c, timeout_s=30), 0)
        self.assertEqual(c.state, ContainerState.TERMINATED)
        try:
            os.waitpid(c._direct_launcher_pid, os.WNOHANG)
        except ChildProcessError:
            pass  # already reaped by wait() — expected
        else:
            self.fail("setup child still un-reaped after wait()")

    def test_legacy_unshare_path_runs_command_through_init(self):
        if shutil.which("unshare") is None:
            self.skipTest("unshare(1) not available")
        m = ContainerManager(use_cgroups_v2=False, use_direct_syscalls=False)
        c = self._spawn(m, ["/bin/sleep", "1"])
        self.assertEqual(m.wait(c, timeout_s=30), 0)


class TestRustLauncherLoader(unittest.TestCase):
    """The Rust launcher-init binary locator (``backend/rust_launcher.py``
    — ADR-0020): search order, override, and force semantics."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, "nyrqis-launcher")
        with open(self.bin, "wb") as fh:
            fh.write(b"#!/bin/sh\nexit 0\n")
        os.chmod(self.bin, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_override_wins(self):
        # The locator is deliberately uncached (a stale cached path
        # would exec a dead binary) — each lookup re-stats.
        with mock.patch.dict(
            os.environ, {"NYRQIS_LAUNCHER": self.bin}, clear=False
        ):
            self.assertEqual(rust_launcher.launcher_path(), self.bin)

    def _no_force(self, **extra):
        # Explicitly disable the force var (the conformance gate runs
        # with NYRQIS_LAUNCHER_FORCE=1 set globally, which would turn
        # these "missing binary" cases into raises).
        env = dict(extra, NYRQIS_LAUNCHER_FORCE="0")
        return mock.patch.dict(os.environ, env, clear=False)

    def test_missing_binary_is_none_without_force(self):
        with self._no_force(NYRQIS_LAUNCHER="/nonexistent/launcher"):
            self.assertIsNone(rust_launcher.launcher_path())
            self.assertFalse(rust_launcher.available())

    def test_force_raises_on_missing(self):
        with mock.patch.dict(
            os.environ, {
                "NYRQIS_LAUNCHER": "/nonexistent/launcher",
                "NYRQIS_LAUNCHER_FORCE": "1",
            }, clear=False,
        ):
            with self.assertRaises(RuntimeError):
                rust_launcher.launcher_path()

    def test_non_executable_is_skipped(self):
        os.chmod(self.bin, 0o644)
        with self._no_force(NYRQIS_LAUNCHER=self.bin):
            self.assertIsNone(rust_launcher.launcher_path())

    def test_deleted_binary_no_longer_available(self):
        # The uncached locator must not keep serving a path whose file
        # has gone away (regression: a cached path would make spawns
        # exec a dead binary — exit 126).
        with self._no_force(NYRQIS_LAUNCHER=self.bin):
            self.assertEqual(rust_launcher.launcher_path(), self.bin)
            os.unlink(self.bin)
            self.assertIsNone(rust_launcher.launcher_path())


class TestLauncherInitRust(unittest.TestCase):
    """The compiled launcher-init (``rust/launcher``, ADR-0020): the
    manager hands the container the Rust binary (with the PRE-BUILT
    seccomp program) when it is available, the Python launcher
    otherwise; real containers launch through it with the init
    contract intact (exit-status propagation, SIGTERM forwarding,
    active seccomp filter).
    """

    def _manager(self):
        return ContainerManager(use_cgroups_v2=False, use_direct_syscalls=True)

    def test_launcher_exec_uses_rust_binary_when_available(self):
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        argv = m._launcher_exec(c)
        if rust_launcher.available():
            self.assertTrue(os.path.basename(argv[0]).startswith("nyrqis-launcher"))
            self.assertIn("--", argv)
            self.assertEqual(argv[argv.index("--") + 1:], ["/bin/true"])
        else:
            # crate-less hosts: the Python launcher argv (argv[0] is
            # the interpreter, argv[1] is launcher.py)
            self.assertEqual(argv[0], sys.executable)
            self.assertIn("launcher.py", os.path.basename(argv[1]))

    def test_launcher_exec_falls_back_to_python(self):
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        with mock.patch.object(
            rust_launcher, "available", return_value=False
        ):
            argv = m._launcher_exec(c)
        self.assertEqual(argv[0], sys.executable)
        self.assertIn("launcher.py", os.path.basename(argv[1]))

    def test_launcher_exec_writes_bpf_file_when_seccomp(self):
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=True))
        before = set(m._bpf_files)
        try:
            argv = m._launcher_exec(c)
            if not rust_launcher.available():
                self.skipTest("Rust launcher-init not built on this host")
            bpf = argv[argv.index("--bpf-file") + 1]
            self.assertIn(bpf, m._bpf_files)
            # A classic-BPF program: a non-empty, 8-byte-aligned file
            # whose first record is the arch load (code 0x20 = BPF_LD|BPF_W|BPF_ABS).
            data = open(bpf, "rb").read()
            self.assertTrue(len(data) > 0 and len(data) % 8 == 0, len(data))
            self.assertEqual(struct.unpack("<H", data[:2])[0], 0x20)
        finally:
            m._cleanup_policy_files()

    def test_bpf_file_round_trips_through_rust_parser(self):
        # The manager's serialization is byte-for-byte the format
        # rust/launcher's parse_bpf reads: little-endian <HBBI records.
        import backend.rust_launcher as rl
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=True))
        try:
            if not rust_launcher.available():
                self.skipTest("Rust launcher-init not built on this host")
            argv = m._launcher_exec(c)
            bpf = argv[argv.index("--bpf-file") + 1]
            data = open(bpf, "rb").read()
            records = [
                struct.unpack("<HBBI", data[i:i + 8])
                for i in range(0, len(data), 8)
            ]
            self.assertEqual(
                [r[0] for r in records[:1]], [0x20], "first instr: ld [4]")
            self.assertTrue(all(
                0 <= r[1] <= 0xFF and 0 <= r[2] <= 0xFF for r in records))
        finally:
            m._cleanup_policy_files()

    def test_rust_init_propagates_exit_status(self):
        # Real container through the compiled init: the exit status of
        # the command (7) reaches the manager's wait() intact.
        if not rust_launcher.available():
            self.skipTest("Rust launcher-init not built on this host")
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/sh", "-c", "exit 7"], seccomp=False))
        m.spawn(c)
        try:
            self.assertEqual(m.wait(c, timeout_s=30), 7)
            self.assertEqual(c.state, ContainerState.TERMINATED)
        finally:
            try:
                m.terminate(c)
            except Exception:  # noqa: BLE001 - already gone
                pass

    def test_rust_init_forwards_sigterm(self):
        # SIGTERM to the compiled init reaches the command (PID-1
        # semantics would discard it without a handler), and the init
        # dies by the signal so wait() reports 128+15.
        if not rust_launcher.available():
            self.skipTest("Rust launcher-init not built on this host")
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        m = self._manager()
        c = m.create(ContainerConfig(command=["/bin/sleep", "30"], seccomp=False))
        m.spawn(c)
        try:
            time.sleep(0.5)
            os.kill(c._init_pid, signal.SIGTERM)
            try:
                deadline = time.time() + 8
                while time.time() < deadline:
                    try:
                        os.kill(c.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                else:
                    self.fail("command survived SIGTERM to the init")
            except ProcessLookupError:
                pass  # the command was already gone
            self.assertEqual(m.wait(c, timeout_s=15), 128 + signal.SIGTERM)
        finally:
            try:
                m.terminate(c)
            except Exception:  # noqa: BLE001 - already gone
                pass

    def test_rust_init_sets_hostname_and_seccomp(self):
        # The compiled init sets the UTS hostname AND installs the
        # container's filter. Proof the filter is ACTIVE: a file
        # create at an arbitrary path is denied by the DEFAULT
        # capability set (CAP_FILESYSTEM_WRITE is not a default grant)
        # — the shell exits 9 via `|| exit 9` (the same denial the
        # Python launcher produces with the same policy).
        if not rust_launcher.available():
            self.skipTest("Rust launcher-init not built on this host")
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        m = self._manager()
        tmp = tempfile.mkdtemp(prefix="nyrqis-rust-init-")
        out = os.path.join(tmp, "hn.txt")
        try:
            c = m.create(ContainerConfig(
                command=["/bin/sh", "-c",
                         f"hostname > {out} || exit 9"],
                seccomp=True, hostname="rust-hn-test"))
            m.spawn(c)
            rc = m.wait(c, timeout_s=30)
            # The filter denies the create: rc=9 (the `|| exit 9`
            # path) and no marker file. hostname itself ran fine (the
            # filter allows it) — hostname denial would also surface
            # here, which is equally a filter-active proof.
            self.assertEqual(rc, 9)
            self.assertFalse(os.path.exists(out))
        finally:
            try:
                m.terminate(c)
            except Exception:  # noqa: BLE001 - already gone
                pass
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rust_init_runs_command_with_network(self):
        # The compiled init is also used on the network path (own
        # netns + loopback up).
        if not rust_launcher.available():
            self.skipTest("Rust launcher-init not built on this host")
        if not _netns_launch_supported():
            self.skipTest(TestNetworkNamespaceIsolation._NETNS)
        m = self._manager()
        c = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=False, network=True))
        m.spawn(c)
        try:
            self.assertEqual(m.wait(c, timeout_s=30), 0)
        finally:
            try:
                m.terminate(c)
            except Exception:  # noqa: BLE001 - already gone
                pass


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
        # server through the AUTO-maintained registry (the manager
        # registers the pid at spawn — before the ready marker lets the
        # container send, so no TOCTOU — and drops it on terminate),
        # the seccomp filter permits the socket family (network caps
        # granted) and the marker write (filesystem cap), and the
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
        ipc_registry = ContainerIpcRegistry()
        server = IPCDatagramServer(
            ipc_manager, "ep-svc", svc_path, pid_registry=ipc_registry)
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
            use_cgroups_v2=False, use_direct_syscalls=True,
            ipc_registry=ipc_registry)
        container = ctr_manager.create(ContainerConfig(
            # The registry authenticates by container id — the client's
            # sender_id must match it.
            name="container-cli",
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
    def test_container_calls_status_service(self):
        # The first real backend service, end-to-end through a REAL
        # container: the container CALLs the BackendStatusService over
        # the datagram transport, the kernel's SCM_CREDENTIALS
        # authenticate it (auto-registry), the server enforces
        # CAP_IPC_SEND, and the service enforces CAP_SYSTEM_INFO (both
        # default grants) before answering with the container's own
        # identity and capability set. Zero manual pid bookkeeping.
        import threading
        base = tempfile.mkdtemp(prefix="nyrqis-status-e2e-")
        svc_path = os.path.join(base, "svc.sock")
        cli_path = os.path.join(base, "cli.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")

        ipc_manager = IPCManager()
        ipc_manager.create_endpoint("container-svc", "ep-svc")
        ipc_registry = ContainerIpcRegistry()
        # The control-plane manager is shared by the server AND the
        # container manager: the container is granted its defaults
        # (CAP_IPC_SEND for the server check, CAP_SYSTEM_INFO for the
        # status check) AUTOMATICALLY at spawn — no manual
        # initialize_container.
        caps = CapabilityManager()
        server = IPCDatagramServer(
            ipc_manager, "ep-svc", svc_path,
            pid_registry=ipc_registry, capability_manager=caps)
        service = BackendStatusService(
            capability_manager=caps, backend_version="test")
        service.attach(server)
        server.bind()
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
            "r = c.call(sys.argv[3], b'{\"op\": \"status\"}', timeout_s=10)\n"
            "open(sys.argv[5], 'w').write(r.payload.decode() if r else 'NONE')\n"
        )
        ctr_manager = ContainerManager(
            use_cgroups_v2=False, use_direct_syscalls=True,
            ipc_registry=ipc_registry, capability_manager=caps)
        container = ctr_manager.create(ContainerConfig(
            name="container-cli",
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
            with open(ready_path, "w") as fh:
                fh.write("go")
            deadline = time.time() + 20.0
            while time.time() < deadline and not os.path.exists(marker):
                time.sleep(0.05)
            self.assertTrue(
                os.path.exists(marker),
                "container never reached the status service",
            )
            with open(marker) as fh:
                body = fh.read()
            self.assertNotEqual(body, "NONE", "container got no status reply")
            resp = json.loads(body)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["container"], "container-cli")
            self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
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
        self.assertEqual(rust_syscalls.CLONE_SIGCHLD, 17)

    def test_clone_routes_through_ffi_when_loaded(self):
        # The Rust-native child (ABI 1.2.0): clone() crosses flags, the
        # entry function address (resolved from the loaded lib — never
        # a Python callback), and the LaunchArgs byref; a positive rc is
        # the child's pid. The entry is a REAL function pointer (from
        # libc) so the loader's ctypes.cast resolves a genuine address
        # without requiring the crate on this host.
        fake = mock.Mock()
        fake.nyrqis_syscalls_clone.return_value = 4242
        fake.nyrqis_syscalls_launch_child = ctypes.CDLL(None).free
        args = rust_syscalls.LaunchArgs.build(
            4, 1000, 1000, ["/bin/true"])
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            pid = rust_syscalls.clone(0x2000_0000 | 17, args)
        self.assertEqual(pid, 4242)
        call_args = fake.nyrqis_syscalls_clone.call_args.args
        self.assertEqual(call_args[0], 0x2000_0000 | 17)  # flags
        self.assertIsNotNone(call_args[1])  # the entry address
        self.assertIsNotNone(call_args[2])  # the LaunchArgs byref

    def test_clone_negative_rc_becomes_oserror(self):
        fake = mock.Mock()
        fake.nyrqis_syscalls_clone.return_value = -errno.EPERM
        fake.nyrqis_syscalls_launch_child = ctypes.CDLL(None).free
        args = rust_syscalls.LaunchArgs.build(4, 1000, 1000, ["/bin/true"])
        with mock.patch.object(
            rust_syscalls, "_load_rust_backend", return_value=fake
        ):
            with self.assertRaises(OSError) as cm:
                rust_syscalls.clone(17, args)
        self.assertEqual(cm.exception.errno, errno.EPERM)

    def test_clone_without_backend_raises(self):
        # clone is Rust-only by design (a raw child cannot run a Python
        # callback) — no ctypes fallback; the manager branches on
        # available() first.
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(rust_syscalls._load_rust_backend())
        args = rust_syscalls.LaunchArgs.build(4, 1000, 1000, ["/bin/true"])
        with self.assertRaises(RuntimeError):
            rust_syscalls.clone(17, args)
        self.assertFalse(rust_syscalls.available())

    def test_launch_args_build_is_execv_ready(self):
        # The argv the Rust entry execs: argc entries + a NULL
        # terminator, each a valid NUL-terminated string that survives
        # garbage collection.
        argv = ["/usr/bin/python3", "/x/launcher.py", "--", "/bin/true"]
        args = rust_syscalls.LaunchArgs.build(4, 1000, 1000, argv)
        self.assertEqual(args.argc, 4)
        import gc
        gc.collect()
        for i, expected in enumerate(argv):
            self.assertEqual(
                ctypes.string_at(
                    ctypes.cast(args.argv[i], ctypes.c_void_p).value
                ).decode(),
                expected,
            )
        # The execv NULL terminator must sit right after the last argv
        # slot (the kernel scans for it; without it execv reads past the
        # array -> EFAULT).
        self.assertEqual(args.argv[args.argc], None)

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


class TestContainerIpcRegistry(unittest.TestCase):
    """The auto-maintained pid → container registry for transport
    sender authentication (``ipc/registry.py``): the manager registers
    direct-syscall containers at spawn and drops them on terminate, and
    the transport server resolves senders through it with no manual
    bookkeeping. The container→service e2e
    (``test_container_ipc_call_service``) proves the whole chain.
    """

    def test_register_resolve_callable_and_len(self):
        r = ContainerIpcRegistry()
        self.assertEqual(len(r), 0)
        r.register(100, "ctr-a")
        r.register(200, "ctr-b")
        self.assertEqual(r.resolve(100), "ctr-a")
        self.assertEqual(r(200), "ctr-b")  # callable → server pid_registry
        self.assertIsNone(r.resolve(300))
        self.assertIn(100, r)
        self.assertEqual(len(r), 2)
        r.unregister(100)
        self.assertIsNone(r.resolve(100))
        self.assertEqual(len(r), 1)

    def test_unregister_missing_is_idempotent(self):
        r = ContainerIpcRegistry()
        r.register(1, "x")
        r.unregister(999)  # never registered
        r.unregister(None)  # container never spawned
        self.assertEqual(len(r), 1)
        r.unregister(1)
        self.assertEqual(len(r), 0)

    def test_on_change_fires_after_every_mutation(self):
        # ADR-0021's per-container pid-table refresh: the daemon hooks
        # the registry so every spawn/terminate pushes the new policy
        # to the Rust serving loop. The callback fires AFTER the map is
        # updated, so a snapshot taken inside it is current.
        r = ContainerIpcRegistry()
        events = []
        r.set_on_change(lambda: events.append(r.snapshot()))
        r.register(100, "ctr-a")
        r.register(200, "ctr-b")
        r.unregister(100)
        self.assertEqual(events, [
            {100: "ctr-a"},
            {100: "ctr-a", 200: "ctr-b"},
            {200: "ctr-b"},
        ])
        # Replacing the hook detaches it; unregistering an unknown pid
        # or None does NOT fire (nothing changed).
        events.clear()
        r.set_on_change(lambda: events.append(1))
        r.unregister(999)
        r.unregister(None)
        self.assertEqual(events, [])

    def test_on_change_failure_is_swallowed(self):
        # A failing policy-refresh callback must never break container
        # lifecycle: register/unregister proceed and the exception is
        # logged, not raised.
        r = ContainerIpcRegistry()

        def boom():
            raise RuntimeError("policy push failed")

        r.set_on_change(boom)
        with mock.patch("ipc.registry.logger.warning"):
            r.register(100, "ctr-a")
            r.unregister(100)
        self.assertEqual(len(r), 0)
        self.assertIsNone(r.resolve(100))

    def test_server_authenticates_via_registry(self):
        registry = ContainerIpcRegistry()
        registry.register(1234, "ctr-a")
        server = IPCDatagramServer(
            IPCManager(), "ep-svc", "/tmp/nyrqis-registry-test.sock",
            pid_registry=registry)
        self.assertEqual(server._resolve_sender(1234), "ctr-a")
        self.assertIsNone(server._resolve_sender(9999))

    def test_spawn_registers_direct_path_and_terminate_unregisters(self):
        registry = ContainerIpcRegistry()
        m = ContainerManager(use_cgroups_v2=False, use_direct_syscalls=True,
                             ipc_registry=registry)
        c = m.create(ContainerConfig(name="ctr-1", command=["/bin/true"]))

        def fake_spawn(container):
            container.pid = 4242
            container._direct_launcher_pid = 4241

        with mock.patch.object(m, "_setup_cgroups"), \
             mock.patch.object(m, "_spawn", side_effect=fake_spawn), \
             mock.patch.object(m, "_attach_to_cgroups"):
            m.spawn(c)
        self.assertEqual(registry.resolve(4242), "ctr-1")
        self.assertEqual(len(registry), 1)

        with mock.patch.object(c, "is_running", return_value=False), \
             mock.patch("backend.container.os.kill"), \
             mock.patch.object(m, "_freeze_control", return_value=None):
            m.terminate(c)
        self.assertIsNone(registry.resolve(4242))
        self.assertEqual(len(registry), 0)

    def test_spawn_does_not_register_legacy_path(self):
        # The legacy unshare(1) path is not tracked: the command runs as
        # a grandchild with a different pid, so its datagrams fail
        # closed (documented in ipc/registry.py).
        registry = ContainerIpcRegistry()
        m = ContainerManager(use_cgroups_v2=False, use_direct_syscalls=False,
                             ipc_registry=registry)
        c = m.create(ContainerConfig(name="ctr-legacy", command=["/bin/true"]))

        def fake_spawn(container):
            container.pid = 9999  # the unshare(1) Popen pid

        with mock.patch.object(m, "_setup_cgroups"), \
             mock.patch.object(m, "_spawn", side_effect=fake_spawn), \
             mock.patch.object(m, "_attach_to_cgroups"):
            m.spawn(c)
        self.assertEqual(len(registry), 0)
        self.assertIsNone(registry.resolve(9999))

    def test_wait_unregisters_on_popen_path(self):
        registry = ContainerIpcRegistry()
        m = ContainerManager(use_cgroups_v2=False, use_direct_syscalls=False,
                             ipc_registry=registry)
        c = m.create(ContainerConfig(name="ctr-w", command=["/bin/true"]))
        c.transition_to(ContainerState.RUNNING)
        c.pid = 7777
        registry.register(7777, "ctr-w")
        proc = mock.Mock()
        proc.wait.return_value = 0
        c._proc = proc
        code = m.wait(c)
        self.assertEqual(code, 0)
        self.assertIsNone(registry.resolve(7777))


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
        # recv must drive the FFI entry point with the caller's reusable
        # buffers and translate the written outputs into the floor's
        # tuple — no allocation, no free (FFI surface v2).
        fake = mock.Mock()
        payload = b"recv-frame"
        wire_buf = ctypes.create_string_buffer(transport_codec.RECV_WIRE_SIZE)
        path_buf = ctypes.create_string_buffer(transport_codec.RECV_PATH_SIZE)

        def fake_recv(fd, ms, wbuf, wcap, olen, pbuf, pcap, oplen,
                      op, ou, og):
            # The byref'd outputs arrive as CArgObjects; write through
            # them with cast (verified: cast works on CArgObject). The
            # caller's buffers are passed by address (c_void_p).
            ctypes.memmove(wbuf, payload, len(payload))
            ctypes.cast(olen, ctypes.POINTER(ctypes.c_size_t))[0] = len(payload)
            ctypes.memmove(pbuf, b"/tmp/sender.sock", len(b"/tmp/sender.sock"))
            ctypes.cast(oplen, ctypes.POINTER(ctypes.c_size_t))[0] = (
                len(b"/tmp/sender.sock"))
            ctypes.cast(op, ctypes.POINTER(ctypes.c_int))[0] = os.getpid()
            ctypes.cast(ou, ctypes.POINTER(ctypes.c_int))[0] = os.getuid()
            ctypes.cast(og, ctypes.POINTER(ctypes.c_int))[0] = os.getgid()
            return 0

        fake.nyrqis_transport_recv.side_effect = fake_recv
        with mock.patch.object(
            transport_codec, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.transport_codec.ctypes.CDLL") as cdll_mock:
            result = transport_codec.recv(7, 250, wire_buf, path_buf)
        self.assertEqual(
            result,
            (payload, os.getpid(), os.getuid(), os.getgid(), "/tmp/sender.sock"),
        )
        fake.nyrqis_transport_recv.assert_called_once()
        cdll_mock.assert_not_called()
        # The v2 surface never allocates or frees — the caller owns the
        # buffers (Mock auto-creates the attr, so assert call_count 0).
        self.assertEqual(fake.nyrqis_transport_free.call_count, 0)
        args = fake.nyrqis_transport_recv.call_args.args
        self.assertEqual(args[0], 7)  # fd
        self.assertEqual(args[3], transport_codec.RECV_WIRE_SIZE)  # wire_cap
        self.assertEqual(args[6], transport_codec.RECV_PATH_SIZE)  # path_cap

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

    def test_binary_payload_with_embedded_nul_bytes(self):
        # The zero-copy send passes the immutable bytes buffer by
        # pointer (c_char_p) and the Rust side reads exactly wire_len
        # bytes — embedded NULs MUST survive end-to-end (real IPC wire
        # frames are binary and contain them). This is the regression
        # guard for the v2 send path.
        base = tempfile.mkdtemp(prefix="nyrqis-rust-transport-")
        try:
            a = UnixDatagramEndpoint(os.path.join(base, "a.sock")).bind()
            b = UnixDatagramEndpoint(os.path.join(base, "b.sock")).bind()
            try:
                payload = b"NYRQ\x00\x01\x00frame\x00\x00tail"
                self.assertIn(b"\x00", payload)  # guard: must contain NULs
                a.send(payload, b.path)
                got = b.receive(timeout=2.0)
                self.assertIsNotNone(got)
                data, pid, uid, gid, sender_path = got
                self.assertEqual(data, payload)
                self.assertEqual(pid, os.getpid())
                self.assertEqual(sender_path, a.path)
            finally:
                a.close()
                b.close()
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


class TestRustIpcdLoader(unittest.TestCase):
    """ADR-0021 FFI loader behavior (see rust/ipcd/): the fallback
    contract and error mapping for the Rust IPC serving loop. Like the
    other migration loader tests, these pin the loader on hosts WITHOUT
    the crate built; when the crate lands, the CI conformance job
    (NYRQIS_RUST_FORCE=1) is the real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        ipc_loop._RUST_LIB = None
        ipc_loop._RUST_LIB_CHECKED = False

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        ipc_loop._RUST_LIB = None
        ipc_loop._RUST_LIB_CHECKED = False

    @staticmethod
    def _no_backend():
        os.environ["NYRQIS_RUST_LIB"] = "/nonexistent/libnyrqis_ipcd.so"

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_RUST_LIB"] = "/custom/libnyrqis_ipcd.so"
        self.assertEqual(
            ipc_loop._rust_lib_candidates(),
            ["/custom/libnyrqis_ipcd.so"],
        )

    def test_error_mapping_negative_errno_becomes_oserror(self):
        with self.assertRaises(OSError) as cm:
            ipc_loop._raise_rust_error(-errno.EINVAL, "test")
        self.assertEqual(cm.exception.errno, errno.EINVAL)

    def test_error_mapping_internal_is_runtime_error(self):
        with self.assertRaises(RuntimeError):
            ipc_loop._raise_rust_error(ipc_loop.RUST_ERR_INTERNAL, "test")

    def test_absent_backend_raises_backend_unavailable(self):
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        self.assertIsNone(ipc_loop._load_rust_backend())
        with self.assertRaises(ipc_loop.BackendUnavailable):
            ipc_loop.IpcdLoop(3)

    def test_force_mode_raises_when_backend_unavailable(self):
        self._no_backend()
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            ipc_loop.IpcdLoop(3)

    def test_ffi_loop_new_routing_with_fake_lib(self):
        # With a lib loaded, IpcdLoop must drive the FFI entry points
        # with the expected arguments and never touch ctypes.CDLL.
        fake = mock.Mock()
        fake.nyrqis_ipcd_loop_new.return_value = 0x1234
        fake.nyrqis_ipcd_loop_step.return_value = 0
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.loop.ctypes.CDLL") as cdll_mock:
            loop = ipc_loop.IpcdLoop(
                7, batch_max=8,
                pids={os.getpid(): "ctr-a"},
                trusted_uids=[os.getuid()],
                operator_id="host-op",
            )
            processed = loop.step(250)
            loop.close()
        cdll_mock.assert_not_called()
        # loop_new: fd, batch_max, pid table (pid + container), trusted
        # uids, operator id.
        new_args = fake.nyrqis_ipcd_loop_new.call_args.args
        self.assertEqual(new_args[0], 7)
        self.assertEqual(new_args[1], 8)
        self.assertEqual(new_args[3], 1)  # one pid entry
        entry = new_args[2][0]
        self.assertEqual(entry.pid, os.getpid())
        self.assertEqual(entry.container, b"ctr-a")
        self.assertEqual(new_args[5], 1)  # one trusted uid
        self.assertEqual(new_args[6], b"host-op")
        # step: (handle, timeout_ms).
        self.assertEqual(fake.nyrqis_ipcd_loop_step.call_args.args[1], 250)
        self.assertEqual(processed, 0)
        # free: the handle (wrapped as c_void_p).
        self.assertEqual(
            fake.nyrqis_ipcd_loop_free.call_args.args[0].value, 0x1234)

    def test_ffi_loop_step_error_mapping(self):
        fake = mock.Mock()
        fake.nyrqis_ipcd_loop_new.return_value = 0x99
        fake.nyrqis_ipcd_loop_step.return_value = -errno.EPIPE
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ):
            loop = ipc_loop.IpcdLoop(3)
            with self.assertRaises(OSError) as cm:
                loop.step(10)
            self.assertEqual(cm.exception.errno, errno.EPIPE)

    def test_force_mode_raises_on_rust_loop_new_failure(self):
        fake = mock.Mock()
        fake.nyrqis_ipcd_loop_new.return_value = None
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ), mock.patch.dict(os.environ, {"NYRQIS_RUST_FORCE": "1"}):
            with self.assertRaises(RuntimeError):
                ipc_loop.IpcdLoop(3)

    def test_ffi_dispatch_routing_with_fake_lib(self):
        # ADR-0021 decision point 1 routing (drain → enqueue →
        # discard) with a fake lib: the driver must marshal the FFI
        # arguments exactly as the real crate expects, so crate-less
        # hosts still pin the contract (the CI gate drives the real
        # crate).
        fake = mock.Mock()
        fake.nyrqis_ipcd_loop_new.return_value = 0x55
        fake.nyrqis_ipcd_loop_step.return_value = 0

        def fake_drain(handle, buf, cap):
            rec = b"\x05\x00\x00\x00hello"  # [u32 len][wire]
            ctypes.memmove(buf, rec, len(rec))
            return len(rec)

        fake.nyrqis_ipcd_loop_drain_requests.side_effect = fake_drain
        fake.nyrqis_ipcd_loop_enqueue_replies.return_value = 0
        fake.nyrqis_ipcd_loop_discard_requests.return_value = 0
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ), mock.patch("ipc.loop.ctypes.CDLL") as cdll_mock:
            loop = ipc_loop.IpcdLoop(7, batch_max=8)
            wires = loop.drain_requests()
            self.assertEqual(wires, [b"hello"])
            loop.enqueue_replies([b"reply-wire-1"])
            loop.discard_requests()
            loop.close()
        cdll_mock.assert_not_called()
        # drain: (handle, caller buffer, size) — buffer sized from
        # batch_max * (64 KiB + 4).
        drain_args = fake.nyrqis_ipcd_loop_drain_requests.call_args.args
        self.assertEqual(drain_args[0].value, 0x55)
        self.assertGreaterEqual(drain_args[2], 8 * (64 * 1024 + 4))
        # enqueue: (handle, ReplyWire array, count) — the wire bytes
        # marshalled with their length.
        enq_args = fake.nyrqis_ipcd_loop_enqueue_replies.call_args.args
        self.assertEqual(enq_args[0].value, 0x55)
        self.assertEqual(enq_args[2], 1)
        self.assertEqual(enq_args[1][0].wire_len, len(b"reply-wire-1"))
        # discard: (handle).
        self.assertEqual(
            fake.nyrqis_ipcd_loop_discard_requests.call_args.args[0].value,
            0x55)

    def test_client_call_routing_with_fake_lib(self):
        # ADR-0021 client half: the driver must marshal the FFI
        # arguments exactly as the crate expects (fd, peer path, call
        # wire, timeout) and map the returns (reply length → bytes,
        # -ETIMEDOUT → None). Pinned here on crate-less hosts; the CI
        # gate drives the real crate.
        fake = mock.Mock()

        def fake_call(fd, peer, call_arr, wire_len, buf, cap, timeout):
            # A canned reply wire: MAGIC + REPLY + "reply-1" + reply_to
            # "call-1" + payload.
            from ipc.core import IPCMessage, IPCMessageType
            reply = IPCMessage(
                message_type=IPCMessageType.REPLY,
                payload=b'{"ok": true}',
                reply_to="call-1",
            ).to_wire()
            ctypes.memmove(buf, reply, len(reply))
            return len(reply)

        fake.nyrqis_ipcd_client_call.side_effect = fake_call
        # Keep the codec on its fallback path for the whole test:
        # under the CI conformance gate (Nyrqis_RUST_FORCE=1 with the
        # ipcd crate but not the codec crate loaded) the force check
        # would otherwise raise inside the fake's to_wire()/from_wire()
        # round trip. Disabling the codec's force flag falls it back to
        # the struct floor — byte-identical by contract, so nothing
        # observable changes.
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ), mock.patch.object(
            ipc_codec, "_force_enabled", return_value=False
        ):
            out = ipc_loop.client_call(7, "/tmp/peer.sock", b"call-wire", 500)
            from ipc.core import IPCMessage
            reply = IPCMessage.from_wire(out)
            self.assertEqual(reply.payload, b'{"ok": true}')
            self.assertEqual(reply.reply_to, "call-1")
            args = fake.nyrqis_ipcd_client_call.call_args.args
            self.assertEqual(args[0], 7)
            self.assertEqual(args[1], b"/tmp/peer.sock")
            self.assertEqual(args[3], len(b"call-wire"))
            self.assertEqual(args[6], 500)

    def test_client_call_timeout_returns_none(self):
        fake = mock.Mock()
        fake.nyrqis_ipcd_client_call.return_value = -errno.ETIMEDOUT
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ):
            self.assertIsNone(
                ipc_loop.client_call(7, "/tmp/peer.sock", b"call-wire", 60))

    def test_client_call_absent_backend_falls_back(self):
        # No crate → BackendUnavailable (the caller uses the Python
        # floor loop); force mode turns it into an error.
        self._no_backend()
        os.environ.pop("NYRQIS_RUST_FORCE", None)
        with self.assertRaises(ipc_loop.BackendUnavailable):
            ipc_loop.client_call(7, "/tmp/peer.sock", b"call-wire", 60)
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            ipc_loop.client_call(7, "/tmp/peer.sock", b"call-wire", 60)

    def test_drain_enobufs_retries_with_grown_buffer(self):
        fake = mock.Mock()
        fake.nyrqis_ipcd_loop_new.return_value = 0x55

        def fake_drain(handle, buf, cap):
            # batch_max=1 sizes the first buffer at 64 KiB + 4; the
            # retry grows it past 1 MiB. Fail until the grown buffer.
            if cap < (1 << 20):
                return -errno.ENOBUFS
            rec = b"\x04\x00\x00\x00wire"
            ctypes.memmove(buf, rec, len(rec))
            return len(rec)

        fake.nyrqis_ipcd_loop_drain_requests.side_effect = fake_drain
        fake.nyrqis_ipcd_loop_discard_requests.return_value = 0
        with mock.patch.object(
            ipc_loop, "_load_rust_backend", return_value=fake
        ):
            loop = ipc_loop.IpcdLoop(3, batch_max=1)
            wires = loop.drain_requests()
            self.assertEqual(wires, [b"wire"])
            loop.close()
        # The retry grew the buffer past the first size.
        sizes = [
            c.args[2] for c in
            fake.nyrqis_ipcd_loop_drain_requests.call_args_list
        ]
        self.assertEqual(len(sizes), 2)
        self.assertLess(sizes[0], sizes[1])


class TestIpcdLoopConformance(unittest.TestCase):
    """ADR-0021 differential: the Rust serving loop (via the FFI) must
    reproduce the Python floor's serving semantics for the built-in
    ping op — reply correlation (reply_to = call id), payload
    byte-identical, empty sender/receiver, metadata {}. Runs when the
    crate is built (the CI gate builds it and forces the class; locally
    it runs when the crate is present). The message_id and timestamp of
    the reply are per-message (uuid/now on the floor, generated in the
    loop) and are NOT part of the differential — everything else is.
    """

    @classmethod
    def setUpClass(cls):
        ipc_loop._RUST_LIB = None
        ipc_loop._RUST_LIB_CHECKED = False
        cls.available = ipc_loop.available()

    def setUp(self):
        if not self.available:
            self.skipTest(
                "Rust IPC serving loop crate not built (CI gate builds it)")
        self.tmp = tempfile.mkdtemp(prefix="nyrqis-ipcd-")
        # The gate env (NYRQIS_RUST_FORCE=1, NYRQIS_RUST_LIB pointing at
        # the ipcd cdylib) would make the OTHER loaders fail their own
        # force checks on any class that touches them (the documented
        # cross-loader hazard — each gate runs only classes that don't
        # depend on the other modules' libs). This class's data path
        # goes through the wire codec and the transport, so pin those
        # two loaders to their pure-Python floors (byte-identical,
        # proven by their own conformance gates): only the ipcd module
        # is the forced lib under this gate.
        self._codec_force = mock.patch.object(
            ipc_codec, "_force_enabled", return_value=False)
        self._transport_force = mock.patch.object(
            transport_codec, "force_enabled", return_value=False)
        self._codec_force.start()
        self._transport_force.start()

    def tearDown(self):
        self._transport_force.stop()
        self._codec_force.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _floor_server(self, name):
        """The Python floor: an IPCDatagramServer with the status
        service attached, same policy as the loop (pid→container,
        trusted uid, operator id)."""
        svc_path = os.path.join(self.tmp, f"{name}-svc.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={os.getpid(): "container-A"},
            trusted_uids={os.getuid()},
        )
        BackendStatusService().attach(server)
        server.bind()
        return manager, server

    def _loop_server(self, name):
        """The Rust loop over the same socket setup."""
        svc_path = os.path.join(self.tmp, f"{name}-svc.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={os.getpid(): "container-A"},
            trusted_uids={os.getuid()},
        )
        server.bind()
        loop = ipc_loop.IpcdLoop(
            server.endpoint._sock.fileno(),
            batch_max=16,
            pids={os.getpid(): "container-A"},
            trusted_uids=[os.getuid()],
        )
        stop = threading.Event()

        def drive():
            while not stop.is_set():
                try:
                    loop.step(100)
                except Exception:
                    break

        thread = threading.Thread(target=drive, daemon=True)
        thread.start()
        return manager, server, loop, stop, thread

    @staticmethod
    def _assert_reply_matches_floor(reply, call_msg, expected_payload):
        # The differential: every field the floor sets is compared —
        # message_id and timestamp are per-message by design.
        assert reply.message_type == IPCMessageType.REPLY
        assert reply.reply_to == call_msg.message_id
        assert reply.sender_id == ""
        assert reply.receiver_id == ""
        assert reply.capabilities == []
        assert reply.metadata == {}
        assert reply.payload == expected_payload

    def test_ping_reply_matches_floor_semantics(self):
        # The differential: drive the floor once, drive the loop once
        # with the identical policy and request, and compare every
        # reply field the floor sets (message_id/timestamp are
        # per-message by design and excluded).
        _, floor = self._floor_server("floor")
        mgr, loop_srv, loop, stop, thread = self._loop_server("loop")
        client = IPCClient("container-A", os.path.join(self.tmp, "cli.sock")).bind()
        floor_stop = threading.Event()
        threading.Thread(
            target=floor.serve, args=(floor_stop,), daemon=True).start()
        try:
            # Floor path: synchronous call through the real server.
            call_msg = client.call(
                floor.endpoint.path, b'{"op": "ping"}', timeout_s=5.0)
            self.assertIsNotNone(call_msg, "floor must answer")
            self.assertEqual(call_msg.message_type, IPCMessageType.REPLY)
            # Loop path: the identical request wire, driven by the loop
            # thread; the client receives the correlated reply.
            req = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-A",
                receiver_id="",
                payload=b'{"op": "ping"}',
            )
            client.endpoint.send(req.to_wire(), peer_path=loop_srv.endpoint.path)
            got = client.endpoint.receive(timeout=3.0)
            self.assertIsNotNone(got, "loop must answer")
            data, pid, uid, gid, sender_path = got
            loop_reply = IPCMessage.from_wire(data)
            self.assertEqual(loop_reply.message_type, IPCMessageType.REPLY)
            self.assertEqual(loop_reply.reply_to, req.message_id)
            self.assertEqual(loop_reply.sender_id, "")
            self.assertEqual(loop_reply.receiver_id, "")
            self.assertEqual(loop_reply.capabilities, [])
            self.assertEqual(loop_reply.metadata, {})
            # The payload is byte-identical to the floor's.
            self.assertEqual(loop_reply.payload, call_msg.payload)
            self.assertIn(
                b'"container": "container-A"', loop_reply.payload)
        finally:
            floor_stop.set()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            loop_srv.close()
            client.close()
            floor.close()

    def test_loop_batches_multiple_pings_in_one_step(self):
        mgr, srv, loop, stop, thread = self._loop_server("batch")
        client = IPCClient("container-A", os.path.join(self.tmp, "batch-cli.sock")).bind()
        try:
            ids = []
            for i in range(5):
                req = IPCMessage(
                    message_type=IPCMessageType.CALL,
                    sender_id="container-A",
                    payload=b'{"op": "ping"}',
                )
                ids.append(req.message_id)
                client.endpoint.send(req.to_wire(), peer_path=srv.endpoint.path)
            time.sleep(0.3)  # let the loop thread drain the batch
            for expected in ids:
                got = client.endpoint.receive(timeout=2.0)
                self.assertIsNotNone(got)
                reply = IPCMessage.from_wire(got[0])
                self.assertEqual(reply.reply_to, expected)
        finally:
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            srv.close()
            client.close()

    def test_client_half_matches_floor_client(self):
        # ADR-0021 client half differential: the Rust client must
        # produce the same CALL/REPLY semantics as the Python floor
        # client against the SAME server. A floor server (status
        # service, pid-registered container) answers one ping through
        # each client; the replies are byte-identical and correlated.
        mgr, server, loop, stop, thread = self._loop_server("chalf-loop")
        floor = self._floor_server("chalf-floor")[1]
        client = IPCClient(
            "container-A", os.path.join(self.tmp, "chalf-cli.sock")).bind()
        floor_stop = threading.Event()
        threading.Thread(
            target=floor.serve, args=(floor_stop,), daemon=True).start()
        try:
            # Floor client → floor server.
            floor_reply = client.call(
                floor.endpoint.path, b'{"op": "ping"}', timeout_s=5.0)
            self.assertIsNotNone(floor_reply)
            # Rust client → the same floor server (cross-validation:
            # the client half against the Python server).
            rust_reply = ipc_loop.client_call(
                client.endpoint._sock.fileno(),
                floor.endpoint.path,
                IPCMessage(
                    message_type=IPCMessageType.CALL,
                    sender_id="container-A",
                    payload=b'{"op": "ping"}',
                ).to_wire(),
                5000,
            )
            self.assertIsNotNone(rust_reply, "Rust client must get a reply")
            rust_msg = IPCMessage.from_wire(rust_reply)
            self.assertEqual(rust_msg.message_type, IPCMessageType.REPLY)
            # Each call has its own message_id, so the reply_to values
            # differ across the two calls by construction; the Rust
            # half correlates internally (it only returns a matching
            # reply), so the differential is the payload + field
            # semantics below.
            self.assertEqual(rust_msg.sender_id, "")
            self.assertEqual(rust_msg.receiver_id, "")
            self.assertEqual(rust_msg.capabilities, [])
            self.assertEqual(rust_msg.metadata, {})
            # The payload is byte-identical to the floor client's.
            self.assertEqual(rust_msg.payload, floor_reply.payload)
        finally:
            floor_stop.set()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            server.close()
            floor.close()
            client.close()

    def test_client_half_times_out_without_reply(self):
        # A server that never answers (nothing bound at the path) → the
        # Rust client times out and returns None, like the floor.
        mgr, server, loop, stop, thread = self._loop_server("ctime")
        client = IPCClient(
            "container-A", os.path.join(self.tmp, "ctime-cli.sock")).bind()
        quiet_ep = UnixDatagramEndpoint(
            os.path.join(self.tmp, "quiet.sock")).bind()
        try:
            wire = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-A",
                payload=b'{"op": "ping"}',
            ).to_wire()
            t0 = time.time()
            self.assertIsNone(ipc_loop.client_call(
                client.endpoint._sock.fileno(), quiet_ep.path, wire, 100))
            self.assertLess(time.time() - t0, 3.0, "must honor the timeout")
        finally:
            quiet_ep.close()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            server.close()
            client.close()

    def _dispatch_server(self, name):
        """A server pair (floor + loop-with-dispatcher) serving the
        status service over a granted container identity — the setup
        for the non-ping dispatch differential. Returns the floor
        server, the loop server, the dispatcher, the stop event, and
        the drive thread."""
        cap_mgr = CapabilityManager()
        cap_mgr.initialize_container("container-A")
        service = BackendStatusService(
            capability_manager=cap_mgr, backend_version="9.9.9")

        floor_path = os.path.join(self.tmp, f"{name}-floor.sock")
        floor_mgr = IPCManager()
        floor_mgr.create_endpoint("container-svc", "ep-svc")
        floor = IPCDatagramServer(
            floor_mgr, "ep-svc", floor_path,
            pid_registry={os.getpid(): "container-A"},
            capability_manager=cap_mgr,
            trusted_uids={os.getuid()},
        )
        service.attach(floor)
        floor.bind()

        loop_path = os.path.join(self.tmp, f"{name}-loop.sock")
        loop_mgr = IPCManager()
        loop_mgr.create_endpoint("container-svc", "ep-svc")
        loop_srv = IPCDatagramServer(
            loop_mgr, "ep-svc", loop_path,
            pid_registry={os.getpid(): "container-A"},
            capability_manager=cap_mgr,
            trusted_uids={os.getuid()},
        )
        loop_srv.bind()
        loop = ipc_loop.IpcdLoop(
            loop_srv.endpoint._sock.fileno(),
            batch_max=16,
            pids={os.getpid(): "container-A"},
            trusted_uids=[os.getuid()],
        )
        router = ServiceRouter()
        router.register("status", BackendStatusService(
            capability_manager=cap_mgr, backend_version="9.9.9"))
        dispatcher = IpcdLoopDispatcher(
            loop, router, capability_manager=cap_mgr)
        stop = threading.Event()

        def drive():
            while not stop.is_set():
                try:
                    dispatcher.serve_once(100)
                except Exception:
                    break

        thread = threading.Thread(target=drive, daemon=True)
        thread.start()
        return floor, loop_srv, loop, dispatcher, stop, thread, cap_mgr

    def test_non_ping_dispatch_matches_floor(self):
        # ADR-0021 decision point 1 differential: a non-ping op over
        # the loop (queued → drained → Python service handler → reply
        # wire → loop routes it) must match the floor's reply. The
        # unknown-op reply is fully deterministic, so it is compared
        # byte-for-byte; `status` carries per-run fields (uptime_s), so
        # its deterministic fields are compared semantically.
        floor, loop_srv, loop, dispatcher, stop, thread, cap_mgr = \
            self._dispatch_server("dispatch")
        client = IPCClient(
            "container-A", os.path.join(self.tmp, "dispatch-cli.sock")).bind()
        floor_stop = threading.Event()
        threading.Thread(
            target=floor.serve, args=(floor_stop,), daemon=True).start()
        try:
            # Deterministic: an unknown op → identical reply bytes.
            floor_reply = client.call(
                floor.endpoint.path, b'{"op": "bogus"}', timeout_s=5.0)
            self.assertIsNotNone(floor_reply, "floor must answer")
            loop_reply = client.call(
                loop_srv.endpoint.path, b'{"op": "bogus"}', timeout_s=5.0)
            self.assertIsNotNone(loop_reply, "loop must answer via dispatch")
            self.assertEqual(loop_reply.message_type, IPCMessageType.REPLY)
            # The client's call() already correlated the reply to its
            # own request, so reply_to is correct by construction; the
            # differential is the payload.
            self.assertEqual(
                loop_reply.payload, floor_reply.payload,
                "the dispatch-handoff reply must be byte-identical to "
                "the floor's")
            resp = json.loads(loop_reply.payload.decode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown operation", resp["error"])

            # Semantics: `status` returns the full service reply with
            # the caller's identity + granted capabilities.
            status_reply = client.call(
                loop_srv.endpoint.path, b'{"op": "status"}', timeout_s=5.0)
            self.assertIsNotNone(status_reply)
            resp = json.loads(status_reply.payload.decode())
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["service"], "nyrqis.backend.status")
            self.assertEqual(resp["backend_version"], "9.9.9")
            self.assertEqual(resp["container"], "container-A")
            self.assertIn("CAP_SYSTEM_INFO", resp["capabilities"])
        finally:
            floor_stop.set()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            loop_srv.close()
            floor.close()
            client.close()

    def test_dispatch_drops_sender_without_cap_ipc_send(self):
        # Floor parity for the dispatch handoff: a container WITHOUT
        # CAP_IPC_SEND is dropped BEFORE dispatch in both backends (the
        # loop authorized it, but the dispatcher mirrors the floor's
        # CAP_IPC_SEND gate) — no reply, never dispatched.
        floor, loop_srv, loop, dispatcher, stop, thread, cap_mgr = \
            self._dispatch_server("nogrant")
        client = IPCClient(
            "container-A", os.path.join(self.tmp, "nogrant-cli.sock")).bind()
        floor_stop = threading.Event()
        threading.Thread(
            target=floor.serve, args=(floor_stop,), daemon=True).start()
        try:
            # Revoke the container's grants (defaults include
            # CAP_IPC_SEND + CAP_SYSTEM_INFO) — now it has neither.
            cap_mgr.reset_container("container-A")
            floor_reply = client.call(
                floor.endpoint.path, b'{"op": "status"}', timeout_s=1.0)
            self.assertIsNone(floor_reply, "floor drops the ungranted sender")
            loop_reply = client.call(
                loop_srv.endpoint.path, b'{"op": "status"}', timeout_s=1.0)
            self.assertIsNone(
                loop_reply,
                "the dispatch handoff must drop an ungranted sender "
                "exactly like the floor")
        finally:
            floor_stop.set()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            loop_srv.close()
            floor.close()
            client.close()

    def test_loop_drops_non_ping_and_unknown_sender(self):
        mgr, srv, loop, stop, thread = self._loop_server("drop")
        client = IPCClient("container-A", os.path.join(self.tmp, "drop-cli.sock")).bind()
        try:
            # Non-ping op from a known sender: drained but not answered.
            req = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-A",
                payload=b'{"op": "status"}',
            )
            client.endpoint.send(req.to_wire(), peer_path=srv.endpoint.path)
            self.assertIsNone(client.endpoint.receive(timeout=0.5))
            # Forged sender_id (pid authenticates as container-A): the
            # loop drops it — the wire sender is not the kernel identity.
            forged = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-evil",
                payload=b'{"op": "ping"}',
            )
            client.endpoint.send(forged.to_wire(), peer_path=srv.endpoint.path)
            self.assertIsNone(client.endpoint.receive(timeout=0.5))
        finally:
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            srv.close()
            client.close()

    def test_set_policy_refreshes_pid_table(self):
        # ADR-0021's per-container pid-table refresh through the driver:
        # ``IpcdLoop.set_policy`` replaces the loop's authorization in
        # place (the daemon pushes the registry snapshot on every
        # container spawn/terminate). The loop starts authorizing
        # ``container-A`` (from ``_loop_server``); after the refresh to
        # an EMPTY pid table, our pid resolves only via the trusted-uid
        # operator fallback, so a container-id ping is dropped; after a
        # second refresh registering ``container-B``, that identity is
        # answered. The floor equivalent needs no refresh (it reads the
        # registry live) — the crate test pins the mechanics, this test
        # pins the driver round trip.
        mgr, srv, loop, stop, thread = self._loop_server("refresh")
        client = IPCClient("container-A", os.path.join(self.tmp, "refresh-cli.sock")).bind()
        try:
            # Baseline: container-A is authorized at creation.
            req = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-A",
                payload=b'{"op": "ping"}',
            )
            client.endpoint.send(req.to_wire(), peer_path=srv.endpoint.path)
            got = client.endpoint.receive(timeout=2.0)
            self.assertIsNotNone(got, "container-A must be answered at creation")
            self.assertIn(b'"container": "container-A"', got[0])

            # Refresh to an empty pid table: our pid now resolves to the
            # operator via trusted uid → a container-A wire is forged.
            loop.set_policy(pids={})
            req = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-A",
                payload=b'{"op": "ping"}',
            )
            client.endpoint.send(req.to_wire(), peer_path=srv.endpoint.path)
            self.assertIsNone(
                client.endpoint.receive(timeout=0.5),
                "a pid removed from the table must no longer be answered")

            # Refresh again, registering container-B: the new identity
            # is answered immediately (no loop recreation).
            loop.set_policy(pids={os.getpid(): "container-B"})
            req = IPCMessage(
                message_type=IPCMessageType.CALL,
                sender_id="container-B",
                payload=b'{"op": "ping"}',
            )
            client.endpoint.send(req.to_wire(), peer_path=srv.endpoint.path)
            got = client.endpoint.receive(timeout=2.0)
            self.assertIsNotNone(got, "container-B must be answered after the refresh")
            self.assertIn(b'"container": "container-B"', got[0])
        finally:
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            srv.close()
            client.close()



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
    suite.addTests(loader.loadTestsFromTestCase(TestBackendStatusService))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerCapabilityLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestStatusServiceHost))
    suite.addTests(loader.loadTestsFromTestCase(TestOperatorCli))
    suite.addTests(loader.loadTestsFromTestCase(TestServiceRouter))
    suite.addTests(loader.loadTestsFromTestCase(TestControlService))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageGuarantees))
    suite.addTests(loader.loadTestsFromTestCase(TestBootLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestSystemdUnit))
    suite.addTests(loader.loadTestsFromTestCase(TestDaemonState))
    suite.addTests(loader.loadTestsFromTestCase(TestLoggingConfig))
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
    suite.addTests(loader.loadTestsFromTestCase(TestPid1Init))
    suite.addTests(loader.loadTestsFromTestCase(TestRustLauncherLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestLauncherInitRust))
    suite.addTests(loader.loadTestsFromTestCase(TestRustSyscallsConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSCodecLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestNyFSCodecConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCWireLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestIPCCodecConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestTransportRustLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestTransportConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestRustIpcdLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestIpcdLoopConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerIpcRegistry))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesConformance))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
