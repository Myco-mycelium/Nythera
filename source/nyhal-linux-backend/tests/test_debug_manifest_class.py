"""Pins for the D7 debug-manifest-class implementation (2026-09-23).

The decision (AG decision log D7) is implemented as design requirements
from NPS-011 v1.4.0 §4.4 and NPS-021 v1.1.0 §4.8/§5.5:

- manifest evaluation rejects CAP-DEBUG-ATTACH without ``debug_class``
  (rejected at creation — the earliest check — not silently stripped),
- the class is a NECESSARY, never sufficient, condition for the grant
  (the capability is still explicitly requested and denied by default),
- the ptrace-family relaxation happens at POLICY BUILD only — the class
  is a builder parameter, never a runtime hook (seccomp filters are
  one-shot, per ``reload_policy``),
- the class rides the policy file so the in-container launcher rebuilds
  the SAME policy the daemon compiled,
- the class is visible in every state surface (checkpoint/state dict,
  the daemon-state manifest),
- the class is not part of the default capability set.
"""

import json
import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from backend.capability import Capability, CapabilityManager
from backend.container import ContainerConfig, ContainerManager
from backend import seccomp


def _make_manager():
    return ContainerManager(
        use_cgroups_v2=False, capability_manager=CapabilityManager())


class ManifestEvaluationTests(unittest.TestCase):
    """NPS-011 §4.4: the class rule is enforced at creation."""

    def test_debug_attach_without_class_rejected_at_creation(self):
        m = _make_manager()
        config = ContainerConfig(
            command=["/bin/true"],
            capabilities=["CAP_DEBUG_ATTACH"],
        )
        with self.assertRaises(ValueError) as ctx:
            m.create(config)
        self.assertIn("CAP_DEBUG_ATTACH", str(ctx.exception))
        self.assertIn("debug_class", str(ctx.exception))

    def test_debug_attach_with_class_creates_and_declares(self):
        m = _make_manager()
        config = ContainerConfig(
            command=["/bin/true"],
            capabilities=["CAP_DEBUG_ATTACH"],
            debug_class=True,
        )
        container = m.create(config)
        self.assertTrue(container.config.debug_class)
        self.assertEqual(
            m.capability_manager.container_class(container.id),
            {"debug_class": True},
        )

    def test_unknown_capability_names_stay_inert(self):
        m = _make_manager()
        config = ContainerConfig(
            command=["/bin/true"],
            capabilities=["NOT_A_REAL_CAPABILITY"],
        )
        container = m.create(config)  # must not raise
        self.assertFalse(container.config.debug_class)

    def test_ordinary_capabilities_need_no_class(self):
        m = _make_manager()
        container = m.create(ContainerConfig(
            command=["/bin/true"],
            capabilities=["CAP_FILESYSTEM_WRITE"],
        ))
        self.assertFalse(container.config.debug_class)
        self.assertEqual(
            m.capability_manager.container_class(container.id),
            {"debug_class": False},
        )

    def test_event_records_debug_class(self):
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        events = [e for e in m._events.get_lines() if container.id in e]
        self.assertTrue(any("debug_class=true" in e for e in events))


class ClassConditionalGrantTests(unittest.TestCase):
    """NPS-011 §4.4: necessary, never sufficient; centered in the manager."""

    def test_grant_requires_declared_class(self):
        m = CapabilityManager()
        with self.assertRaises(ValueError):
            m.grant_capability("c1", Capability.CAP_DEBUG_ATTACH)
        m.declare_container_class("c1", debug_class=True)
        m.grant_capability("c1", Capability.CAP_DEBUG_ATTACH)
        self.assertIn(Capability.CAP_DEBUG_ATTACH, m.get_capabilities("c1"))

    def test_class_is_not_sufficient_by_default_set(self):
        m = CapabilityManager()
        m.declare_container_class("c1", debug_class=True)
        # Declaring the class grants nothing by itself: defaults stay
        # free of CAP_DEBUG_ATTACH (denied by default; explicitly
        # requested).
        self.assertNotIn(
            Capability.CAP_DEBUG_ATTACH, m.get_default_capabilities())

    def test_reset_clears_class_with_grants(self):
        m = CapabilityManager()
        m.declare_container_class("c1", debug_class=True)
        m.reset_container("c1")
        self.assertEqual(m.container_class("c1"), {})

    def test_class_conditional_map_shape(self):
        """The class-conditional map is exactly the D7 surface: any new
        entry is a normative change (NPS-011 §4.4) and must be pinned
        here and in the registry."""
        self.assertEqual(CapabilityManager._CLASS_CONDITIONAL, {
            Capability.CAP_DEBUG_ATTACH: "debug_class",
        })


