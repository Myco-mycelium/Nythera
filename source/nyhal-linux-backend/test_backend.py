#!/usr/bin/env python3
"""
Test Suite for the Nythera Linux Backend

Tests the implementation of NPS-017 §4 (Backend Requirements).
Covers container primitives, capability enforcement, IPC, storage, and boot.

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
"""

import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add source directory to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.container import (
    Container, ContainerManager, ContainerConfig, ContainerState, ResourceLimits
)
from backend.capability import (
    CapabilityManager, Capability, CapabilityGrant
)
from ipc.core import (
    IPCManager, IPCMessage, IPCMessageType, IPCEndpoint, TokenBucket
)
from fuse.nyfs import NyFSFilesystem, NyFSBlock
from boot.lifecycle import BootSequence, BootPhase


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
    suite.addTests(loader.loadTestsFromTestCase(TestConformance))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
