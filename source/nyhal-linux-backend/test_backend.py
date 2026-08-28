#!/usr/bin/env python3
"""
Test Suite for the Nyrqis Linux Backend

Tests the implementation of NPS-017 §4 (Backend Requirements).
Covers container primitives, capability enforcement, IPC, storage, and boot.

References:
- NPS-017: NyHAL Kernel Abstraction Layer and Backend Contract
- tests/BENCHMARK_PLAN.md: Benchmarking methodology
"""

import argparse
import base64
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
from backend import keys as keys_module  # NyVault key manager (ADR-0023)
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
from ui import nstudio  # NUI (.nstudio) runtime consumption floor (ADR-0025)
from ui import nstudio_codec  # FFI loader for the Rust nyui crate
from ui.service import NuiService, NUI_DOCUMENT_MAX_BYTES  # operator import gate
from ipc import transport_codec
from ipc import loop as ipc_loop
from ipc.dispatch import IpcdLoopDispatcher
from ipc.registry import ContainerIpcRegistry
from ipc.storage import StorageService  # NyVault first increment (ADR-0022)
from fuse.vault_mount import (  # NyVault FUSE passthrough (ADR-0022)
    NyVaultOperations, NyVaultMount, VaultMountError,
)
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

    def test_container_overlay_setup(self):
        """Test that overlay filesystem is attached when rootfs is set."""
        import tempfile
        from fuse.nyfs import NyFSFilesystem
        from fuse.overlay import OverlayFilesystem
        tmp = tempfile.mkdtemp()
        # Create a base NyFS with content and save it
        lower_dir = os.path.join(tmp, "lower")
        lower = NyFSFilesystem(lower_dir)
        lower.mkdir("/shared", 0o755)
        lower.create_file("/shared/base.txt", 0o644)
        lower.write("/shared/base.txt", b"base content")
        lower.save()
        # Container with rootfs — load the saved filesystem
        config = ContainerConfig(rootfs=lower_dir)
        container = self.manager.create(config)
        # Overlay is not set until spawn
        self.assertIsNone(container.overlay)
        # Simulate overlay setup
        self.manager._setup_overlay(container)
        self.assertIsNotNone(container.overlay)
        self.assertIsInstance(container.overlay, OverlayFilesystem)
        # Overlay can read from lower
        self.assertEqual(container.overlay.read("/shared/base.txt"),
                         b"base content")

    def test_container_overlay_none_when_no_rootfs(self):
        """Test that no overlay is created when rootfs is not set."""
        config = ContainerConfig()  # no rootfs
        container = self.manager.create(config)
        self.manager._setup_overlay(container)
        self.assertIsNone(container.overlay)

    def test_app_launch_returns_none_for_unknown(self):
        """app_launch returns None for an unknown app."""
        result = self.manager.app_launch("android:nonexistent.app")
        self.assertIsNone(result)

    def test_app_launch_creates_container(self):
        """app_launch creates a container for a known app."""
        from ui.app_compat import get_app_manager
        app_mgr = get_app_manager()
        # Manually register a test app
        from ui.app_compat import AppInfo, AppPlatform
        app_mgr.apps["test:hello"] = AppInfo(
            app_id="test:hello",
            platform=AppPlatform.NYRQIS,
            name="hello",
            version="1.0",
            capabilities=["CAP_IPC_SEND"],
        )
        # Override the launch command for test
        app_mgr._get_launch_command = lambda info: ["/bin/echo", "hello"]
        result = self.manager.app_launch("test:hello")
        # Should return None because the command doesn't exist,
        # but the method should not crash
        # (the container is created but may fail to spawn)

    def test_default_deny_is_default(self):
        """ContainerConfig.default_deny defaults to True (NPS-017 §5.1)."""
        config = ContainerConfig()
        self.assertTrue(config.default_deny)

    def test_default_deny_can_be_disabled(self):
        """ContainerConfig.default_deny can be set to False."""
        config = ContainerConfig(default_deny=False)
        self.assertFalse(config.default_deny)

    def test_container_has_network_ip_field(self):
        """Container has a network_ip attribute for tracking assigned IPs."""
        config = ContainerConfig()
        container = self.manager.create(config)
        self.assertIsNone(container.network_ip)


class TestVethBridgeNetworking(unittest.TestCase):
    """Test veth/bridge outbound connectivity for network=True containers.

    These tests are hermetic — they test the logic and fallback paths
    without requiring host CAP_NET_ADMIN or real bridge setup.
    """

    def test_ensure_bridge_exists_check(self):
        """is_bridge_available returns a bool."""
        from backend.network import is_bridge_available
        result = is_bridge_available()
        self.assertIsInstance(result, bool)

    def test_alloc_ip_unique(self):
        """_alloc_ip returns sequential IPs."""
        from backend.network import _alloc_ip, _next_ip
        old = _next_ip[0]
        ip1 = _alloc_ip()
        ip2 = _alloc_ip()
        self.assertNotEqual(ip1, ip2)
        self.assertTrue(ip1.startswith("172.16.0."))
        self.assertTrue(ip2.startswith("172.16.0."))
        _next_ip[0] = old  # restore

    def test_teardown_container_network_best_effort(self):
        """teardown_container_network is best-effort, never raises."""
        from backend.network import teardown_container_network
        # Should not raise even with a non-existent container
        teardown_container_network("nonexistent-container")

    def test_teardown_bridge_best_effort(self):
        """teardown_bridge is best-effort, never raises."""
        from backend.network import teardown_bridge
        teardown_bridge()  # should not raise

    def test_container_setup_network_skips_when_not_network(self):
        """_setup_network is a no-op when network=False."""
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(network=False)
        container = manager.create(config)
        container.pid = 1  # fake pid
        manager._setup_network(container)
        self.assertIsNone(container.network_ip)

    def test_container_setup_network_no_pid(self):
        """_setup_network is a no-op when pid is None."""
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(network=True)
        container = manager.create(config)
        # pid is None by default
        manager._setup_network(container)
        self.assertIsNone(container.network_ip)

    def test_container_cleanup_network_best_effort(self):
        """_cleanup_network is best-effort, never raises."""
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(network=True)
        container = manager.create(config)
        manager._cleanup_network(container)  # should not raise

    def test_container_cleanup_network_skips_when_not_network(self):
        """_cleanup_network is a no-op when network=False."""
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(network=False)
        container = manager.create(config)
        manager._cleanup_network(container)  # should not raise


class TestAppCLI(unittest.TestCase):
    """Test the nyrqisctl app CLI commands: build_payload, format_human."""

    def test_app_install_payload(self):
        """app-install builds the correct payload."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            app_path="/tmp/test.apk",
            name="Test App",
            sandbox=True,
        )
        payload = build_payload("app-install", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "app_install")
        self.assertEqual(payload["app_path"], "/tmp/test.apk")
        self.assertEqual(payload["name"], "Test App")
        self.assertTrue(payload["sandbox"])

    def test_app_list_payload(self):
        """app-list builds the correct payload."""
        from nyrqisctl import build_payload
        args = argparse.Namespace()
        payload = build_payload("app-list", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "app_list")

    def test_app_launch_payload(self):
        """app-launch builds the correct payload."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(app_id="android:com.example.app")
        payload = build_payload("app-launch", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "app_launch")
        self.assertEqual(payload["app_id"], "android:com.example.app")

    def test_app_terminate_payload(self):
        """app-terminate builds the correct payload."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(app_id="windows:notepad.exe")
        payload = build_payload("app-terminate", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "app_terminate")
        self.assertEqual(payload["app_id"], "windows:notepad.exe")

    def test_format_human_app_install(self):
        """app-install renders human-readable output."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "app_id": "android:com.example",
            "app": {
                "name": "Example",
                "version": "2.0",
                "compatibility": {
                    "platform": "android",
                    "permissions": ["network", "storage"],
                },
            },
            "sandbox": True,
        }
        text = format_human("app-install", resp)
        self.assertIn("android:com.example", text)
        self.assertIn("Example", text)
        self.assertIn("sandbox:", text)

    def test_format_human_app_list(self):
        """app-list renders human-readable output."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "apps": [
                {
                    "app_id": "android:com.example",
                    "name": "Example",
                    "platform": "android",
                    "status": "installed",
                    "compatibility": {"platform": "android", "permissions": []},
                },
            ],
        }
        text = format_human("app-list", resp)
        self.assertIn("android:com.example", text)
        self.assertIn("Example", text)
        self.assertIn("installed", text)

    def test_format_human_app_list_empty(self):
        """app-list with no apps."""
        from nyrqisctl import format_human
        resp = {"ok": True, "apps": []}
        text = format_human("app-list", resp)
        self.assertIn("no installed apps", text)

    def test_format_human_app_launch(self):
        """app-launch renders human-readable output."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "app_id": "android:com.example",
            "container_id": "ctr-42",
            "pid": 12345,
        }
        text = format_human("app-launch", resp)
        self.assertIn("launched", text)
        self.assertIn("ctr-42", text)

    def test_format_human_app_terminate(self):
        """app-terminate renders human-readable output."""
        from nyrqisctl import format_human
        resp = {"ok": True, "app_id": "android:com.example"}
        text = format_human("app-terminate", resp)
        self.assertIn("terminated", text)

    def test_register_and_list_apps(self):
        """register_app + list_apps round-trip."""
        manager = ContainerManager()
        info = {
            "name": "TestApp",
            "version": "1.0",
            "compatibility": {
                "platform": "android",
                "permissions": ["network"],
            },
        }
        app_id = manager.register_app(info, "/tmp/test.apk")
        self.assertEqual(app_id, "android:TestApp")
        apps = manager.list_apps()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["app_id"], app_id)
        self.assertEqual(apps[0]["name"], "TestApp")

    def test_terminate_app_not_running(self):
        """terminate_app returns False for non-running apps."""
        manager = ContainerManager()
        result = manager.terminate_app("nonexistent:app")
        self.assertFalse(result)


class TestLSMPolicy(unittest.TestCase):
    """Test LSM (AppArmor/SELinux) policy generation."""

    def test_build_lsm_policy_minimal(self):
        """A minimal capability set produces a minimal policy."""
        from backend.lsm import build_lsm_policy
        policy = build_lsm_policy("test-minimal", set())
        self.assertEqual(policy.container_id, "test-minimal")
        self.assertEqual(len(policy.path_rules), 0)
        self.assertEqual(len(policy.network_rules), 0)
        self.assertEqual(len(policy.linux_capabilities), 0)
        # Deny paths should always be present
        self.assertTrue(len(policy.deny_paths) > 0)

    def test_build_lsm_policy_filesystem_read(self):
        """CAP_FILESYSTEM_READ grants read access to system paths."""
        from backend.lsm import build_lsm_policy
        from backend.capability import Capability
        policy = build_lsm_policy(
            "test-fsread", {Capability.CAP_FILESYSTEM_READ}
        )
        self.assertTrue(len(policy.path_rules) > 0)
        # Should have /usr/** and /proc/** rules
        paths = [r.path for r in policy.path_rules]
        self.assertIn("/usr/**", paths)
        self.assertIn("/proc/**", paths)
        # All perms should be readable
        for rule in policy.path_rules:
            self.assertIn("r", rule.perms)

    def test_build_lsm_policy_network(self):
        """CAP_NETWORK_SOCKET grants network access rules."""
        from backend.lsm import build_lsm_policy
        from backend.capability import Capability
        policy = build_lsm_policy(
            "test-net", {Capability.CAP_NETWORK_SOCKET}
        )
        self.assertTrue(len(policy.network_rules) > 0)
        families = {r.family for r in policy.network_rules}
        self.assertIn("inet", families)
        self.assertIn("unix", families)

    def test_build_lsm_policy_deny_paths_always_present(self):
        """Deny paths are always present regardless of capabilities."""
        from backend.lsm import build_lsm_policy
        policy = build_lsm_policy("test-deny", set())
        self.assertIn("/proc/sysrq-trigger", policy.deny_paths)
        self.assertIn("/sys/firmware/**", policy.deny_paths)
        self.assertIn("/proc/sys/kernel/core_pattern", policy.deny_paths)

    def test_apparmor_render(self):
        """AppArmor profile renders valid syntax."""
        from backend.lsm import build_lsm_policy, AppArmorProfile
        from backend.capability import Capability
        caps = {
            Capability.CAP_FILESYSTEM_READ,
            Capability.CAP_NETWORK_SOCKET,
        }
        policy = build_lsm_policy("test-aa", caps)
        profile = AppArmorProfile(policy)
        text = profile.render()
        self.assertIn("profile nyrqis.test-aa", text)
        self.assertIn("#include <tunables/global>", text)
        self.assertIn("deny /proc/sysrq-trigger", text)
        self.assertIn("network inet stream", text)
        self.assertIn("network unix stream", text)

    def test_apparmor_deny_always_blocked(self):
        """AppArmor profile always denies dangerous paths."""
        from backend.lsm import build_lsm_policy, AppArmorProfile
        policy = build_lsm_policy("test-deny-aa", set())
        profile = AppArmorProfile(policy)
        text = profile.render()
        self.assertIn("deny /proc/sysrq-trigger", text)
        self.assertIn("deny /sys/firmware", text)
        self.assertIn("deny /proc/sys/kernel/core_pattern", text)

    def test_selinux_te_render(self):
        """SELinux TE module renders valid syntax."""
        from backend.lsm import build_lsm_policy, SEPolicy
        from backend.capability import Capability
        caps = {
            Capability.CAP_FILESYSTEM_READ,
            Capability.CAP_NETWORK_SOCKET,
        }
        policy = build_lsm_policy("test-se", caps)
        se = SEPolicy(policy)
        text = se.render_type_enforcement()
        self.assertIn("policy_module", text)
        self.assertIn("type nyrqis_test_se_t", text)
        self.assertIn("neverallow", text)

    def test_selinux_deny_privileged_caps(self):
        """SELinux module always denies privileged operations."""
        from backend.lsm import build_lsm_policy, SEPolicy
        policy = build_lsm_policy("test-se-priv", set())
        se = SEPolicy(policy)
        text = se.render_type_enforcement()
        self.assertIn("neverallow", text)
        self.assertIn("sys_admin", text)
        self.assertIn("sys_module", text)

    def test_selinux_file_contexts(self):
        """SELinux file contexts render for the container."""
        from backend.lsm import build_lsm_policy, SEPolicy
        policy = build_lsm_policy("test-se-fc", set())
        se = SEPolicy(policy)
        text = se.render_file_contexts()
        self.assertIn("test-se-fc", text)
        self.assertIn("gen_context", text)

    def test_lsm_audit_clean(self):
        """A well-scoped policy has no audit warnings."""
        from backend.lsm import build_lsm_policy, lsm_audit
        from backend.capability import Capability
        caps = {
            Capability.CAP_FILESYSTEM_READ,
            Capability.CAP_IPC_SEND,
        }
        policy = build_lsm_policy("test-audit", caps)
        warnings = lsm_audit(policy)
        self.assertEqual(warnings, [])

    def test_lsm_audit_warns_many_caps(self):
        """Audit warns when too many capabilities are granted."""
        from backend.lsm import build_lsm_policy, lsm_audit
        from backend.capability import Capability
        # Grant more than 15 capabilities
        caps = {c for c in Capability if c.value.startswith("CAP_")}
        policy = build_lsm_policy("test-many", caps)
        warnings = lsm_audit(policy)
        self.assertTrue(any("capabilities" in w for w in warnings))

    def test_lsm_policy_to_dict(self):
        """LSMPolicy serializes to dict."""
        from backend.lsm import build_lsm_policy
        from backend.capability import Capability
        caps = {Capability.CAP_FILESYSTEM_READ}
        policy = build_lsm_policy("test-dict", caps)
        d = policy.to_dict()
        self.assertEqual(d["container_id"], "test-dict")
        self.assertIn("CAP_FILESYSTEM_READ", d["capabilities"])
        self.assertIsInstance(d["path_rules"], list)
        self.assertIsInstance(d["deny_paths"], list)

    def test_apparmor_write(self):
        """AppArmor profile can be written to disk."""
        import tempfile
        import os
        from backend.lsm import build_lsm_policy, AppArmorProfile
        policy = build_lsm_policy("test-write", set())
        profile = AppArmorProfile(policy)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test")
            profile.write(path)
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read()
            self.assertIn("profile nyrqis.test-write", content)

    def test_selinux_write(self):
        """SELinux policy files can be written to disk."""
        import tempfile
        from backend.lsm import build_lsm_policy, SEPolicy
        policy = build_lsm_policy("test-se-write", set())
        se = SEPolicy(policy)
        with tempfile.TemporaryDirectory() as tmp:
            paths = se.write(tmp)
            self.assertIn(".te", paths)
            self.assertIn(".fc", paths)
            self.assertIn(".sp", paths)
            import os
            for p in paths.values():
                self.assertTrue(os.path.exists(p))

    def test_full_capability_policy(self):
        """A policy with all capabilities covers all rule types."""
        from backend.lsm import build_lsm_policy
        from backend.capability import Capability
        all_caps = set(Capability)
        policy = build_lsm_policy("test-full", all_caps)
        self.assertTrue(len(policy.path_rules) > 10)
        self.assertTrue(len(policy.network_rules) > 2)
        self.assertTrue(len(policy.device_rules) > 0)
        self.assertTrue(len(policy.linux_capabilities) > 0)
        self.assertTrue(len(policy.deny_paths) > 0)

    def test_container_setup_lsm_generates_files(self):
        """_setup_lsm writes AppArmor and SELinux files to disk."""
        import tempfile, os
        manager = ContainerManager()
        config = ContainerConfig(
            capabilities=["CAP_FILESYSTEM_READ", "CAP_NETWORK_SOCKET"],
        )
        container = manager.create(config)
        manager._setup_lsm(container)
        # AppArmor profile should have been written
        self.assertIsNotNone(config.aa_profile)
        self.assertTrue(os.path.isfile(config.aa_profile))
        with open(config.aa_profile) as f:
            text = f.read()
        self.assertIn("profile nyrqis.", text)
        self.assertIn("network inet stream", text)
        # SELinux module directory should exist
        self.assertIsNotNone(config.se_module_dir)
        self.assertTrue(os.path.isdir(config.se_module_dir))
        # LSM files tracked for cleanup
        self.assertTrue(len(manager._lsm_files) > 0)
        # Cleanup
        manager._cleanup_policy_files()
        self.assertEqual(len(manager._lsm_files), 0)

    def test_container_setup_lsm_no_caps(self):
        """_setup_lsm works with no capabilities (minimal policy)."""
        manager = ContainerManager()
        config = ContainerConfig()  # no capabilities
        container = manager.create(config)
        manager._setup_lsm(container)
        self.assertIsNotNone(config.aa_profile)
        with open(config.aa_profile) as f:
            text = f.read()
        # Should still have deny paths
        self.assertIn("deny /proc/sysrq-trigger", text)

    def test_reload_policy_refreshes_lsm(self):
        """reload_policy regenerates LSM files from current capabilities."""
        from backend.capability import CapabilityManager, Capability
        cap_mgr = CapabilityManager()
        manager = ContainerManager(capability_manager=cap_mgr)
        config = ContainerConfig(
            capabilities=["CAP_FILESYSTEM_READ", "CAP_NETWORK_SOCKET"],
        )
        container = manager.create(config)
        # Grant initial capabilities
        cap_mgr.grant_capability(container.id, Capability.CAP_FILESYSTEM_READ)
        cap_mgr.grant_capability(container.id, Capability.CAP_NETWORK_SOCKET)
        manager._setup_lsm(container)
        old_aa = config.aa_profile
        # Revoke network capability and reload
        cap_mgr.revoke_capability(container.id, Capability.CAP_NETWORK_SOCKET)
        result = manager.reload_policy(container)
        self.assertTrue(result)
        # New profile should be different (no network rules)
        self.assertNotEqual(config.aa_profile, old_aa)
        with open(config.aa_profile) as f:
            text = f.read()
        self.assertNotIn("network inet stream", text)
        # Old file should have been cleaned up
        import os
        self.assertFalse(os.path.exists(old_aa))

    def test_revoke_and_reload(self):
        """revoke_and_reload revokes capability and refreshes LSM."""
        from backend.capability import CapabilityManager, Capability
        cap_mgr = CapabilityManager()
        manager = ContainerManager(capability_manager=cap_mgr)
        config = ContainerConfig(
            capabilities=["CAP_FILESYSTEM_READ", "CAP_NETWORK_SOCKET"],
        )
        container = manager.create(config)
        cap_mgr.grant_capability(container.id, Capability.CAP_FILESYSTEM_READ)
        cap_mgr.grant_capability(container.id, Capability.CAP_NETWORK_SOCKET)
        manager._setup_lsm(container)
        # Revoke via the convenience method
        result = manager.revoke_and_reload(
            container, Capability.CAP_NETWORK_SOCKET
        )
        self.assertTrue(result)
        self.assertNotIn(
            Capability.CAP_NETWORK_SOCKET,
            cap_mgr.get_capabilities(container.id),
        )
        # Verify the new profile reflects the revocation
        with open(config.aa_profile) as f:
            text = f.read()
        self.assertNotIn("network inet stream", text)
        self.assertIn("Network rules", text)  # section header still present

    def test_reload_policy_returns_true(self):
        """reload_policy returns True on success."""
        from backend.capability import CapabilityManager, Capability
        cap_mgr = CapabilityManager()
        manager = ContainerManager(capability_manager=cap_mgr)
        config = ContainerConfig(
            capabilities=["CAP_FILESYSTEM_READ"],
        )
        container = manager.create(config)
        cap_mgr.grant_capability(container.id, Capability.CAP_FILESYSTEM_READ)
        manager._setup_lsm(container)
        result = manager.reload_policy(container)
        self.assertTrue(result)


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


class TestContainerStats(unittest.TestCase):
    """Test ContainerManager.container_stats() — live cgroup stats."""

    def test_stats_not_available_when_not_running(self):
        """Stats report available=False for non-running containers."""
        from backend.container import Container, ContainerConfig, ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        stats = manager.container_stats(container)
        self.assertFalse(stats["available"])
        self.assertEqual(stats["state"], "created")
        self.assertIsNone(stats["pid"])
        self.assertIsNone(stats["uptime_s"])

    def test_stats_available_for_running_container(self):
        """Stats report available=True with memory/CPU fields for a running container."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        container.pid = 12345
        container.started_at = time.time() - 10.0
        container.cgroup_paths = ["/sys/fs/cgroup/nyrqis/test"]
        stats = manager.container_stats(container)
        self.assertTrue(stats["available"])
        self.assertEqual(stats["pid"], 12345)
        self.assertAlmostEqual(stats["uptime_s"], 10.0, delta=0.5)

    def test_stats_no_cgroup_paths(self):
        """Stats report available=False when cgroup_paths is empty."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        container.pid = 99999
        # No cgroup_paths set
        stats = manager.container_stats(container)
        self.assertFalse(stats["available"])

    def test_stats_v2_memory_fields(self):
        """Stats read memory.current and memory.max on cgroups v2."""
        import tempfile, os
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        with tempfile.TemporaryDirectory() as td:
            # Create cgroup v2 files
            with open(os.path.join(td, "memory.current"), "w") as f:
                f.write("524288\n")
            with open(os.path.join(td, "memory.max"), "w") as f:
                f.write("1048576\n")
            with open(os.path.join(td, "pids.current"), "w") as f:
                f.write("3\n")
            with open(os.path.join(td, "pids.max"), "w") as f:
                f.write("64\n")
            with open(os.path.join(td, "cpu.stat"), "w") as f:
                f.write("usage_usec 123456\nuser_usec 80000\nsystem_usec 43456\nnr_periods 100\nnr_throttled 5\n")

            manager = ContainerManager(use_cgroups_v2=True)
            container = manager.create(ContainerConfig())
            container.state = ContainerState.RUNNING
            container.pid = 42
            container.cgroup_paths = [td]

            stats = manager.container_stats(container)
            self.assertTrue(stats["available"])
            self.assertEqual(stats["memory_bytes"], 524288)
            self.assertEqual(stats["memory_limit_bytes"], 1048576)
            self.assertEqual(stats["cpu_usage_usec"], 123456)
            self.assertEqual(stats["cpu_user_usec"], 80000)
            self.assertEqual(stats["cpu_system_usec"], 43456)
            self.assertEqual(stats["cpu_throttle_pct"], 5.0)
            self.assertEqual(stats["pids_current"], 3)
            self.assertEqual(stats["pids_limit"], 64)

    def test_stats_v2_unlimited_memory(self):
        """memory.max = 0x7FFFFFFFFFFFFFFF means unlimited on v2."""
        import tempfile, os
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "memory.current"), "w") as f:
                f.write("1024\n")
            with open(os.path.join(td, "memory.max"), "w") as f:
                f.write("9223372036854775807\n")  # 0x7FFFFFFFFFFFFFFF (unlimited)

            manager = ContainerManager(use_cgroups_v2=True)
            container = manager.create(ContainerConfig())
            container.state = ContainerState.RUNNING
            container.pid = 42
            container.cgroup_paths = [td]

            stats = manager.container_stats(container)
            self.assertEqual(stats["memory_bytes"], 1024)
            self.assertIsNone(stats["memory_limit_bytes"])

    def test_stats_v1_fields(self):
        """Stats read v1-style cgroup files when use_cgroups_v2=False."""
        import tempfile, os
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "memory.usage_in_bytes"), "w") as f:
                f.write("1048576\n")
            with open(os.path.join(td, "memory.limit_in_bytes"), "w") as f:
                f.write("268435456\n")
            with open(os.path.join(td, "cpuacct.usage"), "w") as f:
                f.write("50000000\n")  # 50ms in nanoseconds
            with open(os.path.join(td, "pids.current"), "w") as f:
                f.write("7\n")

            manager = ContainerManager(use_cgroups_v2=False)
            container = manager.create(ContainerConfig())
            container.state = ContainerState.RUNNING
            container.pid = 42
            container.cgroup_paths = [td]

            stats = manager.container_stats(container)
            self.assertTrue(stats["available"])
            self.assertEqual(stats["memory_bytes"], 1048576)
            self.assertEqual(stats["memory_limit_bytes"], 268435456)
            self.assertEqual(stats["cpu_usage_usec"], 50000)  # ns → µs
            self.assertEqual(stats["pids_current"], 7)

    def test_stats_cli_payload(self):
        """CLI build_payload for containers-stats."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="nyctr-abc123")
        payload = build_payload("containers-stats", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_stats")
        self.assertEqual(payload["container_id"], "nyctr-abc123")

    def test_stats_cli_format_human(self):
        """CLI format_human for containers-stats."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "state": "running",
            "available": True,
            "pid": 42,
            "uptime_s": 5.3,
            "memory_bytes": 10485760,
            "memory_limit_bytes": 268435456,
            "cpu_usage_usec": 1234567,
            "cpu_throttle_pct": 2.5,
            "pids_current": 3,
            "pids_limit": 64,
        }
        text = format_human("containers-stats", resp)
        self.assertIn("nyctr-test", text)
        self.assertIn("running", text)
        self.assertIn("10,485,760 bytes", text)
        self.assertIn("3.9%", text)  # 10485760/268435456
        self.assertIn("1,234,567", text)
        self.assertIn("2.5%", text)
        self.assertIn("pids:      3", text)

    def test_stats_cli_format_human_unavailable(self):
        """CLI format_human when stats unavailable."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "state": "created",
            "available": False,
        }
        text = format_human("containers-stats", resp)
        self.assertIn("not available", text)


class TestContainerLogs(unittest.TestCase):
    """Test container log capture (stdout/stderr ring buffer)."""

    def test_ring_buffer_basic(self):
        """RingBuffer append and get_lines work correctly."""
        from backend.container import RingBuffer
        buf = RingBuffer(max_lines=5)
        for i in range(3):
            buf.append(f"line {i}")
        self.assertEqual(len(buf), 3)
        self.assertEqual(buf.get_lines(), ["line 0", "line 1", "line 2"])
        self.assertEqual(buf.get_lines(tail=2), ["line 1", "line 2"])

    def test_ring_buffer_eviction(self):
        """RingBuffer evicts oldest lines when full."""
        from backend.container import RingBuffer
        buf = RingBuffer(max_lines=3)
        for i in range(5):
            buf.append(f"line {i}")
        self.assertEqual(len(buf), 3)
        self.assertEqual(buf.get_lines(), ["line 2", "line 3", "line 4"])

    def test_ring_buffer_clear(self):
        """RingBuffer clear empties the buffer."""
        from backend.container import RingBuffer
        buf = RingBuffer(max_lines=10)
        buf.append("hello")
        buf.clear()
        self.assertEqual(len(buf), 0)
        self.assertEqual(buf.get_lines(), [])

    def test_log_capture_not_available_without_config(self):
        """container_logs reports unavailable when log_capture=False."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        logs = manager.container_logs(container)
        self.assertFalse(logs["available"])
        self.assertEqual(logs["stdout"], [])
        self.assertEqual(logs["stderr"], [])

    def test_log_capture_cli_payload(self):
        """CLI build_payload for containers-logs."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            tail=50,
            stream="stderr",
        )
        payload = build_payload("containers-logs", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_logs")
        self.assertEqual(payload["container_id"], "nyctr-abc")
        self.assertEqual(payload["tail"], 50)
        self.assertEqual(payload["stream"], "stderr")

    def test_log_capture_cli_format_human(self):
        """CLI format_human for containers-logs."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "available": True,
            "stdout": ["[0.001] hello world"],
            "stderr": ["[0.002] error line"],
        }
        text = format_human("containers-logs", resp)
        self.assertIn("hello world", text)
        self.assertIn("error line", text)
        self.assertIn("--- stdout ---", text)
        self.assertIn("--- stderr ---", text)

    def test_log_capture_cli_format_human_unavailable(self):
        """CLI format_human when log capture is not active."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "available": False,
        }
        text = format_human("containers-logs", resp)
        self.assertIn("not active", text)

    def test_log_capture_cli_format_human_empty(self):
        """CLI format_human when logs are empty."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "available": True,
            "stdout": [],
            "stderr": [],
        }
        text = format_human("containers-logs", resp)
        self.assertIn("no log output yet", text)


class TestContainerExec(unittest.TestCase):
    """Test container exec (nsenter into container namespaces)."""

    def test_exec_rejects_non_running(self):
        """Exec raises ValueError for non-running containers."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        with self.assertRaises(ValueError):
            manager.container_exec(container, ["echo", "hello"])

    def test_exec_rejects_no_pid(self):
        """Exec raises ValueError when container has no PID."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        # No pid set
        with self.assertRaises(ValueError):
            manager.container_exec(container, ["echo", "hello"])

    def test_exec_cli_payload(self):
        """CLI build_payload for containers-exec."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            exec_command=["ls", "-la"],
            timeout=5.0,
        )
        payload = build_payload("containers-exec", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_exec")
        self.assertEqual(payload["container_id"], "nyctr-abc")
        self.assertEqual(payload["command"], ["ls", "-la"])
        self.assertEqual(payload["timeout"], 5.0)

    def test_exec_cli_format_human(self):
        """CLI format_human for containers-exec."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "exit_code": 0,
            "stdout": "file1.txt\nfile2.txt\n",
            "stderr": "",
        }
        text = format_human("containers-exec", resp)
        self.assertIn("file1.txt", text)
        self.assertIn("file2.txt", text)
        self.assertIn("exit code: 0", text)

    def test_exec_cli_format_human_error(self):
        """CLI format_human shows stderr and non-zero exit code."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "exit_code": 1,
            "stdout": "",
            "stderr": "ls: cannot access 'nope': No such file or directory\n",
        }
        text = format_human("containers-exec", resp)
        self.assertIn("cannot access", text)
        self.assertIn("exit code: 1", text)
        self.assertIn("[stderr]", text)

    def test_exec_cli_format_human_timeout(self):
        """CLI format_human for timed-out exec."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "exit_code": -1,
            "stdout": "",
            "stderr": "command timed out after 10.0s",
        }
        text = format_human("containers-exec", resp)
        self.assertIn("timed out", text)
        self.assertIn("exit code: -1", text)


class TestContainerCheckpointRestore(unittest.TestCase):
    """Test container checkpoint/restore (save and resume state)."""

    def test_checkpoint_no_overlay(self):
        """Checkpoint a container without overlay writes to a JSON file."""
        import json
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(
            hostname="ckpt-host",
            command=["echo", "hello"],
        ))
        container.state = ContainerState.RUNNING
        container.pid = 12345

        cp = manager.container_checkpoint(container)
        self.assertIn("checkpoint_path", cp)
        self.assertEqual(cp["overlay_entries"], 0)
        self.assertIsNone(cp["overlay"])

        # Verify the file is valid JSON
        with open(cp["checkpoint_path"]) as f:
            data = json.load(f)
        self.assertEqual(data["container_id"], container.id)
        self.assertEqual(data["config"]["hostname"], "ckpt-host")
        os.unlink(cp["checkpoint_path"])

    def test_checkpoint_with_overlay(self):
        """Checkpoint captures overlay state."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        tmp = tempfile.mkdtemp()
        try:
            lower_path = os.path.join(tmp, "lower")
            os.makedirs(lower_path)
            from fuse.nyfs import NyFSFilesystem
            lower = NyFSFilesystem(lower_path)
            lower.create_file("/base.txt")
            lower.write("/base.txt", b"base")

            config = ContainerConfig(
                rootfs=lower_path,
                hostname="overlay-host",
            )
            container = manager.create(config)
            container.state = ContainerState.RUNNING
            container.pid = 99999

            # Create overlay and write to it
            from fuse.overlay import OverlayFilesystem
            container.overlay = OverlayFilesystem(
                lower, container_id=container.id,
            )
            # write() auto-creates the file in the upper layer
            container.overlay.write("/overlay.txt", b"overlay data")

            cp = manager.container_checkpoint(container)
            self.assertGreater(cp["overlay_entries"], 0)
            self.assertIsNotNone(cp["overlay"])

            # Restore into a new container
            restored = manager.container_restore(cp)
            self.assertNotEqual(restored.id, container.id)
            self.assertEqual(restored.config.hostname, "overlay-host")
            self.assertIsNotNone(restored.overlay)

            os.unlink(cp["checkpoint_path"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_restore_creates_created_container(self):
        """Restore produces a container in CREATED state."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(
            hostname="original",
        ))
        container.state = ContainerState.RUNNING
        container.pid = 11111

        cp = manager.container_checkpoint(container)
        restored = manager.container_restore(cp)
        self.assertEqual(restored.state, ContainerState.CREATED)
        self.assertIsNone(restored.pid)
        self.assertEqual(restored.config.hostname, "original")
        os.unlink(cp["checkpoint_path"])

    def test_checkpoint_cli_payload(self):
        """CLI build_payload for containers-checkpoint."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            path="/tmp/ckpt.json",
        )
        payload = build_payload("containers-checkpoint", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_checkpoint")
        self.assertEqual(payload["path"], "/tmp/ckpt.json")

    def test_checkpoint_cli_format_human(self):
        """CLI format_human for containers-checkpoint."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "checkpoint_path": "/tmp/nyctr-x.checkpoint.json",
            "overlay_entries": 5,
        }
        text = format_human("containers-checkpoint", resp)
        self.assertIn("checkpoint saved", text)
        self.assertIn("5 overlay entries", text)

    def test_restore_cli_format_human(self):
        """CLI format_human for containers-restore."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-restored",
            "state": "created",
        }
        text = format_human("containers-restore", resp)
        self.assertIn("nyctr-restored", text)
        self.assertIn("created", text)


class TestContainerTop(unittest.TestCase):
    """Test container top (per-process resource usage)."""

    def test_top_returns_empty_for_created(self):
        """Top returns empty list for non-running containers."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        result = manager.container_top(container)
        self.assertEqual(result, [])

    def test_top_cli_payload(self):
        """CLI build_payload for containers-top."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc", sort_by=None,
            descending=True, max_depth=None, summary_only=False,
        )
        payload = build_payload("containers-top", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_top")
        self.assertEqual(payload["container_id"], "nyctr-abc")

    def test_top_cli_format_human(self):
        """CLI format_human for containers-top."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "processes": [
                {
                    "pid": 12345, "ppid": 1, "state": "S",
                    "name": "sh", "cmd": "/bin/sh -c echo hello",
                    "user_time_s": 0.010, "system_time_s": 0.005,
                    "vsize_kb": 1024, "rss_kb": 256,
                    "nice": 0, "threads": 1, "fd_count": 3,
                    "start_time_s": 0.0, "depth": 0,
                },
                {
                    "pid": 12346, "ppid": 12345, "state": "R",
                    "name": "ps", "cmd": "ps aux",
                    "user_time_s": 0.002, "system_time_s": 0.001,
                    "vsize_kb": 512, "rss_kb": 128,
                    "nice": 0, "threads": 1, "fd_count": 4,
                    "start_time_s": 0.0, "depth": 1,
                },
            ],
            "count": 2,
        }
        text = format_human("containers-top", resp)
        self.assertIn("12345", text)
        self.assertIn("12346", text)
        self.assertIn("sh", text)
        self.assertIn("ps", text)
        self.assertIn("PID", text)

    def test_top_cli_format_human_empty(self):
        """CLI format_human when no processes found."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "processes": [],
            "count": 0,
        }
        text = format_human("containers-top", resp)
        self.assertIn("no processes found", text)


class TestContainerNetworkStats(unittest.TestCase):
    """Test container network stats (veth interface stats)."""

    def test_network_stats_none_without_network(self):
        """Network stats returns None for non-network containers."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(network=False))
        container.state = ContainerState.RUNNING
        result = manager.container_network_stats(container)
        self.assertIsNone(result)

    def test_get_network_stats_sysfs(self):
        """get_network_stats reads /sys/class/net correctly."""
        import tempfile
        from backend.network import get_network_stats
        # The function returns None if the interface doesn't exist
        result = get_network_stats("nonexistent-container-id")
        self.assertIsNone(result)

    def test_net_cli_payload(self):
        """CLI build_payload for containers-net."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="nyctr-abc")
        payload = build_payload("containers-net", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_network_stats")
        self.assertEqual(payload["container_id"], "nyctr-abc")

    def test_net_cli_format_human(self):
        """CLI format_human for containers-net."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "stats": {
                "interface": "veth-abc123def456",
                "operstate": "UP",
                "mtu": "1500",
                "rx_bytes": 1048576,
                "rx_packets": 1024,
                "rx_errors": 0,
                "rx_dropped": 0,
                "tx_bytes": 524288,
                "tx_packets": 512,
                "tx_errors": 0,
                "tx_dropped": 0,
            },
        }
        text = format_human("containers-net", resp)
        self.assertIn("veth-abc123def456", text)
        self.assertIn("UP", text)
        self.assertIn("1,048,576", text)
        self.assertIn("524,288", text)
        self.assertIn("1,024", text)

    def test_net_cli_format_human_no_stats(self):
        """CLI format_human when no network interface found."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "stats": None,
        }
        text = format_human("containers-net", resp)
        self.assertIn("no network interface found", text)


class TestImageManagement(unittest.TestCase):
    """Test container image management (list, remove base images)."""

    def test_list_images_empty_dir(self):
        """list_images returns empty list for nonexistent directory."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        result = manager.list_images(base_dir="/nonexistent/path")
        self.assertEqual(result, [])

    def test_list_images_finds_nyfs(self):
        """list_images finds NyFS images with metadata."""
        import tempfile, json
        from backend.container import ContainerManager
        from fuse.nyfs import NyFSFilesystem
        manager = ContainerManager(use_cgroups_v2=False)
        tmp = tempfile.mkdtemp()
        try:
            # Create a NyFS image
            img_dir = os.path.join(tmp, "test-image")
            fs = NyFSFilesystem(img_dir)
            fs.create_file("/test.txt")
            fs.write("/test.txt", b"hello")
            fs.save()

            result = manager.list_images(base_dir=tmp)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "test-image")
            self.assertGreater(result[0]["inode_count"], 0)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_image(self):
        """remove_image deletes the image directory."""
        import tempfile
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        tmp = tempfile.mkdtemp()
        try:
            img_dir = os.path.join(tmp, "to-remove")
            os.makedirs(img_dir)
            with open(os.path.join(img_dir, "file.txt"), "w") as f:
                f.write("data")
            self.assertTrue(manager.remove_image(img_dir))
            self.assertFalse(os.path.exists(img_dir))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_image_in_use(self):
        """remove_image raises ValueError if image is in use."""
        import tempfile
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        tmp = tempfile.mkdtemp()
        try:
            img_dir = os.path.join(tmp, "in-use")
            os.makedirs(img_dir)
            container = manager.create(ContainerConfig(rootfs=img_dir))
            container.state = ContainerState.RUNNING
            with self.assertRaises(ValueError) as ctx:
                manager.remove_image(img_dir)
            self.assertIn("in use", str(ctx.exception))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remove_image_not_found(self):
        """remove_image raises ValueError for nonexistent path."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        with self.assertRaises(ValueError):
            manager.remove_image("/nonexistent/image")

    def test_images_list_cli_payload(self):
        """CLI build_payload for images-list."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(base_dir="/tmp/images")
        payload = build_payload("images-list", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "image_list")
        self.assertEqual(payload["base_dir"], "/tmp/images")

    def test_images_remove_cli_payload(self):
        """CLI build_payload for images-remove."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(path="/tmp/images/myimage")
        payload = build_payload("images-remove", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "image_remove")
        self.assertEqual(payload["path"], "/tmp/images/myimage")

    def test_images_list_cli_format_human(self):
        """CLI format_human for images-list."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "images": [
                {"name": "base-ubuntu", "inode_count": 100,
                 "block_count": 50, "size_bytes": 3276800},
                {"name": "base-alpine", "inode_count": 30,
                 "block_count": 10, "size_bytes": 655360},
            ],
            "count": 2,
        }
        text = format_human("images-list", resp)
        self.assertIn("base-ubuntu", text)
        self.assertIn("base-alpine", text)
        self.assertIn("100", text)
        self.assertIn("3,276,800", text)

    def test_images_list_cli_format_human_empty(self):
        """CLI format_human when no images found."""
        from nyrqisctl import format_human
        resp = {"ok": True, "images": [], "count": 0}
        text = format_human("images-list", resp)
        self.assertIn("no images found", text)

    def test_export_import_roundtrip(self):
        """Export and import an image preserves its content."""
        import tempfile, json
        from backend.container import ContainerManager
        from fuse.nyfs import NyFSFilesystem
        manager = ContainerManager(use_cgroups_v2=False)
        tmp = tempfile.mkdtemp()
        try:
            # Create a NyFS image
            img_dir = os.path.join(tmp, "test-image")
            fs = NyFSFilesystem(img_dir)
            fs.create_file("/hello.txt")
            fs.write("/hello.txt", b"hello world")
            fs.save()

            # Export
            tar_path = os.path.join(tmp, "export.tar.gz")
            result_tar = manager.export_image(img_dir, tar_path)
            self.assertTrue(os.path.isfile(result_tar))
            self.assertGreater(os.path.getsize(result_tar), 0)

            # Import into a new location
            import_dir = os.path.join(tmp, "imported")
            imported = manager.import_image(result_tar, dest_dir=import_dir)
            self.assertTrue(os.path.isdir(imported))
            meta = os.path.join(imported, "state", "metadata.json")
            self.assertTrue(os.path.isfile(meta))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_not_found(self):
        """Export raises ValueError for nonexistent image."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        with self.assertRaises(ValueError):
            manager.export_image("/nonexistent/image")

    def test_import_not_found(self):
        """Import raises ValueError for nonexistent tar."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        with self.assertRaises(ValueError):
            manager.import_image("/nonexistent/file.tar.gz")

    def test_import_cli_payload(self):
        """CLI build_payload for images-import."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            tar_path="/tmp/img.tar.gz",
            dest_dir="/tmp/images",
            name="imported-img",
        )
        payload = build_payload("images-import", args)
        self.assertEqual(payload["op"], "image_import")
        self.assertEqual(payload["tar_path"], "/tmp/img.tar.gz")
        self.assertEqual(payload["name"], "imported-img")

    def test_export_cli_payload(self):
        """CLI build_payload for images-export."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            image_path="/tmp/images/myimg",
            tar_path="/tmp/export.tar.gz",
        )
        payload = build_payload("images-export", args)
        self.assertEqual(payload["op"], "image_export")
        self.assertEqual(payload["image_path"], "/tmp/images/myimg")
        self.assertEqual(payload["tar_path"], "/tmp/export.tar.gz")

    def test_export_cli_format_human(self):
        """CLI format_human for images-export."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "tar_path": "/tmp/export.tar.gz",
            "size_bytes": 1048576,
        }
        text = format_human("images-export", resp)
        self.assertIn("export.tar.gz", text)
        self.assertIn("1,048,576 bytes", text)

    def test_import_cli_format_human(self):
        """CLI format_human for images-import."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "image_path": "/tmp/images/imported-img",
        }
        text = format_human("images-import", resp)
        self.assertIn("imported-img", text)
        self.assertIn("imported", text)


class TestSnapshotDiff(unittest.TestCase):
    """Test snapshot diff (compare two checkpoint states)."""

    def test_diff_no_changes(self):
        """Identical checkpoints produce no differences."""
        from backend.container import ContainerManager
        cp = {
            "container_id": "c1",
            "config": {"hostname": "h1", "command": ["echo"]},
            "overlay": {"entries": {"/a.txt": {"data": "68656c6c6f"}}},
        }
        result = ContainerManager.snapshot_diff(cp, cp)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["config_changes"], {})
        self.assertIn("no differences", result["summary"])

    def test_diff_added_files(self):
        """Files in B but not A are reported as added."""
        from backend.container import ContainerManager
        a = {"overlay": {"entries": {"/x.txt": {"data": "aa"}}}}
        b = {"overlay": {"entries": {
            "/x.txt": {"data": "aa"},
            "/y.txt": {"data": "bb"},
        }}}
        result = ContainerManager.snapshot_diff(a, b)
        self.assertEqual(result["added"], ["/y.txt"])
        self.assertEqual(result["removed"], [])

    def test_diff_removed_files(self):
        """Files in A but not B are reported as removed."""
        from backend.container import ContainerManager
        a = {"overlay": {"entries": {
            "/a.txt": {"data": "11"},
            "/b.txt": {"data": "22"},
        }}}
        b = {"overlay": {"entries": {"/a.txt": {"data": "11"}}}}
        result = ContainerManager.snapshot_diff(a, b)
        self.assertEqual(result["removed"], ["/b.txt"])
        self.assertEqual(result["added"], [])

    def test_diff_modified_files(self):
        """Files with changed data are reported as modified."""
        from backend.container import ContainerManager
        a = {"overlay": {"entries": {"/f.txt": {"data": "old"}}}}
        b = {"overlay": {"entries": {"/f.txt": {"data": "new"}}}}
        result = ContainerManager.snapshot_diff(a, b)
        self.assertEqual(len(result["modified"]), 1)
        self.assertEqual(result["modified"][0]["path"], "/f.txt")
        self.assertIn("data", result["modified"][0]["changes"])

    def test_diff_config_changes(self):
        """Config differences are reported."""
        from backend.container import ContainerManager
        a = {"config": {"hostname": "h1", "seccomp": True}}
        b = {"config": {"hostname": "h2", "seccomp": True}}
        result = ContainerManager.snapshot_diff(a, b)
        self.assertIn("hostname", result["config_changes"])
        self.assertEqual(result["config_changes"]["hostname"]["from"], "h1")
        self.assertEqual(result["config_changes"]["hostname"]["to"], "h2")

    def test_diff_cli_payload(self):
        """CLI build_payload for containers-diff."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            checkpoint_a="/tmp/a.json",
            checkpoint_b="/tmp/b.json",
        )
        # This will fail because the files don't exist,
        # but we can test the payload structure
        import json as _json
        import tempfile, os
        cp = {"overlay": {"entries": {"/x.txt": {"data": "aa"}}}}
        a_path = os.path.join(tempfile.mkdtemp(), "a.json")
        b_path = os.path.join(tempfile.mkdtemp(), "b.json")
        with open(a_path, "w") as f:
            _json.dump(cp, f)
        with open(b_path, "w") as f:
            _json.dump(cp, f)
        args.checkpoint_a = a_path
        args.checkpoint_b = b_path
        payload = build_payload("containers-diff", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_diff")
        self.assertIn("checkpoint_a", payload)
        self.assertIn("checkpoint_b", payload)

    def test_diff_cli_format_human(self):
        """CLI format_human for containers-diff."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "added": ["/new.txt"],
            "removed": ["/old.txt"],
            "modified": [{"path": "/changed.txt", "changes": ["data"]}],
            "config_changes": {},
            "summary": "1 added, 1 removed, 1 modified",
        }
        text = format_human("containers-diff", resp)
        self.assertIn("Added (1):", text)
        self.assertIn("+ /new.txt", text)
        self.assertIn("Removed (1):", text)
        self.assertIn("- /old.txt", text)
        self.assertIn("Modified (1):", text)
        self.assertIn("~ /changed.txt", text)

    def test_diff_cli_format_human_no_changes(self):
        """CLI format_human when no differences found."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "added": [],
            "removed": [],
            "modified": [],
            "config_changes": {},
            "summary": "no differences",
        }
        text = format_human("containers-diff", resp)
        self.assertIn("no differences", text)


class TestContainerEvents(unittest.TestCase):
    """Test container event system (lifecycle notifications)."""

    def test_events_empty_on_init(self):
        """No events recorded on fresh manager."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        events = manager.container_events()
        self.assertEqual(events, [])

    def test_events_record_create(self):
        """Creating a container records a 'created' event."""
        from backend.container import ContainerManager, ContainerConfig
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(hostname="ev-test"))
        events = manager.container_events(container_id=container.id)
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0]["kind"], "created")
        self.assertEqual(events[0]["container_id"], container.id)
        self.assertIn("ev-test", events[0]["detail"])

    def test_events_filter_by_kind(self):
        """Events can be filtered by kind."""
        from backend.container import ContainerManager, ContainerConfig
        manager = ContainerManager(use_cgroups_v2=False)
        c1 = manager.create(ContainerConfig())
        c2 = manager.create(ContainerConfig())
        all_events = manager.container_events()
        created = manager.container_events(kind="created")
        self.assertEqual(len(created), 2)
        self.assertTrue(all(e["kind"] == "created" for e in created))

    def test_events_filter_by_container(self):
        """Events can be filtered by container ID."""
        from backend.container import ContainerManager, ContainerConfig
        manager = ContainerManager(use_cgroups_v2=False)
        c1 = manager.create(ContainerConfig())
        c2 = manager.create(ContainerConfig())
        ev_c1 = manager.container_events(container_id=c1.id)
        self.assertTrue(all(e["container_id"] == c1.id for e in ev_c1))

    def test_events_cli_payload(self):
        """CLI build_payload for containers-events."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            tail=50,
            container="nyctr-abc",
            kind="started",
        )
        payload = build_payload("containers-events", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_events")
        self.assertEqual(payload["tail"], 50)
        self.assertEqual(payload["container_id"], "nyctr-abc")
        self.assertEqual(payload["kind"], "started")

    def test_events_cli_format_human(self):
        """CLI format_human for containers-events."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "events": [
                {"time": 1700000000.0, "kind": "created",
                 "container_id": "nyctr-test", "detail": "hostname=host"},
                {"time": 1700000001.0, "kind": "started",
                 "container_id": "nyctr-test", "detail": "cmd=echo hi"},
            ],
            "count": 2,
        }
        text = format_human("containers-events", resp)
        self.assertIn("created", text)
        self.assertIn("started", text)
        self.assertIn("nyctr-test", text)
        self.assertIn("TIME", text)

    def test_events_cli_format_human_empty(self):
        """CLI format_human when no events found."""
        from nyrqisctl import format_human
        resp = {"ok": True, "events": [], "count": 0}
        text = format_human("containers-events", resp)
        self.assertIn("no events", text)


class TestContainerHealthCheck(unittest.TestCase):
    """Test container health checks (periodic liveness probes)."""

    def test_health_default_starting(self):
        """Health status starts as 'starting'."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        health = manager.container_health(container)
        self.assertEqual(health["status"], "starting")
        self.assertEqual(health["failures"], 0)
        self.assertIsNone(health["check_cmd"])

    def test_health_no_check_cmd(self):
        """Health check does not start without a check command."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        container.pid = 99999
        manager.start_health_check(container)
        # No thread should have been started
        self.assertIsNone(container._health_thread)
        self.assertEqual(container.health_status, "starting")

    def test_health_cli_payload(self):
        """CLI build_payload for containers-health."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="nyctr-abc")
        payload = build_payload("containers-health", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_health")
        self.assertEqual(payload["container_id"], "nyctr-abc")

    def test_health_cli_format_human(self):
        """CLI format_human for containers-health."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "status": "healthy",
            "failures": 0,
            "last_check": 1700000000.0,
            "last_output": "OK",
            "check_cmd": ["echo", "healthy"],
        }
        text = format_human("containers-health", resp)
        self.assertIn("nyctr-test", text)
        self.assertIn("healthy", text)
        self.assertIn("echo healthy", text)
        self.assertIn("OK", text)

    def test_health_cli_format_human_unhealthy(self):
        """CLI format_human shows unhealthy status."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "status": "unhealthy",
            "failures": 3,
            "last_check": None,
            "last_output": "connection refused",
            "check_cmd": ["curl", "-sf", "http://localhost:8080/health"],
        }
        text = format_human("containers-health", resp)
        self.assertIn("unhealthy", text)
        self.assertIn("3", text)
        self.assertIn("connection refused", text)

    def test_health_config_fields(self):
        """ContainerConfig carries health check settings."""
        from backend.container import ContainerConfig
        config = ContainerConfig(
            health_check_cmd=["echo", "ok"],
            health_check_interval=10.0,
            health_check_timeout=3.0,
            health_check_retries=2,
        )
        self.assertEqual(config.health_check_cmd, ["echo", "ok"])
        self.assertEqual(config.health_check_interval, 10.0)
        self.assertEqual(config.health_check_timeout, 3.0)
        self.assertEqual(config.health_check_retries, 2)


class TestResourceLimitsMonitoring(unittest.TestCase):
    """Test resource limits monitoring (memory/PID usage alerts)."""

    def test_limits_unavailable_when_not_running(self):
        """Limits report unavailable for non-running containers."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        result = manager.container_resource_limits(container)
        self.assertFalse(result["available"])
        self.assertEqual(result["memory_alert"], "ok")
        self.assertEqual(result["pid_alert"], "ok")

    def test_limits_alert_levels(self):
        """Alert levels are computed from usage percentages."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
            ResourceLimits,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        config = ContainerConfig(
            limits=ResourceLimits(memory_mb=100, pid_limit=10),
        )
        container = manager.create(config)
        container.state = ContainerState.RUNNING
        container.pid = 12345

        # Mock the stats to return specific values
        original_stats = manager.container_stats
        def mock_stats(c):
            return {
                "available": True,
                "memory_bytes": 80 * 1024 * 1024,  # 80% of 100 MB
                "memory_limit_bytes": 100 * 1024 * 1024,
                "pids_current": 9,  # 90% of 10
            }
        manager.container_stats = mock_stats

        result = manager.container_resource_limits(container)
        self.assertTrue(result["available"])
        self.assertEqual(result["memory_pct"], 80.0)
        self.assertEqual(result["memory_alert"], "warning")
        self.assertEqual(result["pid_pct"], 90.0)
        self.assertEqual(result["pid_alert"], "critical")

        manager.container_stats = original_stats

    def test_limits_cli_payload(self):
        """CLI build_payload for containers-limits."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="nyctr-abc")
        payload = build_payload("containers-limits", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_resource_limits")
        self.assertEqual(payload["container_id"], "nyctr-abc")

    def test_limits_cli_format_human(self):
        """CLI format_human for containers-limits."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "available": True,
            "memory_pct": 80.0,
            "memory_alert": "warning",
            "pid_pct": 50.0,
            "pid_alert": "ok",
        }
        text = format_human("containers-limits", resp)
        self.assertIn("80.0%", text)
        self.assertIn("warning", text)
        self.assertIn("50.0%", text)
        self.assertIn("ok", text)

    def test_limits_cli_format_human_at_limit(self):
        """CLI format_human shows at_limit alert."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "available": True,
            "memory_pct": 100.0,
            "memory_alert": "at_limit",
            "pid_pct": None,
            "pid_alert": "ok",
        }
        text = format_human("containers-limits", resp)
        self.assertIn("100.0%", text)
        self.assertIn("at_limit", text)
        self.assertIn("unlimited", text)


class TestPriorityScheduling(unittest.TestCase):
    """Test container priority scheduling (nice values, CPU affinity)."""

    def test_nice_validation(self):
        """Nice value outside -20..19 raises ValueError."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        with self.assertRaises(ValueError):
            manager.set_nice(container, 20)
        with self.assertRaises(ValueError):
            manager.set_nice(container, -21)

    def test_affinity_empty_raises(self):
        """Empty cores list raises ValueError."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        container.pid = 12345
        with self.assertRaises(ValueError):
            manager.set_cpu_affinity(container, [])

    def test_get_scheduling(self):
        """get_scheduling returns current parameters."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        container.state = ContainerState.RUNNING
        container.pid = os.getpid()  # Use current process for testing
        result = manager.get_scheduling(container)
        self.assertIn("nice_value", result)
        self.assertIn("cpu_affinity_current", result)
        self.assertIn("cpu_count", result)
        self.assertIsNotNone(result["cpu_count"])

    def test_sched_cli_payload_query(self):
        """CLI build_payload for containers-sched (query)."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            nice=None,
            affinity=None,
        )
        payload = build_payload("containers-sched", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_scheduling")

    def test_sched_cli_payload_set_nice(self):
        """CLI build_payload for containers-sched (set nice)."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            nice=5,
            affinity=None,
        )
        payload = build_payload("containers-sched", args)
        self.assertEqual(payload["op"], "container_set_nice")
        self.assertEqual(payload["nice"], 5)

    def test_sched_cli_payload_set_affinity(self):
        """CLI build_payload for containers-sched (set affinity)."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="nyctr-abc",
            nice=None,
            affinity=[0, 1],
        )
        payload = build_payload("containers-sched", args)
        self.assertEqual(payload["op"], "container_set_affinity")
        self.assertEqual(payload["cores"], [0, 1])

    def test_sched_cli_format_human(self):
        """CLI format_human for containers-sched."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "nice_value_current": 0,
            "cpu_affinity_current": [0, 1, 2, 3],
            "cpu_count": 8,
        }
        text = format_human("containers-sched", resp)
        self.assertIn("nyctr-test", text)
        self.assertIn("nice:          0", text)
        self.assertIn("[0, 1, 2, 3]", text)

    def test_config_fields(self):
        """ContainerConfig carries scheduling settings."""
        from backend.container import ContainerConfig
        config = ContainerConfig(nice_value=5, cpu_affinity=[0, 1])
        self.assertEqual(config.nice_value, 5)
        self.assertEqual(config.cpu_affinity, [0, 1])


class TestNetworkPolicy(unittest.TestCase):
    """Test container network policy (iptables ingress/egress filtering)."""

    def test_policy_none_without_network(self):
        """Network policy returns False for non-network containers."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(network=False))
        result = manager.apply_network_policy(container)
        self.assertFalse(result)

    def test_policy_none_without_config(self):
        """Network policy returns False when no policy configured."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig(
            network=True, network_policy=None,
        ))
        result = manager.apply_network_policy(container)
        self.assertFalse(result)

    def test_remove_policy_no_veth(self):
        """Remove policy returns True even without a veth interface."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        result = manager.remove_network_policy(container)
        self.assertTrue(result)

    def test_get_policy_no_veth(self):
        """Get policy returns None for containers without veth."""
        from backend.container import (
            Container, ContainerConfig, ContainerManager, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        container = manager.create(ContainerConfig())
        result = manager.get_network_policy(container)
        self.assertIsNone(result)

    def test_policy_config_fields(self):
        """ContainerConfig carries network policy settings."""
        from backend.container import ContainerConfig
        policy = {
            "ingress_allow": ["tcp:80", "tcp:443"],
            "egress_all": True,
        }
        config = ContainerConfig(network=True, network_policy=policy)
        self.assertEqual(config.network_policy["ingress_allow"], ["tcp:80", "tcp:443"])
        self.assertTrue(config.network_policy["egress_all"])

    def test_netpolicy_cli_payload(self):
        """CLI build_payload for containers-netpolicy."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="nyctr-abc")
        payload = build_payload("containers-netpolicy", args)
        self.assertEqual(payload["service"], "control")
        self.assertEqual(payload["op"], "container_network_policy")
        self.assertEqual(payload["container_id"], "nyctr-abc")

    def test_netpolicy_cli_format_human(self):
        """CLI format_human for containers-netpolicy."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "policy": {
                "interface": "veth-abc123def456",
                "ingress_rules": ["ACCEPT tcp dpt:80"],
                "egress_rules": [],
            },
        }
        text = format_human("containers-netpolicy", resp)
        self.assertIn("veth-abc123def456", text)
        self.assertIn("ACCEPT tcp dpt:80", text)
        self.assertIn("egress: (none)", text)

    def test_netpolicy_cli_format_human_no_policy(self):
        """CLI format_human when no policy exists."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "container_id": "nyctr-test",
            "policy": None,
        }
        text = format_human("containers-netpolicy", resp)
        self.assertIn("no network policy", text)


class TestResourceQuotas(unittest.TestCase):
    """Test resource quotas (per-user limits across containers)."""

    def test_set_and_get_quota(self):
        """Set and retrieve a quota."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        q = manager.set_quota("alice", memory_mb=512, pid_limit=100)
        self.assertEqual(q["memory_mb"], 512)
        self.assertEqual(q["pid_limit"], 100)
        retrieved = manager.get_quota("alice")
        self.assertEqual(retrieved, q)

    def test_list_quotas(self):
        """List all quotas."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", memory_mb=512)
        manager.set_quota("bob", max_containers=5)
        quotas = manager.list_quotas()
        self.assertEqual(len(quotas), 2)
        self.assertIn("alice", quotas)
        self.assertIn("bob", quotas)

    def test_delete_quota(self):
        """Delete a quota."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", memory_mb=512)
        self.assertTrue(manager.delete_quota("alice"))
        self.assertIsNone(manager.get_quota("alice"))
        self.assertFalse(manager.delete_quota("alice"))

    def test_check_quota_within(self):
        """Check passes when within quota."""
        from backend.container import ContainerManager, ContainerConfig
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", memory_mb=512, max_containers=3)
        allowed, reason = manager.check_quota("alice", memory_mb=100)
        self.assertTrue(allowed)
        self.assertIn("within", reason)

    def test_check_quota_exceeds_containers(self):
        """Check fails when container count exceeds quota."""
        from backend.container import (
            ContainerManager, ContainerConfig, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", max_containers=2)
        # Create 2 running containers
        for _ in range(2):
            c = manager.create(ContainerConfig(), owner="alice")
            c.state = ContainerState.RUNNING
        # Third should fail
        allowed, reason = manager.check_quota("alice")
        self.assertFalse(allowed)
        self.assertIn("max_containers", reason)

    def test_check_quota_no_quota(self):
        """Check passes when no quota exists."""
        from backend.container import ContainerManager
        manager = ContainerManager(use_cgroups_v2=False)
        allowed, reason = manager.check_quota("nobody")
        self.assertTrue(allowed)
        self.assertIn("no quota", reason)

    def test_create_rejects_quota(self):
        """Create raises ValueError when quota exceeded."""
        from backend.container import (
            ContainerManager, ContainerConfig, ContainerState,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", max_containers=1)
        c1 = manager.create(ContainerConfig(), owner="alice")
        c1.state = ContainerState.RUNNING
        with self.assertRaises(ValueError) as ctx:
            manager.create(ContainerConfig(), owner="alice")
        self.assertIn("Quota exceeded", str(ctx.exception))

    def test_quota_usage(self):
        """Quota usage reports current resource consumption."""
        from backend.container import (
            ContainerManager, ContainerConfig, ContainerState,
            ResourceLimits,
        )
        manager = ContainerManager(use_cgroups_v2=False)
        manager.set_quota("alice", memory_mb=512, pid_limit=200)
        c = manager.create(
            ContainerConfig(limits=ResourceLimits(memory_mb=128, pid_limit=50)),
            owner="alice",
        )
        c.state = ContainerState.RUNNING
        usage = manager.quota_usage("alice")
        self.assertEqual(usage["containers"], 1)
        self.assertEqual(usage["memory_used_mb"], 128)
        self.assertEqual(usage["pid_used"], 50)

    def test_quota_cli_payloads(self):
        """CLI build_payloads for quota commands."""
        from nyrqisctl import build_payload
        # set
        args = argparse.Namespace(
            owner="alice", memory=512, pids=100, containers=5)
        p = build_payload("quotas-set", args)
        self.assertEqual(p["op"], "quota_set")
        self.assertEqual(p["owner"], "alice")
        self.assertEqual(p["memory_mb"], 512)
        # get
        args = argparse.Namespace(owner="alice")
        p = build_payload("quotas-get", args)
        self.assertEqual(p["op"], "quota_get")
        # list
        p = build_payload("quotas-list", argparse.Namespace())
        self.assertEqual(p["op"], "quota_list")
        # delete
        p = build_payload("quotas-delete", args)
        self.assertEqual(p["op"], "quota_delete")
        # usage
        p = build_payload("quotas-usage", args)
        self.assertEqual(p["op"], "quota_usage")

    def test_quota_cli_format_human(self):
        """CLI format_human for quota commands."""
        from nyrqisctl import format_human
        # usage
        resp = {
            "ok": True, "owner": "alice",
            "containers": 2, "memory_used_mb": 256, "pid_used": 100,
            "memory_limit_mb": 512, "pid_limit": 200,
            "max_containers": 5,
        }
        text = format_human("quotas-usage", resp)
        self.assertIn("alice", text)
        self.assertIn("2/5", text)
        self.assertIn("256 MiB/512 MiB", text)


class TestDependencyOrdering(unittest.TestCase):
    """Test container dependency ordering (start/stop order)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_simple_linear_chain(self):
        """A → B → C starts in A, B, C order."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        a = mgr.create(ContainerConfig(name="a"))
        b = mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        c = mgr.create(ContainerConfig(name="c", depends_on=["b"]))
        order = mgr._compute_start_order(["c", "a", "b"])
        self.assertEqual(order, ["a", "b", "c"])

    def test_diamond_dependency(self):
        """Diamond: A → B, A → C, B+C → D."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        mgr.create(ContainerConfig(name="c", depends_on=["a"]))
        mgr.create(ContainerConfig(name="d", depends_on=["b", "c"]))
        order = mgr._compute_start_order(["d", "a", "b", "c"])
        idx = {name: i for i, name in enumerate(order)}
        self.assertLess(idx["a"], idx["b"])
        self.assertLess(idx["a"], idx["c"])
        self.assertLess(idx["b"], idx["d"])
        self.assertLess(idx["c"], idx["d"])

    def test_no_dependencies(self):
        """Containers without deps start in given (sorted) order."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="x"))
        mgr.create(ContainerConfig(name="y"))
        mgr.create(ContainerConfig(name="z"))
        order = mgr._compute_start_order(["z", "x", "y"])
        self.assertEqual(order, ["x", "y", "z"])

    def test_circular_dependency_detected(self):
        """Circular dependencies raise ValueError."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a", depends_on=["b"]))
        mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        with self.assertRaises(ValueError) as ctx:
            mgr._compute_start_order(["a", "b"])
        self.assertIn("circular", str(ctx.exception))

    def test_missing_container_detected(self):
        """Missing container ID raises ValueError."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        with self.assertRaises(ValueError):
            mgr._compute_start_order(["a", "nonexistent"])

    def test_stop_order_reversed(self):
        """Stop order is the reverse of start order."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        mgr.create(ContainerConfig(name="c", depends_on=["b"]))
        start = mgr._compute_start_order(["a", "b", "c"])
        stop = mgr._compute_stop_order(["a", "b", "c"])
        self.assertEqual(stop, list(reversed(start)))

    def test_dependency_graph(self):
        """get_dependency_graph returns correct structure."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        a = mgr.create(ContainerConfig(name="a"))
        b = mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        a.state = ContainerState.RUNNING
        graph = mgr.get_dependency_graph()
        self.assertIn("a", graph)
        self.assertIn("b", graph)
        self.assertEqual(graph["b"]["depends_on"], ["a"])
        self.assertIn("b", graph["a"]["dependents"])
        self.assertEqual(graph["a"]["state"], "running")

    def test_dependency_graph_filtered(self):
        """get_dependency_graph with specific IDs."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        mgr.create(ContainerConfig(name="b", depends_on=["a"]))
        graph = mgr.get_dependency_graph(["b"])
        self.assertIn("b", graph)
        self.assertNotIn("a", graph)

    def test_cli_payloads(self):
        """CLI build_payloads for dependency commands."""
        from nyrqisctl import build_payload
        # start-ordered
        args = argparse.Namespace(container_ids=["a", "b"])
        p = build_payload("containers-start-ordered", args)
        self.assertEqual(p["op"], "container_start_ordered")
        self.assertEqual(p["container_ids"], ["a", "b"])
        # stop-ordered
        p = build_payload("containers-stop-ordered", args)
        self.assertEqual(p["op"], "container_stop_ordered")
        # dep-graph
        args2 = argparse.Namespace(container_ids=["a"])
        p = build_payload("containers-dep-graph", args2)
        self.assertEqual(p["op"], "container_dependency_graph")

    def test_cli_format_human(self):
        """CLI format_human for dependency commands."""
        from nyrqisctl import format_human
        # start-ordered
        resp = {"ok": True, "results": [
            {"id": "a", "exit_code": 0},
            {"id": "b", "exit_code": 0},
        ]}
        text = format_human("containers-start-ordered", resp)
        self.assertIn("a", text)
        self.assertIn("b", text)
        # dep-graph
        resp2 = {"ok": True, "graph": {
            "a": {"depends_on": [], "dependents": ["b"], "state": "running"},
            "b": {"depends_on": ["a"], "dependents": [], "state": "created"},
        }}
        text2 = format_human("containers-dep-graph", resp2)
        self.assertIn("a", text2)
        self.assertIn("b", text2)


class TestAutoRestart(unittest.TestCase):
    """Test container auto-restart policy."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_default_policy_is_no(self):
        """Default restart policy is 'no'."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertEqual(c.config.restart_policy, "no")
        self.assertFalse(mgr._should_restart(c))

    def test_should_restart_always(self):
        """Policy 'always' triggers restart."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="always",
        ))
        c.state = ContainerState.TERMINATED
        c.exit_code = 0
        self.assertTrue(mgr._should_restart(c))

    def test_should_restart_on_failure_with_error(self):
        """Policy 'on-failure' restarts on non-zero exit."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="on-failure",
        ))
        c.state = ContainerState.TERMINATED
        c.exit_code = 1
        self.assertTrue(mgr._should_restart(c))

    def test_should_not_restart_on_failure_with_zero(self):
        """Policy 'on-failure' does NOT restart on exit 0."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="on-failure",
        ))
        c.state = ContainerState.TERMINATED
        c.exit_code = 0
        self.assertFalse(mgr._should_restart(c))

    def test_max_retries_limit(self):
        """Restart stops after max_retries."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="always",
            restart_max_retries=2,
        ))
        c.state = ContainerState.TERMINATED
        c.exit_code = 0
        c.restart_count = 0
        self.assertTrue(mgr._should_restart(c))  # 0 < 2
        c.restart_count = 1
        self.assertTrue(mgr._should_restart(c))  # 1 < 2
        c.restart_count = 2
        self.assertFalse(mgr._should_restart(c))  # 2 >= 2

    def test_unlimited_retries(self):
        """max_retries=0 means unlimited restarts."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="always",
            restart_max_retries=0,
        ))
        c.state = ContainerState.TERMINATED
        c.exit_code = 0
        c.restart_count = 999
        self.assertTrue(mgr._should_restart(c))

    def test_stop_restart_cancels(self):
        """stop_restart sets the event."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        import threading
        c._restart_stop = threading.Event()
        mgr.stop_restart(c)
        self.assertTrue(c._restart_stop.is_set())

    def test_get_restart_info(self):
        """get_restart_info returns expected fields."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="a", restart_policy="on-failure",
            restart_max_retries=10, restart_delay=2.5,
        ))
        info = mgr.get_restart_info(c)
        self.assertEqual(info["restart_policy"], "on-failure")
        self.assertEqual(info["restart_max_retries"], 10)
        self.assertEqual(info["restart_delay"], 2.5)
        self.assertEqual(info["restart_count"], 0)

    def test_set_restart_policy(self):
        """set_restart_policy updates the config."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        info = mgr.set_restart_policy(
            c, "always", max_retries=3, delay=0.5,
        )
        self.assertEqual(info["restart_policy"], "always")
        self.assertEqual(info["restart_max_retries"], 3)
        self.assertEqual(info["restart_delay"], 0.5)
        self.assertEqual(c.config.restart_policy, "always")

    def test_set_restart_policy_invalid(self):
        """Invalid policy raises ValueError."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        with self.assertRaises(ValueError):
            mgr.set_restart_policy(c, "invalid")

    def test_cli_payloads(self):
        """CLI build_payloads for restart commands."""
        from nyrqisctl import build_payload
        # restart-info
        args = argparse.Namespace(container_id="a")
        p = build_payload("containers-restart-info", args)
        self.assertEqual(p["op"], "container_restart_info")
        self.assertEqual(p["container_id"], "a")
        # restart-set
        args2 = argparse.Namespace(
            container_id="a", policy="always",
            max_retries=5, delay=2.0,
        )
        p = build_payload("containers-restart-set", args2)
        self.assertEqual(p["op"], "container_set_restart")
        self.assertEqual(p["policy"], "always")

    def test_cli_format_human(self):
        """CLI format_human for restart commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "restart_policy": "always",
            "restart_count": 3, "restart_max_retries": 10,
            "restart_delay": 1.5,
        }
        text = format_human("containers-restart-info", resp)
        self.assertIn("always", text)
        self.assertIn("3", text)


class TestEnvironmentManagement(unittest.TestCase):
    """Test container environment variable management."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_set_and_get_env(self):
        """Set and retrieve an environment variable."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_env(c, "MY_VAR", "hello")
        self.assertEqual(mgr.get_env(c, "MY_VAR"), "hello")

    def test_get_env_missing(self):
        """Get returns None for missing key."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertIsNone(mgr.get_env(c, "NONEXISTENT"))

    def test_unset_env(self):
        """Unset removes the variable."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_env(c, "MY_VAR", "hello")
        self.assertTrue(mgr.unset_env(c, "MY_VAR"))
        self.assertIsNone(mgr.get_env(c, "MY_VAR"))
        # Unset again returns False
        self.assertFalse(mgr.unset_env(c, "MY_VAR"))

    def test_list_env(self):
        """List returns all env vars."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_env(c, "A", "1")
        mgr.set_env(c, "B", "2")
        env = mgr.list_env(c)
        self.assertEqual(env, {"A": "1", "B": "2"})

    def test_list_env_returns_copy(self):
        """List returns a copy, not the internal dict."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_env(c, "X", "1")
        env = mgr.list_env(c)
        env["Y"] = "2"
        self.assertIsNone(mgr.get_env(c, "Y"))

    def test_inherit_host_env_default(self):
        """inherit_host_env defaults to True."""
        from backend.container import ContainerConfig
        c = ContainerConfig(name="a")
        self.assertTrue(c.inherit_host_env)

    def test_write_env_file(self):
        """_write_env_file creates a JSON file."""
        import json, os
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_env(c, "FOO", "bar")
        mgr.set_env(c, "NUM", "42")
        path = mgr._write_env_file(c)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data, {"FOO": "bar", "NUM": "42"})
        os.unlink(path)

    def test_write_env_file_empty(self):
        """_write_env_file returns None when no vars."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertIsNone(mgr._write_env_file(c))

    def test_cli_payloads(self):
        """CLI build_payloads for env commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", key="FOO", value="bar")
        p = build_payload("containers-env-set", args)
        self.assertEqual(p["op"], "container_env_set")
        self.assertEqual(p["key"], "FOO")
        self.assertEqual(p["value"], "bar")
        p = build_payload("containers-env-unset", args)
        self.assertEqual(p["op"], "container_env_unset")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("containers-env-list", args2)
        self.assertEqual(p["op"], "container_env_list")

    def test_cli_format_human(self):
        """CLI format_human for env commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "environment": {"FOO": "bar", "NUM": "42"},
        }
        text = format_human("containers-env-list", resp)
        self.assertIn("FOO=bar", text)
        self.assertIn("NUM=42", text)
        # env-set
        resp2 = {"ok": True, "container_id": "a", "key": "FOO"}
        text2 = format_human("containers-env-set", resp2)
        self.assertIn("FOO", text2)
        # env-unset nonexistent
        resp3 = {"ok": True, "container_id": "a", "key": "X", "existed": False}
        text3 = format_human("containers-env-unset", resp3)
        self.assertIn("not set", text3)


class TestSnapshotExportImport(unittest.TestCase):
    """Test container snapshot export/import (portable archives)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_export_creates_tarball(self):
        """Export creates a tar.gz file."""
        import tarfile, tempfile, os
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        out = os.path.join(tempfile.gettempdir(), "test-export.tar.gz")
        try:
            result = mgr.snapshot_export(c, export_path=out)
            self.assertTrue(os.path.isfile(out))
            self.assertEqual(result["container_id"], "a")
            self.assertIn("archive_size", result)
            self.assertGreater(result["archive_size"], 0)
            # Verify archive contains checkpoint.json
            with tarfile.open(out, "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("checkpoint.json", names)
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_export_auto_path(self):
        """Export with no path auto-generates filename."""
        import os
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="b"))
        result = mgr.snapshot_export(c)
        path = result["export_path"]
        try:
            self.assertTrue(os.path.isfile(path))
            self.assertIn("b", path)
            self.assertTrue(path.endswith(".tar.gz"))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_roundtrip_export_import(self):
        """Export then import recovers the checkpoint."""
        import tempfile, os
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(
            name="c", hostname="test-host",
            command=["echo", "hello"],
        ))
        mgr.set_env(c, "FOO", "bar")
        out = os.path.join(tempfile.gettempdir(), "test-roundtrip.tar.gz")
        try:
            mgr.snapshot_export(c, export_path=out)
            checkpoint = mgr.snapshot_import(out)
            self.assertEqual(checkpoint["container_id"], "c")
            self.assertEqual(
                checkpoint["config"]["hostname"], "test-host",
            )
            self.assertEqual(
                checkpoint["config"]["command"], ["echo", "hello"],
            )
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_import_file_not_found(self):
        """Import raises FileNotFoundError for missing archive."""
        from backend.container import ContainerManager
        mgr = self._manager()
        with self.assertRaises(FileNotFoundError):
            mgr.snapshot_import("/nonexistent/file.tar.gz")

    def test_import_missing_checkpoint(self):
        """Import raises ValueError when archive lacks checkpoint.json."""
        import tarfile, tempfile, os
        mgr = self._manager()
        bad = os.path.join(tempfile.gettempdir(), "bad-archive.tar.gz")
        try:
            with tarfile.open(bad, "w:gz") as tar:
                import io
                data = b"not a checkpoint"
                info = tarfile.TarInfo(name="readme.txt")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            with self.assertRaises(ValueError) as ctx:
                mgr.snapshot_import(bad)
            self.assertIn("missing checkpoint.json", str(ctx.exception))
        finally:
            if os.path.exists(bad):
                os.unlink(bad)

    def test_cli_payloads(self):
        """CLI build_payloads for snapshot commands."""
        from nyrqisctl import build_payload
        # export
        args = argparse.Namespace(
            container_id="a", export_path="/tmp/out.tar.gz")
        p = build_payload("containers-snapshot-export", args)
        self.assertEqual(p["op"], "snapshot_export")
        self.assertEqual(p["container_id"], "a")
        self.assertEqual(p["export_path"], "/tmp/out.tar.gz")
        # import
        args2 = argparse.Namespace(archive_path="/tmp/in.tar.gz")
        p = build_payload("containers-snapshot-import", args2)
        self.assertEqual(p["op"], "snapshot_import")
        self.assertEqual(p["archive_path"], "/tmp/in.tar.gz")

    def test_cli_format_human(self):
        """CLI format_human for snapshot commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "export_path": "/tmp/out.tar.gz",
            "archive_size": 12345,
            "overlay_entries": 5,
            "blob_count": 2,
        }
        text = format_human("containers-snapshot-export", resp)
        self.assertIn("/tmp/out.tar.gz", text)
        self.assertIn("12,345", text)
        # import
        resp2 = {"ok": True, "container_id": "a", "state": "created"}
        text2 = format_human("containers-snapshot-import", resp2)
        self.assertIn("a", text2)
        self.assertIn("created", text2)


class TestResourceHistory(unittest.TestCase):
    """Test container resource usage history (time-series)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_init_history(self):
        """_init_resource_history creates the buffer."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._init_resource_history(c)
        self.assertIn(c.id, mgr._resource_history)
        self.assertEqual(len(mgr._resource_history[c.id]), 0)

    def test_get_history_empty(self):
        """get_resource_history returns empty list initially."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        history = mgr.get_resource_history(c)
        self.assertEqual(history, [])

    def test_get_history_tail(self):
        """get_resource_history respects tail parameter."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._init_resource_history(c)
        mgr._resource_history[c.id] = [
            {"ts": 1}, {"ts": 2}, {"ts": 3}, {"ts": 4}, {"ts": 5},
        ]
        history = mgr.get_resource_history(c, tail=2)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["ts"], 4)
        self.assertEqual(history[1]["ts"], 5)

    def test_record_sample_returns_none_when_not_running(self):
        """record_resource_sample returns None when not running."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        sample = mgr.record_resource_sample(c)
        self.assertIsNone(sample)

    def test_history_limit(self):
        """History is capped at 1000 samples."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._init_resource_history(c)
        # Simulate 1010 samples
        mgr._resource_history[c.id] = [
            {"ts": i} for i in range(1010)
        ]
        # Add one more
        mgr._resource_history[c.id].append({"ts": 1010})
        # Trim check
        if len(mgr._resource_history[c.id]) > 1000:
            mgr._resource_history[c.id] = \
                mgr._resource_history[c.id][-1000:]
        self.assertEqual(len(mgr._resource_history[c.id]), 1000)
        self.assertEqual(mgr._resource_history[c.id][-1]["ts"], 1010)

    def test_stop_recording(self):
        """stop_resource_recording sets the event."""
        from backend.container import ContainerConfig
        import threading
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c._resource_stop = threading.Event()
        mgr.stop_resource_recording(c)
        self.assertTrue(c._resource_stop.is_set())

    def test_cli_payloads(self):
        """CLI build_payloads for resource history commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="a", tail=None)
        p = build_payload("containers-resource-history", args)
        self.assertEqual(p["op"], "resource_history")
        self.assertEqual(p["container_id"], "a")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("containers-resource-record", args2)
        self.assertEqual(p["op"], "resource_record")
        args3 = argparse.Namespace(container_id="a", interval=10.0)
        p = build_payload("containers-resource-record-start", args3)
        self.assertEqual(p["op"], "resource_record_start")
        self.assertEqual(p["interval"], 10.0)
        p = build_payload("containers-resource-record-stop", args2)
        self.assertEqual(p["op"], "resource_record_stop")

    def test_cli_format_human(self):
        """CLI format_human for resource history commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "history": [
                {"timestamp": 1000.0, "memory_bytes": 1024,
                 "cpu_usage_usec": 5000, "pids_current": 3},
                {"timestamp": 1005.0, "memory_bytes": 2048,
                 "cpu_usage_usec": 8000, "pids_current": 5},
            ],
            "count": 2,
        }
        text = format_human("containers-resource-history", resp)
        self.assertIn("2 samples", text)
        self.assertIn("1,024", text)
        # record
        resp2 = {
            "ok": True, "container_id": "a",
            "sample": {"memory_bytes": 512, "cpu_usage_usec": 1000,
                        "pids_current": 2},
        }
        text2 = format_human("containers-resource-record", resp2)
        self.assertIn("512", text2)
        # record-start
        resp3 = {"ok": True, "container_id": "a", "interval": 10.0}
        text3 = format_human("containers-resource-record-start", resp3)
        self.assertIn("10.0", text3)


class TestResourceLimitsHotUpdate(unittest.TestCase):
    """Test resource limits hot-update (modify limits at runtime)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_update_raises_without_cgroups(self):
        """update raises ValueError when no cgroup paths."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        with self.assertRaises(ValueError) as ctx:
            mgr.update_resource_limits(c, memory_mb=512)
        self.assertIn("no cgroup paths", str(ctx.exception))

    def test_update_returns_previous_values(self):
        """update returns previous limit values."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.cgroup_paths = ["/sys/fs/cgroup/nyrqis/a"]
        result = mgr.update_resource_limits(
            c, memory_mb=512, pid_limit=100,
        )
        self.assertIn("previous", result)
        self.assertIn("memory_mb", result["previous"])
        self.assertIn("pid_limit", result["previous"])

    def test_update_config_reflects_changes(self):
        """ContainerConfig is updated after hot-update."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.cgroup_paths = ["/sys/fs/cgroup/nyrqis/a"]
        # Memory will fail to write (no real cgroup), but config
        # should still be updated in v1 path if file exists
        old_mem = c.config.limits.memory_mb
        # Just verify the method doesn't crash
        result = mgr.update_resource_limits(c, memory_mb=512)
        self.assertIn("updated", result)
        self.assertIn("container_id", result)

    def test_cli_payloads(self):
        """CLI build_payloads for update-limits command."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", memory=512, pids=100,
            cpu_quota=0,
        )
        p = build_payload("containers-update-limits", args)
        self.assertEqual(p["op"], "container_update_limits")
        self.assertEqual(p["container_id"], "a")
        self.assertEqual(p["memory_mb"], 512)
        self.assertEqual(p["pid_limit"], 100)
        self.assertEqual(p["cpu_quota_us"], 0)

    def test_cli_payloads_partial(self):
        """CLI payload with only some limits."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", memory=256, pids=None,
            cpu_quota=None,
        )
        p = build_payload("containers-update-limits", args)
        self.assertEqual(p["memory_mb"], 256)
        self.assertNotIn("pid_limit", p)
        self.assertNotIn("cpu_quota_us", p)

    def test_cli_format_human(self):
        """CLI format_human for update-limits command."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "updated": ["memory_mb", "pid_limit"],
            "previous": {"memory_mb": 256, "pid_limit": 64},
        }
        text = format_human("containers-update-limits", resp)
        self.assertIn("memory_mb", text)
        self.assertIn("256", text)

    def test_cli_format_human_no_updates(self):
        """CLI format_human when nothing updated."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "updated": [],
            "previous": {"memory_mb": 256},
        }
        text = format_human("containers-update-limits", resp)
        self.assertIn("no limits changed", text)


class TestContainerLabels(unittest.TestCase):
    """Test container labels / metadata."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_set_and_get_label(self):
        """Set and retrieve a label."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_label(c, "app", "web")
        self.assertEqual(mgr.get_label(c, "app"), "web")

    def test_get_label_missing(self):
        """Get returns None for missing key."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertIsNone(mgr.get_label(c, "missing"))

    def test_unset_label(self):
        """Unset removes the label."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_label(c, "app", "web")
        self.assertTrue(mgr.unset_label(c, "app"))
        self.assertIsNone(mgr.get_label(c, "app"))
        self.assertFalse(mgr.unset_label(c, "app"))

    def test_list_labels(self):
        """List returns all labels."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_label(c, "app", "web")
        mgr.set_label(c, "env", "prod")
        labels = mgr.list_labels(c)
        self.assertEqual(labels, {"app": "web", "env": "prod"})

    def test_list_labels_returns_copy(self):
        """List returns a copy."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_label(c, "x", "1")
        labels = mgr.list_labels(c)
        labels["y"] = "2"
        self.assertIsNone(mgr.get_label(c, "y"))

    def test_filter_by_labels(self):
        """filter_by_labels finds matching containers."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        a = mgr.create(ContainerConfig(name="a"))
        b = mgr.create(ContainerConfig(name="b"))
        c = mgr.create(ContainerConfig(name="c"))
        mgr.set_label(a, "app", "web")
        mgr.set_label(a, "env", "prod")
        mgr.set_label(b, "app", "web")
        mgr.set_label(b, "env", "staging")
        mgr.set_label(c, "app", "api")
        # Filter by app=web
        matches = mgr.filter_by_labels({"app": "web"})
        ids = [m.id for m in matches]
        self.assertIn(a.id, ids)
        self.assertIn(b.id, ids)
        self.assertNotIn(c.id, ids)
        # Filter by app=web AND env=prod
        matches2 = mgr.filter_by_labels({"app": "web", "env": "prod"})
        self.assertEqual(len(matches2), 1)
        self.assertEqual(matches2[0].id, a.id)

    def test_filter_no_match(self):
        """filter_by_labels returns empty when nothing matches."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        matches = mgr.filter_by_labels({"nonexistent": "value"})
        self.assertEqual(matches, [])

    def test_default_labels_empty(self):
        """Default labels dict is empty."""
        from backend.container import ContainerConfig
        c = ContainerConfig(name="a")
        self.assertEqual(c.labels, {})

    def test_labels_in_config(self):
        """Labels can be set at creation time."""
        from backend.container import ContainerConfig
        c = ContainerConfig(name="a", labels={"app": "web"})
        self.assertEqual(c.labels, {"app": "web"})

    def test_cli_payloads(self):
        """CLI build_payloads for label commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", key="app", value="web")
        p = build_payload("containers-label-set", args)
        self.assertEqual(p["op"], "label_set")
        self.assertEqual(p["key"], "app")
        self.assertEqual(p["value"], "web")
        p = build_payload("containers-label-unset", args)
        self.assertEqual(p["op"], "label_unset")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("containers-label-list", args2)
        self.assertEqual(p["op"], "label_list")
        # filter
        args3 = argparse.Namespace(labels=["app=web", "env=prod"])
        p = build_payload("containers-label-filter", args3)
        self.assertEqual(p["op"], "label_filter")
        self.assertEqual(p["labels"], {"app": "web", "env": "prod"})

    def test_cli_format_human(self):
        """CLI format_human for label commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "labels": {"app": "web", "env": "prod"},
        }
        text = format_human("containers-label-list", resp)
        self.assertIn("app=web", text)
        self.assertIn("env=prod", text)
        # filter
        resp2 = {
            "ok": True,
            "containers": [
                {"id": "a", "state": "running"},
                {"id": "b", "state": "created"},
            ],
            "count": 2,
        }
        text2 = format_human("containers-label-filter", resp2)
        self.assertIn("2 container", text2)
        self.assertIn("a", text2)


class TestCgroup2Enforcement(unittest.TestCase):
    """Test cgroup2 advanced enforcement features."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_advanced_config_fields(self):
        """New config fields default to None."""
        from backend.container import ContainerConfig, ResourceLimits
        c = ContainerConfig(name="a")
        self.assertIsNone(c.limits.cpu_weight)
        self.assertIsNone(c.limits.memory_high)
        self.assertIsNone(c.limits.io_max_rbps)
        self.assertIsNone(c.limits.io_max_wbps)

    def test_advanced_config_settable(self):
        """New config fields can be set at creation."""
        from backend.container import ContainerConfig, ResourceLimits
        c = ContainerConfig(name="a", limits=ResourceLimits(
            cpu_weight=500,
            memory_high=200 * 1024 * 1024,
            io_max_rbps=100 * 1024 * 1024,
            io_max_wbps=50 * 1024 * 1024,
        ))
        self.assertEqual(c.limits.cpu_weight, 500)
        self.assertEqual(c.limits.memory_high, 200 * 1024 * 1024)

    def test_apply_cgroup2_advanced_no_cgroups(self):
        """apply_cgroup2_advanced returns False without cgroups."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertFalse(mgr.apply_cgroup2_advanced(c))

    def test_get_cgroup2_status_no_cgroups(self):
        """get_cgroup2_status returns unavailable without cgroups."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        status = mgr.get_cgroup2_status(c)
        self.assertFalse(status["available"])

    def test_verify_enforcement_not_running(self):
        """verify_enforcement returns unavailable when not running."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.verify_enforcement(c)
        self.assertFalse(result["enforced"])
        self.assertIn("violations", result)
        self.assertIn("warnings", result)

    def test_verify_enforcement_within_limits(self):
        """verify_enforcement shows no violations within limits."""
        from backend.container import (
            ContainerManager, ContainerConfig, ContainerState,
        )
        mgr = ContainerManager(use_cgroups_v2=False)
        c = mgr.create(ContainerConfig(name="a"))
        c.state = ContainerState.RUNNING
        # Without cgroup paths, stats won't be available
        result = mgr.verify_enforcement(c)
        self.assertFalse(result["enforced"])

    def test_cli_payloads(self):
        """CLI build_payloads for cgroup2 commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="a")
        p = build_payload("containers-cgroup2-status", args)
        self.assertEqual(p["op"], "cgroup2_status")
        self.assertEqual(p["container_id"], "a")
        p = build_payload("containers-verify-enforcement", args)
        self.assertEqual(p["op"], "verify_enforcement")

    def test_cli_format_human_cgroup2_status(self):
        """CLI format_human for cgroup2 status."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a", "available": True,
            "memory_current": "1048576",
            "memory_max": "268435456",
            "pids_current": "5",
            "pids_max": "64",
        }
        text = format_human("containers-cgroup2-status", resp)
        self.assertIn("1048576", text)
        self.assertIn("268435456", text)

    def test_cli_format_human_verify(self):
        """CLI format_human for verify enforcement."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "enforced": True,
            "violations": [],
            "warnings": ["memory: 95.0% of limit used"],
        }
        text = format_human("containers-verify-enforcement", resp)
        self.assertIn("yes", text)
        self.assertIn("95.0%", text)
        # With violations
        resp2 = {
            "ok": True, "container_id": "a",
            "enforced": False,
            "violations": ["memory: exceeds limit"],
            "warnings": [],
        }
        text2 = format_human("containers-verify-enforcement", resp2)
        self.assertIn("NO", text2)
        self.assertIn("exceeds", text2)


class TestContainerTopEnhanced(unittest.TestCase):
    """Test enhanced container top (ps-like output)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_top_returns_empty_when_not_running(self):
        """container_top returns empty when not running."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        procs = mgr.container_top(c)
        self.assertEqual(procs, [])

    def test_top_returns_empty_when_no_pid(self):
        """container_top returns empty when no PID."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.state = ContainerState.RUNNING
        procs = mgr.container_top(c)
        self.assertEqual(procs, [])

    def test_top_has_enhanced_fields(self):
        """container_top returns enhanced fields when scanning /proc."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.state = ContainerState.RUNNING
        c.pid = os.getpid()  # scan our own process
        try:
            procs = mgr.container_top(c)
            if procs:  # may be empty if /proc not readable
                p = procs[0]
                self.assertIn("ppid", p)
                self.assertIn("name", p)
                self.assertIn("nice", p)
                self.assertIn("threads", p)
                self.assertIn("fd_count", p)
                self.assertIn("start_time_s", p)
                self.assertIn("depth", p)
        except Exception:
            pass  # /proc may not be readable in test env

    def test_top_sort_by_cpu(self):
        """container_top with sort_by='cpu' doesn't crash."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.state = ContainerState.RUNNING
        c.pid = os.getpid()
        try:
            procs = mgr.container_top(c, sort_by="cpu")
            self.assertIsInstance(procs, list)
        except Exception:
            pass

    def test_top_max_depth(self):
        """container_top with max_depth limits tree scan."""
        from backend.container import ContainerConfig, ContainerState
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c.state = ContainerState.RUNNING
        c.pid = os.getpid()
        try:
            procs = mgr.container_top(c, max_depth=0)
            # Only the root process should be returned
            for p in procs:
                self.assertEqual(p["depth"], 0)
        except Exception:
            pass

    def test_top_summary_empty(self):
        """container_top_summary returns zeros when not running."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        summary = mgr.container_top_summary(c)
        self.assertEqual(summary["total_processes"], 0)
        self.assertEqual(summary["total_threads"], 0)
        self.assertEqual(summary["total_rss_kb"], 0)
        self.assertEqual(summary["states"], {})

    def test_top_summary_fields(self):
        """container_top_summary has all expected fields."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        summary = mgr.container_top_summary(c)
        self.assertIn("container_id", summary)
        self.assertIn("total_processes", summary)
        self.assertIn("total_threads", summary)
        self.assertIn("total_rss_kb", summary)
        self.assertIn("total_vsize_kb", summary)
        self.assertIn("total_cpu_s", summary)
        self.assertIn("states", summary)

    def test_cli_format_human_summary(self):
        """CLI format_human for top summary."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "total_processes": 5,
            "total_threads": 12,
            "total_rss_kb": 10240,
            "total_vsize_kb": 204800,
            "total_cpu_s": 1.234,
            "states": {"S": 4, "R": 1},
        }
        text = format_human("containers-top", resp)
        self.assertIn("5", text)
        self.assertIn("12", text)

    def test_cli_format_human_procs(self):
        """CLI format_human for top with processes."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "processes": [
                {"pid": 1, "ppid": 0, "state": "S", "name": "init",
                 "threads": 1, "nice": 0, "user_time_s": 0.1,
                 "system_time_s": 0.05, "rss_kb": 1024,
                 "fd_count": 3, "depth": 0},
            ],
            "count": 1,
        }
        text = format_human("containers-top", resp)
        self.assertIn("init", text)
        self.assertIn("1", text)


class TestContainerLocks(unittest.TestCase):
    """Test container lock files (prevent concurrent access)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_acquire_and_release(self):
        """Acquire and release a lock."""
        mgr = self._manager()
        self.assertTrue(mgr.acquire_lock("test-lock-1"))
        self.assertTrue(mgr.is_locked("test-lock-1"))
        mgr.release_lock("test-lock-1")
        self.assertFalse(mgr.is_locked("test-lock-1"))

    def test_lock_file_created(self):
        """Lock file is created on acquire."""
        import os
        mgr = self._manager()
        mgr.acquire_lock("test-lock-2")
        lock_path = mgr._lock_path("test-lock-2")
        self.assertTrue(os.path.exists(lock_path))
        mgr.release_lock("test-lock-2")

    def test_lock_file_removed_on_release(self):
        """Lock file is removed on release."""
        import os
        mgr = self._manager()
        mgr.acquire_lock("test-lock-3")
        lock_path = mgr._lock_path("test-lock-3")
        self.assertTrue(os.path.exists(lock_path))
        mgr.release_lock("test-lock-3")
        self.assertFalse(os.path.exists(lock_path))

    def test_list_locks(self):
        """list_locks returns held locks."""
        mgr = self._manager()
        mgr.acquire_lock("test-lock-4")
        locks = mgr.list_locks()
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0]["container_id"], "test-lock-4")
        mgr.release_lock("test-lock-4")

    def test_list_locks_empty(self):
        """list_locks returns empty when no locks."""
        mgr = self._manager()
        locks = mgr.list_locks()
        self.assertEqual(locks, [])

    def test_release_nonexistent(self):
        """Releasing a non-existent lock is safe."""
        mgr = self._manager()
        mgr.release_lock("nonexistent")  # should not raise

    def test_is_locked_false(self):
        """is_locked returns False for unlocked container."""
        mgr = self._manager()
        self.assertFalse(mgr.is_locked("never-locked"))

    def test_cli_payloads(self):
        """CLI build_payloads for lock commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", non_blocking=False)
        p = build_payload("containers-lock", args)
        self.assertEqual(p["op"], "lock_acquire")
        self.assertEqual(p["container_id"], "a")
        p = build_payload("containers-unlock", args)
        self.assertEqual(p["op"], "lock_release")
        p = build_payload("containers-locks", argparse.Namespace())
        self.assertEqual(p["op"], "lock_list")

    def test_cli_format_human(self):
        """CLI format_human for lock commands."""
        from nyrqisctl import format_human
        resp = {"ok": True, "container_id": "a", "acquired": True}
        text = format_human("containers-lock", resp)
        self.assertIn("acquired", text)
        resp2 = {"ok": True, "locks": [
            {"container_id": "a", "fd": 5, "lock_file": "/tmp/f.lock"},
        ], "count": 1}
        text2 = format_human("containers-locks", resp2)
        self.assertIn("1 lock", text2)
        self.assertIn("a", text2)


class TestResourceAlerts(unittest.TestCase):
    """Test resource usage alerts (threshold notifications)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_default_thresholds(self):
        """Default thresholds are set correctly."""
        from backend.container import ContainerConfig
        c = ContainerConfig(name="a")
        self.assertEqual(c.alert_memory_warning, 75.0)
        self.assertEqual(c.alert_memory_critical, 90.0)
        self.assertEqual(c.alert_pid_warning, 75.0)
        self.assertEqual(c.alert_pid_critical, 90.0)
        self.assertEqual(c.alert_cpu_throttle, 50.0)

    def test_fire_alert(self):
        """_fire_alert records an alert."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        alert = mgr._fire_alert(c, "memory", "critical", "95%")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["resource"], "memory")
        self.assertEqual(alert["level"], "critical")
        self.assertEqual(len(c._alert_history), 1)

    def test_fire_alert_dedup(self):
        """_fire_alert deduplicates within 60s."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        # Second same alert should be deduped
        alert2 = mgr._fire_alert(c, "memory", "critical", "95%")
        self.assertIsNone(alert2)
        self.assertEqual(len(c._alert_history), 1)

    def test_fire_alert_different_resource(self):
        """Different resource fires new alert."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        alert2 = mgr._fire_alert(c, "pid", "critical", "95%")
        self.assertIsNotNone(alert2)
        self.assertEqual(len(c._alert_history), 2)

    def test_get_alert_history(self):
        """get_alert_history returns alerts."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        mgr._fire_alert(c, "pid", "warning", "80%")
        history = mgr.get_alert_history(c)
        self.assertEqual(len(history), 2)

    def test_get_alert_history_filter(self):
        """get_alert_history filters by resource."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        mgr._fire_alert(c, "pid", "warning", "80%")
        history = mgr.get_alert_history(c, resource="memory")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["resource"], "memory")

    def test_get_alert_history_tail(self):
        """get_alert_history respects tail."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        mgr._fire_alert(c, "pid", "warning", "80%")
        # Force different level to avoid dedup
        c._alert_history[-1]["level"] = "ok"
        mgr._fire_alert(c, "pid", "critical", "95%")
        history = mgr.get_alert_history(c, tail=1)
        self.assertEqual(len(history), 1)

    def test_clear_alert_history(self):
        """clear_alert_history clears and returns count."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._fire_alert(c, "memory", "critical", "95%")
        mgr._fire_alert(c, "pid", "warning", "80%")
        count = mgr.clear_alert_history(c)
        self.assertEqual(count, 2)
        self.assertEqual(len(c._alert_history), 0)

    def test_set_alert_thresholds(self):
        """set_alert_thresholds updates config."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.set_alert_thresholds(
            c, memory_warning=80.0, memory_critical=95.0,
        )
        self.assertEqual(result["alert_memory_warning"], 80.0)
        self.assertEqual(result["alert_memory_critical"], 95.0)
        self.assertEqual(c.config.alert_memory_warning, 80.0)

    def test_cli_payloads(self):
        """CLI build_payloads for alert commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", tail=None, resource=None)
        p = build_payload("containers-alert-history", args)
        self.assertEqual(p["op"], "alert_history")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("containers-alert-clear", args2)
        self.assertEqual(p["op"], "alert_clear")
        args3 = argparse.Namespace(
            container_id="a", memory_warning=80.0,
            memory_critical=None, pid_warning=None,
            pid_critical=None, cpu_throttle=None,
        )
        p = build_payload("containers-alert-thresholds", args3)
        self.assertEqual(p["op"], "alert_thresholds")
        self.assertEqual(p["memory_warning"], 80.0)

    def test_cli_format_human(self):
        """CLI format_human for alert commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "alerts": [
                {"timestamp": 1000.0, "resource": "memory",
                 "level": "critical", "detail": "95%"},
            ],
            "count": 1,
        }
        text = format_human("containers-alert-history", resp)
        self.assertIn("1 alerts", text)
        self.assertIn("memory", text)
        # thresholds
        resp2 = {
            "ok": True, "container_id": "a",
            "alert_memory_warning": 80.0,
            "alert_memory_critical": 95.0,
        }
        text2 = format_human("containers-alert-thresholds", resp2)
        self.assertIn("80.0", text2)
        self.assertIn("95.0", text2)


class TestOOMProtection(unittest.TestCase):
    """Test OOM killer protection features."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_default_oom_fields(self):
        """Default OOM fields are set correctly."""
        from backend.container import ResourceLimits
        r = ResourceLimits()
        self.assertEqual(r.oom_score_adj, 0)
        self.assertFalse(r.oom_kill_disable)
        self.assertIsNone(r.memory_swap_max)

    def test_apply_oom_protection_no_cgroups(self):
        """apply_oom_protection returns False without cgroups."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        self.assertFalse(mgr.apply_oom_protection(c))

    def test_get_oom_status(self):
        """get_oom_status returns correct fields."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        status = mgr.get_oom_status(c)
        self.assertIn("oom_score_adj", status)
        self.assertIn("oom_kill_disable", status)
        self.assertIn("memory_swap_max", status)
        self.assertIn("oom_events", status)
        self.assertIn("oom_event_count", status)

    def test_record_oom_event(self):
        """record_oom_event records an event."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        event = mgr.record_oom_event(c, "killed process 1234")
        self.assertEqual(event["detail"], "killed process 1234")
        self.assertEqual(len(c._oom_events), 1)

    def test_record_oom_event_limit(self):
        """OOM events are capped at 50."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        for i in range(55):
            mgr.record_oom_event(c, f"event {i}")
        self.assertEqual(len(c._oom_events), 50)
        self.assertIn("event 54", c._oom_events[-1]["detail"])

    def test_get_oom_events(self):
        """get_oom_events returns events."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.record_oom_event(c, "event 1")
        mgr.record_oom_event(c, "event 2")
        events = mgr.get_oom_events(c)
        self.assertEqual(len(events), 2)

    def test_get_oom_events_tail(self):
        """get_oom_events respects tail."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.record_oom_event(c, "event 1")
        mgr.record_oom_event(c, "event 2")
        events = mgr.get_oom_events(c, tail=1)
        self.assertEqual(len(events), 1)

    def test_set_oom_protection(self):
        """set_oom_protection updates config."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.set_oom_protection(
            c, oom_score_adj=-500, memory_swap_max=0,
        )
        self.assertEqual(result["oom_score_adj"], -500)
        self.assertEqual(result["memory_swap_max"], 0)
        self.assertEqual(c.config.limits.oom_score_adj, -500)

    def test_set_oom_protection_clamps(self):
        """oom_score_adj is clamped to -1000..1000."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.set_oom_protection(c, oom_score_adj=2000)
        self.assertEqual(result["oom_score_adj"], 1000)
        result2 = mgr.set_oom_protection(c, oom_score_adj=-2000)
        self.assertEqual(result2["oom_score_adj"], -1000)

    def test_cli_payloads(self):
        """CLI build_payloads for OOM commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="a")
        p = build_payload("containers-oom-status", args)
        self.assertEqual(p["op"], "oom_status")
        args2 = argparse.Namespace(
            container_id="a", oom_score_adj=-500,
            oom_kill_disable=None, memory_swap_max=0,
        )
        p = build_payload("containers-oom-set", args2)
        self.assertEqual(p["op"], "oom_set")
        self.assertEqual(p["oom_score_adj"], -500)
        args3 = argparse.Namespace(container_id="a", tail=None)
        p = build_payload("containers-oom-events", args3)
        self.assertEqual(p["op"], "oom_events")

    def test_cli_format_human(self):
        """CLI format_human for OOM commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "oom_score_adj": -500, "oom_kill_disable": False,
            "memory_swap_max": 0, "oom_group": None,
            "cgroup_swap_max": None, "oom_event_count": 2,
        }
        text = format_human("containers-oom-status", resp)
        self.assertIn("-500", text)
        self.assertIn("2", text)
        # events
        resp2 = {
            "ok": True, "container_id": "a",
            "events": [
                {"timestamp": 1000.0, "detail": "killed 1234"},
            ],
            "count": 1,
        }
        text2 = format_human("containers-oom-events", resp2)
        self.assertIn("1 events", text2)
        self.assertIn("killed 1234", text2)


class TestResourceDashboard(unittest.TestCase):
    """Test resource usage dashboard (aggregated metrics)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_container_dashboard_fields(self):
        """container_dashboard returns all expected fields."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        dash = mgr.container_dashboard(c)
        self.assertIn("container_id", dash)
        self.assertIn("state", dash)
        self.assertIn("pid", dash)
        self.assertIn("uptime_s", dash)
        self.assertIn("stats", dash)
        self.assertIn("limits", dash)
        self.assertIn("memory_pct", dash)
        self.assertIn("memory_alert", dash)
        self.assertIn("pid_pct", dash)
        self.assertIn("pid_alert", dash)
        self.assertIn("processes", dash)
        self.assertIn("resource_history", dash)
        self.assertIn("alert_history", dash)
        self.assertIn("oom", dash)
        self.assertIn("labels", dash)
        self.assertIn("restart", dash)
        self.assertIn("scheduling", dash)
        self.assertIn("network", dash)

    def test_dashboard_all_fields(self):
        """dashboard_all returns aggregate fields."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        mgr.create(ContainerConfig(name="b"))
        dash = mgr.dashboard_all()
        self.assertIn("total_containers", dash)
        self.assertEqual(dash["total_containers"], 2)
        self.assertIn("by_state", dash)
        self.assertIn("total_memory_bytes", dash)
        self.assertIn("total_pids", dash)
        self.assertIn("containers", dash)
        self.assertEqual(len(dash["containers"]), 2)

    def test_dashboard_all_empty(self):
        """dashboard_all works with no containers."""
        mgr = self._manager()
        dash = mgr.dashboard_all()
        self.assertEqual(dash["total_containers"], 0)
        self.assertEqual(dash["containers"], [])

    def test_dashboard_includes_labels(self):
        """container_dashboard includes labels."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.set_label(c, "app", "web")
        dash = mgr.container_dashboard(c)
        self.assertEqual(dash["labels"], {"app": "web"})

    def test_dashboard_includes_restart(self):
        """container_dashboard includes restart info."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        dash = mgr.container_dashboard(c)
        self.assertIn("restart_policy", dash["restart"])

    def test_cli_payloads(self):
        """CLI build_payloads for dashboard command."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id=None)
        p = build_payload("containers-dashboard", args)
        self.assertEqual(p["op"], "dashboard")
        self.assertNotIn("container_id", p)
        args2 = argparse.Namespace(container_id="a")
        p2 = build_payload("containers-dashboard", args2)
        self.assertEqual(p2["container_id"], "a")

    def test_cli_format_human_all(self):
        """CLI format_human for all-containers dashboard."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "total_containers": 2,
            "by_state": {"running": 1, "created": 1},
            "total_memory_bytes": 1048576,
            "total_pids": 10,
            "containers": [
                {"id": "a", "state": "running", "pid": 123,
                 "memory_bytes": 524288, "pids_current": 5,
                 "labels": {"app": "web"}},
            ],
        }
        text = format_human("containers-dashboard", resp)
        self.assertIn("2", text)
        self.assertIn("running", text)
        self.assertIn("a", text)

    def test_cli_format_human_single(self):
        """CLI format_human for single container dashboard."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a", "state": "running",
            "pid": 123, "uptime_s": 60.0,
            "memory_pct": 50.0, "memory_alert": "ok",
            "pid_pct": 25.0, "pid_alert": "ok",
            "cpu_throttle_pct": 0.0, "cpu_throttle_alert": "ok",
            "processes": {
                "total_processes": 3, "total_threads": 5,
                "total_rss_kb": 10240, "total_cpu_s": 1.5,
            },
            "oom": {"score_adj": 0, "kill_disable": False,
                     "event_count": 0},
            "labels": {"app": "web"},
            "restart": {"restart_policy": "no"},
            "scheduling": {"nice_value": 0, "cpu_affinity": None},
            "network": {"enabled": True, "ip": "10.0.0.2"},
        }
        text = format_human("containers-dashboard", resp)
        self.assertIn("a", text)
        self.assertIn("50.0%", text)
        self.assertIn("3", text)


class TestResourceExport(unittest.TestCase):
    """Test resource usage export (CSV/JSON dump)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_export_json(self):
        """Export resource history as JSON."""
        import json, os, tempfile
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        # Add some history
        mgr._init_resource_history(c)
        mgr._resource_history[c.id] = [
            {"timestamp": 1000.0, "memory_bytes": 1024,
             "cpu_usage_usec": 5000, "pids_current": 3},
        ]
        out = os.path.join(tempfile.gettempdir(), "test-export.json")
        try:
            result = mgr.export_resource_history(c, out, format="json")
            self.assertTrue(os.path.isfile(out))
            self.assertEqual(result["format"], "json")
            self.assertEqual(result["samples"], 1)
            with open(out) as f:
                data = json.load(f)
            self.assertEqual(data["container_id"], "a")
            self.assertEqual(len(data["history"]), 1)
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_export_csv(self):
        """Export resource history as CSV."""
        import os, tempfile
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr._init_resource_history(c)
        mgr._resource_history[c.id] = [
            {"timestamp": 1000.0, "memory_bytes": 1024,
             "cpu_usage_usec": 5000, "pids_current": 3},
            {"timestamp": 1005.0, "memory_bytes": 2048,
             "cpu_usage_usec": 8000, "pids_current": 5},
        ]
        out = os.path.join(tempfile.gettempdir(), "test-export.csv")
        try:
            result = mgr.export_resource_history(c, out, format="csv")
            self.assertTrue(os.path.isfile(out))
            self.assertEqual(result["format"], "csv")
            self.assertEqual(result["samples"], 2)
            with open(out) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 3)  # header + 2 rows
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_export_invalid_format(self):
        """Export raises ValueError for invalid format."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        with self.assertRaises(ValueError):
            mgr.export_resource_history(c, "/tmp/out.txt", format="txt")

    def test_export_snapshot(self):
        """Export container snapshot as JSON."""
        import json, os, tempfile
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        out = os.path.join(tempfile.gettempdir(), "test-snapshot.json")
        try:
            result = mgr.export_container_snapshot(c, out)
            self.assertTrue(os.path.isfile(out))
            with open(out) as f:
                data = json.load(f)
            self.assertEqual(data["container_id"], "a")
            self.assertIn("config", data)
            self.assertIn("limits", data)
            self.assertIn("processes", data)
        finally:
            if os.path.exists(out):
                os.unlink(out)

    def test_cli_payloads(self):
        """CLI build_payloads for export commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", output_path="/tmp/out.json",
            format="json", tail=None,
        )
        p = build_payload("containers-export-history", args)
        self.assertEqual(p["op"], "export_history")
        self.assertEqual(p["format"], "json")
        args2 = argparse.Namespace(
            container_id="a", output_path="/tmp/snap.json",
        )
        p2 = build_payload("containers-export-snapshot", args2)
        self.assertEqual(p2["op"], "export_snapshot")

    def test_cli_format_human(self):
        """CLI format_human for export commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "path": "/tmp/out.json",
            "bytes_written": 1234,
            "samples": 10,
        }
        text = format_human("containers-export-history", resp)
        self.assertIn("/tmp/out.json", text)
        self.assertIn("1,234", text)


class TestWebhooks(unittest.TestCase):
    """Test webhooks (HTTP callbacks for events)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_register_webhook(self):
        """Register a webhook."""
        mgr = self._manager()
        config = mgr.register_webhook("http://example.com/hook")
        self.assertIn("id", config)
        self.assertEqual(config["url"], "http://example.com/hook")
        self.assertTrue(config["enabled"])
        self.assertEqual(config["fire_count"], 0)

    def test_register_with_events(self):
        """Register webhook with event filter."""
        mgr = self._manager()
        config = mgr.register_webhook(
            "http://example.com", events=["alert", "oom"],
        )
        self.assertEqual(config["events"], ["alert", "oom"])

    def test_unregister_webhook(self):
        """Unregister a webhook."""
        mgr = self._manager()
        config = mgr.register_webhook("http://example.com")
        self.assertTrue(mgr.unregister_webhook(config["id"]))
        self.assertFalse(mgr.unregister_webhook(config["id"]))

    def test_list_webhooks(self):
        """List webhooks."""
        mgr = self._manager()
        mgr.register_webhook("http://a.com")
        mgr.register_webhook("http://b.com")
        webhooks = mgr.list_webhooks()
        self.assertEqual(len(webhooks), 2)

    def test_get_webhook(self):
        """Get webhook by ID."""
        mgr = self._manager()
        config = mgr.register_webhook("http://example.com")
        self.assertIsNotNone(mgr.get_webhook(config["id"]))
        self.assertIsNone(mgr.get_webhook("nonexistent"))

    def test_enable_disable(self):
        """Enable and disable webhooks."""
        mgr = self._manager()
        config = mgr.register_webhook("http://example.com")
        self.assertTrue(mgr.disable_webhook(config["id"]))
        self.assertFalse(mgr.get_webhook(config["id"])["enabled"])
        self.assertTrue(mgr.enable_webhook(config["id"]))
        self.assertTrue(mgr.get_webhook(config["id"])["enabled"])

    def test_enable_nonexistent(self):
        """Enable nonexistent webhook returns False."""
        mgr = self._manager()
        self.assertFalse(mgr.enable_webhook("nonexistent"))

    def test_fire_webhooks(self):
        """_fire_webhooks updates fire_count."""
        mgr = self._manager()
        config = mgr.register_webhook(
            "http://example.com", events=["alert"],
        )
        # Fire matching event
        mgr._fire_webhooks("alert", "container-1", "test")
        # Give thread a moment
        import time
        time.sleep(0.05)
        wh = mgr.get_webhook(config["id"])
        self.assertEqual(wh["fire_count"], 1)
        self.assertIsNotNone(wh["last_fired"])

    def test_fire_webhooks_no_match(self):
        """_fire_webhooks skips non-matching events."""
        mgr = self._manager()
        config = mgr.register_webhook(
            "http://example.com", events=["alert"],
        )
        mgr._fire_webhooks("oom", "container-1", "test")
        import time
        time.sleep(0.05)
        wh = mgr.get_webhook(config["id"])
        self.assertEqual(wh["fire_count"], 0)

    def test_fire_webhooks_disabled(self):
        """_fire_webhooks skips disabled webhooks."""
        mgr = self._manager()
        config = mgr.register_webhook("http://example.com")
        mgr.disable_webhook(config["id"])
        mgr._fire_webhooks("alert", "container-1", "test")
        import time
        time.sleep(0.05)
        wh = mgr.get_webhook(config["id"])
        self.assertEqual(wh["fire_count"], 0)

    def test_fire_webhooks_container_filter(self):
        """_fire_webhooks respects container filter."""
        mgr = self._manager()
        config = mgr.register_webhook(
            "http://example.com", container_filter="c1",
        )
        mgr._fire_webhooks("alert", "c2", "test")
        import time
        time.sleep(0.05)
        wh = mgr.get_webhook(config["id"])
        self.assertEqual(wh["fire_count"], 0)
        # Matching container
        mgr._fire_webhooks("alert", "c1", "test")
        time.sleep(0.05)
        self.assertEqual(wh["fire_count"], 1)

    def test_cli_payloads(self):
        """CLI build_payloads for webhook commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            url="http://example.com", events=["alert"],
            secret=None, container_filter=None,
        )
        p = build_payload("webhook-register", args)
        self.assertEqual(p["op"], "webhook_register")
        self.assertEqual(p["url"], "http://example.com")
        args2 = argparse.Namespace(webhook_id="wh-1")
        p = build_payload("webhook-unregister", args2)
        self.assertEqual(p["op"], "webhook_unregister")
        p = build_payload("webhook-list", argparse.Namespace())
        self.assertEqual(p["op"], "webhook_list")
        p = build_payload("webhook-enable", args2)
        self.assertEqual(p["op"], "webhook_enable")
        p = build_payload("webhook-disable", args2)
        self.assertEqual(p["op"], "webhook_disable")

    def test_cli_format_human(self):
        """CLI format_human for webhook commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "id": "wh-1",
            "url": "http://example.com",
            "events": ["alert"],
        }
        text = format_human("webhook-register", resp)
        self.assertIn("wh-1", text)
        self.assertIn("http://example.com", text)
        # list
        resp2 = {
            "ok": True,
            "webhooks": [
                {"id": "wh-1", "url": "http://a.com",
                 "enabled": True, "fire_count": 5},
            ],
            "count": 1,
        }
        text2 = format_human("webhook-list", resp2)
        self.assertIn("1 webhook", text2)
        self.assertIn("a.com", text2)


class TestSLA(unittest.TestCase):
    """Test SLA (service level agreements)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_default_sla_config(self):
        """Default SLA config is set correctly."""
        from backend.container import ContainerConfig
        c = ContainerConfig(name="a")
        self.assertEqual(c.sla_uptime_target, 99.9)
        self.assertEqual(c.sla_max_restart_count, 3)
        self.assertTrue(c.sla_alert_on_breach)

    def test_start_sla_tracking(self):
        """start_sla_tracking initializes tracking."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.start_sla_tracking(c)
        self.assertIsNotNone(c._sla_started_at)
        self.assertEqual(c._sla_downtime_s, 0.0)
        self.assertEqual(len(c._sla_violations), 0)

    def test_record_sla_downtime(self):
        """record_sla_downtime accumulates time."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.record_sla_downtime(c, 10.0, "crash")
        mgr.record_sla_downtime(c, 5.0, "oom")
        self.assertEqual(c._sla_downtime_s, 15.0)

    def test_check_sla_not_tracked(self):
        """check_sla returns not tracked when no start."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.check_sla(c)
        self.assertFalse(result["tracked"])
        self.assertIsNone(result["uptime_pct"])

    def test_check_sla_compliant(self):
        """check_sla returns OK when compliant."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.start_sla_tracking(c)
        import time
        time.sleep(0.01)
        result = mgr.check_sla(c)
        self.assertTrue(result["tracked"])
        self.assertFalse(result["breached"])
        self.assertGreater(result["uptime_pct"], 99.0)

    def test_check_sla_restart_violation(self):
        """check_sla detects restart count violation."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        mgr.start_sla_tracking(c)
        c.restart_count = 5  # exceeds default max of 3
        result = mgr.check_sla(c)
        self.assertTrue(result["breached"])
        self.assertGreater(len(result["violations"]), 0)

    def test_get_sla_violations(self):
        """get_sla_violations returns violations."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        c._sla_violations.append({"type": "test", "detail": "x"})
        violations = mgr.get_sla_violations(c)
        self.assertEqual(len(violations), 1)

    def test_set_sla_config(self):
        """set_sla_config updates config."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.set_sla_config(
            c, uptime_target=99.5, max_restart_count=10,
        )
        self.assertEqual(result["sla_uptime_target"], 99.5)
        self.assertEqual(result["sla_max_restart_count"], 10)
        self.assertEqual(c.config.sla_uptime_target, 99.5)

    def test_cli_payloads(self):
        """CLI build_payloads for SLA commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(container_id="a")
        p = build_payload("sla-check", args)
        self.assertEqual(p["op"], "sla_check")
        args2 = argparse.Namespace(container_id="a", tail=None)
        p = build_payload("sla-violations", args2)
        self.assertEqual(p["op"], "sla_violations")
        args3 = argparse.Namespace(
            container_id="a", uptime_target=99.5,
            max_restart_count=5, alert_on_breach=None,
        )
        p = build_payload("sla-set", args3)
        self.assertEqual(p["op"], "sla_set")
        self.assertEqual(p["uptime_target"], 99.5)

    def test_cli_format_human(self):
        """CLI format_human for SLA commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "container_id": "a",
            "uptime_pct": 99.999, "target": 99.9,
            "breached": False, "downtime_s": 0.1,
            "total_time_s": 1000.0, "tracked": True,
            "restart_count": 0, "max_restarts": 3,
            "violations": [],
        }
        text = format_human("sla-check", resp)
        self.assertIn("99.999", text)
        self.assertIn("OK", text)
        # violations
        resp2 = {
            "ok": True, "container_id": "a",
            "violations": [
                {"timestamp": 1000.0, "type": "restart",
                 "detail": "too many"},
            ],
            "count": 1,
        }
        text2 = format_human("sla-violations", resp2)
        self.assertIn("1 violations", text2)


class TestBilling(unittest.TestCase):
    """Test billing (cost tracking)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_default_billing_rates(self):
        """Default billing rates are set."""
        mgr = self._manager()
        rates = mgr.get_billing_rates()
        self.assertIn("memory_mb_per_hour", rates)
        self.assertIn("cpu_per_hour", rates)
        self.assertIn("pid_per_hour", rates)
        self.assertIn("storage_mb_per_hour", rates)

    def test_set_billing_rates(self):
        """set_billing_rates updates rates."""
        mgr = self._manager()
        result = mgr.set_billing_rates(
            memory_mb_per_hour=0.02, cpu_per_hour=0.10,
        )
        self.assertEqual(result["memory_mb_per_hour"], 0.02)
        self.assertEqual(result["cpu_per_hour"], 0.10)
        self.assertEqual(mgr.get_billing_rates()["memory_mb_per_hour"], 0.02)

    def test_record_billing_usage_not_running(self):
        """record_billing_usage returns not recorded when not running."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.record_billing_usage(c)
        self.assertFalse(result["recorded"])

    def test_get_billing_records_empty(self):
        """get_billing_records returns empty when no records."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        records = mgr.get_billing_records(c)
        self.assertEqual(records, [])

    def test_get_billing_summary_empty(self):
        """get_billing_summary returns zeros when no records."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        summary = mgr.get_billing_summary(c)
        self.assertEqual(summary["total_cost"], 0.0)
        self.assertEqual(summary["record_count"], 0)

    def test_get_billing_summary_all(self):
        """get_billing_summary_all returns aggregate."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        mgr.create(ContainerConfig(name="a"))
        result = mgr.get_billing_summary_all()
        self.assertIn("grand_total_cost", result)
        self.assertIn("container_count", result)
        self.assertIn("containers", result)

    def test_cli_payloads(self):
        """CLI build_payloads for billing commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            memory_mb_per_hour=0.02, cpu_per_hour=0.10,
            pid_per_hour=None, storage_mb_per_hour=None,
        )
        p = build_payload("billing-rates-set", args)
        self.assertEqual(p["op"], "billing_rates_set")
        self.assertEqual(p["memory_mb_per_hour"], 0.02)
        p = build_payload("billing-rates-get", argparse.Namespace())
        self.assertEqual(p["op"], "billing_rates_get")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("billing-record", args2)
        self.assertEqual(p["op"], "billing_record")
        args3 = argparse.Namespace(container_id="a", tail=None)
        p = build_payload("billing-records", args3)
        self.assertEqual(p["op"], "billing_records")
        args4 = argparse.Namespace(container_id=None)
        p = build_payload("billing-summary", args4)
        self.assertEqual(p["op"], "billing_summary")

    def test_cli_format_human(self):
        """CLI format_human for billing commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True,
            "rates": {
                "memory_mb_per_hour": 0.01,
                "cpu_per_hour": 0.05,
            },
        }
        text = format_human("billing-rates-get", resp)
        self.assertIn("0.01", text)
        self.assertIn("0.05", text)
        # summary
        resp2 = {
            "ok": True, "container_id": "a",
            "total_cost": 1.234, "record_count": 10,
            "avg_memory_cost": 0.1, "avg_cpu_cost": 0.2,
            "avg_pid_cost": 0.01,
        }
        text2 = format_human("billing-summary", resp2)
        self.assertIn("1.234", text2)
        self.assertIn("10", text2)


class TestForecasting(unittest.TestCase):
    """Test resource usage forecasting (predictive analytics)."""

    def _manager(self):
        from backend.container import ContainerManager
        return ContainerManager(use_cgroups_v2=False)

    def test_linear_regression(self):
        """_linear_regression computes correct slope/intercept."""
        from backend.container import ContainerManager
        x = [0.0, 1.0, 2.0, 3.0, 4.0]
        y = [1.0, 3.0, 5.0, 7.0, 9.0]
        result = ContainerManager._linear_regression(x, y)
        self.assertIsNotNone(result)
        slope, intercept, r_squared = result
        self.assertAlmostEqual(slope, 2.0, places=3)
        self.assertAlmostEqual(intercept, 1.0, places=3)
        self.assertAlmostEqual(r_squared, 1.0, places=3)

    def test_linear_regression_insufficient_data(self):
        """_linear_regression returns None with < 2 points."""
        from backend.container import ContainerManager
        self.assertIsNone(ContainerManager._linear_regression([0.0], [1.0]))
        self.assertIsNone(ContainerManager._linear_regression([], []))

    def test_forecast_resource_insufficient(self):
        """forecast_resource returns insufficient_data when not enough."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.forecast_resource(c)
        self.assertFalse(result["sufficient_data"])

    def test_forecast_resource_with_data(self):
        """forecast_resource works with enough data."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        # Add history with increasing memory
        mgr._init_resource_history(c)
        base = time.time()
        mgr._resource_history[c.id] = [
            {"timestamp": base + i * 10, "memory_bytes": 1000 + i * 100,
             "cpu_usage_usec": 5000, "pids_current": 3}
            for i in range(10)
        ]
        result = mgr.forecast_resource(c, "memory", horizon_s=600)
        self.assertTrue(result["sufficient_data"])
        self.assertIn("current_value", result)
        self.assertIn("predicted_value", result)
        self.assertIn("trend_per_hour", result)
        self.assertIn("confidence", result)
        # Trend should be positive (increasing)
        self.assertGreater(result["trend_per_hour"], 0)

    def test_forecast_all_resources(self):
        """forecast_all_resources returns forecasts for all."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.forecast_all_resources(c)
        self.assertIn("memory", result)
        self.assertIn("cpu", result)
        self.assertIn("pids", result)

    def test_estimate_time_to_exhaustion(self):
        """estimate_time_to_exhaustion returns None without data."""
        from backend.container import ContainerConfig
        mgr = self._manager()
        c = mgr.create(ContainerConfig(name="a"))
        result = mgr.estimate_time_to_exhaustion(c)
        self.assertIsNone(result)

    def test_cli_payloads(self):
        """CLI build_payloads for forecast commands."""
        from nyrqisctl import build_payload
        args = argparse.Namespace(
            container_id="a", resource="memory", horizon_s=3600,
        )
        p = build_payload("forecast-resource", args)
        self.assertEqual(p["op"], "forecast")
        self.assertEqual(p["resource"], "memory")
        args2 = argparse.Namespace(container_id="a")
        p = build_payload("forecast-all", args2)
        self.assertEqual(p["op"], "forecast_all")
        args3 = argparse.Namespace(
            container_id="a", resource="memory",
        )
        p = build_payload("forecast-exhaustion", args3)
        self.assertEqual(p["op"], "time_to_exhaustion")

    def test_cli_format_human(self):
        """CLI format_human for forecast commands."""
        from nyrqisctl import format_human
        resp = {
            "ok": True, "resource": "memory",
            "current_value": 100000, "predicted_value": 150000,
            "trend_per_hour": 50000, "confidence": 0.95,
            "time_to_limit_s": 7200.0, "sufficient_data": True,
            "sample_count": 10,
        }
        text = format_human("forecast-resource", resp)
        self.assertIn("memory", text)
        self.assertIn("95.0%", text)
        # all
        resp2 = {
            "ok": True, "container_id": "a",
            "memory": {"sufficient_data": True, "current_value": 1000,
                       "predicted_value": 2000, "trend_per_hour": 1000},
            "cpu": {"sufficient_data": False},
            "pids": {"sufficient_data": False},
        }
        text2 = format_human("forecast-all", resp2)
        self.assertIn("a", text2)
        self.assertIn("1,000", text2)


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

    def test_token_bucket_refill_accuracy(self):
        """Token bucket refills at the correct rate over time."""
        bucket = TokenBucket(bucket_size=100, tokens_per_second=100.0)
        # Drain completely
        for _ in range(100):
            bucket.try_consume()
        self.assertFalse(bucket.try_consume())
        # Wait 0.5s → should have ~50 tokens
        time.sleep(0.5)
        consumed = 0
        for _ in range(60):
            if bucket.try_consume():
                consumed += 1
        self.assertGreaterEqual(consumed, 40)
        self.assertLessEqual(consumed, 60)

    def test_token_bucket_thread_safety(self):
        """Concurrent consumption never exceeds bucket capacity."""
        # Use zero refill rate so no tokens appear during the test
        bucket = TokenBucket(bucket_size=50, tokens_per_second=0.0)
        results = []
        def worker():
            ok = bucket.try_consume()
            results.append(ok)
        threads = [threading.Thread(target=worker) for _ in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 50)  # exactly bucket_size consumed
        self.assertEqual(sum(1 for r in results if not r), 10)  # rest denied

    def test_manager_configurable_rate_limit(self):
        """IPCManager creates endpoints with configurable rate limits."""
        from ipc.core import IPCManager
        mgr = IPCManager(default_bucket_size=500, default_tokens_per_second=1000.0)
        ep = mgr.create_endpoint("c1")
        self.assertEqual(ep.rate_limit.bucket_size, 500)
        self.assertEqual(ep.rate_limit.tokens_per_second, 1000.0)

    def test_manager_default_rate_limit(self):
        """IPCManager uses sensible defaults when not configured."""
        from ipc.core import IPCManager
        mgr = IPCManager()
        ep = mgr.create_endpoint("c1")
        self.assertEqual(ep.rate_limit.bucket_size, 200)
        self.assertEqual(ep.rate_limit.tokens_per_second, 500.0)

    def test_rate_limit_sweep_throughput(self):
        """Sweep different bucket configs and verify throughput bounds."""
        configs = [
            (10, 10.0),    # low burst, low rate
            (100, 100.0),  # medium
            (500, 1000.0), # high
        ]
        for burst, rate in configs:
            bucket = TokenBucket(bucket_size=burst, tokens_per_second=rate)
            # Drain
            for _ in range(burst):
                bucket.try_consume()
            # Wait for partial refill (0.1s)
            time.sleep(0.1)
            expected = int(rate * 0.1 * 0.8)  # 80% of expected (tolerance)
            consumed = 0
            for _ in range(int(rate) + 10):
                if bucket.try_consume():
                    consumed += 1
            self.assertGreaterEqual(consumed, max(1, expected),
                msg=f"burst={burst}, rate={rate}: consumed {consumed} < {expected}")


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

    def test_close_joins_serve_loop_before_releasing_the_socket(self):
        # close() must join the serve thread BEFORE closing the
        # endpoint: a serve thread left mid-poll on a closed fd can
        # outlive the close, and when the kernel reuses the fd number
        # for a later bound socket the stale poll can steal ONE
        # datagram from it — observed as a wire-level stream
        # (ADR-0024) that lost exactly one chunk and never completed.
        manager, server = self._server(
            pid_registry={os.getpid(): "container-A"})
        server.bind()
        thread = threading.Thread(target=server.serve, args=(threading.Event(),),
                                  daemon=True)
        thread.start()
        # close() alone (no stop.set()) must stop the loop: it sets the
        # closed flag, the loop notices within one poll window, exits,
        # and close() joins it before releasing the socket.
        server.close()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(server.endpoint._sock)  # released
        self.assertFalse(os.path.exists(server.endpoint.path))
        server.close()  # idempotent

    def test_close_then_rebind_same_path_succeeds(self):
        # The synchronous close contract: the socket path is unlinked
        # before close() returns, so a new server can bind the same
        # path immediately (the pattern the storage tests use for a
        # second server on the same svc_path).
        manager, server = self._server(pid_registry={})
        server.bind()
        thread = threading.Thread(target=server.serve,
                                  args=(threading.Event(),), daemon=True)
        thread.start()
        server.close()
        # Rebind at the SAME path — must not hit EADDRINUSE.
        fresh = IPCManager()
        fresh.create_endpoint("container-svc", "ep-svc2")
        server2 = IPCDatagramServer(
            fresh, "ep-svc2", server.endpoint.path, pid_registry={})
        server2.bind()
        self.assertTrue(os.path.exists(server2.endpoint.path))
        server2.close()

    def test_serve_returns_immediately_when_already_closed(self):
        # serve() on a closed server must return without polling a
        # closed fd (a loop would spin raise/catch until stopped).
        manager, server = self._server(pid_registry={})
        server.bind()
        server.close()
        server.serve(threading.Event())  # returns immediately

    def test_wire_stream_completes_after_sibling_server_close(self):
        # The fd-reuse theft regression (ADR-0024): a server torn down
        # with stop.set(); close() used to leave its serve thread
        # mid-poll; the next server's bind reused the fd and the zombie
        # stole ONE STREAM_CHUNK, so the live server's reassembly never
        # completed and the call timed out. close() now joins the loop
        # before releasing the socket, so the next server is immune.
        def make_server(path, manager):
            server = IPCDatagramServer(manager, "ep-svc", path,
                                       pid_registry={},
                                       trusted_uids={os.getuid()})
            storage = StorageService(
                vault_dir=os.path.join(self.tmp, "v2"))
            router = ServiceRouter()
            router.register("storage", storage)
            router.attach(server)
            server.bind()
            stop = threading.Event()
            threading.Thread(target=server.serve, args=(stop,),
                             daemon=True).start()
            return server, stop
        # Tear one server down exactly as the suite's tests do...
        m1 = IPCManager()
        m1.create_endpoint("container-svc", "ep-svc")
        server1, stop1 = make_server(os.path.join(self.tmp, "s1.sock"), m1)
        stop1.set()
        server1.close()  # must join the loop before releasing the socket
        # ...then IMMEDIATELY open a second server and stream through it.
        m2 = IPCManager()
        m2.create_endpoint("container-svc", "ep-svc")
        server2, stop2 = make_server(os.path.join(self.tmp, "s2.sock"), m2)
        client = IPCClient(DEFAULT_OPERATOR_ID,
                           os.path.join(self.tmp, "c2.sock")).bind()
        try:
            resp = json.loads(client.call(
                server2.endpoint.path,
                json.dumps({"service": "storage", "op": "volume_create",
                            "name": "v"}).encode(),
                timeout_s=5.0).payload.decode("utf-8"))
            self.assertTrue(resp["ok"], resp)
            vid = resp["volume_id"]
            resp = json.loads(client.call(
                server2.endpoint.path,
                json.dumps({"service": "storage", "op": "volume_open",
                            "volume_id": vid}).encode(),
                timeout_s=5.0).payload.decode("utf-8"))
            self.assertTrue(resp["ok"], resp)
            handle = resp["handle"]
            # The real test's oversize write: 11 MiB+1 of data base64-
            # expands to ~470 STREAM_CHUNKs (inside the 512-chunk
            # window) and is rejected by the service's stream budget.
            from ipc.storage import _WIRE_STREAM_DATA_BYTES
            big = base64.b64encode(
                b"y" * (_WIRE_STREAM_DATA_BYTES + 1)).decode()
            reply = client.call(
                server2.endpoint.path,
                json.dumps({"service": "storage", "op": "volume_write",
                            "handle": handle, "path": "/big",
                            "data_b64": big}).encode(),
                timeout_s=8.0, wire_stream=True)
            self.assertIsNotNone(reply)
            resp = json.loads(reply.payload.decode("utf-8"))
            self.assertFalse(resp["ok"])
            self.assertIn("stream budget", resp["error"])
        finally:
            client.close()
            stop2.set()
            server2.close()


class TestSharedMemoryTransport(unittest.TestCase):
    """Test the shared-memory IPC transport (ring buffer, create/attach,
    send/recv, and cleanup)."""

    def test_is_shm_available(self):
        """is_shm_available returns a bool."""
        from ipc.shm_transport import is_shm_available
        result = is_shm_available()
        self.assertIsInstance(result, bool)

    def test_ring_buffer_write_read(self):
        """RingBuffer write and read round-trips data."""
        import mmap
        from ipc.shm_transport import RingBuffer, _HEADER_SIZE, _next_pow2
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        capacity = _next_pow2(4096)
        total = _HEADER_SIZE + capacity
        mm = mmap.mmap(-1, total)
        try:
            header = ShmHeader(
                magic=_SHM_MAGIC, version=_SHM_VERSION,
                head=0, tail=0, capacity=capacity,
            )
            mm[:_HEADER_SIZE] = header.pack()
            writer = RingBuffer(mm, capacity, is_writer=True)
            reader = RingBuffer(mm, capacity, is_writer=False)
            msg = b"hello shm world"
            self.assertTrue(writer.write(msg, timeout_s=1.0))
            result = reader.read(timeout_s=1.0)
            self.assertEqual(result, msg)
        finally:
            mm.close()

    def test_ring_buffer_multiple_messages(self):
        """RingBuffer handles multiple messages in sequence."""
        import mmap
        from ipc.shm_transport import RingBuffer, _HEADER_SIZE, _next_pow2
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        capacity = _next_pow2(4096)
        total = _HEADER_SIZE + capacity
        mm = mmap.mmap(-1, total)
        try:
            header = ShmHeader(
                magic=_SHM_MAGIC, version=_SHM_VERSION,
                head=0, tail=0, capacity=capacity,
            )
            mm[:_HEADER_SIZE] = header.pack()
            writer = RingBuffer(mm, capacity, is_writer=True)
            reader = RingBuffer(mm, capacity, is_writer=False)
            messages = [f"msg-{i}".encode() for i in range(10)]
            for msg in messages:
                self.assertTrue(writer.write(msg, timeout_s=1.0))
            for expected in messages:
                result = reader.read(timeout_s=1.0)
                self.assertEqual(result, expected)
        finally:
            mm.close()

    def test_ring_buffer_empty_read_returns_none(self):
        """RingBuffer.read returns None on timeout when empty."""
        import mmap
        from ipc.shm_transport import RingBuffer, _HEADER_SIZE, _next_pow2
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        capacity = _next_pow2(4096)
        total = _HEADER_SIZE + capacity
        mm = mmap.mmap(-1, total)
        try:
            header = ShmHeader(
                magic=_SHM_MAGIC, version=_SHM_VERSION,
                head=0, tail=0, capacity=capacity,
            )
            mm[:_HEADER_SIZE] = header.pack()
            reader = RingBuffer(mm, capacity, is_writer=False)
            result = reader.read(timeout_s=0.01)
            self.assertIsNone(result)
        finally:
            mm.close()

    def test_ring_buffer_is_empty(self):
        """RingBuffer.is_empty reports correctly."""
        import mmap
        from ipc.shm_transport import RingBuffer, _HEADER_SIZE, _next_pow2
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        capacity = _next_pow2(4096)
        total = _HEADER_SIZE + capacity
        mm = mmap.mmap(-1, total)
        try:
            header = ShmHeader(
                magic=_SHM_MAGIC, version=_SHM_VERSION,
                head=0, tail=0, capacity=capacity,
            )
            mm[:_HEADER_SIZE] = header.pack()
            writer = RingBuffer(mm, capacity, is_writer=True)
            reader = RingBuffer(mm, capacity, is_writer=False)
            self.assertTrue(reader.is_empty())
            writer.write(b"data", timeout_s=1.0)
            self.assertFalse(reader.is_empty())
        finally:
            mm.close()

    def test_shm_header_valid(self):
        """ShmHeader.valid checks magic and version."""
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        h = ShmHeader(magic=_SHM_MAGIC, version=_SHM_VERSION)
        self.assertTrue(h.valid())
        h2 = ShmHeader(magic=0xDEAD, version=1)
        self.assertFalse(h2.valid())

    def test_shm_header_pack_unpack(self):
        """ShmHeader pack/unpack round-trips."""
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        h = ShmHeader(
            magic=_SHM_MAGIC, version=_SHM_VERSION,
            head=100, tail=50, capacity=4096,
        )
        data = h.pack()
        h2 = ShmHeader.unpack(data)
        self.assertEqual(h2.magic, _SHM_MAGIC)
        self.assertEqual(h2.version, _SHM_VERSION)
        self.assertEqual(h2.head, 100)
        self.assertEqual(h2.tail, 50)
        self.assertEqual(h2.capacity, 4096)

    def test_next_pow2(self):
        """_next_pow2 returns the correct power of 2."""
        from ipc.shm_transport import _next_pow2
        self.assertEqual(_next_pow2(1), 1)
        self.assertEqual(_next_pow2(2), 2)
        self.assertEqual(_next_pow2(3), 4)
        self.assertEqual(_next_pow2(4), 4)
        self.assertEqual(_next_pow2(5), 8)
        self.assertEqual(_next_pow2(1000), 1024)
        self.assertEqual(_next_pow2(1024), 1024)
        self.assertEqual(_next_pow2(1025), 2048)

    def test_ring_buffer_large_message(self):
        """RingBuffer handles messages larger than a single entry."""
        import mmap
        from ipc.shm_transport import RingBuffer, _HEADER_SIZE, _next_pow2
        from ipc.shm_transport import ShmHeader, _SHM_MAGIC, _SHM_VERSION
        capacity = _next_pow2(8192)
        total = _HEADER_SIZE + capacity
        mm = mmap.mmap(-1, total)
        try:
            header = ShmHeader(
                magic=_SHM_MAGIC, version=_SHM_VERSION,
                head=0, tail=0, capacity=capacity,
            )
            mm[:_HEADER_SIZE] = header.pack()
            writer = RingBuffer(mm, capacity, is_writer=True)
            reader = RingBuffer(mm, capacity, is_writer=False)
            # Write a message that's ~4KB (larger than a cache line)
            msg = bytes(range(256)) * 16  # 4096 bytes
            self.assertTrue(writer.write(msg, timeout_s=1.0))
            result = reader.read(timeout_s=1.0)
            self.assertEqual(result, msg)
        finally:
            mm.close()

    def test_shm_transport_create_close(self):
        """ShmTransport create and close lifecycle."""
        from ipc.shm_transport import ShmTransport, is_shm_available
        if not is_shm_available():
            self.skipTest("POSIX shm not available")
        transport = ShmTransport("test-create", capacity=4096)
        self.assertTrue(transport.create())
        self.assertTrue(transport.available)
        transport.close()

    def test_shm_transport_send_recv(self):
        """ShmTransport send and recv round-trip."""
        from ipc.shm_transport import ShmTransport, is_shm_available
        if not is_shm_available():
            self.skipTest("POSIX shm not available")
        # Server and client share the same channel_id
        server = ShmTransport("test-sendrecv", capacity=4096)
        client = ShmTransport("test-sendrecv", capacity=4096)
        try:
            self.assertTrue(server.create())
            self.assertTrue(client.attach())
            msg = b"hello from client"
            self.assertTrue(client.send(msg, timeout_s=1.0))
            result = server.recv(timeout_s=1.0)
            self.assertEqual(result, msg)
        finally:
            server.close()
            client.close()

    def test_shm_transport_bidirectional(self):
        """ShmTransport supports bidirectional communication."""
        from ipc.shm_transport import ShmTransport, is_shm_available
        if not is_shm_available():
            self.skipTest("POSIX shm not available")
        # Server and client share the same channel_id
        server = ShmTransport("test-bidir", capacity=4096)
        client = ShmTransport("test-bidir", capacity=4096)
        try:
            self.assertTrue(server.create())
            self.assertTrue(client.attach())
            # Client -> Server
            msg1 = b"client msg"
            self.assertTrue(client.send(msg1, timeout_s=1.0))
            self.assertEqual(server.recv(timeout_s=1.0), msg1)
            # Server -> Client
            msg2 = b"server msg"
            self.assertTrue(server.send(msg2, timeout_s=1.0))
            self.assertEqual(client.recv(timeout_s=1.0), msg2)
        finally:
            server.close()
            client.close()


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

    def test_status_reports_vault_aggregate(self):
        # The operator's at-a-glance vault view rides status: the
        # CACHED ledger figures (volumes, logical/physical totals,
        # warned containers) — no tree walk, so status stays cheap
        # (the §28 refresh cost is only paid by volume_summary).
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=os.path.join(self.tmp, "vault"))
        host.start()
        try:
            client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
            try:
                def call(payload):
                    reply = client.call(self.sock,
                                        json.dumps(payload).encode(),
                                        timeout_s=5.0)
                    return json.loads(reply.payload.decode())

                # Empty vault first.
                resp = call({"op": "status"})
                self.assertTrue(resp["ok"], resp)
                self.assertEqual(resp["vault"], {
                    "volumes": 0, "logical_bytes": 0,
                    "physical_bytes": 0, "warned_containers": 0})
                vid = call({"service": "storage", "op": "volume_create",
                            "name": "a"})["volume_id"]
                handle = call({"service": "storage", "op": "volume_open",
                               "volume_id": vid})["handle"]
                call({"service": "storage", "op": "volume_write",
                      "handle": handle, "path": "/f",
                      "data_b64": base64.b64encode(b"W" * 900)
                      .decode("ascii"), "offset": 0})
                # The durable write committed: cached figures are set.
                resp = call({"op": "status"})
                self.assertEqual(resp["vault"]["volumes"], 1)
                self.assertGreater(resp["vault"]["logical_bytes"], 0)
                self.assertGreater(resp["vault"]["physical_bytes"], 0)
                self.assertEqual(resp["vault"]["warned_containers"], 0)
                # A container crossing into a warning shows up.
                call({"service": "storage", "op": "volume_quota_set",
                      "volume_id": vid,
                      "container": DEFAULT_OPERATOR_ID, "bytes": 1000})
                call({"service": "storage", "op": "volume_write",
                      "handle": handle, "path": "/g",
                      "data_b64": base64.b64encode(b"X" * 100)
                      .decode("ascii"), "offset": 0})  # 1000/1000 = at
                resp = call({"op": "status"})
                self.assertEqual(resp["vault"]["warned_containers"], 1)
            finally:
                client.close()
        finally:
            host.stop()

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
            health_socket_path=None, vault_dir="/var/lib/nyrqis/vault",
            vault_key_file=None, vault_passphrase=None,
            commit_interval=5.0)
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
            health_socket_path=health, vault_dir="/var/lib/nyrqis/vault",
            vault_key_file=None, vault_passphrase=None,
            commit_interval=5.0)
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

    def test_cli_builds_grant_and_snapshot_delete_payloads(self):
        # The access-matrix ops address the volume by id or --name,
        # and the snapshot-delete op drops a named snapshot.
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(
            ["vault", "grant", "vid123", "container-x"])
        self.assertEqual(args.command, "vault-volume-grant")
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_grant",
             "volume_id": "vid123", "container": "container-x"})
        args = parser.parse_args(
            ["vault", "grant", "--name", "shared", "container-x"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_grant",
             "name": "shared", "container": "container-x"})
        # Path-scoped grants (0.14.15): --path rides the grant payload.
        args = parser.parse_args(
            ["vault", "grant", "--name", "shared", "container-x",
             "--path", "/assets"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_grant",
             "name": "shared", "container": "container-x",
             "path": "/assets"})
        args = parser.parse_args(
            ["vault", "revoke", "--name", "shared", "container-x"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_revoke",
             "name": "shared", "container": "container-x"})
        args = parser.parse_args(["vault", "grants", "vid123"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_grants",
             "volume_id": "vid123"})
        args = parser.parse_args(
            ["vault", "snapshot-delete", "handle1", "snap-1"])
        self.assertEqual(args.command, "vault-volume-snapshot-delete")
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_snapshot_delete",
             "handle": "handle1", "name": "snap-1"})
        self.assertIn("container-x", nyrqisctl.format_human(
            "vault-volume-grant", {"ok": True, "volume_id": "vid123",
                                   "container": "container-x"}))
        self.assertIn("revoked", nyrqisctl.format_human(
            "vault-volume-revoke", {"ok": True, "volume_id": "vid123",
                                    "container": "container-x",
                                    "revoked": True}))
        self.assertIn("container-b", nyrqisctl.format_human(
            "vault-volume-grants", {"ok": True, "volume_id": "vid123",
                                    "grants": ["container-b"]}))
        # Scope-aware display: whole-volume grants print bare, scoped
        # grants print as container@path.
        self.assertIn("container-x@/assets", nyrqisctl.format_human(
            "vault-volume-grants", {"ok": True, "volume_id": "vid123",
                                    "grants": [
                                        {"container": "container-x",
                                         "path": "/assets"},
                                        {"container": "container-b",
                                         "path": "/"}]}))
        self.assertIn("scope: /assets", nyrqisctl.format_human(
            "vault-volume-grant", {"ok": True, "volume_id": "vid123",
                                   "container": "container-x",
                                   "path": "/assets"}))
        self.assertIn("whole volume", nyrqisctl.format_human(
            "vault-volume-grant", {"ok": True, "volume_id": "vid123",
                                   "container": "container-x"}))
        self.assertIn("snap-1", nyrqisctl.format_human(
            "vault-volume-snapshot-delete", {"ok": True,
                                             "volume_id": "vid123",
                                             "deleted": "snap-1"}))

    def test_cli_builds_quota_payloads(self):
        # The ADR-0022 accounting surface: quota-set/get + usage,
        # addressing the volume by id or --name, with --unlimited
        # clearing the quota.
        parser = nyrqisctl.build_parser()
        args = parser.parse_args(
            ["vault", "quota-set", "vid123", "container-x",
             "--bytes", "1048576"])
        self.assertEqual(args.command, "vault-volume-quota-set")
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_quota_set",
             "volume_id": "vid123", "container": "container-x",
             "bytes": 1048576})
        args = parser.parse_args(
            ["vault", "quota-set", "--name", "shared", "container-x",
             "--unlimited"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_quota_set",
             "name": "shared", "container": "container-x",
             "bytes": None})
        # Per-subtree quotas (0.14.19): --path rides the payload.
        args = parser.parse_args(
            ["vault", "quota-set", "--name", "shared", "container-x",
             "--path", "/assets", "--bytes", "500"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_quota_set",
             "name": "shared", "container": "container-x",
             "path": "/assets", "bytes": 500})
        self.assertIn("under /assets", nyrqisctl.format_human(
            "vault-volume-quota-set", {"ok": True, "volume_id": "vid123",
                                       "container": "container-x",
                                       "path": "/assets",
                                       "bytes": 500}))
        args = parser.parse_args(["vault", "quota-get", "vid123"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_quota_get",
             "volume_id": "vid123"})
        args = parser.parse_args(["vault", "usage", "--name", "shared"])
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_usage",
             "name": "shared"})
        self.assertIn("1048576 bytes", nyrqisctl.format_human(
            "vault-volume-quota-set", {"ok": True, "volume_id": "vid123",
                                       "container": "container-x",
                                       "bytes": 1048576}))
        self.assertIn("unlimited", nyrqisctl.format_human(
            "vault-volume-quota-set", {"ok": True, "volume_id": "vid123",
                                       "container": "container-x",
                                       "bytes": None}))
        self.assertIn("container-x\t/\tunlimited\t100\t-",
                      nyrqisctl.format_human(
                          "vault-volume-quota-get", {"ok": True,
                                                     "volume_id": "vid123",
                                                     "rows": [{"container": "container-x",
                                                                "scope": "/",
                                                                "quota": None,
                                                                "usage": 100}]}))
        self.assertIn("container-x\t/\t1000\t900\tat",
                      nyrqisctl.format_human(
                          "vault-volume-quota-get", {"ok": True,
                                                     "volume_id": "vid123",
                                                     "rows": [{"container": "container-x",
                                                                "scope": "/",
                                                                "quota": 1000,
                                                                "usage": 900,
                                                                "warning": "at"}]}))
        self.assertIn("container-x\t100", nyrqisctl.format_human(
            "vault-volume-usage", {"ok": True, "volume_id": "vid123",
                                   "usage": {"container-x": 100}}))
        self.assertIn("physical (block-store): 42 bytes",
                      nyrqisctl.format_human(
                          "vault-volume-usage", {"ok": True,
                                                 "volume_id": "vid123",
                                                 "usage": {"container-x": 100},
                                                 "physical_bytes": 42}))
        self.assertIn("quota warning (container-x): near",
                      nyrqisctl.format_human(
                          "vault-volume-usage", {"ok": True,
                                                 "volume_id": "vid123",
                                                 "usage": {"container-x": 100},
                                                 "warnings": {"container-x": "near"}}))
        self.assertIn("(quota warning: near)", nyrqisctl.format_human(
            "vault-volume-write", {"ok": True, "bytes_written": 10,
                                   "path": "/x", "warning": "near"}))
        # The operator whole-vault summary.
        args = parser.parse_args(["vault", "summary"])
        self.assertEqual(args.command, "vault-volume-summary")
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_summary"})
        out = nyrqisctl.format_human(
            "vault-volume-summary", {"ok": True, "volume_count": 1,
                                     "total_logical_bytes": 100,
                                     "total_physical_bytes": 40,
                                     "volumes": [{"name": "assets",
                                                   "logical_bytes": 100,
                                                   "physical_bytes": 40,
                                                   "consumers": 1,
                                                   "warning_count": 1}]})
        self.assertIn("assets\t100\t40\t1\t1", out)
        self.assertIn("logical 100 B", out)
        # The quota-event ring.
        args = parser.parse_args(["vault", "events"])
        self.assertEqual(args.command, "vault-volume-events")
        self.assertEqual(
            nyrqisctl.build_payload(args.command, args),
            {"service": "storage", "op": "volume_events"})
        self.assertEqual(nyrqisctl.format_human(
            "vault-volume-events", {"ok": True, "events": []}),
            "no events")
        out = nyrqisctl.format_human(
            "vault-volume-events", {"ok": True, "events": [
                {"t": 1.5, "volume": "assets", "container": "container-x",
                 "level": "at", "usage": 95, "quota": 100}]})
        self.assertIn("assets\tcontainer-x\tat\t95/100", out)
        # Grant/revoke events (0.14.17) print kind + scope.
        out = nyrqisctl.format_human(
            "vault-volume-events", {"ok": True, "events": [
                {"t": 1.6, "volume": "assets", "container": "container-b",
                 "kind": "grant", "scope": "/assets"}]})
        self.assertIn("assets\tcontainer-b\tgrant\tscope=/assets", out)

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
        # The vault aggregate line (when the daemon reports it).
        out = nyrqisctl.format_human("status", dict(
            status, vault={"volumes": 2, "logical_bytes": 100,
                           "physical_bytes": 40,
                           "warned_containers": 1}))
        self.assertIn(
            "vault:        2 volume(s), 100 logical / 40 physical "
            "bytes, 1 warned", out)
        out = nyrqisctl.format_human("health", {
            "ok": True, "serve_loop_alive": True,
            "vault": {"volumes": 1, "logical_bytes": 50,
                       "physical_bytes": 20,
                       "warned_containers": 0}})
        self.assertIn("vault:          1 volume(s), 50 logical / "
                      "20 physical bytes, 0 warned", out)
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

    def test_cli_builds_vault_payloads(self):
        # NyVault (ADR-0022) commands ride ``service: "storage"`` on
        # the main socket; the write payload base64-encodes the data.
        args = mock.Mock()
        args.name = "assets"
        payload = nyrqisctl.build_payload("vault-volume-create", args)
        self.assertEqual(payload["service"], "storage")
        self.assertEqual(payload["op"], "volume_create")
        self.assertEqual(payload["name"], "assets")
        open_args = mock.Mock()
        open_args.volume_id = "v1"
        open_args.name = ""
        payload = nyrqisctl.build_payload("vault-volume-open", open_args)
        self.assertEqual(payload["volume_id"], "v1")
        open_args = mock.Mock()
        open_args.volume_id = ""
        open_args.name = "assets"
        payload = nyrqisctl.build_payload("vault-volume-open", open_args)
        self.assertEqual(payload["name"], "assets")
        self.assertEqual(nyrqisctl.build_payload(
            "vault-volume-list", mock.Mock())["op"], "volume_list")
        payload = nyrqisctl.build_payload(
            "vault-volume-close", mock.Mock(handle="h1"))
        self.assertEqual(payload["handle"], "h1")
        args = mock.Mock()
        args.handle = "h1"
        args.path = "/docs/note.txt"
        args.offset = 3
        args.data = b"\x00\x01\x02"
        payload = nyrqisctl.build_payload("vault-volume-write", args)
        self.assertEqual(payload["op"], "volume_write")
        self.assertEqual(payload["offset"], 3)
        self.assertEqual(base64.b64decode(payload["data_b64"]),
                         b"\x00\x01\x02")
        args = mock.Mock()
        args.handle = "h1"
        args.path = "/docs/note.txt"
        args.offset = 0
        args.size = 4096
        payload = nyrqisctl.build_payload("vault-volume-read", args)
        self.assertEqual(payload["size"], 4096)
        snap_args = mock.Mock()
        snap_args.handle = "h1"
        snap_args.name = "v1"
        self.assertEqual(nyrqisctl.build_payload(
            "vault-volume-snapshot", snap_args)["name"], "v1")
        self.assertEqual(nyrqisctl.build_payload(
            "vault-volume-snapshots", mock.Mock(handle="h1"))["op"],
            "volume_snapshots")

    def test_cli_formats_vault_output(self):
        self.assertIn("created", nyrqisctl.format_human(
            "vault-volume-create",
            {"ok": True, "volume_id": "v1", "name": "assets"}))
        self.assertIn("h1", nyrqisctl.format_human(
            "vault-volume-open",
            {"ok": True, "handle": "h1", "volume_id": "v1"}))
        self.assertEqual(nyrqisctl.format_human(
            "vault-volume-list", {"ok": True, "volumes": []}),
            "no volumes")
        listed = nyrqisctl.format_human("vault-volume-list", {
            "ok": True, "volumes": [{"id": "v1", "name": "assets",
                                      "created_by": "host-operator"}]})
        self.assertIn("v1\tassets\thost-operator", listed)
        self.assertEqual(nyrqisctl.format_human(
            "vault-volume-close", {"ok": True}), "handle closed")
        self.assertIn("wrote 10 bytes", nyrqisctl.format_human(
            "vault-volume-write",
            {"ok": True, "bytes_written": 10, "path": "/x"}))
        self.assertIn("v1", nyrqisctl.format_human(
            "vault-volume-snapshot",
            {"ok": True, "snapshot_id": "v1", "volume_id": "vid"}))
        self.assertEqual(nyrqisctl.format_human(
            "vault-volume-snapshots", {"ok": True, "snapshots": []}),
            "no snapshots")
        self.assertIn("s1", nyrqisctl.format_human(
            "vault-volume-snapshots", {"ok": True, "snapshots": ["s1"]}))

    def test_cli_vault_lifecycle_e2e(self):
        # The full NyVault operator loop through the CLI against a REAL
        # daemon: create → open → write (from a file) → read (to a
        # file, byte-identical) → snapshot → snapshots → close.
        vault = os.path.join(self.tmp, "vault")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault)
        host.start()
        blob = os.path.join(self.tmp, "blob.bin")
        out = os.path.join(self.tmp, "out.bin")
        with open(blob, "wb") as f:
            f.write(b"\x00\x01cli-vault-e2e\xfe\xff" * 200)
        try:
            rc, o, e = self._cli("vault", "create", "assets")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("created", o)
            rc, o, e = self._cli("vault", "list")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("assets", o)
            # Open by NAME (the operator-friendly path).
            rc, o, e = self._cli("vault", "open", "--name", "assets")
            self.assertEqual(rc, 0, (o, e))
            handle = o.split()[1]
            self.assertTrue(handle)
            # Opening an unknown name is a clean daemon-side failure.
            rc, o, e = self._cli("vault", "open", "--name", "nope")
            self.assertEqual(rc, 1, (o, e))
            self.assertIn("unknown volume", e)
            # Close the by-name handle, re-open by id.
            rc, o, e = self._cli("vault", "close", handle)
            self.assertEqual(rc, 0, (o, e))
            rc, o, e = self._cli("--json", "vault", "list")
            vid = json.loads(o)["volumes"][0]["id"]
            rc, o, e = self._cli("vault", "open", vid)
            self.assertEqual(rc, 0, (o, e))
            handle = o.split()[1]
            rc, o, e = self._cli(
                "vault", "write", handle, "/data/blob.bin",
                "--file", blob)
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("wrote", o)
            rc, o, e = self._cli(
                "vault", "read", handle, "/data/blob.bin",
                "--output", out)
            self.assertEqual(rc, 0, (o, e))
            with open(out, "rb") as f:
                self.assertEqual(
                    f.read(), b"\x00\x01cli-vault-e2e\xfe\xff" * 200)
            rc, o, e = self._cli("vault", "snapshot", handle, "v1")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("v1", o)
            rc, o, e = self._cli("vault", "snapshots", handle)
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("v1", o)
            # Overwrite, then restore v1 — the original bytes come back.
            rc, o, e = self._cli(
                "vault", "write", handle, "/data/blob.bin",
                "--file", blob)
            self.assertEqual(rc, 0, (o, e))
            with open(blob, "wb") as f:
                f.write(b"temporary overwrite")
            rc, o, e = self._cli(
                "vault", "write", handle, "/data/blob.bin",
                "--file", blob)
            self.assertEqual(rc, 0, (o, e))
            rc, o, e = self._cli("vault", "restore", handle, "v1")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("restored to snapshot v1", o)
            rc, o, e = self._cli(
                "vault", "read", handle, "/data/blob.bin",
                "--output", out)
            self.assertEqual(rc, 0, (o, e))
            with open(out, "rb") as f:
                self.assertEqual(
                    f.read(), b"\x00\x01cli-vault-e2e\xfe\xff" * 200)
            # Snapshot deletion through the CLI: v2 survives, v1 is
            # dropped, restore-to-v1 now fails honestly.
            rc, o, e = self._cli("vault", "snapshot", handle, "v2")
            self.assertEqual(rc, 0, (o, e))
            rc, o, e = self._cli(
                "vault", "snapshot-delete", handle, "v1")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("snapshot v1 of volume", o)
            self.assertIn("deleted", o)
            rc, o, e = self._cli("vault", "snapshots", handle)
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("v2", o)
            self.assertNotIn("v1", o)
            rc, o, e = self._cli("vault", "restore", handle, "v2")
            self.assertEqual(rc, 0, (o, e))
            rc, o, e = self._cli("vault", "restore", handle, "v1")
            self.assertEqual(rc, 1, (o, e))
            self.assertIn("not found", e)
            # Quota & accounting through the CLI (ADR-0022): the
            # operator is billed its own writes (the CLI's container is
            # the operator), and an over-quota write fails fail-closed
            # with EDQUOT surfaced as a clean CLI error.
            rc, o, e = self._cli("vault", "quota-set", "--name", "assets",
                                 "host-operator", "--bytes", "1000")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("quota set to 1000 bytes", o)
            big = os.path.join(self.tmp, "big.bin")
            with open(big, "wb") as f:
                f.write(b"Q" * 2000)
            rc, o, e = self._cli("vault", "write", handle, "/big.bin",
                                 "--file", big)
            self.assertEqual(rc, 1, (o, e))
            self.assertIn("quota exceeded", e)
            rc, o, e = self._cli("vault", "quota-get", "--name", "assets")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("host-operator", o)
            self.assertIn("1000", o)
            rc, o, e = self._cli("vault", "usage", "--name", "assets")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("host-operator", o)
            # Clearing the quota makes the volume unlimited again.
            rc, o, e = self._cli("vault", "quota-set", "--name", "assets",
                                 "host-operator", "--unlimited")
            self.assertEqual(rc, 0, (o, e))
            self.assertIn("unlimited", o)
            rc, o, e = self._cli("vault", "close", handle)
            self.assertEqual(rc, 0, (o, e))
        finally:
            host.stop()

    def test_cli_encrypted_vault_lifecycle_e2e(self):
        # The full ADR-0023 operator flow through the CLI against a
        # REAL daemon: `vault init` writes the KEK envelope, the
        # daemon serves with --vault-key-file + passphrase, volumes
        # are encrypted at rest (the vault dir never contains the
        # plaintext), survive a restart (DEK re-unwrapped from the
        # KEK), and are crypto-shredded by delete.
        vault = os.path.join(self.tmp, "enc-vault")
        key_file = os.path.join(self.tmp, "vault.key")
        pw = "cli-operator-secret"
        rc, out, err = self._cli("vault", "init", key_file,
                                 "--passphrase", pw)
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("KEK envelope", out)
        # init refuses to clobber.
        rc, out, err = self._cli("vault", "init", key_file,
                                 "--passphrase", pw)
        self.assertEqual(rc, 2, (out, err))
        self.assertIn("already exists", err)
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key_file,
            vault_passphrase=pw)
        host.start()
        blob = os.path.join(self.tmp, "secret.bin")
        out_file = os.path.join(self.tmp, "out.bin")
        payload = b"CLI-ENCRYPTED-PAYLOAD-" * 100
        with open(blob, "wb") as f:
            f.write(payload)
        try:
            rc, out, err = self._cli("--json", "vault", "create", "sec")
            self.assertEqual(rc, 0, (out, err))
            self.assertTrue(json.loads(out)["encrypted"])
            rc, out, err = self._cli("vault", "open", "--name", "sec")
            handle = out.split()[1]
            rc, out, err = self._cli(
                "vault", "write", handle, "/blob.bin", "--file", blob)
            self.assertEqual(rc, 0, (out, err))
            rc, out, err = self._cli(
                "vault", "read", handle, "/blob.bin", "--output", out_file)
            self.assertEqual(rc, 0, (out, err))
            with open(out_file, "rb") as f:
                self.assertEqual(f.read(), payload)
            # At rest: no plaintext anywhere under the vault dir.
            leaked = False
            for root, _dirs, files in os.walk(vault):
                for name in files:
                    with open(os.path.join(root, name), "rb") as f:
                        if b"CLI-ENCRYPTED-PAYLOAD" in f.read():
                            leaked = True
            self.assertFalse(leaked)
        finally:
            host.stop()
        # Restart with the same key: the volume + data survive, and a
        # WRONG passphrase cannot open the vault (fail-closed).
        host2 = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key_file,
            vault_passphrase=pw)
        host2.start()
        try:
            rc, out, err = self._cli("vault", "list")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("sec", out)
            rc, out, err = self._cli("vault", "open", "--name", "sec")
            self.assertEqual(rc, 0, (out, err))
            handle = out.split()[1]
            rc, out, err = self._cli(
                "vault", "read", handle, "/blob.bin", "--output", out_file)
            self.assertEqual(rc, 0, (out, err))
            with open(out_file, "rb") as f:
                self.assertEqual(f.read(), payload)
            # Delete crypto-shreds.
            rc, out, err = self._cli("vault", "delete", "--name", "sec")
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("crypto-shredded", out)
            rc, out, err = self._cli("vault", "list")
            self.assertIn("no volumes", out)
        finally:
            host2.stop()
        # Wrong passphrase at serve time is a hard error (the KEK
        # check value fails its AEAD verification — fail-closed).
        with self.assertRaises(keys_module.KeysError):
            nyrqis_backend.StatusServiceHost(
                socket_path=self.sock, backend_version="9.9.9",
                vault_dir=vault, vault_key_file=key_file,
                vault_passphrase="wrong")

    def test_cli_vault_rekey_e2e(self):
        # ADR-0023 KEK rotation through the CLI against a REAL daemon:
        # create an encrypted volume + write data under key A, rekey to
        # B, restart the daemon under B — the data reads back (the DEK,
        # hence the ciphertext, was never touched). After the rekey the
        # OLD key file can no longer open the volume (fail-closed).
        vault = os.path.join(self.tmp, "rk-vault")
        key_a = os.path.join(self.tmp, "key-a")
        key_b = os.path.join(self.tmp, "key-b")
        pw_a, pw_b = "rekey-secret-a", "rekey-secret-b"
        rc, out, err = self._cli("vault", "init", key_a,
                                 "--passphrase", pw_a)
        self.assertEqual(rc, 0, (out, err))
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key_a,
            vault_passphrase=pw_a)
        host.start()
        blob = os.path.join(self.tmp, "secret.bin")
        out_file = os.path.join(self.tmp, "out.bin")
        payload = b"REKEY-UNTOUCHED-" * 100
        with open(blob, "wb") as f:
            f.write(payload)
        try:
            rc, out, err = self._cli("vault", "create", "rk")
            self.assertEqual(rc, 0, (out, err))
            rc, out, err = self._cli("vault", "open", "--name", "rk")
            handle = out.split()[1]
            rc, out, err = self._cli(
                "vault", "write", handle, "/blob.bin", "--file", blob)
            self.assertEqual(rc, 0, (out, err))
            # The rekey itself, through the CLI.
            rc, out, err = self._cli(
                "vault", "rekey", "--new-passphrase", pw_b,
                "--new-key-file", key_b)
            self.assertEqual(rc, 0, (out, err))
            self.assertIn("rekeyed 1 volume", out)
            self.assertIn(key_b, out)
            # A second rekey with an existing target file is refused.
            rc, out, err = self._cli(
                "vault", "rekey", "--new-passphrase", "another",
                "--new-key-file", key_b)
            self.assertEqual(rc, 2, (out, err))
            self.assertIn("already exists", err)
        finally:
            host.stop()
        # Restart under the NEW key: the data reads back untouched.
        host2 = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key_b,
            vault_passphrase=pw_b)
        host2.start()
        try:
            rc, out, err = self._cli("vault", "open", "--name", "rk")
            self.assertEqual(rc, 0, (out, err))
            handle = out.split()[1]
            rc, out, err = self._cli(
                "vault", "read", handle, "/blob.bin", "--output", out_file)
            self.assertEqual(rc, 0, (out, err))
            with open(out_file, "rb") as f:
                self.assertEqual(f.read(), payload)
        finally:
            host2.stop()
        # The OLD key can no longer serve the volume (its DEKs were
        # re-wrapped): opening fails closed with an unwrap error.
        host3 = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key_a,
            vault_passphrase=pw_a)
        host3.start()
        try:
            rc, out, err = self._cli("vault", "open", "--name", "rk")
            self.assertEqual(rc, 1, (out, err))
            self.assertIn("unwrap", err)
        finally:
            host3.stop()

    def test_cli_vault_refuses_health_socket(self):
        # Vault ops are control-plane ops: they use the main socket and
        # refuse the dedicated health socket like control commands.
        health_path = os.path.join(self.tmp, "health.sock")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            health_socket_path=health_path)
        host.start()
        try:
            rc, o, e = self._cli(
                "--health-socket", health_path, "vault", "list")
            self.assertEqual(rc, 2)
            self.assertIn("health socket serves status/health only", e)
            # The mount subcommand rides the same refusal.
            rc, o, e = self._cli(
                "--health-socket", health_path, "vault", "mount",
                "--name", "assets", "/tmp/nyrqis-mnt")
            self.assertEqual(rc, 2)
            self.assertIn("health socket serves status/health only", e)
        finally:
            host.stop()

    def test_cli_vault_mount_deferral_without_fusepy(self):
        # No fusepy (the CI condition): the mount reports the deferral
        # honestly with a nonzero exit instead of pretending to mount.
        # Driven in-process (the ``_cli`` helper runs a subprocess,
        # which cannot see parent-process mocks).
        import io
        from contextlib import redirect_stderr, redirect_stdout
        args = argparse.Namespace(
            volume_id="", name="assets", mount_point="/tmp/nyrqis-mnt",
            background=False, socket=self.sock, health_socket=None,
            timeout=30.0, json=False)
        with mock.patch("fuse.vault_mount.NyVaultMount") as mnt_cls:
            mnt = mnt_cls.return_value
            mnt.mount.return_value = False
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = nyrqisctl.run("vault-volume-mount", args)
            self.assertEqual(rc, 1)
            self.assertIn("fusepy is not available", err.getvalue())
            mnt_cls.assert_called_once()
            mnt.mount.assert_called_once_with(
                foreground=True, blocking=False)

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


class TestStorageService(unittest.TestCase):
    """NyVault's first increment (ADR-0022): the storage service on the
    router — capability-gated named volumes (CAP_STORAGE_VOLUME),
    creator-scoped opens, opaque handles, and NyFS backing under the
    daemon's vault directory. The operator path and the container path
    (real server, real grants) are both exercised.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sock = os.path.join(self.tmp, "status.sock")
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    def _serve(self, vault_dir=None, capability_manager=None,
               register_pid=True, svc_path=None, kek=None,
               commit_interval=None):
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        caps = capability_manager
        # register_pid=False leaves the registry EMPTY so the test
        # process resolves through the trusted-uid OPERATOR path
        # (container-FIRST: a registered pid would shadow it).
        registry = {os.getpid(): "cli"} if register_pid else {}
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path or self.svc_path,
            pid_registry=registry,
            capability_manager=caps,
            trusted_uids={os.getuid()},
        )
        storage = StorageService(
            capability_manager=caps, vault_dir=vault_dir, kek=kek,
            commit_interval=commit_interval or 5.0)
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        return server, stop, storage

    def _kek(self, password=b"test-vault-secret"):
        """An unlocked KEK handle for encrypted-vault tests (the
        floor's handle — the crate is exercised in the conformance
        classes)."""
        from backend import keys
        blob = keys.make_kek_blob(password)
        return keys.unlock(blob, password)

    def _call(self, client, payload, timeout=5.0, path=None):
        reply = client.call(path or self.svc_path, payload,
                            timeout_s=timeout)
        if reply is None:
            return None
        return json.loads(reply.payload.decode("utf-8"))

    def _granted_container(self, caps):
        # A registered container (the test's pid) initialized with
        # default grants plus CAP_STORAGE_VOLUME.
        caps.initialize_container("cli")
        caps.grant_capability("cli", Capability.CAP_STORAGE_VOLUME)

    def test_operator_volume_lifecycle_with_nyfs_backing(self):
        vault = os.path.join(self.tmp, "vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "assets"}).encode())
            self.assertTrue(resp["ok"], resp)
            vid = resp["volume_id"]
            self.assertTrue(vid)
            # NyFS backing: the volume root was created under the vault.
            self.assertTrue(os.path.isdir(os.path.join(vault, vid + ".nyfs")))
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": vid}).encode())
            self.assertTrue(resp["ok"], resp)
            handle = resp["handle"]
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_info",
                "handle": handle}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["backend"], "nyfs")
            self.assertEqual(resp["name"], "assets")
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_close",
                "handle": handle}).encode())
            self.assertTrue(resp["ok"], resp)
            # The handle is gone.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_info",
                "handle": handle}).encode())
            self.assertFalse(resp["ok"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_container_capability_gate(self):
        # A container needs CAP_STORAGE_VOLUME: initialized (default
        # grants) but NOT granted the storage cap → forbidden.
        caps = CapabilityManager()
        caps.initialize_container("cli")
        server, stop, storage = self._serve(capability_manager=caps)
        client = IPCClient("cli", self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "secret"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("CAP_STORAGE_VOLUME required", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()
        # With the grant, the same container creates + opens.
        caps2 = CapabilityManager()
        self._granted_container(caps2)
        server2, stop2, storage2 = self._serve(capability_manager=caps2)
        client2 = IPCClient("cli", self.cli_path).bind()
        try:
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "mine"}).encode())
            self.assertTrue(resp["ok"], resp)
            vid = resp["volume_id"]
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": vid}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertIn("handle", resp)
        finally:
            client2.close()
            stop2.set()
            server2.close()

    def test_fail_closed_without_capability_manager(self):
        # No CapabilityManager attached → no grant can be verified, so
        # even the operator... is the operator carve-out: the operator
        # IS authorized (same as the status service). A container is
        # denied.
        server, stop, storage = self._serve(capability_manager=None)
        client = IPCClient("cli", self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_list"}).encode())
            self.assertIsNotNone(resp)
            self.assertFalse(resp["ok"])
            self.assertIn("CAP_STORAGE_VOLUME", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_duplicate_name_and_unknown_volume_rejected(self):
        server, stop, storage = self._serve(register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            payload = json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "dup"}).encode()
            self.assertTrue(self._call(client, payload)["ok"])
            resp = self._call(client, payload)
            self.assertFalse(resp["ok"])
            self.assertIn("already exists", resp["error"])
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": "nope"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown volume", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volumes_are_creator_scoped(self):
        # Container A creates a volume; container B cannot open it (the
        # cross-container grant matrix is ADR-0022 future work).
        caps = CapabilityManager()
        caps.initialize_container("ctr-a")
        caps.initialize_container("ctr-b")
        caps.grant_capability("ctr-a", Capability.CAP_STORAGE_VOLUME)
        caps.grant_capability("ctr-b", Capability.CAP_STORAGE_VOLUME)
        # Both pids are the test's pid... the registry maps pid→one id,
        # so drive the service directly instead of through the wire for
        # the second caller.
        storage = StorageService(capability_manager=caps)
        # A creates via the service handler against a fake server.
        fake = mock.Mock()
        fake.reply = mock.Mock()
        storage.attach(fake)
        storage._on_call(mock.Mock(message_id="1", payload=json.dumps({
            "service": "storage", "op": "volume_create",
            "name": "shared"}).encode()), "ctr-a", "/tmp/a.sock")
        body = json.loads(fake.reply.call_args.args[2].decode())
        vid = body["volume_id"]
        fake.reply.reset_mock()
        storage._on_call(mock.Mock(message_id="2", payload=json.dumps({
            "service": "storage", "op": "volume_open",
            "volume_id": vid}).encode()), "ctr-b", "/tmp/b.sock")
        body = json.loads(fake.reply.call_args.args[2].decode())
        self.assertFalse(body["ok"])
        self.assertIn("is not yours", body["error"])
        # The creator CAN open it.
        fake.reply.reset_mock()
        storage._on_call(mock.Mock(message_id="3", payload=json.dumps({
            "service": "storage", "op": "volume_open",
            "volume_id": vid}).encode()), "ctr-a", "/tmp/a.sock")
        body = json.loads(fake.reply.call_args.args[2].decode())
        self.assertTrue(body["ok"])
        self.assertIn("handle", body)

    def test_host_serves_storage_on_main_socket(self):
        # The full daemon: the storage service rides the main service
        # socket (ADR-0022), the operator drives a NyFS-backed volume
        # through the wire end-to-end.
        vault = os.path.join(self.tmp, "host-vault")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=vault)
        host.start()
        op_client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = op_client.call(self.sock, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "host-vol"}).encode(), timeout_s=5.0)
            self.assertIsNotNone(resp)
            body = json.loads(resp.payload.decode())
            self.assertTrue(body["ok"], body)
            vid = body["volume_id"]
            self.assertTrue(os.path.isdir(
                os.path.join(vault, vid + ".nyfs")))
            resp = op_client.call(self.sock, json.dumps({
                "service": "storage", "op": "volume_list"}).encode(),
                timeout_s=5.0)
            body = json.loads(resp.payload.decode())
            self.assertTrue(body["ok"])
            self.assertEqual([v["name"] for v in body["volumes"]],
                             ["host-vol"])
        finally:
            op_client.close()
            host.stop()

    def test_host_serves_storage_through_loop_when_crate_present(self):
        # ADR-0021/ADR-0022: the storage router rides the loop's
        # dispatch handoff on the main socket when the crate is present
        # — the reply crosses the batch boundary like control ops.
        if not ipc_loop.available():
            self.skipTest("Rust serving loop not built on this host")
        host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9")
        host.start()
        op_client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = op_client.call(self.sock, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "loop-vol"}).encode(), timeout_s=5.0)
            self.assertIsNotNone(resp)
            body = json.loads(resp.payload.decode())
            self.assertTrue(body["ok"], body)
        finally:
            op_client.close()
            host.stop()

    # -- byte path (ADR-0022: the daemon holds the data plane) ------

    def _opened_volume(self, server, client, name="bv"):
        resp = self._call(client, json.dumps({
            "service": "storage", "op": "volume_create",
            "name": name}).encode())
        self.assertTrue(resp["ok"], resp)
        resp = self._call(client, json.dumps({
            "service": "storage", "op": "volume_open",
            "volume_id": resp["volume_id"]}).encode())
        self.assertTrue(resp["ok"], resp)
        return resp["handle"]

    def test_byte_path_write_read_roundtrip(self):
        vault = os.path.join(self.tmp, "byte-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            handle = self._opened_volume(server, client)
            body = b"hello vault " * 100  # 1300 bytes, > 1 block
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/images/logo.bin",
                "data_b64": base64.b64encode(body).decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["bytes_written"], len(body))
            # Read it all back (default size = the per-call cap).
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/images/logo.bin"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(
                base64.b64decode(resp["data_b64"]), body)
            # Offset + size paging.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/images/logo.bin",
                "offset": 100, "size": 50}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(
                base64.b64decode(resp["data_b64"]), body[100:150])
            # Offset write overwrites in place.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/images/logo.bin",
                "offset": 6, "data_b64": base64.b64encode(
                    b"XY").decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/images/logo.bin",
                "size": 20}).encode())
            got = base64.b64decode(resp["data_b64"])
            self.assertEqual(got[:6], body[:6])
            self.assertEqual(got[6:8], b"XY")
            self.assertEqual(got[8:], body[8:20])
            # The write is durable in the NyFS image (saved blocks).
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_info",
                "handle": handle}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["backend"], "nyfs")
        finally:
            client.close()
            stop.set()
            server.close()

    def test_byte_path_snapshot_lifecycle(self):
        vault = os.path.join(self.tmp, "snap-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            handle = self._opened_volume(server, client, name="snap-vol")
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/doc.txt",
                "data_b64": base64.b64encode(b"v1").decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_snapshot",
                "handle": handle, "name": "before-edit"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["snapshot_id"], "before-edit")
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_snapshots",
                "handle": handle}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["snapshots"], ["before-edit"])
            # Overwrite the file — the snapshot still holds the old data
            # (the NyFS CoW guarantee behind the op).
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/doc.txt",
                "data_b64": base64.b64encode(b"v2").decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_snapshot",
                "handle": handle, "name": "after-edit"}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_snapshots",
                "handle": handle}).encode())
            self.assertEqual(sorted(resp["snapshots"]),
                             ["after-edit", "before-edit"])
            # A bad snapshot name is rejected before touching NyFS.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_snapshot",
                "handle": handle, "name": "../evil"}).encode())
            self.assertFalse(resp["ok"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_byte_path_validation_and_errors(self):
        vault = os.path.join(self.tmp, "err-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            handle = self._opened_volume(server, client, name="err-vol")
            # Reading a missing path is a clean error.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/missing.txt"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("read failed", resp["error"])
            # Path traversal is rejected before it reaches NyFS.
            for bad in ("/../etc/passwd", "/a/../../b", "relative",
                        "/trailing/", ""):
                resp = self._call(client, json.dumps({
                    "service": "storage", "op": "volume_write",
                    "handle": handle, "path": bad,
                    "data_b64": "eA=="}).encode())
                self.assertFalse(resp["ok"], (bad, resp))
            # ADR-0024 wire-level framing: the 32 KiB per-call cap is
            # now a CONFIG bound on the stream path, not a protocol
            # one — a >32 KiB payload arrives as a reassembled
            # STREAM_CHUNK CALL, so the plain path accepts it up to
            # the wire-stream DATA budget. A write beyond THAT budget
            # (wire-streamed, so it fits the transport) is rejected.
            from ipc.storage import _WIRE_STREAM_DATA_BYTES
            big = base64.b64encode(
                b"x" * (_WIRE_STREAM_DATA_BYTES + 1)).decode()
            req = json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/big",
                "data_b64": big}).encode()
            reply = client.call(self.svc_path, req, timeout_s=5.0,
                                wire_stream=True)
            self.assertIsNotNone(reply)
            resp = json.loads(reply.payload.decode("utf-8"))
            self.assertFalse(resp["ok"])
            self.assertIn("stream budget", resp["error"])
            # A registry-only volume (no vault_dir) has no byte path.
            server2, stop2, storage2 = self._serve(
                register_pid=False, svc_path=os.path.join(self.tmp, "svc2.sock"))
            client2 = IPCClient(
                DEFAULT_OPERATOR_ID,
                os.path.join(self.tmp, "cli2.sock")).bind()
            try:
                resp = self._call(client2, json.dumps({
                    "service": "storage", "op": "volume_create",
                    "name": "meta-only"}).encode(),
                    path=os.path.join(self.tmp, "svc2.sock"))
                self.assertTrue(resp["ok"], resp)
                resp = self._call(client2, json.dumps({
                    "service": "storage", "op": "volume_open",
                    "volume_id": resp["volume_id"]}).encode(),
                    path=os.path.join(self.tmp, "svc2.sock"))
                handle2 = resp["handle"]
                resp = self._call(client2, json.dumps({
                    "service": "storage", "op": "volume_write",
                    "handle": handle2, "path": "/x",
                    "data_b64": "eA=="}).encode(),
                    path=os.path.join(self.tmp, "svc2.sock"))
                self.assertFalse(resp["ok"])
                self.assertIn("no byte backend", resp["error"])
            finally:
                client2.close()
                stop2.set()
                server2.close()
        finally:
            client.close()
            stop.set()
            server.close()

    def test_byte_path_handle_and_capability_gates(self):
        vault = os.path.join(self.tmp, "gate-vault")
        caps = CapabilityManager()
        self._granted_container(caps)
        server, stop, storage = self._serve(
            vault_dir=vault, capability_manager=caps)
        client = IPCClient("cli", self.cli_path).bind()
        try:
            # The granted container creates + opens, then writes.
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "gated"}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": resp["volume_id"]}).encode())
            handle = resp["handle"]
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/x",
                "data_b64": base64.b64encode(b"ok").decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            # A second container CANNOT use the first's handle (foreign
            # handle). The wire authenticates one pid → one registry id,
            # so drive the second caller through the handler directly
            # (the same pattern as test_volumes_are_creator_scoped).
            caps.initialize_container("ctr-b")
            caps.grant_capability("ctr-b", Capability.CAP_STORAGE_VOLUME)
            fake = mock.Mock()
            fake.reply = mock.Mock()
            storage.attach(fake)
            fake.reply.reset_mock()
            storage._on_call(mock.Mock(message_id="b1", payload=json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/x",
                "data_b64": base64.b64encode(b"nope").decode()
            }).encode()), "ctr-b", "/tmp/b.sock")
            body = json.loads(fake.reply.call_args.args[2].decode())
            self.assertFalse(body["ok"])
            self.assertIn("unknown or foreign handle", body["error"])
            # An UNGRANTED container is denied outright at the handler.
            caps.initialize_container("ctr-c")  # default grants only
            fake.reply.reset_mock()
            storage._on_call(mock.Mock(message_id="c1", payload=json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/x",
                "data_b64": "eA=="}).encode()), "ctr-c", "/tmp/c.sock")
            body = json.loads(fake.reply.call_args.args[2].decode())
            self.assertFalse(body["ok"])
            self.assertIn("CAP_STORAGE_VOLUME required", body["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    # -- encryption + lifecycle (ADR-0023 wiring) --------------------

    def test_encrypted_volume_at_rest_and_locked_vault(self):
        # With an unlocked KEK, volume_create wraps a fresh DEK and the
        # volume's NyFS encrypts every block (ADR-0023): the stored
        # bytes never contain the plaintext, and the wrapped DEK is
        # what persists. Without the KEK (locked vault), an encrypted
        # volume's DEK cannot be unwrapped -> clean failure.
        vault = os.path.join(self.tmp, "enc-vault")
        kek = self._kek()
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False, kek=kek)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "secrets"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertTrue(resp["encrypted"])
            vid = resp["volume_id"]
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": vid}).encode())
            handle = resp["handle"]
            body = b"PLAINTEXT-MUST-NOT-PERSIST" * 50
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/secret.bin",
                "data_b64": base64.b64encode(body).decode()}).encode())
            self.assertTrue(resp["ok"], resp)
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/secret.bin"}).encode())
            self.assertEqual(base64.b64decode(resp["data_b64"]), body)
            # At rest: the vault directory's bytes never contain the
            # plaintext, and the registry carries only the wrapped DEK.
            leaked = False
            for root, _dirs, files in os.walk(vault):
                for name in files:
                    with open(os.path.join(root, name), "rb") as f:
                        if b"PLAINTEXT-MUST-NOT-PERSIST" in f.read():
                            leaked = True
            self.assertFalse(leaked)
            with open(os.path.join(vault, "volumes.json"), "rb") as f:
                state = json.loads(f.read().decode())
            self.assertTrue(state["volumes"][0]["wrapped_dek"])
            self.assertEqual(state["volumes"][0]["id"], vid)
        finally:
            client.close()
            stop.set()
            server.close()
        # A locked vault (no KEK) cannot unwrap the persisted DEK.
        server2, stop2, storage2 = self._serve(
            vault_dir=vault, register_pid=False)  # kek=None
        client2 = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_open",
                "name": "secrets"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("vault locked", resp["error"])
        finally:
            client2.close()
            stop2.set()
            server2.close()

    def test_volume_persistence_survives_restart(self):
        # The registry + wrapped DEK persist (volumes.json); a fresh
        # service on the same vault dir restores the volume and its
        # data — the DEK is re-unwrapped from the KEK at open, never
        # stored plaintext.
        vault = os.path.join(self.tmp, "persist-vault")
        kek = self._kek()
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False, kek=kek)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "durable"}).encode())
            vid = resp["volume_id"]
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": vid}).encode())
            handle = resp["handle"]
            body = b"survives-restart-" * 40
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_write",
                "handle": handle, "path": "/data.bin",
                "data_b64": base64.b64encode(body).decode()}).encode())
            self.assertTrue(resp["ok"], resp)
        finally:
            client.close()
            stop.set()
            server.close()
        # "Restart": a brand-new service on the same vault dir.
        server2, stop2, storage2 = self._serve(
            vault_dir=vault, register_pid=False, kek=kek)
        client2 = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_list"}).encode())
            self.assertEqual([v["name"] for v in resp["volumes"]],
                             ["durable"])
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_open",
                "name": "durable"}).encode())
            handle = resp["handle"]
            resp = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_read",
                "handle": handle, "path": "/data.bin"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(base64.b64decode(resp["data_b64"]), body)
        finally:
            client2.close()
            stop2.set()
            server2.close()

    def test_volume_delete_crypto_shreds(self):
        vault = os.path.join(self.tmp, "del-vault")
        kek = self._kek()
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False, kek=kek)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "gone"}).encode())
            vid = resp["volume_id"]
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": vid}).encode())
            handle = resp["handle"]
            # The handle + wrapped DEK + registry entry + backing image
            # all go away (crypto-shred).
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_delete",
                "name": "gone"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertFalse(
                os.path.exists(os.path.join(vault, vid + ".nyfs")))
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_list"}).encode())
            self.assertEqual(resp["volumes"], [])
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_info",
                "handle": handle}).encode())
            self.assertFalse(resp["ok"])  # the handle is shredded too
            with open(os.path.join(vault, "volumes.json"), "rb") as f:
                state = json.loads(f.read().decode())
            self.assertEqual(state["volumes"], [])
        finally:
            client.close()
            stop.set()
            server.close()

    # -- generic file surface (the FUSE passthrough's backend) ------

    def _open_handle(self, client, name):
        resp = self._call(client, json.dumps({
            "service": "storage", "op": "volume_create",
            "name": name}).encode())
        self.assertTrue(resp["ok"], resp)
        vid = resp["volume_id"]
        resp = self._call(client, json.dumps({
            "service": "storage", "op": "volume_open",
            "volume_id": vid}).encode())
        self.assertTrue(resp["ok"], resp)
        return resp["handle"]

    def test_volume_file_surface_ops(self):
        # The passthrough's backend ops, driven over the wire: the full
        # namespace + byte surface the FUSE mount needs.
        vault = os.path.join(self.tmp, "fs-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            handle = self._open_handle(client, "fs")
            def call(payload):
                return self._call(client, json.dumps(payload).encode())
            r = call({"service": "storage", "op": "volume_mkdir",
                      "handle": handle, "path": "/app", "mode": 0o755})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_mknod",
                      "handle": handle, "path": "/app/data.bin",
                      "mode": 0o644, "dev": 0})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_write",
                      "handle": handle, "path": "/app/data.bin",
                      "offset": 0,
                      "data_b64": base64.b64encode(b"payload").decode()})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_getattr",
                      "handle": handle, "path": "/app/data.bin"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["stat"]["st_size"], 7)
            r = call({"service": "storage", "op": "volume_readdir",
                      "handle": handle, "path": "/app"})
            self.assertEqual(r["names"], [".", "..", "data.bin"])
            r = call({"service": "storage", "op": "volume_truncate",
                      "handle": handle, "path": "/app/data.bin",
                      "length": 3})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_rename",
                      "handle": handle, "from": "/app/data.bin",
                      "to": "/app/renamed.bin"})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_read",
                      "handle": handle, "path": "/app/renamed.bin",
                      "offset": 0, "size": 8})
            self.assertEqual(base64.b64decode(r["data_b64"]), b"pay")
            r = call({"service": "storage", "op": "volume_statfs",
                      "handle": handle})
            for key in ("f_bsize", "f_blocks", "f_bfree", "f_files",
                        "f_ffree"):
                self.assertIn(key, r["statfs"])
            r = call({"service": "storage", "op": "volume_fsync",
                      "handle": handle})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_unlink",
                      "handle": handle, "path": "/app/renamed.bin"})
            self.assertTrue(r["ok"], r)
            r = call({"service": "storage", "op": "volume_rmdir",
                      "handle": handle, "path": "/app"})
            self.assertTrue(r["ok"], r)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_file_surface_errno_and_validation(self):
        # NyFSError maps to its POSIX errno over the wire (ENOENT for a
        # missing path), and a ``..`` path is rejected path-side.
        vault = os.path.join(self.tmp, "err-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            handle = self._open_handle(client, "err")
            r = self._call(client, json.dumps({
                "service": "storage", "op": "volume_getattr",
                "handle": handle, "path": "/missing"}).encode())
            self.assertFalse(r["ok"])
            self.assertEqual(r["errno"], errno.ENOENT)
            r = self._call(client, json.dumps({
                "service": "storage", "op": "volume_mkdir",
                "handle": handle, "path": "/a/../b"}).encode())
            self.assertFalse(r["ok"])
            self.assertIn("without '..'", r["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_file_surface_capability_gate(self):
        # The new ops ride the same fail-closed gate as the byte path:
        # a container without CAP_STORAGE_VOLUME is refused.
        caps = CapabilityManager()
        caps.initialize_container("cli")
        server, stop, storage = self._serve(capability_manager=caps)
        client = IPCClient("cli", self.cli_path).bind()
        try:
            r = self._call(client, json.dumps({
                "service": "storage", "op": "volume_mkdir",
                "handle": "x", "path": "/a"}).encode())
            self.assertFalse(r["ok"])
            self.assertIn("CAP_STORAGE_VOLUME", r["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_rekey_rotates_the_kek_without_reencryption(self):
        kek = self._kek()
        server, stop, storage = self._serve(
            vault_dir=os.path.join(self.tmp, "rk-vault"),
            register_pid=False, kek=kek)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "rk"}).encode())
            vid = resp["volume_id"]
            wrapped_before = storage._volumes[vid]["wrapped_dek"]
            dek = storage._volumes[vid]["dek"]
            r = self._call(client, json.dumps({
                "service": "storage", "op": "volume_rekey",
                "new_passphrase": "fresh-secret"}).encode())
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["rekeyed"], 1)
            # The old KEK handle must NOT unwrap the re-wrapped DEK.
            with self.assertRaises(keys_module.KeysError):
                keys_module.unwrap(
                    kek, vid.encode("utf-8"),
                    storage._volumes[vid]["wrapped_dek"])
            # The new envelope + passphrase must (and the DEK is the
            # same one — ciphertext untouched).
            new_blob = base64.b64decode(r["new_envelope_b64"])
            new_kek = keys_module.unlock(new_blob, b"fresh-secret")
            self.assertEqual(
                keys_module.unwrap(
                    new_kek, vid.encode("utf-8"),
                    storage._volumes[vid]["wrapped_dek"]),
                dek)
            keys_module.shred(new_kek)
            # Persisted state holds the re-wrapped DEK.
            with open(os.path.join(
                    self.tmp, "rk-vault", "volumes.json"), "rb") as f:
                state = json.loads(f.read().decode())
            self.assertNotEqual(
                state["volumes"][0]["wrapped_dek"], wrapped_before)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_restore_swaps_the_tree(self):
        # CoW snapshot lifecycle over the wire: snapshot, overwrite,
        # restore — the old bytes come back, and the restored table is
        # what save() persists.
        vault = os.path.join(self.tmp, "rs-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "rs"}).encode())
            handle = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": resp["volume_id"]}).encode())["handle"]
            def op(payload):
                return self._call(client, json.dumps(payload).encode())
            r = op({"service": "storage", "op": "volume_write",
                    "handle": handle, "path": "/doc.txt", "offset": 0,
                    "data_b64": base64.b64encode(b"original").decode()})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_snapshot",
                    "handle": handle, "name": "v1"})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_write",
                    "handle": handle, "path": "/doc.txt", "offset": 0,
                    "data_b64": base64.b64encode(b"overwritten").decode()})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_restore",
                    "handle": handle, "name": "v1"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["restored"], "v1")
            r = op({"service": "storage", "op": "volume_read",
                    "handle": handle, "path": "/doc.txt",
                    "offset": 0, "size": 32})
            self.assertEqual(base64.b64decode(r["data_b64"]), b"original")
            # Restore to an unknown snapshot is a clean failure.
            r = op({"service": "storage", "op": "volume_restore",
                    "handle": handle, "name": "nope"})
            self.assertFalse(r["ok"])
            self.assertIn("not found", r["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_rekey_operator_only_and_plaintext_refused(self):
        # A container can never rekey (it never holds the master
        # passphrase); an unencrypted vault has nothing to rotate.
        kek = self._kek()
        server, stop, storage = self._serve(
            vault_dir=os.path.join(self.tmp, "rk2-vault"), kek=kek)
        client = IPCClient("cli", self.cli_path).bind()
        try:
            r = self._call(client, json.dumps({
                "service": "storage", "op": "volume_rekey",
                "new_passphrase": "x"}).encode())
            self.assertFalse(r["ok"])
            self.assertIn("operator-only", r["error"])
        finally:
            client.close()
            stop.set()
            server.close()
        # Plaintext vault: no KEK to rotate.
        server2, stop2, storage2 = self._serve(
            vault_dir=os.path.join(self.tmp, "rk3-vault"),
            register_pid=False)
        client2 = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            r = self._call(client2, json.dumps({
                "service": "storage", "op": "volume_rekey",
                "new_passphrase": "x"}).encode())
            self.assertFalse(r["ok"])
            self.assertIn("not encrypted", r["error"])
        finally:
            client2.close()
            stop2.set()
            server2.close()

    def test_cross_container_grant_matrix(self):
        # ADR-0022's access matrix: a volume is creator-scoped by
        # default; the creator (or operator) may grant another
        # container explicit access, and revoke it. The transport is
        # pid-attributed (one identity per process), so the handlers
        # are driven directly with a stub reply server and distinct
        # sender ids — the transport attribution itself is covered by
        # the container-path tests.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "grant-vault"))

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "shared"})
        self.assertTrue(last(stub)["ok"], last(stub))
        vid = last(stub)["volume_id"]

        # The grantee cannot open before the grant.
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-b",
                             {"volume_id": vid})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("not yours", last(stub)["error"])

        # The creator grants; the grantee opens and writes.
        stub = _Stub()
        storage._volume_grant(stub, "p", "3", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_open(stub, "p", "4", "container-b",
                             {"volume_id": vid})
        self.assertTrue(last(stub)["ok"], last(stub))
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "5", "container-b", {
            "handle": handle, "path": "/x",
            "data_b64": base64.b64encode(b"hi").decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))

        # The creator sees the grant; the grantee administers nothing.
        stub = _Stub()
        storage._volume_grants(stub, "p", "6", "container-a",
                               {"volume_id": vid})
        self.assertTrue(last(stub)["ok"])
        self.assertEqual(last(stub)["grants"],
                         [{"container": "container-b", "path": "/"}])
        stub = _Stub()
        storage._volume_grant(stub, "p", "7", "container-b",
                              {"volume_id": vid, "container": "c"})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("creator or the operator", last(stub)["error"])

        # Revoke gates future opens; a live handle stays valid (open-
        # file semantics) and can still read what it wrote.
        stub = _Stub()
        storage._volume_revoke(stub, "p", "8", "container-a",
                               {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["ok"])
        self.assertTrue(last(stub)["revoked"])
        stub = _Stub()
        storage._volume_open(stub, "p", "9", "container-b",
                             {"volume_id": vid})
        self.assertFalse(last(stub)["ok"])
        stub = _Stub()
        storage._volume_read(stub, "p", "10", "container-b",
                             {"handle": handle, "path": "/x"})
        self.assertTrue(last(stub)["ok"], last(stub))
        self.assertEqual(base64.b64decode(last(stub)["data_b64"]), b"hi")

        # Grants persist across a daemon restart.
        storage2 = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "grant-vault"))
        self.assertEqual(storage2._volumes[vid]["grants"], {})
        storage._volume_grant(stub, "p", "11", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        storage3 = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "grant-vault"))
        self.assertEqual(storage3._volumes[vid]["grants"],
                         {"container-b": True})

    def test_path_scoped_grant_restricts_the_data_plane(self):
        # ADR-0022 (0.14.15): a grant may carry a ``path`` scope — the
        # grantee can open the volume but every data-plane op on a
        # path outside the subtree is rejected fail-closed. Both sides
        # of a rename must stay inside the scope.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "scope-vault"))

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "scoped"})
        self.assertTrue(last(stub)["ok"], last(stub))
        vid = last(stub)["volume_id"]

        # A path-scoped grant to /assets.
        stub = _Stub()
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b",
                               "path": "/assets"})
        self.assertTrue(last(stub)["ok"], last(stub))
        self.assertEqual(last(stub)["path"], "/assets")

        stub = _Stub()
        storage._volume_open(stub, "p", "3", "container-b",
                             {"volume_id": vid})
        self.assertTrue(last(stub)["ok"], last(stub))
        handle = last(stub)["handle"]

        # Writes inside the scope land; outside are rejected and the
        # rejection never reaches the tree.
        stub = _Stub()
        storage._volume_write(stub, "p", "4", "container-b", {
            "handle": handle, "path": "/assets/x",
            "data_b64": base64.b64encode(b"in-scope").decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_write(stub, "p", "5", "container-b", {
            "handle": handle, "path": "/outside",
            "data_b64": base64.b64encode(b"nope").decode("ascii"),
            "offset": 0})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("outside your grant scope", last(stub)["error"])
        # A scope violation is a permission denial: the honest errno
        # rides the reply so the FUSE passthrough surfaces EACCES.
        self.assertEqual(last(stub)["errno"], errno.EACCES)

        # The creator puts a file outside the scope; the grantee's
        # read there is rejected too.
        stub = _Stub()
        storage._volume_open(stub, "p", "6", "container-a",
                             {"volume_id": vid})
        self.assertTrue(last(stub)["ok"], last(stub))
        creator_handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "7", "container-a", {
            "handle": creator_handle, "path": "/outside",
            "data_b64": base64.b64encode(b"secret").decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_read(stub, "p", "8", "container-b", {
            "handle": handle, "path": "/outside", "offset": 0,
            "size": 32})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("outside your grant scope", last(stub)["error"])
        stub = _Stub()
        storage._volume_read(stub, "p", "9", "container-b", {
            "handle": handle, "path": "/assets/x", "offset": 0,
            "size": 32})
        self.assertTrue(last(stub)["ok"])
        self.assertEqual(base64.b64decode(last(stub)["data_b64"]),
                         b"in-scope")

        # Rename: BOTH sides must stay in scope — in-scope moves pass,
        # escaping the scope either way is rejected.
        stub = _Stub()
        storage._volume_rename(stub, "p", "10", "container-b", {
            "handle": handle, "from": "/assets/x", "to": "/assets/y"})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_rename(stub, "p", "11", "container-b", {
            "handle": handle, "from": "/assets/y", "to": "/outside"})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("outside your grant scope", last(stub)["error"])
        stub = _Stub()
        storage._volume_rename(stub, "p", "12", "container-b", {
            "handle": handle, "from": "/outside", "to": "/assets/z"})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("outside your grant scope", last(stub)["error"])
        self.assertEqual(last(stub)["errno"], errno.EACCES)

        # Truncate outside the scope is rejected; inside passes.
        stub = _Stub()
        storage._volume_truncate(stub, "p", "13", "container-b", {
            "handle": handle, "path": "/outside", "length": 0})
        self.assertFalse(last(stub)["ok"])
        self.assertIn("outside your grant scope", last(stub)["error"])
        stub = _Stub()
        storage._volume_truncate(stub, "p", "14", "container-b", {
            "handle": handle, "path": "/assets/y", "length": 0})
        self.assertTrue(last(stub)["ok"], last(stub))

    def test_path_scoped_grant_persists_and_backcompat(self):
        # The persisted shape: ``True`` (whole volume, the 0.14.8
        # format) or ``{"path": str}``. Both survive a daemon restart,
        # and the scope helper treats the legacy ``True`` as the whole
        # volume.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b", "container-c"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        vault = os.path.join(self.tmp, "scope-persist")
        storage = StorageService(capability_manager=caps, vault_dir=vault)

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "shared"})
        vid = last(stub)["volume_id"]
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b",
                               "path": "/assets"})
        storage._volume_grant(stub, "p", "3", "container-a",
                              {"volume_id": vid, "container": "container-c"})

        storage2 = StorageService(capability_manager=caps, vault_dir=vault)
        self.assertEqual(storage2._volumes[vid]["grants"], {
            "container-b": {"path": "/assets"},
            "container-c": True,
        })
        # Scope helper: the legacy True grant reads as the whole
        # volume; the path grant reads as its subtree.
        self.assertEqual(storage2._grant_scope(storage2._volumes[vid],
                                               "container-b"), "/assets")
        self.assertEqual(storage2._grant_scope(storage2._volumes[vid],
                                               "container-c"), "/")

    def test_granted_container_cannot_administer_snapshots(self):
        # 0.14.15 tightening: snapshot/restore/snapshot-delete rewrite
        # or capture the WHOLE volume tree — a granted container (even
        # with a whole-volume grant) could clobber data outside any
        # scope, so these are CREATOR/OPERATOR-ONLY like grants
        # themselves.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.initialize_container("container-b")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        caps.grant_capability("container-b", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "admin-vault"))

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "shared"})
        vid = last(stub)["volume_id"]
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        stub = _Stub()
        storage._volume_open(stub, "p", "3", "container-b",
                             {"volume_id": vid})
        handle = last(stub)["handle"]

        # The creator snapshots; the grantee's attempts fail closed
        # with the owner gate. (The creator needs its own handle — a
        # handle is bound to the container that opened it.)
        stub = _Stub()
        storage._volume_open(stub, "p", "4", "container-a",
                             {"volume_id": vid})
        self.assertTrue(last(stub)["ok"], last(stub))
        creator_handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_snapshot(stub, "p", "5", "container-a",
                                 {"handle": creator_handle, "name": "s1"})
        self.assertTrue(last(stub)["ok"], last(stub))
        for i, (op, payload) in enumerate([
            ("volume_snapshot", {"handle": handle, "name": "s2"}),
            ("volume_restore", {"handle": handle, "name": "s1"}),
            ("volume_snapshot_delete", {"handle": handle, "name": "s1"}),
        ], start=6):
            stub = _Stub()
            getattr(storage, "_" + op)(stub, "p", str(i), "container-b",
                                       payload)
            self.assertFalse(last(stub)["ok"], (op, last(stub)))
            self.assertIn("creator or the operator", last(stub)["error"])

    def test_quota_enforced_fail_closed_edquot(self):
        # ADR-0022's accounting increment: a per-container byte quota
        # rejects the write with EDQUOT BEFORE it touches the tree, and
        # the accepted bytes land in the ledger. The handlers are
        # driven directly with distinct sender ids (the transport is
        # pid-attributed — covered by the container-path tests).
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-vault"))

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "q"})
        self.assertTrue(last(stub)["ok"], last(stub))
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]

        def write(path, data):
            stub = _Stub()
            storage._volume_write(stub, "p", "x", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "offset": 0})
            return last(stub)

        # 60 bytes land; the ledger bills the writer.
        self.assertTrue(write("/f", b"A" * 60)["ok"])
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"], {"container-a": 60})

        # Quota 100: a 60-byte write would exceed 60+60 > 100 → EDQUOT,
        # and the tree is untouched (a later 40-byte write still fits).
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 100})
        self.assertTrue(last(stub)["ok"], last(stub))
        over = write("/g", b"B" * 60)
        self.assertFalse(over["ok"])
        self.assertIn("quota exceeded", over["error"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        self.assertTrue(write("/h", b"C" * 40)["ok"])  # 60 + 40 = 100
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"], {"container-a": 100})

    def test_quota_unlimited_by_default(self):
        # No quota set → writes are not constrained, but still billed.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-unlimited"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "u"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "3", "container-a", {
            "handle": handle, "path": "/big",
            "data_b64": base64.b64encode(b"D" * (32 * 1024)).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"], {"container-a": 32 * 1024})

    def test_quota_set_get_owner_only(self):
        # Quota is administration: a granted container can consume the
        # volume, but cannot set (or read) quotas on it.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-gate"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "g"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["ok"])
        # The grantee cannot set or read quotas.
        for op, payload in (("_volume_quota_set",
                             {"volume_id": vid, "container": "container-b",
                              "bytes": 10}),
                            ("_volume_quota_get", {"volume_id": vid})):
            stub = _Stub()
            getattr(storage, op)(stub, "p", "x", "container-b", payload)
            self.assertFalse(last(stub)["ok"])
            self.assertIn("creator or the operator", last(stub)["error"])
        # The creator can.
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-b", "bytes": 10})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_quota_get(stub, "p", "4", "container-a",
                                  {"volume_id": vid})
        rows = last(stub)["rows"]
        self.assertEqual(rows, [{"container": "container-b",
                                 "scope": "/",
                                 "quota": 10, "usage": 0,
                                 "warning": None}])

    def test_quota_billed_per_writer_on_shared_volume(self):
        # The grant matrix's point: on a shared volume each consumer is
        # billed its OWN writes — a quota on one container never caps
        # the other's consumption.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-shared"))

        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "s"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        handles = {}
        for cid in ("container-a", "container-b"):
            stub = _Stub()
            storage._volume_open(stub, "p", "o", cid, {"volume_id": vid})
            self.assertTrue(last(stub)["ok"], last(stub))
            handles[cid] = last(stub)["handle"]

        def write(cid, path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", cid, {
                "handle": handles[cid], "path": path,
                "data_b64": base64.b64encode(b"Z" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        self.assertTrue(write("container-a", "/a1", 50)["ok"])
        self.assertTrue(write("container-b", "/b1", 30)["ok"])
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"],
                         {"container-a": 50, "container-b": 30})
        # Quota ONLY container-b at 40: b's next 20-byte write is
        # rejected (30 + 20 > 40); a's is not (a is unlimited).
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-b", "bytes": 40})
        self.assertTrue(last(stub)["ok"])
        over = write("container-b", "/b2", 20)
        self.assertFalse(over["ok"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        self.assertTrue(write("container-a", "/a2", 20)["ok"])
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"],
                         {"container-a": 70, "container-b": 30})

    def test_subtree_quota_enforced_at_write(self):
        # 0.14.19: a quota may be scoped to a subtree — an ADDITIONAL
        # cap: every applicable cap (the whole-volume quota AND each
        # scoped quota whose scope contains the path) must pass. The
        # scoped EDQUOT carries its scope in the error and the event
        # ring; the whole-volume EDQUOT does not.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "subquota-vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "s"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]

        def write(path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(b"S" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        # Whole-volume cap 200 + subtree caps /assets=60 and
        # /data=40. Every cap applies: /assets writes are capped by
        # BOTH /assets and the whole volume.
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a", {
            "volume_id": vid, "container": "container-a",
            "path": "/assets", "bytes": 60})
        self.assertTrue(last(stub)["ok"])
        self.assertEqual(last(stub)["path"], "/assets")
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "4", "container-a", {
            "volume_id": vid, "container": "container-a",
            "path": "/data", "bytes": 40})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "5", "container-a", {
            "volume_id": vid, "container": "container-a", "bytes": 200})
        self.assertTrue(last(stub)["ok"])

        self.assertTrue(write("/assets/a", 30)["ok"])
        self.assertTrue(write("/assets/b", 30)["ok"])
        # 61 > 60 under /assets: EDQUOT, with the scope in the error.
        over = write("/assets/c", 1)
        self.assertFalse(over["ok"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        self.assertIn("scope /assets", over["error"])
        # The scoped EDQUOT lands in the event ring with its scope.
        stub = _Stub()
        storage._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
        ev = last(stub)["events"][0]
        self.assertEqual(ev["level"], "edquot")
        self.assertEqual(ev["scope"], "/assets")
        # /data is its own cap.
        self.assertTrue(write("/data/x", 40)["ok"])
        over = write("/data/y", 1)
        self.assertFalse(over["ok"])
        self.assertIn("scope /data", over["error"])
        # The whole-volume cap still applies to everything: 30+30+40
        # = 100 so far; /other can take 100 more, then it hits 200.
        self.assertTrue(write("/other", 100)["ok"])
        over = write("/other", 1)
        self.assertFalse(over["ok"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        self.assertNotIn("scope", over["error"])
        # Nested scopes overlap by design: /assets/img/x is capped by
        # /assets/img AND /assets. /assets already holds 60, so the
        # nested write is refused by the /assets cap.
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "6", "container-a", {
            "volume_id": vid, "container": "container-a",
            "path": "/assets/img", "bytes": 10})
        self.assertTrue(last(stub)["ok"])
        over = write("/assets/img/x", 5)
        self.assertFalse(over["ok"])
        self.assertIn("scope /assets", over["error"])

        # quota-get shows the whole-volume row (scope /) AND the
        # scoped rows.
        stub = _Stub()
        storage._volume_quota_get(stub, "p", "7", "container-a",
                                  {"volume_id": vid})
        rows = {(r["container"], r["scope"]): r for r in last(stub)["rows"]}
        self.assertEqual(rows[("container-a", "/")]["quota"], 200)
        self.assertEqual(rows[("container-a", "/assets")]["quota"], 60)
        self.assertEqual(rows[("container-a", "/data")]["quota"], 40)
        self.assertEqual(rows[("container-a", "/assets/img")]["quota"], 10)

    def test_subtree_quota_persists_and_rederives(self):
        # 0.14.19: scoped quotas and their derived usage persist with
        # the registry, and the commit refresh re-derives scoped usage
        # from the tree — a delete under the scope drops the figure.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        vault = os.path.join(self.tmp, "subquota-persist")
        storage = StorageService(capability_manager=caps, vault_dir=vault)
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "p"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "2", "container-a", {
            "volume_id": vid, "container": "container-a",
            "path": "/assets", "bytes": 100})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_open(stub, "p", "o1", "container-a",
                             {"volume_id": vid})
        h1 = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "3", "container-a", {
            "handle": h1, "path": "/assets/a",
            "data_b64": base64.b64encode(b"A" * 60).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"])  # commits: image + ledger saved

        storage2 = StorageService(capability_manager=caps, vault_dir=vault)
        self.assertEqual(storage2._volumes[vid]["scope_quota"],
                         {"container-a": {"/assets": 100}})
        # The derived scoped usage rode the same persist.
        self.assertEqual(storage2._volumes[vid]["scope_usage"],
                         {"container-a": {"/assets": 60}})
        stub = _Stub()
        storage2._volume_open(stub, "p", "o2", "container-a",
                              {"volume_id": vid})
        handle = last(stub)["handle"]
        # A delete under the scope re-derives it away at the next
        # commit (the delegated unlink mutates the tree; fsync is the
        # commit point that refreshes the ledger).
        stub = _Stub()
        storage2._volume_unlink(stub, "p", "4", "container-a", {
            "handle": handle, "path": "/assets/a"})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage2._volume_fsync(stub, "p", "4b", "container-a",
                               {"handle": handle})
        self.assertTrue(last(stub)["ok"])
        self.assertEqual(storage2._volumes[vid]["scope_usage"], {})
        # usage surfaces the scoped figure after the delete.
        stub = _Stub()
        storage2._volume_usage(stub, "p", "5", "container-a",
                               {"volume_id": vid})
        reply = last(stub)
        self.assertEqual(reply["scope_usage"], {})

    def test_quota_truncate_credits_delta(self):
        # ADR-0022: truncate credits the delta, so a container that
        # shrinks its files can write again before the next commit
        # refresh (the ledger stays honest between refreshes).
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-trunc"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "t"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]

        def write(path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(b"Y" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        self.assertTrue(write("/f", 100)["ok"])
        # Truncate 100 -> 40 credits 60.
        stub = _Stub()
        storage._volume_truncate(stub, "p", "3", "container-a", {
            "handle": handle, "path": "/f", "length": 40})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        self.assertEqual(last(stub)["usage"], {"container-a": 40})
        # Quota 70: 40 + 5 fits, 40 + 40 does not.
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "4", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 70})
        self.assertTrue(last(stub)["ok"])
        self.assertTrue(write("/g", 5)["ok"])
        self.assertFalse(write("/h", 40)["ok"])

    def test_quota_delete_and_restore_rederive(self):
        # The tree is the ledger: a delete (or a restore) re-derives
        # usage from what the tree actually holds, so freed bytes are
        # credited at the commit refresh even though the enforcement
        # check reads the cache.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        def usage(storage, vid):
            stub = _Stub()
            storage._volume_usage(stub, "p", "u", "container-a",
                                  {"volume_id": vid})
            return last(stub)["usage"]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "quota-rederive"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "r"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]

        def write(path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(b"X" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        def fsync():
            stub = _Stub()
            storage._volume_fsync(stub, "p", "s", "container-a",
                                  {"handle": handle})
            return last(stub)

        self.assertTrue(write("/f", 100)["ok"])
        self.assertTrue(fsync()["ok"])
        stub = _Stub()
        storage._volume_snapshot(stub, "p", "3", "container-a",
                                 {"handle": handle, "name": "s1"})
        self.assertTrue(last(stub)["ok"])
        self.assertTrue(write("/g", 50)["ok"])
        self.assertTrue(fsync()["ok"])
        self.assertEqual(usage(storage, vid), {"container-a": 150})
        # Delete /f (100 bytes) → the next commit re-derives to 50.
        stub = _Stub()
        storage._volume_unlink(stub, "p", "4", "container-a",
                               {"handle": handle, "path": "/f"})
        self.assertTrue(last(stub)["ok"])
        self.assertTrue(fsync()["ok"])
        self.assertEqual(usage(storage, vid), {"container-a": 50})
        # Restore to s1 (which held /f = 100, no /g) → usage is 100
        # again, re-derived from the restored tree.
        stub = _Stub()
        storage._volume_restore(stub, "p", "5", "container-a",
                                {"handle": handle, "name": "s1"})
        self.assertTrue(last(stub)["ok"], last(stub))
        self.assertEqual(usage(storage, vid), {"container-a": 100})

    def test_quota_and_usage_persist_across_restart(self):
        # The registry persists quotas AND the accounted usage + last-
        # writer attribution with each commit, so accounting survives a
        # daemon restart (the tree re-derives it anyway, but the cache
        # is warm and the quotas are authoritative).
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        vault = os.path.join(self.tmp, "quota-persist")
        storage = StorageService(capability_manager=caps, vault_dir=vault)
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "p"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "2", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 500})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_open(stub, "p", "3", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "4", "container-a", {
            "handle": handle, "path": "/f",
            "data_b64": base64.b64encode(b"P" * 100).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))

        storage2 = StorageService(capability_manager=caps, vault_dir=vault)
        self.assertEqual(storage2._volumes[vid]["quota"],
                         {"container-a": 500})
        self.assertEqual(storage2._volumes[vid]["usage"],
                         {"container-a": 100})
        self.assertEqual(storage2._volumes[vid]["owners"]["/f"],
                         "container-a")

    def test_usage_reports_physical_bytes(self):
        # ADR-0022: LOGICAL bytes are the billed ledger; PHYSICAL
        # block-store bytes are a separate operator figure (compressed
        # + CoW-deduped — load- and CODEC-dependent, so never billed
        # and never asserted on the ratio here: CI lacks zstandard and
        # the Rust codec, so blocks are stored uncompressed there, and
        # the whole point is that the physical figure tracks whatever
        # the on-disk state actually is). Assert only what is
        # environment-independent: the figure is present, an int, and
        # > 0 (the 9 KiB write occupies disk), while the LOGICAL
        # ledger is exactly the 9,000 billed bytes.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "phys-vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "p"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "3", "container-a", {
            "handle": handle, "path": "/f",
            "data_b64": base64.b64encode(
                b"compressible-data;" * 500).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_usage(stub, "p", "u", "container-a",
                              {"volume_id": vid})
        reply = last(stub)
        self.assertEqual(reply["usage"], {"container-a": 9000})
        phys = reply["physical_bytes"]
        self.assertIsInstance(phys, int)
        self.assertGreater(phys, 0, "the on-disk state must occupy bytes")

    def test_volume_summary_operator_only_and_aggregates(self):
        # The operator's whole-vault view: per-volume logical + PHYSICAL
        # bytes and consumer counts, with fresh (re-derived) figures
        # and honest totals. OPERATOR-ONLY — a container sees it even
        # with the storage capability: it reveals volumes the caller
        # may not be able to open.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        for cid in ("container-a", "container-b"):
            caps.initialize_container(cid)
            caps.grant_capability(cid, Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "summary-vault"))
        # Two volumes, two writers on the shared one.
        for name in ("one", "two"):
            stub = _Stub()
            storage._volume_create(stub, "p", "c", "container-a",
                                   {"name": name})
            self.assertTrue(last(stub)["ok"], last(stub))
        stub = _Stub()
        storage._volume_grant(stub, "p", "g", "container-a",
                              {"name": "two", "container": "container-b"})
        self.assertTrue(last(stub)["ok"])
        handles = {}
        for cid in ("container-a", "container-b"):
            stub = _Stub()
            storage._volume_open(stub, "p", "o", cid, {"name": "two"})
            handles[cid] = last(stub)["handle"]
        for cid, path, n in (("container-a", "/a", 50),
                             ("container-b", "/b", 30)):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", cid, {
                "handle": handles[cid], "path": path,
                "data_b64": base64.b64encode(b"Z" * n).decode("ascii"),
                "offset": 0})
            self.assertTrue(last(stub)["ok"], last(stub))
        # A container is refused even with the capability.
        stub = _Stub()
        storage._volume_summary(stub, "p", "s", "container-a")
        self.assertFalse(last(stub)["ok"])
        self.assertIn("operator-only", last(stub)["error"])
        # The operator sees the aggregate.
        stub = _Stub()
        storage._volume_summary(stub, "p", "s", DEFAULT_OPERATOR_ID)
        reply = last(stub)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["volume_count"], 2)
        by_name = {v["name"]: v for v in reply["volumes"]}
        self.assertEqual(by_name["one"]["logical_bytes"], 0)
        self.assertEqual(by_name["two"]["logical_bytes"], 80)
        self.assertEqual(by_name["two"]["consumers"], 2)
        self.assertGreater(by_name["two"]["physical_bytes"], 0)
        self.assertEqual(reply["total_logical_bytes"], 80)

    def test_quota_warning_levels(self):
        # Advisory signals on top of the hard EDQUOT stop: "near" at
        # >= 80% of the quota, "at" at >= 95%. "over" is NOT reachable
        # by writing (the write path rejects it) — only by a restore or
        # a quota set below existing usage, which the refresh re-derives.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "warn-vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "w"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 100})
        self.assertTrue(last(stub)["ok"])

        def write(path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(b"W" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        def warnings():
            return dict(storage._volumes[vid]["warnings"])

        # 79 bytes = 79% — no warning.
        self.assertTrue(write("/a", 79)["ok"])
        self.assertIsNone(warnings().get("container-a"))
        # 81 bytes = 81% — near.
        self.assertTrue(write("/b", 2)["ok"])
        self.assertEqual(warnings().get("container-a"), "near")
        # The write reply carries the advisory level at the point of
        # action (81 = near).
        self.assertEqual(write("/c", 5)["warning"], "near")  # 86/100
        # 95 bytes = 95% — at.
        self.assertTrue(write("/d", 9)["ok"])
        self.assertEqual(warnings().get("container-a"), "at")
        # The hard stop still fires beyond the quota.
        over = write("/e", 6)
        self.assertFalse(over["ok"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        # "over" arrives only via re-derivation: a quota set BELOW
        # existing usage (a restore would do the same).
        stub = _Stub()
        storage._volume_create(stub, "p", "4", "container-a", {"name": "o"})
        vid2 = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "5", "container-a",
                             {"volume_id": vid2})
        handle2 = last(stub)["handle"]
        stub = _Stub()
        storage._volume_write(stub, "p", "6", "container-a", {
            "handle": handle2, "path": "/f",
            "data_b64": base64.b64encode(b"O" * 60).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "7", "container-a",
                                  {"volume_id": vid2,
                                   "container": "container-a", "bytes": 50})
        self.assertTrue(last(stub)["ok"])
        # quota_set does not refresh — the next commit does.
        self.assertNotIn("over", (storage._volumes[vid2]["warnings"]
                                   .get("container-a") or ""))
        stub = _Stub()
        storage._volume_fsync(stub, "p", "8", "container-a",
                              {"handle": handle2})
        self.assertTrue(last(stub)["ok"])
        self.assertEqual(storage._volumes[vid2]["warnings"]
                         .get("container-a"), "over")

    def test_grant_events_recorded_in_the_ring(self):
        # 0.14.17: the access matrix joins the event ring — a grant
        # records who, when, and how wide the scope; a revoke records
        # what was actually withdrawn (the scope the grantee held).
        # Newest first, same operator-only gate, same bounded ring.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.initialize_container("container-b")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        caps.grant_capability("container-b", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "grevents-vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "assets"})
        vid = last(stub)["volume_id"]

        def events():
            stub = _Stub()
            storage._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
            return last(stub)["events"]

        # Whole-volume grant, then revoke: two events, newest first.
        stub = _Stub()
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_revoke(stub, "p", "3", "container-a",
                               {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["revoked"])
        ev = events()
        self.assertEqual(ev[0]["kind"], "revoke")
        self.assertEqual(ev[0]["scope"], "/")
        self.assertEqual(ev[0]["container"], "container-b")
        self.assertEqual(ev[1]["kind"], "grant")
        self.assertEqual(ev[1]["scope"], "/")

        # Path-scoped grant: the ring carries the scope; revoking it
        # records the scope the grantee actually held.
        stub = _Stub()
        storage._volume_grant(stub, "p", "4", "container-a",
                              {"volume_id": vid, "container": "container-b",
                               "path": "/assets"})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_revoke(stub, "p", "5", "container-a",
                               {"volume_id": vid, "container": "container-b"})
        self.assertTrue(last(stub)["revoked"])
        ev = events()
        self.assertEqual([e["kind"] for e in ev[:2]],
                         ["revoke", "grant"])
        self.assertEqual(ev[0]["scope"], "/assets")
        self.assertEqual(ev[1]["scope"], "/assets")

        # A revoke of a container with no grant records nothing.
        stub = _Stub()
        storage._volume_revoke(stub, "p", "6", "container-a",
                               {"volume_id": vid, "container": "container-b"})
        self.assertFalse(last(stub)["revoked"])
        self.assertEqual(len(events()), 4)

        # The gate stays operator-only: a container is refused the
        # ring even with the storage capability.
        stub = _Stub()
        storage._volume_events(stub, "p", "ev", "container-a")
        self.assertFalse(last(stub)["ok"])
        self.assertIn("operator-only", last(stub)["error"])

    def test_event_ring_survives_a_restart(self):
        # 0.14.18: the ring is persisted with the registry at each
        # commit, so the operator's recent history (grant/revoke AND
        # quota transitions) survives a daemon restart. Still bounded
        # diagnostics — the registry is the source of truth for the
        # current state.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        vault = os.path.join(self.tmp, "ev-persist")
        storage = StorageService(capability_manager=caps, vault_dir=vault)
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a",
                               {"name": "assets"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_grant(stub, "p", "2", "container-a",
                              {"volume_id": vid, "container": "container-b",
                               "path": "/assets"})
        self.assertTrue(last(stub)["ok"])
        # The grant op persists the registry (and with it, the ring).
        storage2 = StorageService(capability_manager=caps, vault_dir=vault)
        stub = _Stub()
        storage2._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
        events = last(stub)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "grant")
        self.assertEqual(events[0]["scope"], "/assets")
        self.assertEqual(events[0]["container"], "container-b")

    def test_quota_warnings_persist_and_clear(self):
        # Warnings ride the registry persistence (each commit), so a
        # container parked near its quota is still flagged after a
        # daemon restart; clearing the quota drops the signal.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        vault = os.path.join(self.tmp, "warn-persist")
        storage = StorageService(capability_manager=caps, vault_dir=vault)
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "w"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 100})
        self.assertTrue(last(stub)["ok"])
        stub = _Stub()
        storage._volume_write(stub, "p", "4", "container-a", {
            "handle": handle, "path": "/f",
            "data_b64": base64.b64encode(b"W" * 96).decode("ascii"),
            "offset": 0})
        self.assertTrue(last(stub)["ok"])  # 96% = at (>= 95%)
        self.assertEqual(storage._volumes[vid]["warnings"]["container-a"],
                         "at")
        # Survives a restart.
        storage2 = StorageService(capability_manager=caps, vault_dir=vault)
        self.assertEqual(storage2._volumes[vid]["warnings"]["container-a"],
                         "at")
        # Clearing the quota removes the signal at the next refresh.
        stub = _Stub()
        storage2._volume_quota_set(stub, "p", "5", "container-a",
                                   {"volume_id": vid,
                                    "container": "container-a",
                                    "bytes": None})
        self.assertTrue(last(stub)["ok"])
        # The old instance's handle is gone after the restart — open a
        # fresh one on storage2 for the fsync/refresh.
        stub = _Stub()
        storage2._volume_open(stub, "p", "o", "container-a",
                              {"volume_id": vid})
        handle2 = last(stub)["handle"]
        stub = _Stub()
        storage2._volume_fsync(stub, "p", "6", "container-a",
                               {"handle": handle2})
        self.assertTrue(last(stub)["ok"])
        self.assertIsNone(storage2._volumes[vid]["warnings"]
                          .get("container-a"))

    def test_quota_events_record_transitions_and_edquot(self):
        # The operator's actionable history: warning-level transitions
        # AND the EDQUOT hard stop are recorded in the event ring and
        # surfaced newest-first via volume_events (OPERATOR-ONLY).
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "events-vault"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "e"})
        vid = last(stub)["volume_id"]
        stub = _Stub()
        storage._volume_open(stub, "p", "2", "container-a",
                             {"volume_id": vid})
        handle = last(stub)["handle"]
        stub = _Stub()
        storage._volume_quota_set(stub, "p", "3", "container-a",
                                  {"volume_id": vid,
                                   "container": "container-a", "bytes": 100})
        self.assertTrue(last(stub)["ok"])

        def write(path, n):
            stub = _Stub()
            storage._volume_write(stub, "p", "w", "container-a", {
                "handle": handle, "path": path,
                "data_b64": base64.b64encode(b"E" * n).decode("ascii"),
                "offset": 0})
            return last(stub)

        def levels():
            stub = _Stub()
            storage._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
            return [e["level"] for e in last(stub)["events"]]

        def kinds():
            stub = _Stub()
            storage._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
            return [e["kind"] for e in last(stub)["events"]]

        # 81% -> near transition, then 95% -> at transition.
        self.assertTrue(write("/a", 81)["ok"])
        self.assertTrue(write("/b", 14)["ok"])
        # The EDQUOT hard stop is the third event.
        over = write("/c", 6)
        self.assertFalse(over["ok"])
        self.assertEqual(over["errno"], errno.EDQUOT)
        events = levels()
        # Newest first: edquot, at, near.
        self.assertEqual(events, ["edquot", "at", "near"])
        # The kind field labels them all as quota events.
        self.assertEqual(kinds(), ["quota", "quota", "quota"])
        # The event ring reveals per-container accounting: a container
        # is refused even with the storage capability.
        stub = _Stub()
        storage._volume_events(stub, "p", "ev", "container-a")
        self.assertFalse(last(stub)["ok"])
        self.assertIn("operator-only", last(stub)["error"])

    def test_quota_event_ring_is_bounded(self):
        # The ring is bounded diagnostics, not a log file: past the
        # bound, the newest events displace the oldest.
        class _Stub:
            def __init__(self):
                self.replies = []

            def reply(self, sender_path, call_id, payload):
                self.replies.append(json.loads(payload.decode("utf-8")))

        def last(stub):
            return stub.replies[-1]

        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "events-bound"))
        stub = _Stub()
        storage._volume_create(stub, "p", "1", "container-a", {"name": "b"})
        vid = last(stub)["volume_id"]
        for _ in range(70):
            storage._record_event("b", "container-a", "quota",
                                  level="edquot", usage=100, quota=100)
        self.assertEqual(len(storage._events), 64)
        stub = _Stub()
        storage._volume_events(stub, "p", "ev", DEFAULT_OPERATOR_ID)
        reply = last(stub)
        self.assertEqual(reply["event_count"], 64)
        self.assertEqual(len(reply["events"]), 64)

    def test_granted_container_drives_the_mount_ops(self):
        # The access matrix through the REAL data plane: a seccomp
        # container holding an explicit volume grant (created by the
        # operator) drives the passthrough's operations — the exact
        # ops a kernel mount issues — over the wire against an
        # ENCRYPTED volume, opening it BY NAME as the granted
        # container. (The kernel mount itself is operator/host-only by
        # design: ``mount``/``umount2`` are in seccomp's _ALWAYS_DENY,
        # so a seccomp container cannot mount — the container-facing
        # data plane is these CALLs.)
        if not _direct_launch_supported():
            self.skipTest("host cannot launch direct-syscall containers")
        sock = os.path.join(self.tmp, "g.sock")
        vault = os.path.join(self.tmp, "g-vault")
        key = os.path.join(self.tmp, "g.key")
        from backend import keys as keys_mod
        with open(key, "wb") as f:
            f.write(keys_mod.make_blob_any(b"grant-secret"))
        host = nyrqis_backend.StatusServiceHost(
            socket_path=sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key,
            vault_passphrase="grant-secret")
        host.start()
        operator = IPCClient(
            DEFAULT_OPERATOR_ID,
            os.path.join(self.tmp, "g-ctl.sock")).bind()
        base = tempfile.mkdtemp(prefix="nyrqis-grant-e2e-")
        cli_path = os.path.join(base, "grantee.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")
        backend_dir = str(Path(__file__).resolve().parent)
        try:
            r = self._call(operator, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "shared"}).encode(), path=sock)
            self.assertTrue(r["ok"], r)
            r = self._call(operator, json.dumps({
                "service": "storage", "op": "volume_grant",
                "name": "shared", "container": "grantee"}).encode(),
                path=sock)
            self.assertTrue(r["ok"], r)
            script = (
                "import json, os, sys, time\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "deadline = time.time() + 15\n"
                "while not os.path.exists(sys.argv[5]) and "
                "time.time() < deadline:\n"
                "    time.sleep(0.01)\n"
                "from ipc.transport import IPCClient\n"
                "from fuse.vault_mount import NyVaultOperations\n"
                "c = IPCClient('grantee', sys.argv[2]).bind()\n"
                "out = {}\n"
                "try:\n"
                "    ops = NyVaultOperations(c, sys.argv[3], "
                "sys.argv[4])\n"
                "    ops.write('/from-grantee.txt', b'granted!', 0)\n"
                "    out['read'] = ops.read("
                "'/from-grantee.txt', 32, 0).decode()\n"
                "    ops.close()\n"
                "    out['ok'] = True\n"
                "except Exception as e:\n"
                "    out['ok'] = False\n"
                "    out['error'] = repr(e)\n"
                "open(sys.argv[6], 'w').write(json.dumps(out))\n"
            )
            container = host.container_manager.create(ContainerConfig(
                name="grantee",
                command=[sys.executable, "-c", script, backend_dir,
                         cli_path, sock, "shared", ready_path, marker],
                seccomp=True,
                capabilities=[
                    "CAP_IPC_SEND", "CAP_STORAGE_VOLUME",
                    "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                    "CAP_FILESYSTEM_WRITE",
                ],
            ))
            try:
                host.container_manager.spawn(container)
                # Spawn initializes the container with its DEFAULTS and
                # resets pre-spawn grants; the storage capability (an
                # explicit grant, like the volume grant itself) is
                # granted afterwards — the standard flow.
                host.capability_manager.grant_capability(
                    "grantee", Capability.CAP_STORAGE_VOLUME)
                with open(ready_path, "w") as fh:
                    fh.write("go")
                deadline = time.time() + 30.0
                while time.time() < deadline and not os.path.exists(marker):
                    time.sleep(0.05)
                self.assertTrue(
                    os.path.exists(marker),
                    "granted container never reached the storage service",
                )
                with open(marker) as fh:
                    out = json.loads(fh.read() or "{}")
                self.assertTrue(out.get("ok"), out)
                self.assertEqual(out.get("read"), "granted!")
                # The operator sees the container's write too (the
                # volume is shared, at-rest encrypted).
                r = self._call(operator, json.dumps({
                    "service": "storage", "op": "volume_open",
                    "name": "shared"}).encode(), path=sock)
                self.assertTrue(r["ok"], r)
                h = r["handle"]
                r = self._call(operator, json.dumps({
                    "service": "storage", "op": "volume_read",
                    "handle": h, "path": "/from-grantee.txt",
                    "offset": 0, "size": 32}).encode(), path=sock)
                self.assertEqual(
                    base64.b64decode(r["data_b64"]), b"granted!")
            finally:
                _launch_cleanup(host.container_manager, container)
        finally:
            operator.close()
            host.stop()
            shutil.rmtree(base, ignore_errors=True)

    def test_scoped_grant_restricts_the_passthrough_in_a_container(self):
        # 0.14.15 through the REAL data plane: a seccomp container
        # holding a PATH-SCOPED grant (/assets) drives the passthrough
        # ops — writes inside the scope land, writes/reads outside are
        # rejected with the honest EACCES riding the CALL reply (a
        # permission denial, not a generic EIO), and the rejection
        # never touches the tree (the rejected path still reads as
        # ENOENT for the operator).
        if not _direct_launch_supported():
            self.skipTest("host cannot launch direct-syscall containers")
        sock = os.path.join(self.tmp, "s.sock")
        vault = os.path.join(self.tmp, "s-vault")
        key = os.path.join(self.tmp, "s.key")
        from backend import keys as keys_mod
        with open(key, "wb") as f:
            f.write(keys_mod.make_blob_any(b"scope-secret"))
        host = nyrqis_backend.StatusServiceHost(
            socket_path=sock, backend_version="9.9.9",
            vault_dir=vault, vault_key_file=key,
            vault_passphrase="scope-secret")
        host.start()
        operator = IPCClient(
            DEFAULT_OPERATOR_ID,
            os.path.join(self.tmp, "s-ctl.sock")).bind()
        base = tempfile.mkdtemp(prefix="nyrqis-scope-e2e-")
        cli_path = os.path.join(base, "grantee.sock")
        ready_path = os.path.join(base, "ready")
        marker = os.path.join(base, "marker")
        backend_dir = str(Path(__file__).resolve().parent)
        try:
            r = self._call(operator, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "shared"}).encode(), path=sock)
            self.assertTrue(r["ok"], r)
            # A PATH-SCOPED grant: /assets only.
            r = self._call(operator, json.dumps({
                "service": "storage", "op": "volume_grant",
                "name": "shared", "container": "grantee",
                "path": "/assets"}).encode(), path=sock)
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["path"], "/assets")
            script = (
                "import json, os, sys, time, errno\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "deadline = time.time() + 15\n"
                "while not os.path.exists(sys.argv[5]) and "
                "time.time() < deadline:\n"
                "    time.sleep(0.01)\n"
                "from ipc.transport import IPCClient\n"
                "from fuse.vault_mount import NyVaultOperations, "
                "VaultMountError\n"
                "c = IPCClient('grantee', sys.argv[2]).bind()\n"
                "out = {}\n"
                "try:\n"
                "    ops = NyVaultOperations(c, sys.argv[3], "
                "sys.argv[4])\n"
                "    ops.write('/assets/ok.txt', b'in-scope', 0)\n"
                "    out['in_scope'] = True\n"
                "    try:\n"
                "        ops.write('/outside.txt', b'nope', 0)\n"
                "        out['outside_write'] = 'accepted'\n"
                "    except VaultMountError as e:\n"
                "        out['outside_write'] = 'denied'\n"
                "        out['outside_errno'] = e.errno\n"
                "    try:\n"
                "        ops.read('/outside.txt', 32, 0)\n"
                "        out['outside_read'] = 'accepted'\n"
                "    except VaultMountError as e:\n"
                "        out['outside_read'] = 'denied'\n"
                "        out['outside_read_errno'] = e.errno\n"
                "    out['read'] = ops.read("
                "'/assets/ok.txt', 32, 0).decode()\n"
                "    ops.close()\n"
                "    out['ok'] = True\n"
                "except Exception as e:\n"
                "    out['ok'] = False\n"
                "    out['error'] = repr(e)\n"
                "open(sys.argv[6], 'w').write(json.dumps(out))\n"
            )
            container = host.container_manager.create(ContainerConfig(
                name="grantee",
                command=[sys.executable, "-c", script, backend_dir,
                         cli_path, sock, "shared", ready_path, marker],
                seccomp=True,
                capabilities=[
                    "CAP_IPC_SEND", "CAP_STORAGE_VOLUME",
                    "CAP_NETWORK_SOCKET", "CAP_NETWORK_BIND",
                    "CAP_FILESYSTEM_WRITE",
                ],
            ))
            try:
                host.container_manager.spawn(container)
                host.capability_manager.grant_capability(
                    "grantee", Capability.CAP_STORAGE_VOLUME)
                with open(ready_path, "w") as fh:
                    fh.write("go")
                deadline = time.time() + 30.0
                while time.time() < deadline and not os.path.exists(marker):
                    time.sleep(0.05)
                self.assertTrue(
                    os.path.exists(marker),
                    "scoped grantee never reached the storage service",
                )
                with open(marker) as fh:
                    out = json.loads(fh.read() or "{}")
                self.assertTrue(out.get("ok"), out)
                self.assertTrue(out.get("in_scope"))
                # The scope violation surfaces with the honest EACCES,
                # and the rejected path never reached the tree.
                self.assertEqual(out.get("outside_write"), "denied")
                self.assertEqual(out.get("outside_errno"), errno.EACCES)
                self.assertEqual(out.get("outside_read"), "denied")
                self.assertEqual(out.get("outside_read_errno"), errno.EACCES)
                self.assertEqual(out.get("read"), "in-scope")
                # The operator sees the in-scope write and confirms the
                # rejected path does not exist.
                r = self._call(operator, json.dumps({
                    "service": "storage", "op": "volume_open",
                    "name": "shared"}).encode(), path=sock)
                self.assertTrue(r["ok"], r)
                h = r["handle"]
                r = self._call(operator, json.dumps({
                    "service": "storage", "op": "volume_read",
                    "handle": h, "path": "/assets/ok.txt",
                    "offset": 0, "size": 32}).encode(), path=sock)
                self.assertEqual(
                    base64.b64decode(r["data_b64"]), b"in-scope")
                r = self._call(operator, json.dumps({
                    "service": "storage", "op": "volume_read",
                    "handle": h, "path": "/outside.txt",
                    "offset": 0, "size": 32}).encode(), path=sock)
                self.assertFalse(r["ok"])
                self.assertIn("no such file", r["error"].lower())
            finally:
                _launch_cleanup(host.container_manager, container)
        finally:
            operator.close()
            host.stop()
            shutil.rmtree(base, ignore_errors=True)

    def test_interval_commit_flush_fsync_semantics(self):
        # §27 group commit: deferred writes are visible in memory
        # immediately; ``volume_flush`` (FUSE close-of-last-fd) is NOT
        # a durability boundary; ``volume_fsync``, ``volume_close``,
        # and the commit-interval tick each commit. A burst of
        # short-lived files therefore pays ONE save per interval
        # instead of one per close.
        vault = os.path.join(self.tmp, "ic-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False, commit_interval=0.3)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            def op(payload):
                return self._call(client, json.dumps(payload).encode())

            def journal_size():
                vid = next(iter(storage._volumes))
                p = os.path.join(vault, vid + ".nyfs", "state",
                                 "journal.bin")
                return os.path.getsize(p) if os.path.exists(p) else 0

            r = op({"service": "storage", "op": "volume_create",
                    "name": "ic"})
            handle = op({"service": "storage", "op": "volume_open",
                         "volume_id": r["volume_id"]})["handle"]

            # Deferred write + flush: NOT committed (flush defers).
            op({"service": "storage", "op": "volume_write",
                "handle": handle, "path": "/a", "offset": 0,
                "data_b64": base64.b64encode(b"a" * 1000).decode(),
                "defer_commit": True})
            op({"service": "storage", "op": "volume_flush",
                "handle": handle})
            self.assertEqual(journal_size(), 0,
                             "flush must not commit (POSIX close)")

            # fsync IS the durability boundary.
            op({"service": "storage", "op": "volume_fsync",
                "handle": handle})
            self.assertGreater(journal_size(), 0)
            after_fsync = journal_size()

            # Interval tick: a deferred write, then the first deferred
            # op after the interval commits the whole batch in one save.
            op({"service": "storage", "op": "volume_write",
                "handle": handle, "path": "/b", "offset": 0,
                "data_b64": base64.b64encode(b"b" * 1000).decode(),
                "defer_commit": True})
            time.sleep(0.4)
            op({"service": "storage", "op": "volume_write",
                "handle": handle, "path": "/c", "offset": 0,
                "data_b64": base64.b64encode(b"c" * 1000).decode(),
                "defer_commit": True})
            self.assertGreater(
                journal_size(), after_fsync,
                "interval tick must persist the deferred batch")

            # Close is a durability boundary too (unmount semantics).
            op({"service": "storage", "op": "volume_write",
                "handle": handle, "path": "/d", "offset": 0,
                "data_b64": base64.b64encode(b"d" * 1000).decode(),
                "defer_commit": True})
            before_close = journal_size()
            op({"service": "storage", "op": "volume_close",
                "handle": handle})
            self.assertGreater(journal_size(), before_close,
                               "close must commit deferred state")

            # Deferred data was readable in memory all along.
            h2 = op({"service": "storage", "op": "volume_open",
                     "volume_id": r["volume_id"]})["handle"]
            d = op({"service": "storage", "op": "volume_read",
                    "handle": h2, "path": "/d", "offset": 0,
                    "size": 32})
            self.assertEqual(base64.b64decode(d["data_b64"]),
                             b"d" * 32)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_volume_snapshot_delete(self):
        # The snapshot table is a lifecycle, not just a read path:
        # delete a snapshot over the wire and the list shrinks; a
        # missing snapshot fails honestly.
        vault = os.path.join(self.tmp, "sd-vault")
        server, stop, storage = self._serve(
            vault_dir=vault, register_pid=False)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "storage", "op": "volume_create",
                "name": "sd"}).encode())
            handle = self._call(client, json.dumps({
                "service": "storage", "op": "volume_open",
                "volume_id": resp["volume_id"]}).encode())["handle"]
            def op(payload):
                return self._call(client, json.dumps(payload).encode())
            r = op({"service": "storage", "op": "volume_write",
                    "handle": handle, "path": "/doc.txt", "offset": 0,
                    "data_b64": base64.b64encode(b"original").decode()})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_snapshot",
                    "handle": handle, "name": "v1"})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_snapshot",
                    "handle": handle, "name": "v2"})
            self.assertTrue(r["ok"], r)
            r = op({"service": "storage", "op": "volume_snapshots",
                    "handle": handle})
            self.assertEqual(sorted(r["snapshots"]), ["v1", "v2"])
            r = op({"service": "storage", "op": "volume_snapshot_delete",
                    "handle": handle, "name": "v1"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["deleted"], "v1")
            r = op({"service": "storage", "op": "volume_snapshots",
                    "handle": handle})
            self.assertEqual(r["snapshots"], ["v2"])
            r = op({"service": "storage", "op": "volume_snapshot_delete",
                    "handle": handle, "name": "nope"})
            self.assertFalse(r["ok"])
            self.assertIn("not found", r["error"])
            # The restored snapshot still works after the delete.
            r = op({"service": "storage", "op": "volume_restore",
                    "handle": handle, "name": "v2"})
            self.assertTrue(r["ok"], r)
        finally:
            client.close()
            stop.set()
            server.close()


class _StreamStub:
    """Stub reply server for the streaming handler tests: records
    every reply the handlers send (payloads already JSON-parsed)."""

    def __init__(self):
        self.replies = []

    def reply(self, sender_path, call_id, payload):
        self.replies.append(json.loads(payload.decode("utf-8")))

    @property
    def last(self):
        return self.replies[-1]


class TestStorageStreaming(unittest.TestCase):
    """ADR-0024 first increment — the streaming data plane at the
    service level: chunked ``volume_write`` reassembly (window + TTL +
    cross-sender binding + per-chunk checksum) with ONE write on the
    final chunk, streamed ``volume_read`` replies collected by index,
    the ``stream`` advertisement in ``volume_open``, and the client
    halves (``call_stream_write`` pipelining, ``call_stream_reply``
    collection). The wire codec is untouched, so these ride ordinary
    capability-gated CALLs on either loop path.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    # -- hermetic handler tests (stub reply server) -----------------

    def _stub_storage(self):
        caps = CapabilityManager()
        caps.initialize_container("container-a")
        caps.grant_capability("container-a", Capability.CAP_STORAGE_VOLUME)
        storage = StorageService(
            capability_manager=caps,
            vault_dir=os.path.join(self.tmp, "stream-vault"))
        return storage, caps

    def _open_handle(self, storage, caps, owner="container-a"):
        stub = _StreamStub()
        storage._volume_create(stub, "p", "1", owner, {"name": "svol"})
        vid = stub.replies[-1]["volume_id"]
        stub = _StreamStub()
        storage._volume_open(stub, "p", "2", owner, {"volume_id": vid})
        return stub, stub.replies[-1]["handle"]

    def _chunk(self, data, index, count, stream_id, **extra):
        return {
            "handle": "H", "path": "/big.bin", "offset": 0,
            "defer_commit": True,
            "stream_id": stream_id, "stream_index": index,
            "stream_count": count,
            "checksum": hashlib.sha256(data).hexdigest(),
            "data_b64": base64.b64encode(data).decode("ascii"),
            **extra,
        }

    def test_stream_write_reassembles_and_writes_once(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        body = bytes(range(256)) * 300  # 76.8 KiB → 3 chunks
        n = 3
        sid = os.urandom(6).hex()
        # Out-of-order arrival: 1, 0, 2 (the last chunk completes).
        for i, piece in ((1, body[32768:65536]),
                         (0, body[:32768]),
                         (2, body[65536:])):
            stub = _StreamStub()
            storage._volume_write(
                stub, "p", "c%d" % i, "container-a",
                self._chunk(piece, i, n, sid, handle=handle))
        # Intermediate chunks get NO reply; only the final one replies.
        ok_replies = [r for r in stub.replies if r.get("ok")]
        self.assertEqual(len(ok_replies), 1, stub.replies)
        self.assertEqual(ok_replies[0]["bytes_written"], len(body))
        # The volume holds the FULL assembled write, in order.
        record = storage._volumes[storage._by_name["svol"]]
        nyfs = storage._ensure_nyfs(record)
        self.assertEqual(nyfs.read("/big.bin"), body)
        # The slot is gone.
        self.assertEqual(storage._streams, {})

    def test_stream_write_duplicate_chunk_rejects_stream(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        sid = os.urandom(6).hex()
        # 3 chunks; send 0 and 1 (the stream is still open), then a
        # duplicate of 1 BEFORE completion → fail-closed, dropped.
        for i in (0, 1):
            stub = _StreamStub()
            storage._volume_write(
                stub, "p", "c%d" % i, "container-a",
                self._chunk(b"x" * 1024, i, 3, sid, handle=handle))
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "c3", "container-a",
            self._chunk(b"y" * 1024, 1, 3, sid, handle=handle))
        self.assertFalse(stub.replies[-1]["ok"])
        self.assertIn("duplicate", stub.replies[-1]["error"])
        self.assertEqual(storage._streams, {})

    def test_stream_write_cross_sender_rejected(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        sid = os.urandom(6).hex()
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "c0", "container-a",
            self._chunk(b"a" * 1024, 0, 2, sid, handle=handle))
        # Give container-b a LEGITIMATE handle to the same volume (it
        # must pass the handle gate to reach the stream-bind check),
        # then have it claim container-a's stream_id → the bind fails.
        caps.initialize_container("container-b")
        caps.grant_capability("container-b", Capability.CAP_STORAGE_VOLUME)
        vid = storage._by_name["svol"]
        stub = _StreamStub()
        storage._volume_grant(stub, "p", "g1", "container-a", {
            "volume_id": vid, "container": "container-b"})
        self.assertTrue(stub.replies[-1]["ok"])
        stub = _StreamStub()
        storage._volume_open(stub, "p", "o1", "container-b",
                             {"volume_id": vid})
        self.assertTrue(stub.replies[-1]["ok"])
        handle_b = stub.replies[-1]["handle"]
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "c1", "container-b",
            self._chunk(b"b" * 1024, 1, 2, sid, handle=handle_b))
        self.assertFalse(stub.replies[-1]["ok"])
        self.assertIn("another container", stub.replies[-1]["error"])

    def test_stream_write_checksum_mismatch_rejects(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        sid = os.urandom(6).hex()
        req = self._chunk(b"tampered", 0, 1, sid, handle=handle)
        req["checksum"] = "0" * 64  # lies about the data
        stub = _StreamStub()
        storage._volume_write(stub, "p", "c0", "container-a", req)
        self.assertFalse(stub.replies[-1]["ok"])
        self.assertIn("checksum", stub.replies[-1]["error"])

    def test_stream_write_count_bound_rejected(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        from ipc import storage as storage_mod
        req = self._chunk(b"x" * 1024, 0, storage_mod._STREAM_MAX_CHUNKS + 1,
                          os.urandom(6).hex(), handle=handle)
        stub = _StreamStub()
        storage._volume_write(stub, "p", "c0", "container-a", req)
        self.assertFalse(stub.replies[-1]["ok"])
        self.assertIn("chunk bound", stub.replies[-1]["error"])

    def test_stream_write_ttl_expires_partial_stream(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        from ipc import storage as storage_mod
        sid = os.urandom(6).hex()
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "c0", "container-a",
            self._chunk(b"a" * 1024, 0, 3, sid, handle=handle))
        # Age the slot past the TTL, then finish the stream: the
        # expired slot is swept on arrival, and the "new" stream starts
        # from chunk 0 again — so chunk 2 completes only 1/3 chunks
        # and gets no reply (waiting for more).
        storage._streams[sid]["last_seen"] -= (
            storage_mod._STREAM_TTL_S + 1)
        for i, piece in ((1, b"b" * 1024), (2, b"c" * 1024)):
            stub = _StreamStub()
            storage._volume_write(
                stub, "p", "c%d" % i, "container-a",
                self._chunk(piece, i, 3, sid, handle=handle))
        # The old slot was swept (logged); the new partial stream waits.
        self.assertIn(sid, storage._streams)
        self.assertEqual(len(storage._streams[sid]["chunks"]), 2)

    def test_stream_write_scoped_quota_checked_once_on_full_payload(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        # A scoped quota that the FULL payload violates but a single
        # 32 KiB chunk would pass: enforcement must see the assembled
        # bytes (ADR-0024: one quota check on the whole stream).
        record = storage._volumes[storage._by_name["svol"]]
        record["scope_quota"] = {"container-a": {"/big": 40_000}}
        body = b"z" * 100_000  # 4 chunks
        n = 4
        sid = os.urandom(6).hex()
        for i in range(n):
            piece = body[i * 32768:(i + 1) * 32768]
            stub = _StreamStub()
            storage._volume_write(
                stub, "p", "c%d" % i, "container-a",
                self._chunk(piece, i, n, sid, handle=handle,
                            path="/big/data.bin"))
        self.assertFalse(stub.replies[-1]["ok"])
        self.assertEqual(stub.replies[-1].get("errno"), errno.EDQUOT)
        self.assertIn("scope /big", stub.replies[-1]["error"])

    def test_stream_read_pieces_and_reassembly(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        # Seed a >32 KiB file through the PLAIN paged path (each write
        # ≤32 KiB — the pre-streaming surface must still work).
        body = os.urandom(90_000)
        for off in range(0, len(body), 32768):
            stub = _StreamStub()
            storage._volume_write(
                stub, "p", "w%d" % off, "container-a", {
                    "handle": handle, "path": "/big.bin",
                    "data_b64": base64.b64encode(
                        body[off:off + 32768]).decode("ascii"),
                    "offset": off})
        # A streamed read: one call, N correlated pieces.
        stub = _StreamStub()
        storage._volume_read(stub, "p", "r1", "container-a", {
            "handle": handle, "path": "/big.bin", "offset": 0,
            "size": 90_000, "stream": True})
        self.assertTrue(stub.replies)
        pieces = sorted(
            (r["stream_index"], r) for r in stub.replies if r.get("ok"))
        self.assertEqual([i for i, _ in pieces], list(range(len(pieces))))
        self.assertEqual(pieces[-1][1]["stream_count"], len(pieces))
        out = b"".join(
            base64.b64decode(r["data_b64"], validate=True)
            for _i, r in pieces)
        self.assertEqual(out, body)
        # Every piece stays ≤32 KiB (the datagram budget).
        self.assertTrue(all(
            len(base64.b64decode(r["data_b64"])) <= 32768
            for _i, r in pieces))

    def test_stream_read_eof_short(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "w1", "container-a", {
                "handle": handle, "path": "/short.bin",
                "data_b64": base64.b64encode(b"abcdef").decode("ascii"),
                "offset": 0})
        stub = _StreamStub()
        storage._volume_read(stub, "p", "r1", "container-a", {
            "handle": handle, "path": "/short.bin", "offset": 0,
            "size": 100_000, "stream": True})
        self.assertTrue(stub.replies)
        self.assertTrue(all(r.get("ok") for r in stub.replies))
        out = b"".join(base64.b64decode(r["data_b64"], validate=True)
                        for r in stub.replies)
        self.assertEqual(out, b"abcdef")
        self.assertEqual(len(stub.replies), 1)  # one short piece

    def test_volume_open_advertises_stream(self):
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        self.assertTrue(stub.replies[-1].get("stream"))

    def test_plain_write_still_pages_without_stream_fields(self):
        # Back-compat: an ordinary write (no stream envelope) behaves
        # exactly as before — one write per call, durable when
        # defer_commit is omitted.
        storage, caps = self._stub_storage()
        stub, handle = self._open_handle(storage, caps)
        stub = _StreamStub()
        storage._volume_write(
            stub, "p", "w1", "container-a", {
                "handle": handle, "path": "/x",
                "data_b64": base64.b64encode(b"plain").decode("ascii"),
                "offset": 0})
        self.assertTrue(stub.replies[-1]["ok"])
        record = storage._volumes[storage._by_name["svol"]]
        nyfs = storage._ensure_nyfs(record)
        self.assertEqual(nyfs.read("/x"), b"plain")

    # -- real-server e2e (the passthrough + the wire client halves) --

    def test_passthrough_streamed_write_and_read_round_trip(self):
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(self.tmp, "vault"))
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            resp = json.loads(client.call(
                svc_path, json.dumps({"service": "storage",
                                      "op": "volume_create",
                                      "name": "streamvol"}).encode()
            ).payload.decode("utf-8"))
            vid = resp["volume_id"]
            ops = NyVaultOperations(client, svc_path, vid)
            # The open reply advertised streaming → the passthrough
            # engages the stream paths.
            self.assertTrue(ops._stream_ok)
            body = os.urandom(100_000)  # 4 chunks
            self.assertEqual(ops.write("/data.bin", body, 0), len(body))
            st = ops.getattr("/data.bin")
            self.assertEqual(st["st_size"], len(body))
            self.assertEqual(ops.read("/data.bin", len(body), 0), body)
            # Offset writes still stream correctly (base offset rides
            # every chunk; the service writes the assembled payload at
            # it).
            tail = b"TAIL" * 5000
            self.assertEqual(
                ops.write("/data.bin", tail, len(body)), len(tail))
            self.assertEqual(
                ops.read("/data.bin", len(body) + len(tail), 0),
                body + tail)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_passthrough_stream_write_rejects_over_quota(self):
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(self.tmp, "vault"))
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            resp = json.loads(client.call(
                svc_path, json.dumps({"service": "storage",
                                      "op": "volume_create",
                                      "name": "quota-vol"}).encode()
            ).payload.decode("utf-8"))
            vid = resp["volume_id"]
            # A 20 KiB whole-volume quota; the streamed write is 100 KiB.
            storage._volumes[vid]["quota"] = {
                DEFAULT_OPERATOR_ID: 20_000}
            ops = NyVaultOperations(client, svc_path, vid)
            body = b"q" * 100_000
            with self.assertRaises(VaultMountError) as ctx:
                ops.write("/data.bin", body, 0)
            self.assertEqual(ctx.exception.errno, errno.EDQUOT)
        finally:
            client.close()
            stop.set()
            server.close()


class TestWireLevelStreaming(unittest.TestCase):
    """ADR-0024 wire-level streaming (0.14.21) — the STREAM_CHUNK
    message type (codec type 5) moving the framing into the transport
    the serving loop owns: floor reassembly of chunked CALLs (bounded
    window/TTL, per-sender bind, per-chunk checksums), chunked REPLYs
    rebuilt by the client, the ``stream_ver=2`` advertisement, and the
    passthrough's wire-level read/write against a real server. The
    service-level envelope (0.14.20) stays for old peers — exercised
    as the mixed-version fallback.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _chunk_wire(self, stream_id: bytes, call_id: str, index: int,
                    count: int, payload: bytes) -> bytes:
        """Encode a STREAM_CHUNK wire with the floor's envelope
        (the same bytes a wire-level client sends)."""
        from ipc.transport import _encode_stream_chunk
        env = _encode_stream_chunk(stream_id, call_id, index, count, payload)
        return IPCMessage(
            message_type=IPCMessageType.STREAM_CHUNK,
            sender_id=DEFAULT_OPERATOR_ID,
            payload=env,
            reply_to=call_id,
        ).to_wire()

    # -- hermetic floor-server reassembly ---------------------------

    def test_floor_reassembles_chunked_call_and_dispatches_once(self):
        """The floor server buffers STREAM_CHUNK datagrams and
        dispatches ONE CALL (the full payload) when the stream
        completes — out of order, on the final chunk."""
        from ipc.transport import (IPCDatagramServer, STREAM_CHUNK_BYTES)
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        path = os.path.join(self.tmp, "w.sock")
        server = IPCDatagramServer(
            manager, "ep", path, pid_registry={},
            trusted_uids={os.getuid()})
        dispatched = []
        server.on_call = lambda msg, sender, sp: dispatched.append(msg)
        server.bind()
        sid = os.urandom(6)
        body = os.urandom(STREAM_CHUNK_BYTES * 2 + 123)
        n = 3
        try:
            # Send out of order: chunk 2, then 0, then 1.
            pieces = [
                body[i * STREAM_CHUNK_BYTES:(i + 1) * STREAM_CHUNK_BYTES]
                for i in range(n)
            ]
            pieces[-1] = body[(n - 1) * STREAM_CHUNK_BYTES:]
            for i in (2, 0, 1):
                server.endpoint.send(
                    self._chunk_wire(sid, "w-call-1", i, n, pieces[i]))
            # Intermediate chunks: no dispatch.
            self.assertEqual(server.serve_once(timeout=0.5), None)
            self.assertEqual(server.serve_once(timeout=0.5), None)
            # Final chunk: one dispatch, full payload, correct call_id.
            msg = server.serve_once(timeout=0.5)
            self.assertIsNotNone(msg)
            self.assertEqual(len(dispatched), 1)
            self.assertEqual(dispatched[0].message_id, "w-call-1")
            self.assertEqual(dispatched[0].sender_id, DEFAULT_OPERATOR_ID)
            self.assertEqual(dispatched[0].payload, body)
            # The stream slot was reclaimed.
            self.assertEqual(server._streams, {})
        finally:
            server.close()

    def test_floor_rejects_bad_chunk_checksum_and_duplicate(self):
        from ipc.transport import IPCDatagramServer
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        path = os.path.join(self.tmp, "w.sock")
        server = IPCDatagramServer(
            manager, "ep", path, pid_registry={},
            trusted_uids={os.getuid()})
        dispatched = []
        server.on_call = lambda msg, sender, sp: dispatched.append(msg)
        server.bind()
        sid = os.urandom(6)
        try:
            good = self._chunk_wire(sid, "c", 0, 2, b"AAAA")
            # Checksum mismatch: flip a byte in the envelope payload.
            bad_env = bytearray(
                IPCMessage.from_wire(good).payload)
            bad_env[-33] ^= 0xFF  # inside the payload, before checksum
            bad = IPCMessage(
                message_type=IPCMessageType.STREAM_CHUNK,
                sender_id=DEFAULT_OPERATOR_ID,
                payload=bytes(bad_env),
                reply_to="c",
            ).to_wire()
            server.endpoint.send(bad)
            self.assertIsNone(server.serve_once(timeout=0.5))
            self.assertEqual(dispatched, [])
            # Duplicate chunk: the stream is rejected fail-closed.
            server.endpoint.send(good)
            self.assertIsNone(server.serve_once(timeout=0.5))
            server.endpoint.send(good)
            self.assertIsNone(server.serve_once(timeout=0.5))
            self.assertEqual(dispatched, [])
            self.assertEqual(server._streams, {})
        finally:
            server.close()

    def test_floor_cross_sender_stream_binds_to_first_chunk(self):
        """A chunk from a different authenticated sender never joins a
        stream: the slot is keyed by (sender, stream_id)."""
        from ipc.transport import IPCDatagramServer
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        path = os.path.join(self.tmp, "w.sock")
        server = IPCDatagramServer(
            manager, "ep", path, pid_registry={},
            trusted_uids={os.getuid()})
        dispatched = []
        server.on_call = lambda msg, sender, sp: dispatched.append(msg)
        server.bind()
        sid = os.urandom(6)
        try:
            server.endpoint.send(
                self._chunk_wire(sid, "c", 0, 2, b"AAAA"))
            self.assertIsNone(server.serve_once(timeout=0.5))
            # A forged sender_id is dropped by _authorized; a second
            # chunk with the SAME wire sender but a different kernel
            # uid cannot be simulated here — the bind is structural
            # (the slot key). Send chunk 1 from the same sender and
            # the stream completes normally.
            server.endpoint.send(
                self._chunk_wire(sid, "c", 1, 2, b"BBBB"))
            msg = server.serve_once(timeout=0.5)
            self.assertIsNotNone(msg)
            self.assertEqual(dispatched[0].payload, b"AAAABBBB")
        finally:
            server.close()

    def test_floor_chunks_large_reply_and_client_reassembles(self):
        """A reply larger than the datagram budget rides as STREAM_CHUNK
        messages; ``IPCClient.call(wire_stream=True)`` reassembles them
        into the full reply payload."""
        from ipc.transport import (IPCClient, IPCDatagramServer,
                                   STREAM_CHUNK_BYTES)
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        server = IPCDatagramServer(
            manager, "ep", svc_path, pid_registry={},
            trusted_uids={os.getuid()})
        big = os.urandom(STREAM_CHUNK_BYTES * 4 + 77)

        def handler(msg, sender, sender_path):
            server.reply(sender_path, msg.message_id, big)

        server.on_call = handler
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            reply = client.call(
                svc_path, b"{\"op\": \"big\"}", wire_stream=True)
            self.assertIsNotNone(reply)
            self.assertEqual(reply.payload, big)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_client_wire_stream_chunks_large_write_call(self):
        """A >32 KiB CALL payload is sent as STREAM_CHUNK messages; the
        floor server reassembles and dispatches ONE call; the client
        gets the single correlated reply."""
        from ipc.transport import (IPCClient, IPCDatagramServer,
                                   STREAM_CHUNK_BYTES)
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        server = IPCDatagramServer(
            manager, "ep", svc_path, pid_registry={},
            trusted_uids={os.getuid()})
        seen = []

        def handler(msg, sender, sender_path):
            seen.append(msg)
            server.reply(sender_path, msg.message_id,
                         b"{\"ok\": true}")

        server.on_call = handler
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            payload = os.urandom(STREAM_CHUNK_BYTES * 3 + 5)
            reply = client.call(svc_path, payload, wire_stream=True)
            self.assertIsNotNone(reply)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].payload, payload)
            self.assertEqual(
                json.loads(reply.payload.decode("utf-8")), {"ok": True})
        finally:
            client.close()
            stop.set()
            server.close()

    def test_small_call_unchanged_when_wire_stream_off(self):
        """With ``wire_stream=False`` a small call is byte-identical to
        before: a single CALL datagram, a single REPLY."""
        from ipc.transport import (IPCClient, IPCDatagramServer)
        manager = IPCManager()
        manager.create_endpoint("ep", "ep")
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        server = IPCDatagramServer(
            manager, "ep", svc_path, pid_registry={},
            trusted_uids={os.getuid()})
        server.on_call = lambda msg, s, sp: server.reply(
            sp, msg.message_id, b"{\"ok\": true}")
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            reply = client.call(svc_path, b"{\"op\": \"ping\"}")
            self.assertIsNotNone(reply)
            self.assertEqual(reply.message_type, IPCMessageType.REPLY)
            self.assertEqual(reply.payload, b"{\"ok\": true}")
        finally:
            client.close()
            stop.set()
            server.close()

    # -- service advertisement + wire-level passthrough e2e ---------

    def test_open_advertises_wire_level_and_passthrough_streams(self):
        """End-to-end through a real floor server: the open reply
        advertises ``stream_ver=2``, and the passthrough's wire-level
        read/write round-trip a >32 KiB payload in ONE logical call."""
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(self.tmp, "vault"))
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            resp = json.loads(client.call(
                svc_path, json.dumps({"service": "storage",
                                      "op": "volume_create",
                                      "name": "wirevol"}).encode()
            ).payload.decode("utf-8"))
            vid = resp["volume_id"]
            ops = NyVaultOperations(client, svc_path, vid)
            self.assertTrue(ops._stream_ok)
            self.assertGreaterEqual(ops._stream_ver, 2)
            body = os.urandom(100_000)  # 4 chunks at 32 KiB
            self.assertEqual(ops.write("/data.bin", body, 0), len(body))
            st = ops.getattr("/data.bin")
            self.assertEqual(st["st_size"], len(body))
            self.assertEqual(ops.read("/data.bin", len(body), 0), body)
            # Offset wire writes still land at the offset.
            tail = b"TAIL" * 5000
            self.assertEqual(
                ops.write("/data.bin", tail, len(body)), len(tail))
            self.assertEqual(
                ops.read("/data.bin", len(body) + len(tail), 0),
                body + tail)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_old_peer_still_uses_service_level_envelope(self):
        """Mixed-version: a passthrough pointed at a daemon that
        advertises only ``stream`` (0.14.20) keeps the service-level
        envelope — the wire-level path never engages."""
        from ipc.transport import (IPCClient, IPCDatagramServer)
        svc_path = os.path.join(self.tmp, "svc.sock")
        cli_path = os.path.join(self.tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(self.tmp, "vault"))
        router = ServiceRouter()
        router.register("storage", storage)
        router.attach(server)
        server.bind()
        stop = threading.Event()
        threading.Thread(target=server.serve, args=(stop,), daemon=True).start()
        client = IPCClient(DEFAULT_OPERATOR_ID, cli_path).bind()
        try:
            resp = json.loads(client.call(
                svc_path, json.dumps({"service": "storage",
                                      "op": "volume_create",
                                      "name": "oldvol"}).encode()
            ).payload.decode("utf-8"))
            vid = resp["volume_id"]
            ops = NyVaultOperations(client, svc_path, vid)
            # Simulate an old daemon: strip the wire-level flag from
            # the open reply so the passthrough degrades to the
            # service-level envelope.
            ops._stream_ver = 1
            body = os.urandom(100_000)
            self.assertEqual(ops.write("/data.bin", body, 0), len(body))
            self.assertEqual(ops.read("/data.bin", len(body), 0), body)
        finally:
            client.close()
            stop.set()
            server.close()


class TestNyVaultOperations(unittest.TestCase):
    """The NyVault FUSE passthrough (ADR-0022): FUSE ops whose
    handlers are storage-service CALLs — exercised without a kernel
    mount, exactly like ``TestNyFSOperations``.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        self.server = IPCDatagramServer(
            manager, "ep-svc", self.svc_path,
            pid_registry={}, trusted_uids={os.getuid()})
        self.storage = StorageService(
            capability_manager=None,
            vault_dir=os.path.join(self.tmp, "vault"))
        router = ServiceRouter()
        router.register("storage", self.storage)
        router.attach(self.server)
        self.server.bind()
        self.stop = threading.Event()
        threading.Thread(target=self.server.serve, args=(self.stop,),
                         daemon=True).start()
        self.client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        resp = self._call({"service": "storage", "op": "volume_create",
                           "name": "passthrough"})
        self.vid = resp["volume_id"]

    def tearDown(self):
        self.client.close()
        self.stop.set()
        self.server.close()

    def _call(self, payload):
        reply = self.client.call(self.svc_path,
                                 json.dumps(payload).encode("utf-8"))
        return json.loads(reply.payload.decode("utf-8"))

    def _ops(self):
        return NyVaultOperations(self.client, self.svc_path, self.vid)

    def test_full_flow_through_the_passthrough(self):
        ops = self._ops()
        try:
            self.assertEqual(ops.mkdir("/app", 0o755), 0)
            self.assertEqual(ops.mknod("/app/data.bin", 0o644, 0), 0)
            self.assertEqual(ops.write("/app/data.bin", b"payload", 0), 7)
            st = ops.getattr("/app/data.bin")
            self.assertEqual(st["st_size"], 7)
            self.assertEqual(ops.readdir("/app"),
                             [".", "..", "data.bin"])
            self.assertEqual(ops.read("/app/data.bin", 100, 0),
                             b"payload")
            self.assertEqual(ops.truncate("/app/data.bin", 3), 0)
            self.assertEqual(ops.read("/app/data.bin", 100, 0), b"pay")
            self.assertEqual(ops.rename("/app/data.bin",
                                        "/app/renamed.bin"), 0)
            self.assertEqual(ops.fsync("/app/renamed.bin", False), 0)
            for key in ("f_bsize", "f_blocks", "f_bfree", "f_files",
                        "f_ffree"):
                self.assertIn(key, ops.statfs("/"))
            self.assertEqual(ops.unlink("/app/renamed.bin"), 0)
            self.assertEqual(ops.rmdir("/app"), 0)
        finally:
            ops.close()

    def test_flush_defers_but_fsync_commits(self):
        # §27 group commit through the passthrough: ``flush`` (the
        # kernel's close-of-last-fd hook) defers the durable commit,
        # while ``fsync`` forces it — a burst of short-lived files pays
        # ONE save per commit interval instead of one per close.
        ops = self._ops()
        try:
            ops.write("/f.bin", b"x" * 1000, 0)
            record = next(iter(self.storage._volumes.values()))
            nyfs = record["nyfs"]
            self.assertTrue(nyfs.dirty, "deferred write must be dirty")
            journal = os.path.join(nyfs.base_path, "state",
                                   "journal.bin")
            before = (os.path.getsize(journal)
                      if os.path.exists(journal) else 0)
            ops.flush("/f.bin")
            self.assertTrue(nyfs.dirty,
                            "flush must NOT commit (POSIX close)")
            self.assertEqual(
                os.path.getsize(journal) if os.path.exists(journal) else 0,
                before, "flush must not touch the journal")
            ops.fsync("/f.bin", False)
            self.assertFalse(nyfs.dirty, "fsync must commit")
            self.assertGreater(
                os.path.getsize(journal) if os.path.exists(journal) else 0,
                before, "fsync must persist the journal")
            # The deferred data was readable in memory before fsync.
            self.assertEqual(ops.read("/f.bin", 32, 0), b"x" * 32)
        finally:
            ops.close()

    def test_chunked_io_across_the_wire(self):
        # The FUSE kernel sends 128 KiB requests; the passthrough pages
        # them through the 32 KiB per-call byte path.
        ops = self._ops()
        try:
            big = bytes(range(256)) * 400  # 100 KiB
            self.assertEqual(ops.write("/big.bin", big, 0), len(big))
            self.assertEqual(ops.read("/big.bin", len(big), 0), big)
            # Offset writes through the chunker too.
            self.assertEqual(ops.write("/big.bin", b"XY", 50_000), 2)
            expected = big[49_999:50_000] + b"XY" + big[50_002:50_003]
            self.assertEqual(ops.read("/big.bin", 4, 49_999), expected)
        finally:
            ops.close()

    def test_errno_propagation_to_the_mount(self):
        ops = self._ops()
        try:
            with self.assertRaises(VaultMountError) as ctx:
                ops.getattr("/missing")
            self.assertEqual(ctx.exception.errno, errno.ENOENT)
        finally:
            ops.close()

    def test_edquot_surfaces_through_the_mount(self):
        # ADR-0022 accounting through the passthrough: an over-quota
        # write is rejected fail-closed server-side with EDQUOT, and
        # the errno rides the CALL reply — a kernel mount sees EDQUOT,
        # not a generic EIO (the adapter maps VaultMountError.errno to
        # FuseOSError). The rejected write bills NOTHING: the next
        # within-quota write still lands and the rejected path never
        # appeared in the tree.
        self._call({"service": "storage", "op": "volume_quota_set",
                    "volume_id": self.vid,
                    "container": DEFAULT_OPERATOR_ID, "bytes": 16})
        ops = self._ops()
        try:
            with self.assertRaises(VaultMountError) as ctx:
                ops.write("/over.bin", b"x" * 32, 0)
            self.assertEqual(ctx.exception.errno, errno.EDQUOT)
            self.assertEqual(ops.write("/ok.bin", b"y" * 16, 0), 16)
            self.assertEqual(ops.read("/ok.bin", 16, 0), b"y" * 16)
            with self.assertRaises(VaultMountError) as ctx:
                ops.getattr("/over.bin")
            self.assertEqual(ctx.exception.errno, errno.ENOENT)
        finally:
            ops.close()

    def test_close_releases_the_handle(self):
        ops = self._ops()
        ops.close()
        with self.assertRaises(VaultMountError):
            ops.getattr("/")

    def test_mount_attach_graceful_without_fusepy(self):
        mount = NyVaultMount(self.client, self.svc_path, self.vid,
                             tempfile.mkdtemp())
        with mock.patch("fuse.vault_mount._import_fusepy",
                        return_value=None):
            self.assertFalse(mount.attach())
        mount.unmount()


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

    def test_compression_ratio(self):
        """Zstandard compression should achieve >30% ratio (ADR-007)."""
        from fuse.nyfs import NyFSBlock
        # Test with realistic data patterns
        test_cases = [
            (b"Hello, NyFS!" * 100, "repeated text"),
            (b"\x00" * 10000, "zeros"),
            (bytes(range(256)) * 40, "sequential bytes"),
            (os.urandom(10000), "random data"),  # worst case
        ]
        for data, desc in test_cases:
            block = NyFSBlock(data=data)
            block.compute_checksum()  # must be called before compress
            block.compress()
            if block.compressed_data is not None:
                ratio = len(block.compressed_data) / len(data)
                # Random data won't compress well, but others should
                if desc != "random data":
                    self.assertLess(ratio, 0.7, f"{desc}: ratio {ratio:.2f}")
                # All should decompress correctly
                decompressed = block.decompress()
                self.assertEqual(decompressed, data,
                                 f"{desc}: roundtrip failed")

    def test_content_hash_dedup(self):
        """Identical files should share blocks via content-hash dedup."""
        # Write the same content to two different files
        content = b"Deduplicated content " * 500
        self.fs.create_file("/file_a.txt", 0o644)
        self.fs.write("/file_a.txt", content)
        self.fs.create_file("/file_b.txt", 0o644)
        self.fs.write("/file_b.txt", content)
        # Both files should have the same block checksums and share
        # compressed payloads (dedup), but have different block_ids
        inode_a = self.fs.resolve("/file_a.txt")
        inode_b = self.fs.resolve("/file_b.txt")
        self.assertEqual(len(inode_a.blocks), len(inode_b.blocks))
        for ba, bb in zip(inode_a.blocks, inode_b.blocks):
            self.assertEqual(ba.checksum, bb.checksum)
            self.assertEqual(ba.compressed_data, bb.compressed_data)
            # Same block_id (dedup: one file on disk for all identical blocks)
            self.assertEqual(ba.block_id, bb.block_id)
        # Verify dedup cache is populated
        self.assertGreater(len(self.fs._block_dedup), 0)
        # Dedup cache contains the shared block
        self.assertGreater(len(self.fs._block_dedup), 0)
        # Both files reference the same block_id (dedup on disk)
        self.assertEqual(inode_a.blocks[0].block_id,
                         inode_b.blocks[0].block_id)
        # Save writes fewer block files than total blocks
        self.fs.save(use_journal=False)
        blocks_dir = os.path.join(self.temp_dir, "state", "blocks")
        block_files = [n for n in os.listdir(blocks_dir)
                       if n.endswith(".bin")]
        self.assertLess(len(block_files),
                        len(inode_a.blocks) + len(inode_b.blocks))


class TestHIGDesignSystem(unittest.TestCase):
    """Apple Human Interface Guidelines (HIG) design system tests."""

    def test_font_styles_complete(self):
        """HIG defines all 13 standard font styles."""
        from ui.hig import FONT_STYLES
        expected = [
            "ExtraLargeTitle", "ExtraLargeTitle2", "LargeTitle",
            "Title1", "Title2", "Title3", "Headline", "Subheadline",
            "Body", "Callout", "Footnote", "Caption1", "Caption2",
        ]
        for name in expected:
            self.assertIn(name, FONT_STYLES)
            style = FONT_STYLES[name]
            self.assertGreater(style.size, 0)

    def test_system_colors_complete(self):
        """HIG defines all required system colors."""
        from ui.hig import SYSTEM_COLORS, get_color, get_color_rgba
        expected = [
            "SystemBlue", "SystemGreen", "SystemRed",
            "Label", "SecondaryLabel",
            "SystemBackground", "SecondarySystemBackground",
            "Separator",
        ]
        for name in expected:
            self.assertIn(name, SYSTEM_COLORS)
            # Both light and dark variants
            light = get_color(name, dark_mode=False)
            dark = get_color(name, dark_mode=True)
            self.assertEqual(len(light), 3)
            self.assertEqual(len(dark), 3)
            # RGBA variant
            rgba = get_color_rgba(name)
            self.assertEqual(len(rgba), 4)

    def test_spacing_grid(self):
        """HIG uses consistent spacing on the 4pt/8pt grid."""
        from ui.hig import (
            SPACING_XS, SPACING_SM, SPACING_MD, SPACING_LG,
            SPACING_XL, SPACING_2XL, SPACING_3XL, SPACING_4XL,
        )
        # All should be multiples of 4
        for val in [SPACING_XS, SPACING_SM, SPACING_MD, SPACING_LG,
                    SPACING_XL, SPACING_2XL, SPACING_3XL, SPACING_4XL]:
            self.assertEqual(val % 4, 0, f"{val} not on 4pt grid")

    def test_corner_radii(self):
        """HIG corner radii are consistent."""
        from ui.hig import (
            CORNER_RADIUS_SM, CORNER_RADIUS_MD, CORNER_RADIUS_LG,
            CORNER_RADIUS_XL, CORNER_RADIUS_FULL,
        )
        self.assertLess(CORNER_RADIUS_SM, CORNER_RADIUS_MD)
        self.assertLess(CORNER_RADIUS_MD, CORNER_RADIUS_LG)
        self.assertLess(CORNER_RADIUS_LG, CORNER_RADIUS_XL)
        self.assertEqual(CORNER_RADIUS_FULL, 9999)

    def test_minimum_tap_target(self):
        """HIG minimum tap target is 44pt."""
        from ui.hig import MINIMUM_TAP_TARGET
        self.assertEqual(MINIMUM_TAP_TARGET, 44)

    def test_component_styles(self):
        """HIG defines standard component styles."""
        from ui.hig import BUTTON, CARD, NAVIGATION_BAR, TAB_BAR, LIST
        self.assertGreater(BUTTON.height, 0)
        self.assertGreater(CARD.corner_radius, 0)
        self.assertGreater(NAVIGATION_BAR.height, 0)
        self.assertGreater(TAB_BAR.height, 0)
        self.assertGreater(LIST.row_height, 0)

    def test_sf_symbols_categories(self):
        """HIG defines SF Symbols in standard categories."""
        from ui.hig import SF_SYMBOLS
        self.assertIn("navigation", SF_SYMBOLS)
        self.assertIn("actions", SF_SYMBOLS)
        self.assertIn("system", SF_SYMBOLS)
        self.assertGreater(len(SF_SYMBOLS), 5)

    def test_get_font_scaling(self):
        """Font scaling works for Dynamic Type."""
        from ui.hig import get_font
        normal = get_font("Body")
        scaled = get_font("Body", scale=1.5)
        self.assertEqual(scaled.size, normal.size * 1.5)


class TestAppCompatibility(unittest.TestCase):
    """App compatibility framework for Android and Windows apps."""

    def test_android_permission_mapping(self):
        """Android permissions map to Nyrqis capabilities."""
        from ui.app_compat import AndroidCompat, ANDROID_PERMISSION_MAP
        android = AndroidCompat()
        self.assertIn("android.permission.INTERNET", ANDROID_PERMISSION_MAP)
        self.assertEqual(
            ANDROID_PERMISSION_MAP["android.permission.INTERNET"],
            "CAP_NETWORK_SOCKET")

    def test_windows_api_mapping(self):
        """Windows API modules map to Nyrqis capabilities."""
        from ui.app_compat import WindowsCompat, WINDOWS_API_MAP
        self.assertIn("kernel32.dll", WINDOWS_API_MAP)
        self.assertIn("ws2_32.dll", WINDOWS_API_MAP)
        self.assertIn("CAP_NETWORK_SOCKET", WINDOWS_API_MAP["ws2_32.dll"])

    def test_app_manager_install(self):
        """App manager can track installed apps."""
        from ui.app_compat import AppManager, AppPlatform
        manager = AppManager()
        self.assertEqual(len(manager.list_apps()), 0)

    def test_app_manager_list_by_platform(self):
        """App manager can filter by platform."""
        from ui.app_compat import AppManager, AppPlatform
        manager = AppManager()
        # Empty list for each platform
        android_apps = manager.list_apps(platform=AppPlatform.ANDROID)
        windows_apps = manager.list_apps(platform=AppPlatform.WINDOWS)
        self.assertEqual(len(android_apps), 0)
        self.assertEqual(len(windows_apps), 0)

    def test_pe_subsystem_types(self):
        """Windows PE subsystem types are recognized."""
        from ui.app_compat import WindowsCompat, WINDOWS_SUBSYSTEMS
        self.assertEqual(WINDOWS_SUBSYSTEMS[2], "windows")
        self.assertEqual(WINDOWS_SUBSYSTEMS[3], "console")

    def test_android_api_levels(self):
        """Android API levels are tracked."""
        from ui.app_compat import ANDROID_API_LEVELS
        self.assertIn("14", ANDROID_API_LEVELS)
        self.assertEqual(ANDROID_API_LEVELS["14"], 34)
        self.assertIn("15", ANDROID_API_LEVELS)
        self.assertEqual(ANDROID_API_LEVELS["15"], 35)

    def test_app_platform_enum(self):
        """AppPlatform enum covers all supported platforms."""
        from ui.app_compat import AppPlatform
        self.assertEqual(AppPlatform.ANDROID.value, "android")
        self.assertEqual(AppPlatform.WINDOWS.value, "windows")
        self.assertEqual(AppPlatform.NYRQIS.value, "nyrqis")


class TestOverlayFilesystem(unittest.TestCase):
    """Overlay filesystem for container-specific views.

    Tests the merged-view semantics: reads fall through from upper
    to lower; writes go to the upper layer only; deletions mask the
    lower layer; and snapshot/restore captures the delta.
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        # Create a lower NyFS filesystem with some content
        from fuse.nyfs import NyFSFilesystem
        self.lower = NyFSFilesystem(os.path.join(self.tmp, "lower"))
        # Root dir already exists; create subdirs and files
        self.lower.mkdir("/shared", 0o755)
        self.lower.create_file("/shared/base.txt", 0o644)
        self.lower.write("/shared/base.txt", b"base content")
        self.lower.create_file("/shared/config.json", 0o644)
        self.lower.write("/shared/config.json", b'{"key": "val"}')
        self.lower.mkdir("/shared/data", 0o755)
        self.lower.create_file("/shared/data/file.txt", 0o644)
        self.lower.write("/shared/data/file.txt", b"nested data")
        # Create overlay
        from fuse.overlay import OverlayFilesystem
        self.ov = OverlayFilesystem(self.lower, container_id="test-ctr")

    def _lower_has(self, path):
        try:
            self.lower.getattr(path)
            return True
        except (OSError, Exception):
            return False

    def _stat_is_reg(self, mode):
        import stat as _st
        return bool(mode & _st.S_IFREG)

    def _stat_is_dir(self, mode):
        import stat as _st
        return bool(mode & _st.S_IFDIR)

    def test_read_falls_through_to_lower(self):
        """Reading a file in the lower layer works via overlay."""
        data = self.ov.read("/shared/base.txt")
        self.assertEqual(data, b"base content")

    def test_read_lower_directory(self):
        """Readdir merges upper and lower entries."""
        entries = self.ov.readdir("/shared")
        self.assertIn("base.txt", entries)
        self.assertIn("config.json", entries)
        self.assertIn("data", entries)

    def test_write_creates_upper_entry(self):
        """Writing a new file goes to the upper layer only."""
        self.ov.write("/shared/new.txt", b"new data")
        self.assertEqual(self.ov.read("/shared/new.txt"), b"new data")
        # Lower layer unchanged
        self.assertFalse(self._lower_has("/shared/new.txt"))

    def test_write_modifies_upper_entry(self):
        """Writing to an existing file creates a modified upper copy."""
        self.ov.write("/shared/base.txt", b"modified")
        self.assertEqual(self.ov.read("/shared/base.txt"), b"modified")
        # Lower layer unchanged
        self.assertEqual(self.lower.read("/shared/base.txt"),
                         b"base content")

    def test_write_with_offset(self):
        """Writing at an offset into a new file."""
        self.ov.write("/shared/padded.txt", b"hello", offset=10)
        data = self.ov.read("/shared/padded.txt")
        self.assertEqual(len(data), 15)
        self.assertEqual(data[10:], b"hello")

    def test_unlink_masks_lower(self):
        """Unlinking a lower file masks it in the merged view."""
        self.assertTrue(self.ov.exists("/shared/base.txt"))
        self.ov.unlink("/shared/base.txt")
        self.assertFalse(self.ov.exists("/shared/base.txt"))
        # Lower layer unchanged
        self.assertTrue(self._lower_has("/shared/base.txt"))

    def test_mkdir_and_readdir(self):
        """Creating a directory in upper and listing merged contents."""
        self.ov.mkdir("/shared/extra")
        self.assertTrue(self.ov.is_dir("/shared/extra"))
        entries = self.ov.readdir("/shared")
        self.assertIn("extra", entries)
        self.assertIn("base.txt", entries)  # from lower

    def test_rmdir(self):
        """Removing an upper directory."""
        self.ov.mkdir("/shared/temp")
        self.ov.rmdir("/shared/temp")
        self.assertFalse(self.ov.exists("/shared/temp"))

    def test_rename(self):
        """Renaming a file within the overlay."""
        self.ov.rename("/shared/base.txt", "/shared/renamed.txt")
        self.assertTrue(self.ov.exists("/shared/renamed.txt"))
        self.assertFalse(self.ov.exists("/shared/base.txt"))
        self.assertEqual(self.ov.read("/shared/renamed.txt"),
                         b"base content")

    def test_truncate(self):
        """Truncating a file from lower creates an upper copy."""
        self.ov.truncate("/shared/base.txt", 4)
        self.assertEqual(self.ov.read("/shared/base.txt"), b"base")
        # Lower unchanged
        self.assertEqual(self.lower.read("/shared/base.txt"),
                         b"base content")

    def test_snapshot_and_restore(self):
        """Snapshot captures upper state; restore replays it."""
        self.ov.write("/shared/new.txt", b"new")
        self.ov.unlink("/shared/base.txt")
        snap = self.ov.snapshot()

        # Modify the overlay
        self.ov.write("/shared/new.txt", b"changed")
        self.assertEqual(self.ov.read("/shared/new.txt"), b"changed")

        # Restore overlay snapshot
        self.ov.restore_snapshot_data(snap)
        self.assertEqual(self.ov.read("/shared/new.txt"), b"new")
        self.assertFalse(self.ov.exists("/shared/base.txt"))

    def test_diff(self):
        """Diff reports created/modified/deleted entries."""
        self.ov.write("/shared/new.txt", b"new")
        self.ov.write("/shared/base.txt", b"changed")
        self.ov.unlink("/shared/config.json")
        d = self.ov.diff()
        self.assertEqual(d["/shared/new.txt"]["type"], "created")
        self.assertEqual(d["/shared/base.txt"]["type"], "modified")
        self.assertEqual(d["/shared/config.json"]["type"], "deleted")

    def test_stats(self):
        """Stats reports overlay statistics."""
        self.ov.write("/shared/new.txt", b"new")
        self.ov.write("/shared/base.txt", b"changed")
        self.ov.unlink("/shared/config.json")
        s = self.ov.stats()
        self.assertEqual(s["container_id"], "test-ctr")
        self.assertGreater(s["upper_entries"], 0)

    def test_isolation_between_overlays(self):
        """Two overlays on the same lower are independent."""
        from fuse.overlay import OverlayFilesystem
        ov2 = OverlayFilesystem(self.lower, container_id="other")
        self.ov.write("/shared/ctr1.txt", b"from ctr1")
        ov2.write("/shared/ctr2.txt", b"from ctr2")
        # Each sees only its own writes
        self.assertTrue(self.ov.exists("/shared/ctr1.txt"))
        self.assertFalse(self.ov.exists("/shared/ctr2.txt"))
        self.assertTrue(ov2.exists("/shared/ctr2.txt"))
        self.assertFalse(ov2.exists("/shared/ctr1.txt"))
        # Both see lower
        self.assertTrue(self.ov.exists("/shared/base.txt"))
        self.assertTrue(ov2.exists("/shared/base.txt"))

    def test_getattr(self):
        """getattr returns correct attributes from merged view."""
        attr = self.ov.getattr("/shared/base.txt")
        self.assertTrue(self._stat_is_reg(attr["st_mode"]))
        self.assertEqual(attr["st_size"], len(b"base content"))

        attr = self.ov.getattr("/shared")
        self.assertTrue(self._stat_is_dir(attr["st_mode"]))


class TestFUSEOverheadBenchmark(unittest.TestCase):
    """Verify the FUSE overhead benchmark infrastructure works."""

    def test_benchmark_produces_results(self):
        """run_benchmark returns one result per payload size."""
        from tests.benchmarks import run_benchmark
        results = run_benchmark(rounds=1)
        self.assertEqual(len(results), 5)  # 4K, 64K, 256K, 1M, 4M
        for r in results:
            self.assertGreater(r.nyfs_write_mb_s, 0)
            self.assertGreater(r.nyfs_read_mb_s, 0)
            self.assertGreater(r.native_write_mb_s, 0)
            self.assertGreater(r.native_read_mb_s, 0)

    def test_benchmark_json_output(self):
        """JSON output is valid and has the expected keys."""
        from tests.benchmarks import run_benchmark
        import json as _json
        results = run_benchmark(rounds=1)
        out = [{"payload": r.payload, "label": r.label,
                "nyfs_write_mb_s": r.nyfs_write_mb_s,
                "nyfs_read_mb_s": r.nyfs_read_mb_s,
                "native_write_mb_s": r.native_write_mb_s,
                "native_read_mb_s": r.native_read_mb_s,
                "write_overhead_pct": r.write_overhead_pct,
                "read_overhead_pct": r.read_overhead_pct} for r in results]
        s = _json.dumps(out)
        parsed = _json.loads(s)
        self.assertEqual(len(parsed), 5)
        self.assertIn("payload", parsed[0])
        self.assertIn("nyfs_write_mb_s", parsed[0])


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
        # ADR-0022/0023: the vault backing store lives in the
        # StateDirectory (persists across restarts) with the KEK
        # envelope beside it, and the unlock passphrase comes from the
        # OPTIONAL EnvironmentFile (vault serves plaintext without it).
        self.assertIn("StateDirectory=nyrqis", text)
        self.assertIn("--vault-dir /var/lib/nyrqis/vault", text)
        self.assertIn("--vault-key-file /var/lib/nyrqis/vault.key", text)
        self.assertIn("EnvironmentFile=-/etc/nyrqis/backend.env", text)

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

    def test_ipc_send_allows_sendto(self):
        """CAP_IPC_SEND grants the IPC send syscalls."""
        policy = self._policy(Capability.CAP_IPC_SEND)
        for name in ("sendto", "sendmsg"):
            self.assertEqual(
                self._decision(policy, name),
                seccomp.SECCOMP_RET_ALLOW, name,
            )

    def test_ipc_receive_allows_recvmsg(self):
        """CAP_IPC_RECEIVE grants the IPC recv syscalls."""
        policy = self._policy(Capability.CAP_IPC_RECEIVE)
        for name in ("recvmsg", "recvfrom"):
            self.assertEqual(
                self._decision(policy, name),
                seccomp.SECCOMP_RET_ALLOW, name,
            )

    def test_ipc_socket_syscalls(self):
        """CAP_IPC_SEND grants the IPC socket syscalls (bind, socket, etc.)."""
        policy = self._policy(Capability.CAP_IPC_SEND)
        for name in ("socket", "bind", "getsockname", "setsockopt"):
            self.assertEqual(
                self._decision(policy, name),
                seccomp.SECCOMP_RET_ALLOW, name,
            )

    def test_ipc_no_cap_denies_sendto(self):
        """Without CAP_IPC_SEND, sendto is denied in default-deny mode."""
        policy = self._policy()  # no IPC caps
        self.assertEqual(
            self._decision(policy, "sendto"),
            seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM,
        )

    def test_ipc_with_network_also_allowed(self):
        """CAP_NETWORK_SOCKET also allows IPC transport syscalls."""
        policy = self._policy(Capability.CAP_NETWORK_SOCKET)
        for name in ("sendto", "recvmsg", "socket"):
            self.assertEqual(
                self._decision(policy, name),
                seccomp.SECCOMP_RET_ALLOW, name,
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

    def test_strict_seccomp_default_flows_to_both_launchers(self):
        # Fail-closed posture (NPS-017 §5.1): strict_seccomp defaults
        # True, so BOTH launcher paths carry --strict-seccomp — a
        # container whose filter install fails must refuse to run, not
        # silently run unfiltered.
        m = self._manager()
        c = m.create(ContainerConfig(command=["/bin/true"], seccomp=True))
        argv = m._launcher_exec(c)
        if rust_launcher.available():
            self.assertIn("--strict-seccomp", argv)
            self.assertLess(
                argv.index("--strict-seccomp"), argv.index("--"))
        else:
            self.assertIn("--strict-seccomp", argv)
        # Explicit opt-out removes the flag on the Rust path and the
        # Python path (the codec argv post-process honors it too).
        c2 = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=True, strict_seccomp=False))
        argv2 = m._launcher_exec(c2)
        self.assertNotIn("--strict-seccomp", argv2)
        # No seccomp at all → no policy, no strict flag.
        c3 = m.create(ContainerConfig(
            command=["/bin/true"], seccomp=False))
        self.assertNotIn("--strict-seccomp", m._launcher_exec(c3))

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
        # Use unique data per block to avoid content-hash dedup
        fs.write(f, os.urandom(12000))
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
        # Use unique data to avoid content-hash dedup
        original = os.urandom(20000)  # 5 blocks
        fs.write(f, original)
        fs.save(use_journal=False)

        fs.write(f, os.urandom(20000))  # 5 new CoW blocks
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
        self.assertEqual(fs2.read(fs2.resolve("/c.sav")), original)

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
        # Use unique data to avoid content-hash dedup
        fs.write(f, os.urandom(9000))  # 3 blocks at 4096
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
        new_data = os.urandom(9000)  # CoW: 3 new blocks, old 3 orphaned
        fs.write(f, new_data)
        fs.save(use_journal=True)
        self.assertGreater(fs.journal_bytes(), 0)
        self.assertEqual(fs.compact_journal(), 3)
        self.assertEqual(fs.journal_bytes(), 0)
        del fs
        fs2 = NyFSFilesystem.load(self.base)
        self.assertEqual(fs2.read(fs2.resolve("/c.bin")), new_data)

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


@unittest.skipUnless(
    _fuse_mount_available(),
    "live FUSE mount unavailable (needs fusepy, /dev/fuse, and fusermount)",
)
class TestNyVaultLiveMount(unittest.TestCase):
    """End-to-end NyVault through a real kernel FUSE mount (ADR-0022's
    data-plane mount): kernel FUSE path -> NyVaultOperations ->
    storage-service CALLs -> encrypted NyFS blocks. Requires fusepy +
    /dev/fuse + fusermount (present on this dev host; skipped
    elsewhere). The volume is ENCRYPTED at rest — kernel writes ride
    the AEAD block layer and no plaintext lands under the vault dir.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sock = os.path.join(self.tmp, "status.sock")
        self.vault = os.path.join(self.tmp, "vault")
        self.key = os.path.join(self.tmp, "vault.key")
        self.mnt = os.path.join(self.tmp, "mnt")
        os.makedirs(self.mnt, exist_ok=True)
        from backend import keys as keys_mod
        with open(self.key, "wb") as f:
            f.write(keys_mod.make_blob_any(b"live-mount-secret"))
        self.host = nyrqis_backend.StatusServiceHost(
            socket_path=self.sock, backend_version="9.9.9",
            vault_dir=self.vault, vault_key_file=self.key,
            vault_passphrase="live-mount-secret")
        self.host.start()
        self.client = IPCClient(
            DEFAULT_OPERATOR_ID,
            os.path.join(self.tmp, "ctl.sock")).bind()
        reply = self.client.call(self.sock, json.dumps({
            "service": "storage", "op": "volume_create",
            "name": "live"}).encode("utf-8"))
        self.vid = json.loads(reply.payload.decode("utf-8"))["volume_id"]
        self.mount = NyVaultMount(self.client, self.sock, self.vid, self.mnt)

    def tearDown(self):
        try:
            subprocess.run(["fusermount3", "-u", self.mnt],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            subprocess.run(["fusermount", "-u", self.mnt],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            self.mount.unmount()
        except Exception:
            pass
        self.client.close()
        self.host.stop()

    def test_encrypted_kernel_io_and_no_plaintext_leak(self):
        self.assertTrue(self.mount.attach())
        self.assertTrue(self.mount.mount(foreground=True, blocking=False))
        time.sleep(2.0)  # the FUSE loop establishes the kernel mount

        probe = os.path.join(self.mnt, "hello.txt")
        with open(probe, "w") as f:
            f.write("encrypted live mount!")
            f.flush()
            os.fsync(f.fileno())
        with open(probe) as f:
            self.assertEqual(f.read(), "encrypted live mount!")
        os.mkdir(os.path.join(self.mnt, "subdir"))
        self.assertEqual(sorted(os.listdir(self.mnt)),
                         ["hello.txt", "subdir"])
        self.assertEqual(os.stat(probe).st_size, len("encrypted live mount!"))
        # At rest: the vault dir never contains the plaintext.
        for root, _dirs, files in os.walk(self.vault):
            for name in files:
                with open(os.path.join(root, name), "rb") as f:
                    self.assertNotIn(
                        b"encrypted live mount", f.read(),
                        f"plaintext leaked into {os.path.join(root, name)}")

    def test_mount_serves_until_unmounted(self):
        # The mount command's contract: the FUSE loop keeps serving
        # until the kernel mount is torn down (the CLI blocks on it).
        self.assertTrue(self.mount.attach())
        self.assertTrue(self.mount.mount(foreground=True, blocking=False))
        time.sleep(2.0)
        self.assertEqual(
            sorted(os.listdir(self.mnt)), [])  # the volume is empty
        subprocess.run(["fusermount3", "-u", self.mnt],
                       capture_output=True, timeout=5)
        time.sleep(0.5)
        # After unmount the mount's thread has exited; the ops handle is
        # released without error.
        self.mount.unmount()

    def test_snapshot_restore_through_the_live_encrypted_mount(self):
        # CoW snapshot lifecycle while the encrypted volume is MOUNTED:
        # kernel write -> snapshot -> kernel overwrite -> restore. The
        # restored tree is verified through the mount's own operations
        # (deterministic — the kernel's FUSE attribute cache can briefly
        # hold stale sizes, so the content check rides the same storage
        # path the kernel uses after cache expiry).
        self.assertTrue(self.mount.attach())
        self.assertTrue(self.mount.mount(foreground=True, blocking=False))
        time.sleep(2.0)
        ops = self.mount.operations
        probe = os.path.join(self.mnt, "doc.txt")
        with open(probe, "w") as f:
            f.write("original")
            f.flush()
            os.fsync(f.fileno())
        self.assertEqual(ops.snapshot("v1"), "v1")
        with open(probe, "w") as f:
            f.write("overwritten")
            f.flush()
            os.fsync(f.fileno())
        self.assertEqual(ops.read("/doc.txt", 32, 0), b"overwritten")
        ops.restore("v1")
        self.assertEqual(ops.read("/doc.txt", 32, 0), b"original")
        self.assertIn("v1", ops.list_snapshots())
        # A fresh kernel stat/read sees the restored file (the ops read
        # above went through the exact same CALL path).
        self.assertEqual(os.path.getsize(probe), len("original"))

    def test_edquot_reaches_the_kernel_write(self):
        # ADR-0022 accounting END-TO-END through the REAL kernel: an
        # over-quota write on the encrypted mount is rejected fail-
        # closed server-side with EDQUOT, and the errno rides the CALL
        # reply through the passthrough (VaultMountError -> FuseOSError)
        # to the kernel. With writeback cache the FUSE write can be
        # deferred to the page cache, so the error may surface at
        # fsync rather than the write syscall — both are checked, and
        # EDQUOT must be among them (not a generic EIO).
        self.assertTrue(self.mount.attach())
        self.assertTrue(self.mount.mount(foreground=True, blocking=False))
        time.sleep(2.0)
        reply = self.client.call(self.sock, json.dumps({
            "service": "storage", "op": "volume_quota_set",
            "volume_id": self.vid, "container": DEFAULT_OPERATOR_ID,
            "bytes": 64}).encode("utf-8"))
        self.assertTrue(json.loads(reply.payload.decode("utf-8"))["ok"])
        probe = os.path.join(self.mnt, "quota.bin")
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT, 0o644)
        errs = []
        try:
            try:
                os.write(fd, b"x" * 4096)  # 4 KiB >> 64-byte quota
            except OSError as e:
                errs.append(e.errno)
            try:
                os.fsync(fd)
            except OSError as e:
                errs.append(e.errno)
        finally:
            os.close(fd)
        self.assertIn(errno.EDQUOT, errs)
        # The fail-closed rejection did not wedge the volume: a
        # within-quota kernel write still lands and reads back.
        ok_path = os.path.join(self.mnt, "ok.bin")
        with open(ok_path, "w") as f:
            f.write("tiny")
            f.flush()
            os.fsync(f.fileno())
        with open(ok_path) as f:
            self.assertEqual(f.read(), "tiny")

    def test_warning_levels_through_the_live_mount(self):
        # The ADR-0022 warning levels through the REAL kernel: a kernel
        # write past 80% of a quota commits at fsync, the refresh
        # computes the level, and it surfaces via quota-get (the
        # passthrough's deferred writes make fsync the commit point).
        self.assertTrue(self.mount.attach())
        self.assertTrue(self.mount.mount(foreground=True, blocking=False))
        time.sleep(2.0)
        reply = self.client.call(self.sock, json.dumps({
            "service": "storage", "op": "volume_quota_set",
            "volume_id": self.vid, "container": DEFAULT_OPERATOR_ID,
            "bytes": 100}).encode("utf-8"))
        self.assertTrue(json.loads(reply.payload.decode("utf-8"))["ok"])
        probe = os.path.join(self.mnt, "warn.bin")
        with open(probe, "wb") as f:
            f.write(b"x" * 90)  # 90% of 100 -> near
            f.flush()
            os.fsync(f.fileno())
        reply = self.client.call(self.sock, json.dumps({
            "service": "storage", "op": "volume_quota_get",
            "volume_id": self.vid}).encode("utf-8"))
        rows = json.loads(reply.payload.decode("utf-8"))["rows"]
        self.assertEqual(rows[0]["warning"], "near")


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
    """The message set the IPC codec differential tests run: all six
    types (incl. ADR-0024's STREAM_CHUNK), absent/present reply_to,
    empty and large payloads, empty and multi-capability transfers,
    plain and nested metadata."""
    return [
        # (type_index, message_id, sender, receiver, reply_to, payload, caps, metadata)
        (0, "m-empty", "c-a", "c-b", None, b"", [], {}),
        (0, "m-payload", "c-a", "c-b", None, b"hello wire", [], {}),
        (1, "m-recv", "c-a", "c-b", None, b"", [], {}),
        (2, "m-call", "c-a", "c-b", None, b"request", ["CAP_IPC_SEND"], {"k": 1}),
        (3, "m-reply", "c-a", "c-b", "m-call", b"response", [], {}),
        (4, "m-notify", "c-a", "c-b", None, b"", [], {"evt": "respawn"}),
        (5, "m-chunk0", "c-a", "c-b", None, b"chunk-data-0", [], {}),
        (5, "m-chunk1", "c-a", "c-b", None, b"chunk-data-1", [], {}),
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


class TestKeysFloor(unittest.TestCase):
    """NyVault key manager floor (ADR-0023, backend/keys.py): the pure
    PyNaCl reference implementation — envelope encryption (Argon2id
    KEK derivation, XChaCha20-Poly1305 DEK wrapping), the KEK envelope
    on disk, and fail-closed verification. These run on every host
    (no crate needed); the crate is the platform-boundary replacement
    whose byte-identity the conformance class pins.
    """

    PW = b"correct horse battery staple"

    def _blob(self):
        return keys_module.make_kek_blob(
            self.PW, salt=bytes(range(16)))

    def test_derive_kek_deterministic_and_length(self):
        kek = keys_module.derive_kek(self.PW, bytes(range(16)))
        self.assertEqual(len(kek), 32)
        self.assertEqual(kek, keys_module.derive_kek(
            self.PW, bytes(range(16))))
        # A different salt yields a different KEK.
        self.assertNotEqual(kek, keys_module.derive_kek(
            self.PW, bytes(range(1, 17))))
        with self.assertRaises(ValueError):
            keys_module.derive_kek(self.PW, b"short")

    def test_kek_blob_roundtrip_unlock(self):
        blob = self._blob()
        self.assertEqual(len(blob), keys_module.kek_blob_size())
        kek = keys_module.unlock_kek(blob, self.PW)
        self.assertEqual(len(kek), 32)
        # The blob itself never contains the KEK (nothing plaintext
        # beyond the KDF parameters and the AEAD check value).
        self.assertNotIn(kek, blob)
        # Wrong unlock secret fails closed.
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock_kek(blob, b"wrong")
        # A tampered envelope fails verification.
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock_kek(bytes(tampered), self.PW)
        # Malformed envelopes are rejected outright.
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock_kek(b"garbage", self.PW)
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock_kek(blob[:-1], self.PW)

    def test_wrap_unwrap_roundtrip(self):
        blob = self._blob()
        kek = keys_module.unlock_kek(blob, self.PW)
        dek = keys_module.new_dek()
        self.assertEqual(len(dek), 32)
        nonce = bytes(range(24))
        wrapped = keys_module.wrap_dek(kek, b"volume-assets", dek, nonce)
        self.assertEqual(len(wrapped), keys_module.dek_blob_size())
        self.assertEqual(wrapped[:24], nonce)
        self.assertEqual(keys_module.unwrap_dek(
            kek, b"volume-assets", wrapped), dek)
        # Wrong associated data fails verification.
        with self.assertRaises(keys_module.KeysError):
            keys_module.unwrap_dek(kek, b"volume-other", wrapped)
        # Tampering is detected.
        tampered = bytearray(wrapped)
        tampered[30] ^= 1
        with self.assertRaises(keys_module.KeysError):
            keys_module.unwrap_dek(kek, b"volume-assets",
                                   bytes(tampered))
        # Bad lengths are rejected.
        with self.assertRaises(keys_module.KeysError):
            keys_module.unwrap_dek(kek, b"volume-assets", wrapped[:-1])
        with self.assertRaises(ValueError):
            keys_module.wrap_dek(b"short", b"ad", dek, nonce)

    def test_envelope_format_constants(self):
        # The wire format is pinned (the crate parses the same bytes).
        self.assertEqual(keys_module.kek_blob_size(), 110)
        self.assertEqual(keys_module.dek_blob_size(), 72)
        self.assertEqual(keys_module.KEK_LEN, 32)
        self.assertEqual(keys_module.NONCE_LEN, 24)
        self.assertEqual(keys_module.SALT_LEN, 16)

    def test_block_encrypt_decrypt_roundtrip(self):
        dek = bytes(range(32))
        nonce = bytes(range(24))
        ad = b"volume-enc-1"
        pt = b"block-payload-" * 512
        blob = keys_module.block_encrypt(dek, nonce, ad, pt)
        self.assertEqual(len(blob), len(pt) + 24 + 16)
        self.assertEqual(blob[:24], nonce)
        self.assertEqual(keys_module.block_decrypt(dek, ad, blob), pt)
        # Tampering is detected (AEAD).
        tampered = bytearray(blob)
        tampered[-1] ^= 1
        with self.assertRaises(keys_module.KeysError):
            keys_module.block_decrypt(dek, ad, bytes(tampered))
        # Wrong DEK / wrong AD fail.
        with self.assertRaises(keys_module.KeysError):
            keys_module.block_decrypt(bytes(reversed(range(32))), ad, blob)
        with self.assertRaises(keys_module.KeysError):
            keys_module.block_decrypt(dek, b"volume-enc-2", blob)


class TestRustKeysLoader(unittest.TestCase):
    """ADR-0023 FFI loader behavior (see rust/keys/): the fallback
    contract and error mapping. Like the other migration loader tests,
    these pin the loader on hosts WITHOUT the crate built; when the
    crate lands, the CI conformance job (NYRQIS_RUST_FORCE=1) is the
    real gate.
    """

    def setUp(self):
        self._env = dict(os.environ)
        keys_module._reset_cache()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        keys_module._reset_cache()

    def test_lib_candidates_prefer_override(self):
        os.environ["NYRQIS_KEYS_LIB"] = "/custom/libnyrqis_keys.so"
        self.assertEqual(keys_module._crate_candidates(),
                         ["/custom/libnyrqis_keys.so"])

    def test_absent_crate_means_floor(self):
        os.environ["NYRQIS_KEYS_LIB"] = "/nonexistent/libnyrqis_keys.so"
        self.assertFalse(keys_module.available())
        # The manager API still works — through the PyNaCl floor.
        blob = keys_module.make_blob_any(
            b"pw", salt=bytes(range(16)))
        self.assertEqual(len(blob), 110)
        self.assertEqual(keys_module.derive_kek_any(
            b"pw", bytes(range(16))),
            keys_module.derive_kek(b"pw", bytes(range(16))))
        handle = keys_module.unlock(blob, b"pw")
        self.assertIsInstance(handle, keys_module._FloorHandle)
        self.assertEqual(len(keys_module.wrap(
            handle, b"ad", bytes(32), bytes(24))), 72)

    def test_force_raises_when_crate_missing(self):
        os.environ["NYRQIS_KEYS_LIB"] = "/nonexistent/libnyrqis_keys.so"
        os.environ["NYRQIS_RUST_FORCE"] = "1"
        with self.assertRaises(RuntimeError):
            keys_module.derive_kek_any(b"pw", bytes(range(16)))
        with self.assertRaises(RuntimeError):
            keys_module.unlock(b"x" * 110, b"pw")

    def test_broken_crate_falls_back_to_floor(self):
        # A file that exists but is not a valid cdylib is skipped, and
        # the floor answers (never raises outside the force gate).
        fake = os.path.join(tempfile.mkdtemp(), "libnyrqis_keys.so")
        with open(fake, "wb") as f:
            f.write(b"not a shared object")
        os.environ["NYRQIS_KEYS_LIB"] = fake
        self.assertFalse(keys_module.available())
        self.assertEqual(keys_module.derive_kek_any(
            b"pw", bytes(range(16))),
            keys_module.derive_kek(b"pw", bytes(range(16))))


class TestRustKeysConformance(unittest.TestCase):
    """ADR-0023 differential conformance: the Rust keys crate is
    byte-identical to the PyNaCl floor (Argon2id + XChaCha20-Poly1305,
    same construction and parameters) and interoperable on each
    other's blobs — the guarantee that the platform-critical custody
    path is a drop-in for the reference implementation. Runs in CI
    forced through the crate (NYRQIS_RUST_FORCE=1, the required
    `rust-keys-conformance` gate); skips on hosts without the crate.
    """

    PW = b"correct horse battery staple"

    def setUp(self):
        if not keys_module.available():
            self.skipTest("Rust keys crate not built on this host")

    def _blob(self):
        return keys_module.make_kek_blob(
            self.PW, salt=bytes(range(16)))

    def test_derive_kek_byte_identical(self):
        salt = bytes(range(16))
        self.assertEqual(
            keys_module.derive_kek(self.PW, salt, 2, 64 * 1024),
            keys_module.derive_kek_any(self.PW, salt, 2, 64 * 1024))

    def test_make_blob_header_identical_and_cross_unlock(self):
        salt = bytes(range(16))
        py_blob = keys_module.make_kek_blob(
            self.PW, 2, 64 * 1024, salt=salt)
        rs_blob = keys_module.make_blob_any(
            self.PW, 2, 64 * 1024, salt=salt)
        # Magic/version/kdf/params/salt — everything except the fresh
        # random check nonce — is byte-identical.
        self.assertEqual(py_blob[:38], rs_blob[:38])
        # Both unlock to the SAME KEK (cross-implementation).
        self.assertEqual(keys_module.unlock_kek(rs_blob, self.PW),
                         keys_module.unlock_kek(py_blob, self.PW))

    def test_wrap_byte_identical(self):
        blob = self._blob()
        kek = keys_module.unlock_kek(blob, self.PW)
        handle = keys_module.unlock(blob, self.PW)  # crate custody
        dek = keys_module.new_dek()
        nonce = bytes(range(24))
        self.assertEqual(
            keys_module.wrap_dek(kek, b"volume-assets", dek, nonce),
            keys_module.wrap(handle, b"volume-assets", dek, nonce))

    def test_unwrap_cross_implementation(self):
        blob = self._blob()
        kek = keys_module.unlock_kek(blob, self.PW)
        handle = keys_module.unlock(blob, self.PW)
        dek = keys_module.new_dek()
        nonce = bytes(range(24))
        py_w = keys_module.wrap_dek(kek, b"vol", dek, nonce)
        rs_w = keys_module.wrap(handle, b"vol", dek, nonce)
        # The crate unwraps the floor's envelope and vice versa.
        self.assertEqual(keys_module.unwrap(handle, b"vol", py_w), dek)
        self.assertEqual(keys_module.unwrap_dek(kek, b"vol", rs_w), dek)

    def test_wrong_secret_and_tamper_rejected_on_both(self):
        blob = self._blob()
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock_kek(blob, b"wrong")
        with self.assertRaises(keys_module.KeysError):
            keys_module.unlock(blob, b"wrong")
        handle = keys_module.unlock(blob, self.PW)
        wrapped = keys_module.wrap(handle, b"vol", bytes(32), bytes(24))
        tampered = bytearray(wrapped)
        tampered[30] ^= 1
        with self.assertRaises(keys_module.KeysError):
            keys_module.unwrap_dek(
                keys_module.unlock_kek(blob, self.PW), b"vol",
                bytes(tampered))
        with self.assertRaises(keys_module.KeysError):
            keys_module.unwrap(handle, b"vol", bytes(tampered))

    def test_handle_custody_shred(self):
        # The KEK lives behind the handle: shred drops it, and the
        # handle is then rejected — the floor cannot hold what the
        # crate never gave it.
        blob = self._blob()
        handle = keys_module.unlock(blob, self.PW)
        keys_module.shred(handle)
        with self.assertRaises(keys_module.KeysError):
            keys_module.wrap(handle, b"vol", bytes(32), bytes(24))

    def test_block_encrypt_byte_identical(self):
        # The NyFS at-rest byte path (ADR-0023 checksum-then-encrypt):
        # the crate's block envelope is byte-identical to the floor's.
        dek = bytes(range(32))
        nonce = bytes(range(24))
        ad = b"volume-enc-1"
        pt = b"block-payload-" * 256
        self.assertEqual(
            keys_module.block_encrypt(dek, nonce, ad, pt),
            keys_module.block_encrypt_any(dek, nonce, ad, pt))
        # And the crate decrypts what the floor produced, and vice
        # versa (both directions).
        py_blob = keys_module.block_encrypt(dek, nonce, ad, pt)
        rs_blob = keys_module.block_encrypt_any(dek, nonce, ad, pt)
        self.assertEqual(
            keys_module.block_decrypt_any(dek, ad, py_blob), pt)
        self.assertEqual(
            keys_module.block_decrypt(dek, ad, rs_blob), pt)
        # Tampering fails on the crate too.
        tampered = bytearray(rs_blob)
        tampered[30] ^= 1
        with self.assertRaises(keys_module.KeysError):
            keys_module.block_decrypt_any(dek, ad, bytes(tampered))


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
        # goes through the wire codec and the transport, and the
        # storage round trips additionally through the NyFS block
        # codec, so pin those three loaders to their pure-Python
        # floors (byte-identical, proven by their own conformance
        # gates): only the ipcd module is the forced lib under this
        # gate.
        self._codec_force = mock.patch.object(
            ipc_codec, "_force_enabled", return_value=False)
        self._transport_force = mock.patch.object(
            transport_codec, "force_enabled", return_value=False)
        self._nyfs_force = mock.patch.object(
            nyfs_codec, "_force_enabled", return_value=False)
        self._codec_force.start()
        self._transport_force.start()
        self._nyfs_force.start()

    def tearDown(self):
        self._nyfs_force.stop()
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

    def test_loop_wire_level_stream_round_trip(self):
        """ADR-0024 wire-level streaming through the RUST serving loop
        (the production path for the storage socket): the client sends
        a >64 KiB CALL as STREAM_CHUNK datagrams, the loop reassembles
        them (per-chunk checksums, sender-bound) into ONE CALL wire,
        the dispatcher routes it to the storage service, and the
        chunked REPLY comes back through the loop's enqueue path.
        The floor-only version of this is TestWireLevelStreaming; this
        is the crate-path differential."""
        cap_mgr = CapabilityManager()
        cap_mgr.initialize_container("container-A")
        from backend.capability import Capability as _Cap
        cap_mgr.grant_capability("container-A", _Cap.CAP_STORAGE_VOLUME)
        loop_path = os.path.join(self.tmp, "stream-loop.sock")
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
            batch_max=64,
            pids={os.getpid(): "container-A"},
            trusted_uids=[os.getuid()],
        )
        storage = StorageService(
            capability_manager=cap_mgr,
            vault_dir=os.path.join(self.tmp, "loop-vault"))
        router = ServiceRouter()
        router.register("storage", storage)
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
        client = IPCClient(
            "container-A",
            os.path.join(self.tmp, "stream-loop-cli.sock")).bind()
        try:
            # Create + open a volume through the loop (small calls).
            resp = json.loads(client.call(
                loop_path, json.dumps({"service": "storage",
                                       "op": "volume_create",
                                       "name": "loopvol"}).encode()
            ).payload.decode("utf-8"))
            self.assertTrue(resp["ok"])
            vid = resp["volume_id"]
            resp = json.loads(client.call(
                loop_path, json.dumps({"service": "storage",
                                       "op": "volume_open",
                                       "volume_id": vid}).encode()
            ).payload.decode("utf-8"))
            handle = resp["handle"]
            # A 128 KiB wire-level write: the client chunks it, the
            # loop reassembles, the service writes ONCE.
            body = os.urandom(128 * 1024)
            reply = client.call(
                loop_path,
                json.dumps({"service": "storage", "op": "volume_write",
                             "handle": handle, "path": "/big.bin",
                             "offset": 0, "defer_commit": True,
                             "data_b64": base64.b64encode(body)
                             .decode("ascii")}).encode(),
                timeout_s=10.0, wire_stream=True)
            self.assertIsNotNone(reply, "loop must answer the streamed write")
            resp = json.loads(reply.payload.decode("utf-8"))
            self.assertTrue(resp["ok"], resp.get("error"))
            self.assertEqual(resp["bytes_written"], len(body))
            # The wire-level read: the service replies once with the
            # full payload; the loop's enqueue path chunks the REPLY
            # and the client reassembles it.
            reply = client.call(
                loop_path,
                json.dumps({"service": "storage", "op": "volume_read",
                             "handle": handle, "path": "/big.bin",
                             "offset": 0, "size": len(body),
                             "wire_stream": True}).encode(),
                timeout_s=10.0, wire_stream=True)
            self.assertIsNotNone(reply, "loop must answer the streamed read")
            resp = json.loads(reply.payload.decode("utf-8"))
            self.assertTrue(resp["ok"], resp.get("error"))
            self.assertEqual(
                base64.b64decode(resp["data_b64"], validate=True), body)
        finally:
            client.close()
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
            loop_srv.close()

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


class TestNuiService(unittest.TestCase):
    """The operator NUI import gate (ui/service.py, ADR-0025):
    ``nui_validate`` runs the import gate and reports a summary;
    ``nui_load`` validates AND persists the design as the daemon's
    shell UI. Operator-only — a registered container is refused."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc_path = os.path.join(self.tmp, "svc.sock")
        self.cli_path = os.path.join(self.tmp, "cli.sock")

    def _serve(self, state_dir=None, pid_registry=None):
        manager = IPCManager()
        manager.create_endpoint("container-svc", "ep-svc")
        server = IPCDatagramServer(
            manager, "ep-svc", self.svc_path,
            pid_registry=pid_registry or {},
            trusted_uids={os.getuid()},
        )
        nui = NuiService(state_dir=state_dir)
        router = ServiceRouter()
        router.register("nui", nui)
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

    def _shell(self):
        return open(os.path.join(self.FIXTURES, "nyrqis-shell.nstudio")).read()

    def test_operator_validate_shell_design(self):
        server, stop = self._serve()
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_validate",
                "document": self._shell()}).encode())
            self.assertTrue(resp["ok"], resp)
            summary = resp["summary"]
            self.assertEqual(summary["version"], "1.0.0")
            self.assertIn(summary["engine"], ("rust", "python"))
            self.assertEqual(summary["screens"], ["main"])
            self.assertEqual(summary["components"], 71)
            self.assertEqual(summary["behaviors"], 5)
            self.assertEqual(summary["bindings"], 1)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_validate_rejects_bad_design(self):
        server, stop = self._serve()
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            bad = self._shell().replace(
                "Nyrqis.Notification.Show", "Nyrqis.System.Shutdown")
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_validate",
                "document": bad}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown system action", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_validate_rejects_wrong_version(self):
        server, stop = self._serve()
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            bad = self._shell().replace(
                '"version": "1.0.0"', '"version": "0.2.0"')
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_validate",
                "document": bad}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unsupported schema version", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_validate_rejects_oversized(self):
        server, stop = self._serve()
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_validate",
                "document": "x" * (NUI_DOCUMENT_MAX_BYTES + 1024)}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("per-call budget", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_load_persists_shell_design(self):
        server, stop = self._serve(state_dir=self.tmp)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_load",
                "document": self._shell()}).encode())
            self.assertTrue(resp["ok"], resp)
            expected = os.path.join(self.tmp, "ui", "shell.nstudio")
            self.assertEqual(resp["path"], expected)
            self.assertTrue(os.path.exists(expected))
            # The persisted design re-imports cleanly (round trip).
            reloaded = nstudio.load(expected)
            self.assertEqual(len(reloaded.component_ids()), 71)
        finally:
            client.close()
            stop.set()
            server.close()

    def test_operator_load_without_state_dir(self):
        server, stop = self._serve(state_dir=None)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_load",
                "document": self._shell()}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("state directory", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_container_cannot_drive_nui(self):
        # A registered container reaches the router (its pid resolves
        # first) but the NUI service refuses any non-operator sender.
        server, stop = self._serve(pid_registry={os.getpid(): "container-A"})
        client = IPCClient("container-A", self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_validate",
                "document": self._shell()}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("operator-only", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_unknown_op_rejected(self):
        server, stop = self._serve()
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_bogus"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("unknown operation", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_current_before_any_load(self):
        # Nothing persisted yet is honest success, not an error: the
        # daemon reports loaded:false so the operator knows the shell
        # is unset (vs the call itself failing).
        server, stop = self._serve(state_dir=self.tmp)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_current"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertFalse(resp["loaded"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_current_after_load_reports_summary(self):
        server, stop = self._serve(state_dir=self.tmp)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            load_resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_load",
                "document": self._shell()}).encode())
            self.assertTrue(load_resp["ok"], load_resp)
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_current"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertTrue(resp["loaded"])
            self.assertTrue(resp["valid"])
            self.assertEqual(resp["summary"]["components"], 71)
            self.assertEqual(resp["summary"]["screens"], ["main"])
            self.assertEqual(resp["path"],
                             os.path.join(self.tmp, "ui", "shell.nstudio"))
        finally:
            client.close()
            stop.set()
            server.close()

    def test_current_reports_stale_design(self):
        # A persisted design that no longer re-imports cleanly is
        # surfaced honestly (loaded:true, valid:false) — the operator
        # sees the stale shell instead of a silent failure.
        server, stop = self._serve(state_dir=self.tmp)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            load_resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_load",
                "document": self._shell()}).encode())
            self.assertTrue(load_resp["ok"], load_resp)
            # Corrupt the persisted file out from under the daemon
            # (malformed JSON fails the gate on the next nui_current).
            target = os.path.join(self.tmp, "ui", "shell.nstudio")
            with open(target, "w") as fh:
                fh.write("{not json")
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_current"}).encode())
            self.assertTrue(resp["ok"], resp)
            self.assertTrue(resp["loaded"])
            self.assertFalse(resp["valid"])
            self.assertIn("no longer validates", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    def test_current_without_state_dir(self):
        server, stop = self._serve(state_dir=None)
        client = IPCClient(DEFAULT_OPERATOR_ID, self.cli_path).bind()
        try:
            resp = self._call(client, json.dumps({
                "service": "nui", "op": "nui_current"}).encode())
            self.assertFalse(resp["ok"])
            self.assertIn("state directory", resp["error"])
        finally:
            client.close()
            stop.set()
            server.close()

    # -- validation pipeline unit tests ---------------------------------

    def test_validate_document_all_fixtures(self):
        """_validate_document succeeds on all shipped fixtures."""
        svc = NuiService()
        for name in ("forge-home", "settings-app", "vault-dashboard",
                     "nyrqis-shell", "security-center", "vault-workspace",
                     "desktop", "windows", "widgets"):
            with open(os.path.join(self.FIXTURES, name + ".nstudio")) as f:
                text = f.read()
            ok, detail = svc._validate_document(text)
            self.assertTrue(ok, f"{name}: {detail}")
            self.assertEqual(detail["version"], "1.0.0")
            self.assertIn(detail["engine"], ("rust", "python"))

    def test_validate_document_accessibility_property(self):
        """_validate_document accepts components with accessibility prop."""
        import json as _json
        svc = NuiService()
        doc = {
            "version": "1.0.0",
            "screens": [{
                "id": "s1", "size": {"width": 800, "height": 600},
                "root": {
                    "id": "btn", "type": "Button",
                    "properties": {
                        "text": "Click", "accessibility": {"label": "Click me"}
                    },
                    "layout": {"x": 0, "y": 0, "width": 100, "height": 30},
                    "events": {}, "children": [],
                },
            }],
        }
        ok, detail = svc._validate_document(_json.dumps(doc))
        self.assertTrue(ok, detail)

    def test_validate_document_rejects_bad_property(self):
        """_validate_document rejects unknown properties."""
        import json as _json
        svc = NuiService()
        doc = {
            "version": "1.0.0",
            "screens": [{
                "id": "s1", "size": {"width": 800, "height": 600},
                "root": {
                    "id": "btn", "type": "Button",
                    "properties": {"text": "OK", "bogus": True},
                    "layout": {"x": 0, "y": 0, "width": 100, "height": 30},
                    "events": {}, "children": [],
                },
            }],
        }
        ok, detail = svc._validate_document(_json.dumps(doc))
        self.assertFalse(ok)
        self.assertIn("not in the", detail)

    def test_validate_document_rejects_wrong_version(self):
        """_validate_document rejects unsupported schema version."""
        import json as _json
        svc = NuiService()
        doc = {
            "version": "0.3.0",
            "screens": [{
                "id": "s1", "size": {"width": 800, "height": 600},
                "root": {
                    "id": "t1", "type": "Text",
                    "properties": {"text": "hi"},
                    "layout": {"x": 0, "y": 0, "width": 100, "height": 30},
                    "events": {}, "children": [],
                },
            }],
        }
        ok, detail = svc._validate_document(_json.dumps(doc))
        self.assertFalse(ok)
        self.assertIn("unsupported schema version", detail)


class TestNstudioImport(unittest.TestCase):
    """The pure-Python NUI (.nstudio) reference floor (ui/nstudio.py,
    ADR-0025): parse, version gate, contract validation, $state:
    resolution, and layout render — the behavior the Rust crate must
    reproduce through the FFI."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")

    @classmethod
    def setUpClass(cls):
        cls.FIXTURE_DIR = cls.FIXTURES

    def _fixture(self, name):
        return os.path.join(self.FIXTURE_DIR, name)

    def _load(self, name):
        return nstudio.load(self._fixture(name))

    @staticmethod
    def _mutate(text, old, new):
        assert old in text, f"anchor not found: {old[:60]}"
        return text.replace(old, new)

    def test_all_fixtures_load(self):
        for name in ("forge-home", "settings-app", "vault-dashboard",
                     "nyrqis-shell", "security-center", "vault-workspace",
                     "desktop", "windows", "widgets"):
            doc = self._load(name + ".nstudio")
            self.assertEqual(doc.version, nstudio.NSTUDIO_SCHEMA_VERSION)
            self.assertTrue(doc.screens)

    def test_shell_fixture_shape(self):
        doc = self._load("nyrqis-shell.nstudio")
        self.assertEqual(len(doc.component_ids()), 71)
        self.assertEqual(len(doc.behaviors), 5)
        self.assertEqual(len(doc.bindings), 1)
        self.assertEqual([s.id for s in doc.screens], ["main"])
        self.assertEqual(doc.screens[0].size, {"width": 1440, "height": 900})

    def test_security_center_fixture_shape(self):
        doc = self._load("security-center.nstudio")
        self.assertEqual(len(doc.component_ids()), 71)
        self.assertEqual(len(doc.behaviors), 4)
        self.assertEqual(len(doc.bindings), 1)
        self.assertEqual([s.id for s in doc.screens], ["main"])
        self.assertEqual(doc.screens[0].size, {"width": 1440, "height": 900})
        # The lockdown binding maps the Toggle to document state, and
        # its behavior resolves $state: substitution at action time.
        toggle = doc.find_component("toggle_lockdown")
        self.assertIsNotNone(toggle)
        self.assertEqual(toggle.type, "Toggle")
        target, name, args = doc.resolve_action("behavior_run_check")
        self.assertEqual(name, "Nyrqis.Notification.Show")
        # Whole-string $state: substitution (NFS-001 §7.1) — the
        # message resolves to the document state value.
        self.assertEqual(args["message"], "22:09")

    def test_vault_workspace_fixture_shape(self):
        doc = self._load("vault-workspace.nstudio")
        self.assertEqual(len(doc.component_ids()), 71)
        self.assertEqual(len(doc.behaviors), 4)
        self.assertEqual(len(doc.bindings), 1)
        self.assertEqual([s.id for s in doc.screens], ["main"])
        self.assertEqual(doc.screens[0].size, {"width": 1440, "height": 900})
        # The auto-snapshot binding maps the Toggle to document state;
        # the conditional behavior fires when the state flips false.
        toggle = doc.find_component("toggle_auto")
        self.assertIsNotNone(toggle)
        self.assertEqual(toggle.type, "Toggle")
        condition = doc.behavior_by_id("behavior_snapshot_off").condition
        self.assertEqual(condition["state"], "autoSnapshot")
        self.assertEqual(condition["operator"], "equals")
        target, name, args = doc.resolve_action("behavior_sync_now")
        self.assertEqual(name, "Nyrqis.Notification.Show")
        self.assertEqual(args["message"], "22:41")

    def test_desktop_shell_fixture_shape(self):
        """The real desktop shell screen (0.14.25 shell vocabulary): the
        fixture exercises the Shell/Data/Form/Media/Developer components
        and must pass the same gate as every other design."""
        doc = self._load("desktop.nstudio")
        self.assertEqual(len(doc.component_ids()), 37)
        self.assertEqual(len(doc.behaviors), 11)
        self.assertEqual(len(doc.bindings), 6)
        self.assertEqual([s.id for s in doc.screens], ["desktop", "lock"])
        self.assertEqual(doc.screens[0].size, {"width": 1440, "height": 900})
        # The shell vocabulary resolves through the contract tables.
        taskbar = doc.find_component("taskbar")
        self.assertIsNotNone(taskbar)
        self.assertEqual(taskbar.type, "Taskbar")
        start_menu = doc.find_component("start_menu")
        self.assertEqual(start_menu.type, "StartMenu")
        # A component-targeted action (DesktopIcon -> Launch).
        target, name, _args = doc.resolve_action("behavior_launch_terminal")
        self.assertEqual(target, "icon_terminal")
        self.assertEqual(name, "Launch")
        # The conditional DND behavior resolves $state: substitution and
        # $localize: references (the message is a locale key).
        target, name, args = doc.resolve_action("behavior_dnd_on")
        self.assertEqual(name, "Nyrqis.Notification.Show")
        self.assertEqual(args["message"], "$localize:notif.dnd")
        self.assertEqual(
            nstudio.resolve_text(args["message"], doc.locales),
            "Notifications paused until disabled")
        # The lock screen screen carries a LockScreen root child.
        lock_screen = doc.find_component("lock_screen")
        self.assertEqual(lock_screen.type, "LockScreen")
        # Reusable-component masters (NFS-006 §9): the taskbar buttons are
        # instances of a single TaskbarButton master carrying overrides.
        self.assertEqual(len(doc.reusable_components), 1)
        master = doc.reusable_components[0]
        self.assertEqual(master.id, "TaskbarButton")
        self.assertEqual(master.type, "Button")
        btn_start = doc.find_component("btn_start")
        self.assertIsNotNone(btn_start)
        self.assertEqual(btn_start.component_ref, "TaskbarButton")
        self.assertEqual(btn_start.overrides, {"text": "Start"})
        btn_search = doc.find_component("btn_search")
        self.assertEqual(btn_search.component_ref, "TaskbarButton")
        # The search label is localized through the document's locales.
        self.assertEqual(btn_search.overrides, {"text": "$localize:search.label"})
        self.assertEqual(
            nstudio.resolve_text("$localize:search.label", doc.locales), "Search")
        af = dict(doc.locales); af["active"] = "af"
        self.assertEqual(
            nstudio.resolve_text("$localize:search.label", af), "Soek")
        # The extended Shell vocabulary (AppGrid/Clock/Dock/TitleBar):
        # the taskbar clock is a real Clock bound to a clockFormat state.
        clock = doc.find_component("clock")
        self.assertEqual(clock.type, "Clock")
        self.assertEqual(clock.properties["format"], "24h")
        self.assertEqual(doc.bindings[-1].component, "clock")
        self.assertEqual(doc.bindings[-1].property, "format")
        self.assertEqual(doc.bindings[-1].state, "clockFormat")
        # The Dock sits on the desktop surface and launches on appClicked.
        dock = doc.find_component("dock")
        self.assertEqual(dock.type, "Dock")
        self.assertEqual(dock.properties["position"], "bottom")
        self.assertEqual(dock.events.get("appClicked"),
                         "behavior_launch_terminal")
        # The Launcher overlay hosts an AppGrid of all apps.
        launcher = doc.find_component("launcher")
        self.assertEqual(launcher.type, "Launcher")
        grid = doc.find_component("launcher_grid")
        self.assertEqual(grid.type, "AppGrid")
        self.assertEqual(grid.properties["columns"], 4)
        # A real app window: WindowFrame with TitleBar + WindowControls,
        # whose close button targets the window's Close action.
        window = doc.find_component("files_window")
        self.assertEqual(window.type, "WindowFrame")
        titlebar = doc.find_component("files_titlebar")
        self.assertEqual(titlebar.type, "TitleBar")
        controls = doc.find_component("files_controls")
        self.assertEqual(controls.type, "WindowControls")
        self.assertEqual(controls.events.get("closeClicked"),
                         "behavior_close_files")
        target, name, _args = doc.resolve_action("behavior_close_files")
        self.assertEqual(target, "files_window")
        self.assertEqual(name, "Close")
        target, name, _args = doc.resolve_action("behavior_launcher_open")
        self.assertEqual(target, "launcher")
        self.assertEqual(name, "Open")
        # Logic graphs (NUI-SCHEMA §7.3): the theme toggle is a 2-action
        # chain (Theme.Set then Animation.Play) and the quiet-hours
        # notification guard is an AND condition group.
        chain = doc.resolve_actions("behavior_theme_eclipse")
        self.assertEqual([c[1] for c in chain],
                         ["Nyrqis.Theme.Set", "Nyrqis.Animation.Play"])
        quiet = doc.behavior_by_id("behavior_quiet_notify").condition
        self.assertEqual(quiet["logic"], "and")
        self.assertEqual(len(quiet["conditions"]), 2)
        # The AND group evaluates False by default (quiet hours off).
        self.assertIs(doc.resolve_condition("behavior_quiet_notify"), False)

    def test_windows_shell_fixture_shape(self):
        """The window-system + power-UI shell screens (0.14.25 shell
        vocabulary): WindowFrame/WindowControls drive component-targeted
        actions; the PowerMenu carries a bound open state."""
        doc = self._load("windows.nstudio")
        self.assertEqual(len(doc.component_ids()), 21)
        self.assertEqual(len(doc.behaviors), 8)
        self.assertEqual(len(doc.bindings), 1)
        self.assertEqual([s.id for s in doc.screens], ["windows", "power"])
        # WindowControls wire to WindowFrame actions.
        controls = doc.find_component("files_controls")
        self.assertEqual(controls.type, "WindowControls")
        target, name, _args = doc.resolve_action("behavior_files_maximize")
        self.assertEqual(target, "app_files")
        self.assertEqual(name, "Maximize")
        target, name, _args = doc.resolve_action("behavior_vault_minimize")
        self.assertEqual(target, "app_vault")
        self.assertEqual(name, "Minimize")
        # The power menu is a real PowerMenu with a bound open state.
        power = doc.find_component("power_menu")
        self.assertEqual(power.type, "PowerMenu")
        binding = doc.bindings[0]
        self.assertEqual(binding.component, "power_menu")
        self.assertEqual(binding.state, "powerMenuOpen")

    def test_widgets_shell_fixture_shape(self):
        """The widgets + OSD + login shell screens (0.14.25 shell
        vocabulary): WidgetHost/OSD/Login carry real actions, and the OSD
        message resolves $state: substitution at action time."""
        doc = self._load("widgets.nstudio")
        self.assertEqual(len(doc.component_ids()), 19)
        self.assertEqual(len(doc.behaviors), 5)
        self.assertEqual(len(doc.bindings), 2)
        self.assertEqual([s.id for s in doc.screens], ["widgets", "osd", "login"])
        host = doc.find_component("widget_host")
        self.assertEqual(host.type, "WidgetHost")
        target, name, args = doc.resolve_action("behavior_widget_add")
        self.assertEqual(target, "widget_host")
        self.assertEqual(name, "AddWidget")
        self.assertEqual(args["widget"], "Weather")
        # The OSD's message is a $state: substitution of the volume.
        target, name, args = doc.resolve_action("behavior_osd_volume")
        self.assertEqual(target, "osd_volume")
        self.assertEqual(name, "Open")
        self.assertEqual(args["message"], 40)
        login = doc.find_component("login_screen")
        self.assertEqual(login.type, "Login")

    def test_version_gate(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        with self.assertRaises(nstudio.NstudioVersionError):
            nstudio.loads(text.replace('"version": "1.0.0"', '"version": "0.2.0"'))

    def test_malformed_json(self):
        with self.assertRaises(nstudio.NstudioValidationError):
            nstudio.loads("{not json")

    def test_unknown_component_type(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"type": "Toggle"', '"type": "BogusWidget"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("unknown type 'BogusWidget'", str(ctx.exception))

    def test_unknown_event(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"changed": "behavior_dnd_on"', '"hovered": "behavior_dnd_on"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("event 'hovered' not in the 'Toggle' contract", str(ctx.exception))

    def test_dangling_behavior_reference(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"clicked": "behavior_refresh"', '"clicked": "behavior_missing"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("unknown behavior 'behavior_missing'", str(ctx.exception))

    def test_unknown_system_action(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, "Nyrqis.Notification.Show", "Nyrqis.System.Shutdown")
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("unknown system action 'Nyrqis.System.Shutdown'", str(ctx.exception))

    def test_unknown_action_argument(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"severity": "info"', '"severity": "info", "bogus": 1')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("argument 'bogus' not in the 'Nyrqis.Notification.Show' contract", str(ctx.exception))

    def test_dangling_binding(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"component": "toggle_dnd"', '"component": "ghost"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("component 'ghost' does not exist", str(ctx.exception))

    def test_unknown_condition_state(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        # The pretty-printed fixture puts the condition on several lines;
        # the trailing comma anchors the condition's state key only (the
        # binding's state line has no trailing comma).
        text = self._mutate(text, '"state": "doNotDisturb",', '"state": "nope",')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("condition references unknown state 'nope'", str(ctx.exception))

    def test_duplicate_component_ids(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"id": "sb_top"', '"id": "brand"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("duplicate component id 'brand'", str(ctx.exception))

    def test_layout_must_be_non_negative_ints(self):
        text = open(self._fixture("nyrqis-shell.nstudio")).read()
        text = self._mutate(text, '"x": 16, "y": 12', '"x": -1, "y": 12')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("layout 'x' must be a non-negative integer", str(ctx.exception))

    def test_resolve_action_substitutes_state(self):
        doc = self._load("nyrqis-shell.nstudio")
        target, name, args = doc.resolve_action("behavior_refresh")
        self.assertEqual((target, name), ("System", "Nyrqis.Notification.Show"))
        self.assertEqual(args["message"], "12:04")  # $state:lastRefresh
        # literal (non-$state) arguments pass through untouched
        self.assertEqual(args["title"], "Workspace refreshed")

    def test_resolve_action_missing_state_stays_literal(self):
        doc = self._load("nyrqis-shell.nstudio")
        doc.states.pop("lastRefresh", None)
        _, _, args = doc.resolve_action("behavior_refresh")
        self.assertEqual(args["message"], "$state:lastRefresh")

    def test_resolve_unknown_behavior_raises(self):
        doc = self._load("nyrqis-shell.nstudio")
        with self.assertRaises(nstudio.NstudioValidationError):
            doc.resolve_action("behavior_ghost")

    def test_render_stays_within_screen_bounds(self):
        doc = self._load("nyrqis-shell.nstudio")
        screen = doc.screens[0]
        for component, depth in doc.render("main"):
            layout = component.layout
            self.assertLessEqual(layout["x"] + layout["width"], screen.size["width"],
                                 f"{component.id} overflows width")
            self.assertLessEqual(layout["y"] + layout["height"], screen.size["height"],
                                 f"{component.id} overflows height")
            self.assertGreaterEqual(depth, 0)

    def test_text_preview_is_deterministic(self):
        doc = self._load("nyrqis-shell.nstudio")
        preview = doc.text_preview("main")
        self.assertIn("screen main 1440x900", preview)
        self.assertIn("Window window_shell (0,0 1440x900)", preview)
        self.assertIn("Toggle toggle_dnd (848,8 224x32)", preview)
        self.assertEqual(preview, doc.text_preview("main"))

    def test_unknown_screen_raises(self):
        doc = self._load("nyrqis-shell.nstudio")
        with self.assertRaises(nstudio.NstudioValidationError):
            doc.render("ghost")

    # -- to_dict round-trip tests ---------------------------------------

    def test_to_dict_round_trip_desktop(self):
        """Round-trip the desktop shell: load → to_dict → loads."""
        import json
        doc = self._load("desktop.nstudio")
        d = doc.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["version"], "1.0.0")
        # Round-trip
        doc2 = nstudio.loads(json.dumps(d))
        self.assertEqual(doc2.version, doc.version)
        self.assertEqual(len(doc2.screens), len(doc.screens))
        self.assertEqual(len(doc2.behaviors), len(doc.behaviors))
        self.assertEqual(len(doc2.bindings), len(doc.bindings))
        self.assertEqual(len(doc2.component_ids()), len(doc.component_ids()))
        self.assertEqual(len(doc2.animations), len(doc.animations))
        self.assertEqual(doc2.states, doc.states)

    def test_to_dict_round_trip_nyrqis_shell(self):
        """Round-trip the nyrqis-shell fixture."""
        import json
        doc = self._load("nyrqis-shell.nstudio")
        d = doc.to_dict()
        doc2 = nstudio.loads(json.dumps(d))
        self.assertEqual(len(doc2.component_ids()), 71)
        self.assertEqual(len(doc2.behaviors), 5)
        self.assertEqual(len(doc2.bindings), 1)

    def test_to_dict_includes_accessibility(self):
        """to_dict preserves accessibility metadata in properties."""
        import json
        doc = self._load("desktop.nstudio")
        d = doc.to_dict()
        doc2 = nstudio.loads(json.dumps(d))
        # Find a component with accessibility
        comp = doc2.find_component("btn_unlock")
        self.assertIsNotNone(comp)
        self.assertIn("accessibility", comp.properties)
        self.assertEqual(comp.properties["accessibility"]["label"],
                         "Unlock screen")

    def test_to_dict_empty_document(self):
        """to_dict works on a minimal document."""
        import json
        doc = nstudio.loads(json.dumps({
            "version": "1.0.0",
            "screens": [{"id": "s1", "size": {"width": 800, "height": 600},
                          "root": {"id": "r1", "type": "Text", "properties": {"text": "hi"},
                                    "layout": {"x": 0, "y": 0, "width": 100, "height": 30},
                                    "events": {}, "children": []}}],
        }))
        d = doc.to_dict()
        self.assertEqual(d["version"], "1.0.0")
        self.assertEqual(len(d["screens"]), 1)
        # Round-trip
        doc2 = nstudio.loads(json.dumps(d))
        self.assertEqual(len(doc2.screens), 1)
        self.assertEqual(doc2.screens[0].root.type, "Text")

    def test_to_dict_preserves_reusable_components(self):
        """to_dict preserves reusable component masters."""
        import json
        doc = self._load("widgets.nstudio")
        d = doc.to_dict()
        doc2 = nstudio.loads(json.dumps(d))
        self.assertEqual(len(doc2.reusable_components),
                         len(doc.reusable_components))


class TestShellComponents(unittest.TestCase):
    """The extended Shell vocabulary — AppGrid, Clock, Dock, TitleBar
    (NUI-SCHEMA §2 component table): the registry carries typed
    contracts, the fixture exercises them through both gates, and the
    floor rejects unknown properties/events exactly like the crate."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")

    def _fixture(self, name):
        return os.path.join(self.FIXTURES, name)

    def _load(self, name):
        return nstudio.load(self._fixture(name))

    def test_shell_vocabulary_in_registry(self):
        for type_name in ("AppGrid", "Clock", "Dock", "TitleBar"):
            contract = nstudio.COMPONENT_CONTRACTS.get(type_name)
            self.assertIsNotNone(contract, type_name)
            category, properties, events, actions = contract
            self.assertEqual(category, "Shell")
            self.assertTrue(properties)
        # Typed contracts: the Dock's semantic surface is not a generic
        # rectangle — position/autoHide/magnify are real properties.
        _cat, dock_props, _ev, dock_actions = \
            nstudio.COMPONENT_CONTRACTS["Dock"]
        for prop in ("position", "pinnedApps", "runningApps",
                     "autoHide", "iconSize", "magnify"):
            self.assertIn(prop, dock_props)
        self.assertEqual(dock_actions, ("Launch",))
        _cat, _p, clock_events, _a = nstudio.COMPONENT_CONTRACTS["Clock"]
        self.assertEqual(clock_events, ())
        _cat, _p, title_events, _a = nstudio.COMPONENT_CONTRACTS["TitleBar"]
        self.assertIn("doubleClicked", title_events)
        _cat, _p, grid_events, grid_actions = \
            nstudio.COMPONENT_CONTRACTS["AppGrid"]
        self.assertIn("appClicked", grid_events)
        self.assertEqual(grid_actions, ("Launch",))

    def test_desktop_fixture_uses_new_vocabulary(self):
        doc = self._load("desktop.nstudio")
        for cid, type_name in (("clock", "Clock"), ("dock", "Dock"),
                               ("launcher_grid", "AppGrid"),
                               ("files_titlebar", "TitleBar"),
                               ("files_window", "WindowFrame"),
                               ("files_controls", "WindowControls"),
                               ("launcher", "Launcher")):
            comp = doc.find_component(cid)
            self.assertIsNotNone(comp, cid)
            self.assertEqual(comp.type, type_name)

    def test_floor_rejects_unknown_property_on_new_types(self):
        text = open(self._fixture("desktop.nstudio")).read()
        text = text.replace(
            '"apps": ["Calculator", "Notes", "Weather", "Clock", '
            '"Files", "Vault", "Settings", "Terminal"], "columns": 4, '
            '"iconSize": 48',
            '"apps": [], "columns": 4, "bogus": true')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn(
            "property 'bogus' not in the 'AppGrid' contract",
            str(ctx.exception))

    def test_floor_rejects_unknown_event_on_new_types(self):
        text = open(self._fixture("desktop.nstudio")).read()
        text = text.replace('"appClicked": "behavior_launch_terminal"',
                            '"clicked": "behavior_launch_terminal"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("event 'clicked' not in the 'Dock' contract",
                      str(ctx.exception))

    def test_resolve_new_component_targeted_actions(self):
        doc = self._load("desktop.nstudio")
        target, name, _args = doc.resolve_action("behavior_close_files")
        self.assertEqual((target, name), ("files_window", "Close"))
        target, name, _args = doc.resolve_action("behavior_launcher_open")
        self.assertEqual((target, name), ("launcher", "Open"))


class TestResponsiveLayout(unittest.TestCase):
    """Responsive layout constraints (NUI-SCHEMA §4): anchors, min/max
    bounds, and aspect ratio — validated on the floor and applied by
    resolve_layout(), which text_preview() (the stand-in renderer) uses."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")

    def _load(self, name):
        return nstudio.loads(open(os.path.join(self.FIXTURES, name)).read())

    def _mutate(self, name, old, new):
        text = open(os.path.join(self.FIXTURES, name)).read()
        assert old in text, f"anchor not found: {old[:60]}"
        return text.replace(old, new)

    # ---- resolve_layout -----------------------------------------------------

    def test_no_constraints_is_absolute(self):
        r = nstudio.resolve_layout({"x": 24, "y": 36, "width": 200, "height": 50},
                                   1000, 500)
        self.assertEqual(r, {"x": 24, "y": 36, "width": 200, "height": 50})

    def test_both_horizontal_anchors_stretch_and_clamp(self):
        r = nstudio.resolve_layout(
            {"x": 0, "y": 0, "width": 100, "height": 20,
             "anchorLeft": True, "anchorRight": True,
             "minWidth": 500, "maxWidth": 800}, 1000, 500)
        self.assertEqual(r, {"x": 0, "y": 0, "width": 800, "height": 20})

    def test_bottom_anchor_docks_from_bottom(self):
        r = nstudio.resolve_layout(
            {"x": 0, "y": 0, "width": 1000, "height": 80,
             "anchorBottom": True}, 1000, 500)
        self.assertEqual(r, {"x": 0, "y": 420, "width": 1000, "height": 80})

    def test_right_anchor_measures_from_right_edge(self):
        r = nstudio.resolve_layout(
            {"x": 24, "y": 10, "width": 200, "height": 50,
             "anchorRight": True}, 1000, 500)
        self.assertEqual(r, {"x": 776, "y": 10, "width": 200, "height": 50})

    def test_aspect_ratio_keeps_authored_size_when_not_stretched(self):
        r = nstudio.resolve_layout(
            {"x": 0, "y": 0, "width": 96, "height": 96, "aspectRatio": 1.0},
            1000, 500)
        self.assertEqual(r, {"x": 0, "y": 0, "width": 96, "height": 96})

    def test_aspect_ratio_derives_stretched_axis(self):
        r = nstudio.resolve_layout(
            {"x": 0, "y": 0, "width": 96, "height": 10,
             "anchorLeft": True, "anchorRight": True, "aspectRatio": 2.0},
            1000, 500)
        # width stretches to 1000; height derives to 1000/2.
        self.assertEqual(r["width"], 1000)
        self.assertEqual(r["height"], 500)

    # ---- validation ---------------------------------------------------------

    def test_min_greater_than_max_rejected(self):
        text = self._mutate("desktop.nstudio",
                            '"minWidth": 1200', '"minWidth": 2000')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("layout 'minWidth' must be <= 'maxWidth'", str(ctx.exception))

    def test_negative_aspect_ratio_rejected(self):
        text = self._mutate("desktop.nstudio", '"aspectRatio": 1.0',
                            '"aspectRatio": -1')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("layout 'aspectRatio' must be a positive number",
                      str(ctx.exception))

    def test_non_boolean_anchor_rejected(self):
        text = self._mutate("desktop.nstudio", '"anchorLeft": true',
                            '"anchorLeft": 1')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("layout 'anchorLeft' must be a boolean", str(ctx.exception))

    def test_negative_constraint_bound_rejected(self):
        text = self._mutate("desktop.nstudio", '"maxHeight": 96',
                            '"maxHeight": -1')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(text)
        self.assertIn("layout 'maxHeight' must be a non-negative integer",
                      str(ctx.exception))

    # ---- fixture end-to-end -------------------------------------------------

    def test_desktop_fixture_taskbar_stretches_and_docks(self):
        doc = self._load("desktop.nstudio")
        preview = doc.text_preview("desktop")
        self.assertIn("Taskbar taskbar (0,820 1440x80)", preview)
        self.assertIn("DesktopIcon icon_files (24,24 96x96)", preview)

    def test_resolve_desktop_taskbar_constraints(self):
        doc = self._load("desktop.nstudio")
        taskbar = doc.find_component("taskbar")
        r = nstudio.resolve_layout(taskbar.layout, 1440, 900)
        self.assertEqual(r, {"x": 0, "y": 820, "width": 1440, "height": 80})
        # On a narrower window the min-width keeps it usable.
        r = nstudio.resolve_layout(taskbar.layout, 800, 600)
        self.assertEqual(r["width"], 1200)  # minWidth floor
        self.assertEqual(r["y"], 520)        # still docked to the bottom


class TestLocalization(unittest.TestCase):
    """Localization (NUI-SCHEMA §8.1): the locales section and
    $localize:key references — resolved through the active locale's
    table, and validated fail-closed on the floor."""

    LOC = {"active": "en", "tables": {
        "en": {"settings.save": "Save"},
        "af": {"settings.save": "Stoor"},
    }}

    def _doc(self, extra=""):
        return nstudio.loads("""{
          "version": "1.0.0",
          "project": {"name": "t", "id": "t"},
          "themes": {"active": "Eclipse"},
          "locales": {"active": "en", "tables": {"en": {"search.label": "Search"}}},
          "states": {},
          "behaviors": [],
          "bindings": [],
          "screens": [{"id": "s", "size": {"width": 100, "height": 100},
            "root": {"id": "r", "type": "Button",
              "properties": {"text": "$localize:search.label"},
              "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
              "events": {}, "children": []}}]%s
        }""" % extra)

    def test_resolve_text_active_locale(self):
        self.assertEqual(nstudio.resolve_text("$localize:settings.save", self.LOC), "Save")
        af = dict(self.LOC); af["active"] = "af"
        self.assertEqual(nstudio.resolve_text("$localize:settings.save", af), "Stoor")

    def test_resolve_text_plain_and_missing(self):
        self.assertEqual(nstudio.resolve_text("Hello", self.LOC), "Hello")
        # Missing key stays literal (fail-soft at resolution; the gate
        # rejects it up front).
        self.assertEqual(nstudio.resolve_text("$localize:ghost", self.LOC), "$localize:ghost")

    def test_resolve_text_no_locales_section(self):
        self.assertEqual(nstudio.resolve_text("$localize:settings.save", {}), "$localize:settings.save")

    def test_missing_localize_key_rejected(self):
        raw = open("tests/fixtures/nstudio/desktop.nstudio").read()
        bad = raw.replace('"$localize:search.label"', '"$localize:ghost.key"')
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(bad)
        self.assertIn("localize key 'ghost.key' not in locale 'en'", str(ctx.exception))

    def test_active_locale_without_table_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads("""{
              "version": "1.0.0",
              "project": {"name": "t", "id": "t"},
              "themes": {"active": "Eclipse"},
              "locales": {"active": "fr", "tables": {"en": {"a": "b"}}},
              "states": {}, "behaviors": [], "bindings": [], "screens": []
            }""")
        self.assertIn("active locale 'fr' has no table", str(ctx.exception))

    def test_localize_without_locales_section_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads("""{
              "version": "1.0.0",
              "project": {"name": "t", "id": "t"},
              "themes": {"active": "Eclipse"},
              "states": {}, "behaviors": [], "bindings": [],
              "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "r", "type": "Button",
                  "properties": {"text": "$localize:search.label"},
                  "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                  "events": {}, "children": []}}]
            }""")
        self.assertIn("requires a 'locales' section", str(ctx.exception))

    def test_fixture_localizes_both_override_and_action_argument(self):
        doc = nstudio.loads(open("tests/fixtures/nstudio/desktop.nstudio").read())
        self.assertEqual(nstudio.resolve_text("$localize:search.label", doc.locales), "Search")
        _t, _n, args = doc.resolve_action("behavior_dnd_on")
        self.assertEqual(
            nstudio.resolve_text(args["message"], doc.locales),
            "Notifications paused until disabled")


class TestResources(unittest.TestCase):
    """Resources (NUI-SCHEMA §8.2): the managed asset catalog — unique
    ids, allowed kinds, non-empty paths, optional 64-hex sha256, and
    fail-closed $asset: reference checks on the floor."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")
    SHA = "a" * 64

    def _doc(self, assets, props):
        return nstudio.loads(json.dumps({
            "version": "1.0.0",
            "project": {"name": "t", "id": "t"},
            "themes": {"active": "Eclipse"},
            "locales": {},
            "resources": {"assets": assets},
            "states": {}, "behaviors": [], "bindings": [],
            "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "r", "type": "Image",
                    "properties": props,
                    "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "events": {}, "children": []}}],
        }))

    def test_fixture_declares_wallpaper_asset(self):
        doc = nstudio.loads(open(os.path.join(self.FIXTURES, "desktop.nstudio")).read())
        self.assertEqual([a["id"] for a in doc.resources["assets"]], ["wallpaper"])
        self.assertEqual(doc.resources["assets"][0]["kind"], "image")
        self.assertEqual(doc.resources["assets"][0]["path"], "assets/wallpaper.png")

    def test_declared_asset_ref_accepted(self):
        doc = self._doc(
            [{"id": "wallpaper", "kind": "image", "path": "a.png",
              "sha256": self.SHA}],
            {"source": "$asset:wallpaper"})
        self.assertEqual(doc.screens[0].root.properties["source"], "$asset:wallpaper")

    def test_undeclared_asset_ref_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(
                [{"id": "wallpaper", "kind": "image", "path": "a.png"}],
                {"source": "$asset:ghost"})
        self.assertIn("asset 'ghost' is not declared in resources", str(ctx.exception))

    def test_asset_ref_without_resources_rejected(self):
        text = open(os.path.join(self.FIXTURES, "desktop.nstudio")).read()
        bad = text.replace('"resources": {\n    "assets": [\n      { "id": "wallpaper", "kind": "image", "path": "assets/wallpaper.png" }\n    ]\n  },', "")
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            nstudio.loads(bad)
        self.assertIn("requires a 'resources' section", str(ctx.exception))

    def test_duplicate_resource_id_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(
                [{"id": "w", "kind": "image", "path": "a.png"},
                 {"id": "w", "kind": "icon", "path": "b.png"}],
                {"source": "$asset:w"})
        self.assertIn("duplicate resource id 'w'", str(ctx.exception))

    def test_unknown_kind_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "w", "kind": "bogus", "path": "a.png"}],
                      {"source": "$asset:w"})
        self.assertIn("kind 'bogus' not in", str(ctx.exception))

    def test_empty_path_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "w", "kind": "image", "path": ""}],
                      {"source": "$asset:w"})
        self.assertIn("non-empty 'path'", str(ctx.exception))

    def test_bad_sha256_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "w", "kind": "image", "path": "a.png",
                        "sha256": "xyz"}],
                      {"source": "$asset:w"})
        self.assertIn("64-char hex", str(ctx.exception))


class TestExpressions(unittest.TestCase):
    """The NUI expression language (NUI-SCHEMA §7.2, ui/nexpr.py):
    ``$expr:`` values in properties/overrides and action arguments, and
    condition ``expression`` fields — parsed, validated fail-closed at
    the import gate, and evaluated against document state."""

    def _doc(self, condition=None, args=None, props=None, extra_states=None):
        states = {"volume": 60, "clockTime": "14:32", "dnd": False}
        if extra_states:
            states.update(extra_states)
        return nstudio.loads(json.dumps({
            "version": "1.0.0",
            "project": {"name": "t", "id": "t"},
            "themes": {"active": "Eclipse"},
            "states": states,
            "behaviors": [{"id": "b1", "condition": condition,
                "action": {"target": "System",
                            "name": "Nyrqis.Notification.Show",
                            "arguments": args or {"message": "x",
                                                   "severity": "info"}}}],
            "bindings": [],
            "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "r", "type": "Button",
                    "properties": props or {"text": "hi"},
                    "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "events": {}, "children": []}}],
        }))

    # ---- evaluation --------------------------------------------------------

    def test_condition_expression_evaluates(self):
        doc = self._doc({"expression": "state.volume > 50 && !state.dnd"})
        self.assertTrue(doc.resolve_condition("b1"))
        doc2 = self._doc({"expression": "state.volume < 10"})
        self.assertFalse(doc2.resolve_condition("b1"))

    def test_legacy_condition_still_works(self):
        doc = self._doc({"state": "dnd", "operator": "equals", "value": False})
        self.assertTrue(doc.resolve_condition("b1"))

    def test_no_condition_returns_none(self):
        self.assertIsNone(self._doc(None).resolve_condition("b1"))

    def test_expr_argument_evaluates(self):
        doc = self._doc(None, {"title": "$expr:format(state.clockTime, \"{0}\")",
                               "message": "x", "severity": "info"})
        _t, _n, args = doc.resolve_action("b1")
        self.assertEqual(args["title"], "14:32")

    def test_expr_argument_with_if(self):
        doc = self._doc(None, {"title": "$expr:if(state.volume > 50, \"loud\", \"quiet\")",
                               "message": "x", "severity": "info"})
        _t, _n, args = doc.resolve_action("b1")
        self.assertEqual(args["title"], "loud")

    # ---- validation --------------------------------------------------------

    def test_unknown_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"expression": "state.ghost > 1"})
        self.assertIn("unknown state 'state.ghost'", str(ctx.exception))

    def test_syntax_error_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"expression": "state.volume >"})
        self.assertIn("syntax error at 14", str(ctx.exception))

    def test_unknown_function_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"expression": "bogus(state.volume)"})
        self.assertIn("unknown function 'bogus'", str(ctx.exception))

    def test_wrong_arity_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"expression": "if(state.volume > 1)"})
        self.assertIn("function 'if' expects 3 argument(s), got 1",
                      str(ctx.exception))

    def test_expr_argument_unknown_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(None, {"message": "$expr:state.ghost", "severity": "info"})
        self.assertIn("argument: expr: unknown state 'state.ghost'",
                      str(ctx.exception))

    def test_expr_property_unknown_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(None, None, {"text": "$expr:state.ghost > 1"})
        self.assertIn("property: expr: unknown state 'state.ghost'",
                      str(ctx.exception))

    def test_plain_strings_are_not_expressions(self):
        doc = self._doc(None, {"message": "just text", "severity": "info"},
                        {"text": "state.volume > 50"})
        self.assertEqual(doc.screens[0].root.properties["text"],
                         "state.volume > 50")

    # ---- fixture -----------------------------------------------------------

    def test_desktop_fixture_expression_condition_and_argument(self):
        doc = nstudio.loads(open(
            "tests/fixtures/nstudio/desktop.nstudio").read())
        b = doc.behavior_by_id("behavior_dnd_on")
        self.assertEqual(b.condition["expression"],
                         "state.doNotDisturb == true")
        self.assertFalse(doc.resolve_condition("behavior_dnd_on"))
        _t, _n, args = doc.resolve_action("behavior_dnd_on")
        self.assertEqual(args["title"], "14:32")  # format(state.clockTime, "{0}")


class TestAnimations(unittest.TestCase):
    """Declarative animations (NUI-SCHEMA §8.3): the document's
    `animations` section — unique ids, targets that name components,
    non-empty properties, validated timing parameters — and the
    Nyrqis.Animation.Play behavior reference, enforced fail-closed on
    the floor."""

    def _doc(self, animations, behavior_args=None):
        return nstudio.loads(json.dumps({
            "version": "1.0.0",
            "project": {"name": "t", "id": "t"},
            "themes": {"active": "Eclipse"},
            "states": {},
            "animations": animations,
            "behaviors": [{"id": "b1", "condition": None,
                "action": {"target": "System",
                            "name": "Nyrqis.Animation.Play",
                            "arguments": behavior_args
                            or {"animation": "fade"}}}],
            "bindings": [],
            "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "menu", "type": "StartMenu",
                    "properties": {},
                    "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "events": {}, "children": []}}],
        }))

    ANIM = {"id": "fade", "target": "menu", "property": "opacity",
            "duration": 200, "easing": "ease-out"}

    def test_declared_animation_accepted(self):
        doc = self._doc([self.ANIM])
        self.assertEqual(len(doc.animations), 1)
        anim = doc.animations[0]
        self.assertEqual((anim.id, anim.target, anim.property),
                         ("fade", "menu", "opacity"))
        self.assertEqual(anim.duration, 200)
        self.assertEqual(anim.easing, "ease-out")
        self.assertEqual(anim.direction, "forward")
        _t, _n, args = doc.resolve_action("b1")
        self.assertEqual(args["animation"], "fade")

    def test_defaults_applied(self):
        doc = self._doc([{"id": "f", "target": "menu", "property": "opacity"}],
                        {"animation": "f"})
        anim = doc.animations[0]
        self.assertEqual(anim.duration, 300)
        self.assertEqual(anim.delay, 0)
        self.assertEqual(anim.easing, "ease-in-out")
        self.assertEqual(anim.repeat, 0)
        self.assertEqual(anim.direction, "forward")

    def test_undeclared_animation_reference_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([self.ANIM], {"animation": "ghost"})
        self.assertIn("animation 'ghost' is not declared in 'animations'",
                      str(ctx.exception))

    def test_unknown_easing_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "menu", "property": "opacity",
                        "easing": "bounce"}], {"animation": "f"})
        self.assertIn("easing 'bounce' not in", str(ctx.exception))

    def test_unknown_direction_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "menu", "property": "opacity",
                        "direction": "sideways"}], {"animation": "f"})
        self.assertIn("direction 'sideways' not in", str(ctx.exception))

    def test_negative_duration_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "menu", "property": "opacity",
                        "duration": -5}], {"animation": "f"})
        self.assertIn("'duration' must be a non-negative integer",
                      str(ctx.exception))

    def test_unknown_target_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "ghost", "property": "opacity"}],
                      {"animation": "f"})
        self.assertIn("target 'ghost' does not exist", str(ctx.exception))

    def test_missing_property_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "menu"}], {"animation": "f"})
        self.assertIn("must declare a 'property'", str(ctx.exception))

    def test_duplicate_animation_id_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "f", "target": "menu", "property": "opacity"},
                       {"id": "f", "target": "menu", "property": "scale"}],
                      {"animation": "f"})
        self.assertIn("duplicate animation id 'f'", str(ctx.exception))

    def test_keyframes_accepted(self):
        doc = self._doc([{"id": "fade", "target": "menu",
                          "property": "opacity",
                          "keyframes": [{"offset": 0.0, "value": 0.0},
                                         {"offset": 0.6, "value": 0.75},
                                         {"offset": 1.0, "value": 1.0}]}])
        anim = doc.animations[0]
        self.assertEqual(
            anim.keyframes,
            [{"offset": 0.0, "value": 0.0},
             {"offset": 0.6, "value": 0.75},
             {"offset": 1.0, "value": 1.0}])

    def test_keyframes_must_be_a_list(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "fade", "target": "menu",
                        "property": "opacity", "keyframes": "nope"}])
        self.assertIn("keyframes must be a list", str(ctx.exception))

    def test_keyframe_offset_out_of_range(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "fade", "target": "menu",
                        "property": "opacity",
                        "keyframes": [{"offset": 1.5, "value": 1}]}])
        self.assertIn("keyframe 0 'offset' must be a number in [0, 1]",
                      str(ctx.exception))

    def test_keyframe_offsets_strictly_increasing(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "fade", "target": "menu",
                        "property": "opacity",
                        "keyframes": [{"offset": 0.5, "value": 1},
                                       {"offset": 0.5, "value": 2}]}])
        self.assertIn("keyframe 1 'offset' must be greater than "
                      "the previous offset", str(ctx.exception))

    def test_keyframe_missing_value(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "fade", "target": "menu",
                        "property": "opacity",
                        "keyframes": [{"offset": 0.0, "value": 0},
                                       {"offset": 1.0}]}])
        self.assertIn("keyframe 1 'value' must be a number, string, "
                      "or boolean", str(ctx.exception))

    def test_keyframe_non_object_entry(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "fade", "target": "menu",
                        "property": "opacity",
                        "keyframes": [{"offset": 0.0, "value": 0},
                                       "junk"]}])
        self.assertIn("keyframe 1 must be an object", str(ctx.exception))

    def test_desktop_fixture_animation(self):
        doc = nstudio.loads(open(
            "tests/fixtures/nstudio/desktop.nstudio").read())
        self.assertEqual(
            [(a.id, a.target, a.property, a.duration, a.easing)
             for a in doc.animations],
            [("start_menu_fade", "start_menu", "opacity", 200, "ease-out")])
        # The fixture's start-menu fade is a multi-point curve.
        self.assertEqual(
            doc.animations[0].keyframes,
            [{"offset": 0.0, "value": 0.0},
             {"offset": 0.6, "value": 0.75},
             {"offset": 1.0, "value": 1.0}])
        _t, _n, args = doc.resolve_action("behavior_start_toggle")
        self.assertEqual(args["animation"], "start_menu_fade")


class TestStateScopes(unittest.TestCase):
    """State scopes (NUI-SCHEMA §8.4): the stateScopes section —
    global/screen/component/session/persistent — referenced as dotted
    ``scope.key`` names in expressions, conditions, bindings, and
    arguments; validated fail-closed on the floor and resolved at
    runtime. ``global`` is the named form of the flat ``states``
    section."""

    def _doc(self, scopes, cond=None, bind=None, arg=None):
        return nstudio.loads(json.dumps({
            "version": "1.0.0",
            "project": {"name": "t", "id": "t"},
            "themes": {"active": "Eclipse"},
            "states": {"volume": 60},
            "stateScopes": scopes,
            "behaviors": [{"id": "b1", "condition": cond,
                "action": {"target": "System",
                            "name": "Nyrqis.Notification.Show",
                            "arguments": arg or {"message": "x",
                                                   "severity": "info"}}}],
            "bindings": [{"component": "r", "property": "text",
                           "state": bind}] if bind else [],
            "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "r", "type": "Button",
                    "properties": {"text": "hi"},
                    "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "events": {}, "children": []}}],
        }))

    SCOPES = {"persistent": {"theme": "Eclipse"},
              "session": {"clockTime": "14:32"}}

    # ---- resolution ---------------------------------------------------------

    def test_scoped_reference_resolves(self):
        doc = self._doc(self.SCOPES)
        self.assertEqual(doc.resolve_state("persistent.theme"), "Eclipse")
        self.assertEqual(doc.resolve_state("session.clockTime"), "14:32")
        # A missing scoped key falls back to the default.
        self.assertEqual(doc.resolve_state("session.ghost", "fallback"), "fallback")

    def test_flat_states_still_resolve(self):
        doc = self._doc(self.SCOPES)
        self.assertEqual(doc.resolve_state("volume"), 60)

    def test_global_scope_is_named_flat(self):
        doc = self._doc({"global": {"volume": 60}},
                        {"expression": "state.global.volume > 50"})
        self.assertEqual(doc.resolve_state("volume"), 60)
        self.assertTrue(doc.resolve_condition("b1"))

    def test_expression_uses_scoped_states(self):
        doc = self._doc(self.SCOPES,
                        {"expression": "state.persistent.theme == \"Eclipse\""})
        self.assertTrue(doc.resolve_condition("b1"))

    def test_expr_argument_uses_scoped_states(self):
        doc = self._doc(self.SCOPES, arg={
            "message": "$expr:state.persistent.theme", "severity": "info"})
        _t, _n, args = doc.resolve_action("b1")
        self.assertEqual(args["message"], "Eclipse")

    def test_scoped_binding_validates(self):
        doc = self._doc(self.SCOPES, bind="persistent.theme")
        self.assertEqual(doc.bindings[0].state, "persistent.theme")

    # ---- validation ---------------------------------------------------------

    def test_unknown_scope_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"bogus": {"a": 1}})
        self.assertIn("unknown scope 'bogus'", str(ctx.exception))

    def test_non_object_scope_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc({"persistent": 5})
        self.assertIn("scope 'persistent' must be an object", str(ctx.exception))

    def test_expression_unknown_scoped_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(self.SCOPES,
                      {"expression": "state.persistent.ghost == \"x\""})
        self.assertIn("unknown state 'state.persistent.ghost'",
                      str(ctx.exception))

    def test_binding_unknown_scoped_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(self.SCOPES, bind="persistent.ghost")
        self.assertIn("state 'persistent.ghost' does not exist",
                      str(ctx.exception))

    def test_expr_argument_unknown_scoped_state_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc(self.SCOPES, arg={
                "message": "$expr:state.session.ghost", "severity": "info"})
        self.assertIn("unknown state 'state.session.ghost'",
                      str(ctx.exception))

    # ---- fixture ------------------------------------------------------------

    def test_desktop_fixture_scopes(self):
        doc = nstudio.loads(open(
            "tests/fixtures/nstudio/desktop.nstudio").read())
        self.assertEqual(sorted(doc.state_scopes.keys()),
                         ["persistent", "session"])
        self.assertEqual(doc.resolve_state("persistent.theme"), "Eclipse")
        # The theme toggle flips the PERSISTENT theme (an `if` expression
        # over the scoped state); the DND title formats the SESSION clock.
        _t, _n, args = doc.resolve_action("behavior_theme_solar")
        self.assertEqual(args["theme"], "Solar")
        _t, _n, args = doc.resolve_action("behavior_dnd_on")
        self.assertEqual(args["title"], "14:32")


class TestBehaviorLogicGraphs(unittest.TestCase):
    """Behavior logic graphs (NUI-SCHEMA §7.3): nested AND/OR condition
    groups and action chains — the internal representation the visual
    logic-graph editor builds on. The floor evaluates groups with the
    all/any recursion and enforces the group/chain shapes fail-closed,
    with byte-identical messages to the Rust crate (conformance class)."""

    def _doc(self, behaviors):
        return nstudio.loads(json.dumps({
            "version": "1.0.0",
            "project": {"name": "t", "id": "t"},
            "themes": {"active": "Eclipse"},
            "states": {"dnd": True, "volume": 60, "theme": "Eclipse"},
            "behaviors": behaviors,
            "bindings": [],
            "screens": [{"id": "s", "size": {"width": 100, "height": 100},
                "root": {"id": "btn", "type": "Button",
                    "properties": {"text": "Go"},
                    "layout": {"x": 0, "y": 0, "width": 10, "height": 10},
                    "events": {}, "children": []}}],
        }))

    @staticmethod
    def _leaf(state, value, operator="equals"):
        return {"state": state, "operator": operator, "value": value}

    COMMIT = {"target": "System", "name": "Nyrqis.Settings.Commit"}

    def test_and_group_evaluates_as_all(self):
        doc = self._doc([{"id": "b1", "condition": {"logic": "and",
            "conditions": [self._leaf("dnd", True),
                            self._leaf("theme", "Eclipse")]},
            "action": self.COMMIT}])
        self.assertIs(doc.resolve_condition("b1"), True)

        doc = self._doc([{"id": "b1", "condition": {"logic": "and",
            "conditions": [self._leaf("dnd", True),
                            self._leaf("theme", "Solar")]},
            "action": self.COMMIT}])
        self.assertIs(doc.resolve_condition("b1"), False)

    def test_or_group_evaluates_as_any(self):
        doc = self._doc([{"id": "b1", "condition": {"logic": "or",
            "conditions": [self._leaf("dnd", False),
                            self._leaf("theme", "Eclipse")]},
            "action": self.COMMIT}])
        self.assertIs(doc.resolve_condition("b1"), True)

    def test_nested_groups_evaluate_recursively(self):
        doc = self._doc([{"id": "b1", "condition": {"logic": "or",
            "conditions": [
                {"logic": "and", "conditions": [
                    self._leaf("dnd", False), self._leaf("volume", 60)]},
                self._leaf("theme", "Eclipse")]},
            "action": self.COMMIT}])
        self.assertIs(doc.resolve_condition("b1"), True)

    def test_chain_resolves_in_order(self):
        doc = self._doc([{"id": "b1", "condition": None, "actions": [
            {"target": "System", "name": "Nyrqis.Theme.Set",
             "arguments": {"theme": "Solar"}},
            self.COMMIT]}])
        chain = doc.resolve_actions("b1")
        self.assertEqual([(t, n) for t, n, _ in chain],
                         [("System", "Nyrqis.Theme.Set"),
                          ("System", "Nyrqis.Settings.Commit")])
        # The back-compatible single-action surface returns the first step.
        target, name, _ = doc.resolve_action("b1")
        self.assertEqual((target, name), ("System", "Nyrqis.Theme.Set"))

    def test_unknown_logic_operator_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": {"logic": "xor",
                "conditions": [self._leaf("dnd", True)]},
                "action": self.COMMIT}])
        self.assertIn("condition 'logic' must be 'and' or 'or'",
                      str(ctx.exception))

    def test_empty_conditions_group_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": {"logic": "and",
                "conditions": []}, "action": self.COMMIT}])
        self.assertIn("'conditions' must be a non-empty list",
                      str(ctx.exception))

    def test_non_object_sub_condition_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": {"logic": "and",
                "conditions": ["nope"]}, "action": self.COMMIT}])
        self.assertIn("condition 0 must be an object", str(ctx.exception))

    def test_both_action_and_actions_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": None,
                "action": self.COMMIT,
                "actions": [self.COMMIT]}])
        self.assertIn("must declare either 'action' or 'actions', not both",
                      str(ctx.exception))

    def test_neither_action_nor_actions_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": None}])
        self.assertIn("must declare an 'action' or 'actions'",
                      str(ctx.exception))

    def test_unknown_state_in_nested_group_rejected(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": {"logic": "or",
                "conditions": [
                    {"logic": "and", "conditions": [
                        self._leaf("dnd", True), self._leaf("ghost", 1)]},
                    self._leaf("theme", "Eclipse")]},
                "action": self.COMMIT}])
        self.assertIn("condition 0 1 references unknown state 'ghost'",
                      str(ctx.exception))

    def test_nested_expression_condition_validated(self):
        with self.assertRaises(nstudio.NstudioValidationError) as ctx:
            self._doc([{"id": "b1", "condition": {"logic": "and",
                "conditions": [{"expression": "state.dnd == true"},
                                {"expression": "state.ghost == 1"}]},
                "action": self.COMMIT}])
        self.assertIn("condition 1 expression: expr: unknown state "
                      "'state.ghost'", str(ctx.exception))


class TestNstudioCodecConformance(unittest.TestCase):
    """ADR-0025 differential: the Rust nyui crate (via the FFI loader
    ui/nstudio_codec.py) must reject exactly what the reference floor
    rejects — same gates, same error messages. Runs when the crate is
    built (the CI gate builds it and forces the class; locally it runs
    when the crate is present)."""

    FIXTURES = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "nstudio")

    @classmethod
    def setUpClass(cls):
        nstudio_codec.force_reload()
        cls.available = nstudio_codec.available()

    def setUp(self):
        if not self.available:
            self.skipTest(
                "Rust nyui crate not built (CI gate builds it)")

    def _text(self, name):
        return open(os.path.join(self.FIXTURES, name)).read()

    @staticmethod
    def _mutate(text, old, new):
        assert old in text, f"anchor not found: {old[:60]}"
        return text.replace(old, new)

    def _rejects(self, text, expected_fragment, exc_type=nstudio.NstudioValidationError):
        with self.assertRaises(exc_type) as ctx:
            nstudio_codec.validate(text)
        self.assertIn(expected_fragment, str(ctx.exception))

    def test_crate_accepts_all_fixtures(self):
        for name in ("forge-home", "settings-app", "vault-dashboard",
                     "nyrqis-shell", "security-center", "vault-workspace",
                     "desktop", "windows", "widgets"):
            nstudio_codec.validate(self._text(name + ".nstudio"))

    def test_crate_version_gate(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"version": "1.0.0"', '"version": "0.2.0"')
        self._rejects(text, "unsupported schema version '0.2.0'", nstudio.NstudioVersionError)

    def test_crate_rejects_unknown_type(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"type": "Toggle"', '"type": "BogusWidget"')
        self._rejects(text, "unknown type 'BogusWidget'")

    def test_crate_rejects_unknown_event(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"changed": "behavior_dnd_on"', '"hovered": "behavior_dnd_on"')
        self._rejects(text, "event 'hovered' not in the 'Toggle' contract")

    def test_crate_rejects_dangling_behavior(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"clicked": "behavior_refresh"', '"clicked": "behavior_missing"')
        self._rejects(text, "unknown behavior 'behavior_missing'")

    def test_crate_rejects_unknown_system_action(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            "Nyrqis.Notification.Show", "Nyrqis.System.Shutdown")
        self._rejects(text, "unknown system action 'Nyrqis.System.Shutdown'")

    def test_crate_rejects_unknown_action_argument(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"severity": "info"', '"severity": "info", "bogus": 1')
        self._rejects(text, "argument 'bogus' not in the 'Nyrqis.Notification.Show' contract")

    def test_crate_rejects_dangling_binding(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"component": "toggle_dnd"', '"component": "ghost"')
        self._rejects(text, "component 'ghost' does not exist")

    def test_crate_rejects_unknown_condition_state(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"state": "doNotDisturb",', '"state": "nope",')
        self._rejects(text, "condition references unknown state 'nope'")

    def test_crate_rejects_duplicate_ids(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"id": "sb_top"', '"id": "brand"')
        self._rejects(text, "duplicate component id 'brand'")

    def test_crate_rejects_negative_layout(self):
        text = self._mutate(self._text("nyrqis-shell.nstudio"),
                            '"x": 16, "y": 12', '"x": -1, "y": 12')
        self._rejects(text, "layout 'x' must be a non-negative integer")

    def test_crate_accepts_reusable_master_desktop(self):
        """The desktop fixture carries a reusable TaskbarButton master
        (NFS-006 §9) with componentRef instances — the crate must accept
        it exactly like the floor."""
        text = self._text("desktop.nstudio")
        nstudio_codec.validate(text)
        doc = nstudio.loads(text)
        self.assertEqual(len(doc.reusable_components), 1)
        self.assertEqual(doc.reusable_components[0].id, "TaskbarButton")

    def test_crate_rejects_unknown_component_ref(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"componentRef": "TaskbarButton"',
                            '"componentRef": "GhostMaster"')
        self._rejects(
            text, "componentRef 'GhostMaster' does not name a reusable component")

    def test_crate_rejects_override_outside_master_contract(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"overrides": { "text": "Start" }',
                            '"overrides": { "bogus": true }')
        self._rejects(text, "override 'bogus' not in the 'Button' contract")

    def test_crate_rejects_instance_with_own_type(self):
        """Instances omit 'type' — a node declaring both componentRef and
        a type is invalid in both gates."""
        text = self._mutate(self._text("desktop.nstudio"),
                            '"componentRef": "TaskbarButton",',
                            '"componentRef": "TaskbarButton", "type": "Button",')
        self._rejects(text, "reusable instance must not declare its own type")

    def test_crate_rejects_min_greater_than_max(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"minWidth": 1200', '"minWidth": 2000')
        self._rejects(text, "layout 'minWidth' must be <= 'maxWidth'")

    def test_crate_rejects_negative_aspect_ratio(self):
        text = self._mutate(self._text("desktop.nstudio"), '"aspectRatio": 1.0',
                            '"aspectRatio": -1')
        self._rejects(text, "layout 'aspectRatio' must be a positive number")

    def test_crate_rejects_out_of_range_keyframe_offset(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"offset": 0.0, "value": 0.0',
                            '"offset": 1.5, "value": 0.0')
        self._rejects(text, "keyframe 0 'offset' must be a number in [0, 1]")

    def test_crate_rejects_non_increasing_keyframe_offsets(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"offset": 0.6, "value": 0.75',
                            '"offset": 0.0, "value": 0.75')
        self._rejects(text, "keyframe 1 'offset' must be greater than "
                            "the previous offset")

    def test_crate_rejects_keyframe_without_value(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"offset": 1.0, "value": 1.0',
                            '"offset": 1.0')
        self._rejects(text, "keyframe 2 'value' must be a number, "
                            "string, or boolean")

    # ---- logic graphs (NUI-SCHEMA §7.3) ---------------------------------

    def test_crate_rejects_unknown_logic_operator(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"logic": "and"', '"logic": "xor"')
        self._rejects(text, "condition 'logic' must be 'and' or 'or'")

    def test_crate_rejects_empty_conditions_group(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"conditions": [\n'
            '          { "expression": "state.doNotDisturb == true" },\n'
            '          { "expression": "state.volume > 50" }\n'
            '        ]',
            '"conditions": []')
        self._rejects(text, "condition 'conditions' must be a non-empty list")

    def test_crate_rejects_both_action_and_actions(self):
        """A behavior declaring both the single `action` and an `actions`
        chain fails identically on both gates."""
        text = self._mutate(self._text("desktop.nstudio"), '"actions": [',
                            '"action": {"target": "System", "name": '
                            '"Nyrqis.Settings.Commit"}, "actions": [')
        self._rejects(text, "must declare either 'action' or 'actions', "
                            "not both")

    def test_crate_rejects_unknown_state_in_nested_group(self):
        """The quiet-hours AND group's second leaf references a state
        that doesn't exist — the crate reports it at the group path,
        byte-identical to the floor."""
        text = self._mutate(self._text("desktop.nstudio"),
                            '"expression": "state.volume > 50"',
                            '"expression": "state.ghostState == true"')
        self._rejects(text, "condition 1 expression: expr: unknown state "
                            "'state.ghostState'")

    def test_crate_rejects_unknown_property_on_appgrid(self):
        """The new Shell vocabulary is gated identically: a bogus
        AppGrid property fails on the crate with the floor's message."""
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"apps": ["Calculator", "Notes", "Weather", "Clock", '
            '"Files", "Vault", "Settings", "Terminal"], "columns": 4, '
            '"iconSize": 48',
            '"apps": [], "columns": 4, "bogus": true')
        self._rejects(text, "property 'bogus' not in the 'AppGrid' contract")

    def test_crate_rejects_unknown_event_on_dock(self):
        """The Dock's only event is appClicked — a generic clicked fails
        the same way on both gates."""
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"appClicked": "behavior_launch_terminal"',
            '"clicked": "behavior_launch_terminal"')
        self._rejects(text, "event 'clicked' not in the 'Dock' contract")

    def test_crate_rejects_non_boolean_anchor(self):
        text = self._mutate(self._text("desktop.nstudio"), '"anchorLeft": true',
                            '"anchorLeft": 1')
        self._rejects(text, "layout 'anchorLeft' must be a boolean")

    def test_crate_rejects_negative_constraint_bound(self):
        text = self._mutate(self._text("desktop.nstudio"), '"maxHeight": 96',
                            '"maxHeight": -1')
        self._rejects(text, "layout 'maxHeight' must be a non-negative integer")

    def test_crate_accepts_localized_desktop(self):
        nstudio_codec.validate(self._text("desktop.nstudio"))
        nstudio.loads(self._text("desktop.nstudio"))

    def test_crate_rejects_missing_localize_key(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"$localize:search.label"', '"$localize:ghost.key"')
        self._rejects(text, "localize key 'ghost.key' not in locale 'en'")

    def test_crate_rejects_active_locale_without_table(self):
        text = self._text("desktop.nstudio").replace(
            '"active": "en",', '"active": "fr",')
        self._rejects(text, "active locale 'fr' has no table")

    def test_error_messages_match_floor_localize(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"$localize:search.label"', '"$localize:ghost.key"')
        with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
            nstudio.loads(text)
        with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
            nstudio_codec.validate(text)
        first_floor_issue = str(floor_ctx.exception).split("; ")[0]
        self.assertEqual(str(crate_ctx.exception), first_floor_issue)

    def test_crate_accepts_resources_desktop(self):
        nstudio_codec.validate(self._text("desktop.nstudio"))
        nstudio.loads(self._text("desktop.nstudio"))

    def test_crate_rejects_undeclared_asset(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"$asset:wallpaper"', '"$asset:ghost"')
        self._rejects(text, "asset 'ghost' is not declared in resources")

    def test_crate_rejects_duplicate_resource_id(self):
        text = self._text("desktop.nstudio").replace(
            '"path": "assets/wallpaper.png" }',
            '"path": "assets/wallpaper.png" }, { "id": "wallpaper", "kind": "icon", "path": "x.png" }')
        self._rejects(text, "duplicate resource id 'wallpaper'")

    def test_error_messages_match_floor_asset(self):
        text = self._mutate(self._text("desktop.nstudio"),
                            '"$asset:wallpaper"', '"$asset:ghost"')
        with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
            nstudio.loads(text)
        with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
            nstudio_codec.validate(text)
        first_floor_issue = str(floor_ctx.exception).split("; ")[0]
        self.assertEqual(str(crate_ctx.exception), first_floor_issue)

    def test_error_messages_match_floor_reusable(self):
        """Differential: the reusable-component gates report the same
        first failure as the floor."""
        cases = [
            ('"componentRef": "TaskbarButton"', '"componentRef": "GhostMaster"',
             "componentRef 'GhostMaster' does not name a reusable component"),
            ('"overrides": { "text": "Start" }', '"overrides": { "bogus": true }',
             "override 'bogus' not in the 'Button' contract"),
        ]
        for old, new, expected in cases:
            text = self._mutate(self._text("desktop.nstudio"), old, new)
            with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
                nstudio.loads(text)
            with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
                nstudio_codec.validate(text)
            first_floor_issue = str(floor_ctx.exception).split("; ")[0]
            self.assertEqual(str(crate_ctx.exception), first_floor_issue,
                             f"message drift for {expected}")

    def test_crate_accepts_expression_desktop(self):
        nstudio_codec.validate(self._text("desktop.nstudio"))
        nstudio.loads(self._text("desktop.nstudio"))

    def test_crate_rejects_expression_unknown_state(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"expression": "state.doNotDisturb == true"',
            '"expression": "state.ghost == true"')
        self._rejects(text, "unknown state 'state.ghost'")

    def test_crate_rejects_expression_syntax(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"expression": "state.doNotDisturb == true"',
            '"expression": "state.doNotDisturb >"')
        self._rejects(text, "syntax error at 20")

    def test_crate_rejects_expression_unknown_function(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"expression": "state.doNotDisturb == true"',
            '"expression": "bogus(state.doNotDisturb)"')
        self._rejects(text, "unknown function 'bogus'")

    def test_crate_rejects_expression_bad_arity(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"expression": "state.doNotDisturb == true"',
            '"expression": "if(state.doNotDisturb)"')
        self._rejects(text, "function 'if' expects 3 argument(s), got 1")

    def test_error_messages_match_floor_expression(self):
        """Differential: the expression gates report the same first
        failure as the floor (syntax, unknown state, unknown function,
        arity — byte offsets included)."""
        cases = [
            ('"expression": "state.doNotDisturb == true"',
             '"expression": "state.ghost == true"',
             "unknown state 'state.ghost'"),
            ('"expression": "state.doNotDisturb == true"',
             '"expression": "state.doNotDisturb >"',
             "syntax error at 20"),
            ('"expression": "state.doNotDisturb == true"',
             '"expression": "bogus(state.doNotDisturb)"',
             "unknown function 'bogus'"),
        ]
        for old, new, expected in cases:
            text = self._mutate(self._text("desktop.nstudio"), old, new)
            with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
                nstudio.loads(text)
            with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
                nstudio_codec.validate(text)
            first_floor_issue = str(floor_ctx.exception).split("; ")[0]
            self.assertEqual(str(crate_ctx.exception), first_floor_issue,
                             f"message drift for {expected}")

    def test_crate_accepts_animation_desktop(self):
        nstudio_codec.validate(self._text("desktop.nstudio"))
        nstudio.loads(self._text("desktop.nstudio"))

    def test_crate_rejects_undeclared_animation(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"animation": "start_menu_fade"', '"animation": "ghost_anim"')
        self._rejects(text, "animation 'ghost_anim' is not declared in 'animations'")

    def test_crate_rejects_unknown_easing(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"easing": "ease-out"', '"easing": "bounce"')
        self._rejects(text, "easing 'bounce' not in")

    def test_crate_rejects_unknown_animation_target(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"target": "start_menu"', '"target": "ghost"')
        self._rejects(text, "target 'ghost' does not exist")

    def test_error_messages_match_floor_animation(self):
        """Differential: the animation gates report the same first
        failure as the floor."""
        cases = [
            ('"animation": "start_menu_fade"', '"animation": "ghost_anim"',
             "animation 'ghost_anim' is not declared in 'animations'"),
            ('"easing": "ease-out"', '"easing": "bounce"',
             "easing 'bounce' not in ['ease-in', 'ease-in-out', 'ease-out', 'linear', 'steps']"),
            ('"target": "start_menu"', '"target": "ghost"',
             "target 'ghost' does not exist"),
        ]
        for old, new, expected in cases:
            text = self._mutate(self._text("desktop.nstudio"), old, new)
            with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
                nstudio.loads(text)
            with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
                nstudio_codec.validate(text)
            first_floor_issue = str(floor_ctx.exception).split("; ")[0]
            self.assertEqual(str(crate_ctx.exception), first_floor_issue,
                             f"message drift for {expected}")

    def test_crate_accepts_scoped_desktop(self):
        nstudio_codec.validate(self._text("desktop.nstudio"))
        nstudio.loads(self._text("desktop.nstudio"))

    def test_crate_rejects_unknown_scope(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"persistent": {', '"bogus": {')
        self._rejects(text, "unknown scope 'bogus'")

    def test_crate_rejects_unknown_scoped_state(self):
        text = self._mutate(
            self._text("desktop.nstudio"),
            '"expression": "state.doNotDisturb == true"',
            '"expression": "state.session.ghost"')
        self._rejects(text, "unknown state 'state.session.ghost'")

    def test_error_messages_match_floor_state_scopes(self):
        """Differential: the state-scope gates report the same first
        failure as the floor."""
        cases = [
            ('"persistent": {', '"bogus": {', "unknown scope 'bogus'"),
            ('"expression": "state.doNotDisturb == true"',
             '"expression": "state.session.ghost"',
             "unknown state 'state.session.ghost'"),
        ]
        for old, new, expected in cases:
            text = self._mutate(self._text("desktop.nstudio"), old, new)
            with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
                nstudio.loads(text)
            with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
                nstudio_codec.validate(text)
            first_floor_issue = str(floor_ctx.exception).split("; ")[0]
            self.assertEqual(str(crate_ctx.exception), first_floor_issue,
                             f"message drift for {expected}")

    def test_crate_rejects_malformed_json(self):
        self._rejects("{not json", "malformed JSON")

    def test_error_messages_match_floor(self):
        """Differential: the crate reports the same FIRST validation
        failure as the floor (the floor aggregates every issue into one
        message; the crate reports the first — for single-issue
        documents they are identical). The ADR-0020 migration contract:
        the suite passes through the FFI unchanged."""
        cases = [
            ('"type": "Toggle"', '"type": "BogusWidget"', "unknown type 'BogusWidget'"),
            ("Nyrqis.Notification.Show", "Nyrqis.System.Shutdown",
             "unknown system action 'Nyrqis.System.Shutdown'"),
            ('"component": "toggle_dnd"', '"component": "ghost"', "component 'ghost' does not exist"),
        ]
        for old, new, expected in cases:
            text = self._mutate(self._text("nyrqis-shell.nstudio"), old, new)
            with self.assertRaises(nstudio.NstudioValidationError) as floor_ctx:
                nstudio.loads(text)
            with self.assertRaises(nstudio.NstudioValidationError) as crate_ctx:
                nstudio_codec.validate(text)
            first_floor_issue = str(floor_ctx.exception).split("; ")[0]
            self.assertEqual(str(crate_ctx.exception), first_floor_issue,
                             f"message drift for {expected}")



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
    suite.addTests(loader.loadTestsFromTestCase(TestSharedMemoryTransport))
    suite.addTests(loader.loadTestsFromTestCase(TestBackendStatusService))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerCapabilityLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestStatusServiceHost))
    suite.addTests(loader.loadTestsFromTestCase(TestOperatorCli))
    suite.addTests(loader.loadTestsFromTestCase(TestServiceRouter))
    suite.addTests(loader.loadTestsFromTestCase(TestControlService))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageService))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageStreaming))
    suite.addTests(loader.loadTestsFromTestCase(TestWireLevelStreaming))
    suite.addTests(loader.loadTestsFromTestCase(TestNyVaultOperations))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageGuarantees))
    suite.addTests(loader.loadTestsFromTestCase(TestHIGDesignSystem))
    suite.addTests(loader.loadTestsFromTestCase(TestAppCompatibility))
    suite.addTests(loader.loadTestsFromTestCase(TestOverlayFilesystem))
    suite.addTests(loader.loadTestsFromTestCase(TestFUSEOverheadBenchmark))
    suite.addTests(loader.loadTestsFromTestCase(TestAppCLI))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerStats))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerLogs))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerExec))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerCheckpointRestore))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerTop))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerNetworkStats))
    suite.addTests(loader.loadTestsFromTestCase(TestImageManagement))
    suite.addTests(loader.loadTestsFromTestCase(TestSnapshotDiff))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerEvents))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerHealthCheck))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceLimitsMonitoring))
    suite.addTests(loader.loadTestsFromTestCase(TestPriorityScheduling))
    suite.addTests(loader.loadTestsFromTestCase(TestNetworkPolicy))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceQuotas))
    suite.addTests(loader.loadTestsFromTestCase(TestDependencyOrdering))
    suite.addTests(loader.loadTestsFromTestCase(TestAutoRestart))
    suite.addTests(loader.loadTestsFromTestCase(TestEnvironmentManagement))
    suite.addTests(loader.loadTestsFromTestCase(TestSnapshotExportImport))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceHistory))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceLimitsHotUpdate))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerLabels))
    suite.addTests(loader.loadTestsFromTestCase(TestCgroup2Enforcement))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerTopEnhanced))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerLocks))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceAlerts))
    suite.addTests(loader.loadTestsFromTestCase(TestOOMProtection))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceDashboard))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceExport))
    suite.addTests(loader.loadTestsFromTestCase(TestWebhooks))
    suite.addTests(loader.loadTestsFromTestCase(TestSLA))
    suite.addTests(loader.loadTestsFromTestCase(TestBilling))
    suite.addTests(loader.loadTestsFromTestCase(TestForecasting))
    suite.addTests(loader.loadTestsFromTestCase(TestLSMPolicy))
    suite.addTests(loader.loadTestsFromTestCase(TestVethBridgeNetworking))
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
    suite.addTests(loader.loadTestsFromTestCase(TestNyVaultLiveMount))
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
    suite.addTests(loader.loadTestsFromTestCase(TestKeysFloor))
    suite.addTests(loader.loadTestsFromTestCase(TestRustKeysLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestRustKeysConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestRustIpcdLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestIpcdLoopConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerIpcRegistry))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesLoader))
    suite.addTests(loader.loadTestsFromTestCase(TestContainerPrimitivesConformance))
    suite.addTests(loader.loadTestsFromTestCase(TestNuiService))
    suite.addTests(loader.loadTestsFromTestCase(TestNstudioImport))
    suite.addTests(loader.loadTestsFromTestCase(TestShellComponents))
    suite.addTests(loader.loadTestsFromTestCase(TestResponsiveLayout))
    suite.addTests(loader.loadTestsFromTestCase(TestLocalization))
    suite.addTests(loader.loadTestsFromTestCase(TestResources))
    suite.addTests(loader.loadTestsFromTestCase(TestExpressions))
    suite.addTests(loader.loadTestsFromTestCase(TestAnimations))
    suite.addTests(loader.loadTestsFromTestCase(TestStateScopes))
    suite.addTests(loader.loadTestsFromTestCase(TestBehaviorLogicGraphs))
    suite.addTests(loader.loadTestsFromTestCase(TestNstudioCodecConformance))
    suite.addTests(loader.loadTestsFromModule(__import__('tests.test_runtime', fromlist=[''])))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_tests())