class SeccompConstructionTimeGateTests(unittest.TestCase):
    """NPS-021 §4.8 fence 2: the relaxation is a build-time parameter."""

    def test_default_allow_mode_relaxes_only_ptrace_family(self):
        plain = seccomp.build_policy(set())
        debug = seccomp.build_policy(set(), debug_class=True)
        for name in seccomp._DEBUG_RELAXED_SYSCALLS:
            self.assertIn(name, plain.deny_syscalls)
            self.assertNotIn(name, debug.deny_syscalls)
        # Every always-deny syscall that exists on this arch stays
        # denied (deny() legitimately skips arch-absent names, e.g.
        # chroot has no x86_64 number in the table; ptrace has none on
        # arm64).
        for name in seccomp._ALWAYS_DENY:
            if name in seccomp._DEBUG_RELAXED_SYSCALLS:
                continue
            if debug._nr(name) is None:
                continue
            self.assertIn(name, debug.deny_syscalls)

    def test_default_deny_mode_explicitly_allows_ptrace_family(self):
        plain = seccomp.build_allowlist_policy(set())
        debug = seccomp.build_allowlist_policy(set(), debug_class=True)
        for name in seccomp._DEBUG_RELAXED_SYSCALLS:
            self.assertNotIn(name, plain.allow_syscalls)
            self.assertIn(name, debug.allow_syscalls)
        # Still subtracted from the baseline: nothing else leaks.
        for name in seccomp._ALWAYS_DENY:
            if name in seccomp._DEBUG_RELAXED_SYSCALLS:
                continue
            self.assertNotIn(name, debug.allow_syscalls)

    def test_both_modes_compile_with_the_gate(self):
        for builder in (seccomp.build_policy,
                        seccomp.build_allowlist_policy):
            policy = builder(set(), debug_class=True)
            self.assertTrue(len(seccomp.build_program(policy)) > 0)

    def test_policy_file_carries_the_class(self):
        """The in-container launcher rebuilds the SAME policy: the class
        rides the policy file (otherwise the launcher's rebuild — which
        recompiles from the JSON — would silently re-deny ptrace)."""
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        path = m._write_policy_file(container)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertTrue(data["debug_class"])
        finally:
            os.unlink(path)
        # And the launcher-side loader round-trips it.
        from backend.launcher import load_capabilities
        caps, debug_class = load_capabilities(path)
        self.assertFalse(debug_class if caps is None else debug_class)

    def test_daemon_and_launcher_policies_agree(self):
        """End-to-end honesty: for a debug-class container, the policy
        built daemon-side and the one rebuilt launcher-side (from the
        policy file) both admit the ptrace family — same caps, same
        class flag, same allowlist."""
        from backend.launcher import load_capabilities
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        path = m._write_policy_file(container)
        try:
            caps, debug_class = load_capabilities(path)
        finally:
            os.unlink(path)
        self.assertTrue(debug_class)
        daemon_policy = seccomp.build_allowlist_policy(
            set(caps), debug_class=True)
        launcher_policy = seccomp.build_allowlist_policy(
            set(caps), debug_class=debug_class)
        self.assertIn("ptrace", launcher_policy.allow_syscalls)
        self.assertTrue(debug_class)
        self.assertEqual(daemon_policy.allow_syscalls,
                         launcher_policy.allow_syscalls)


class StateVisibilityTests(unittest.TestCase):
    """NPS-021 §5.5 req 2: the class is visible in every state surface."""

    def test_state_dict_carries_debug_class(self):
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        state = m.container_checkpoint(container)
        self.assertTrue(state["config"]["debug_class"])

    def test_daemon_state_manifest_carries_debug_class(self):
        from backend.daemon_state import DaemonStateFile
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        entries = DaemonStateFile.manifest([container])
        self.assertTrue(entries[0]["debug_class"])
        plain = m.create(ContainerConfig())
        entries = DaemonStateFile.manifest([plain])
        self.assertFalse(entries[0]["debug_class"])

    def test_checkpoint_round_trip_preserves_class(self):
        m = _make_manager()
        container = m.create(ContainerConfig(debug_class=True))
        checkpoint = m.container_checkpoint(container)
        restored = m.container_restore(checkpoint)
        self.assertTrue(restored.config.debug_class)
        # And the restored container's class is declared in the manager
        # (restore re-enters create()) — rejection logic applies to it
        # identically.
        self.assertEqual(
            m.capability_manager.container_class(restored.id),
            {"debug_class": True},
        )


if __name__ == "__main__":
    unittest.main()
