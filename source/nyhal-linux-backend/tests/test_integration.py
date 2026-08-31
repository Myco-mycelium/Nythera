"""
CI integration tests for batch operations, deployment environments,
and container versioning — end-to-end workflows.
"""
import sys
import os
import unittest
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.container import ContainerManager, ContainerConfig


class TestBatchOperationsIntegration(unittest.TestCase):
    """End-to-end batch operation workflows."""

    def setUp(self):
        self.mgr = ContainerManager()
        self.containers = []

    def tearDown(self):
        for c in self.containers:
            try:
                self.mgr.terminate(c)
            except Exception:
                pass

    def _mk(self, name):
        c = self.mgr.create(ContainerConfig(name=name, command=["sleep", "10"]))
        self.containers.append(c)
        return c

    def test_full_batch_lifecycle(self):
        """Create → execute → verify progress → get results."""
        c1 = self._mk("int-b1")
        c2 = self._mk("int-b2")
        c3 = self._mk("int-b3")

        # Create batch with mixed ops
        batch = self.mgr.create_batch_operation(
            "lifecycle-test",
            [
                {"op": "inspect", "container_id": c1.id},
                {"op": "inspect", "container_id": c2.id},
                {"op": "nonexistent-op", "container_id": c3.id},
            ],
        )
        self.assertTrue(batch["ok"])
        self.assertEqual(batch["total"], 3)

        # Verify initial status
        status = self.mgr.get_batch_status(batch["batch_id"])
        self.assertEqual(status["status"], "created")
        self.assertEqual(status["progress"]["total"], 3)
        self.assertEqual(status["progress"]["completed"], 0)

        # Execute
        result = self.mgr.execute_batch_operation(batch["batch_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"]["total"], 3)
        self.assertGreaterEqual(result["progress"]["completed"], 2)

        # Verify final status
        status = self.mgr.get_batch_status(batch["batch_id"])
        self.assertEqual(status["status"], "completed")
        self.assertIsNotNone(status["completed_at"])

        # Verify per-operation results
        results = self.mgr.get_batch_operation_results(batch["batch_id"])
        self.assertEqual(len(results["operations"]), 3)

    def test_batch_cancel_prevents_execution(self):
        """Cancel before execution skips all ops."""
        c = self._mk("int-cancel")
        batch = self.mgr.create_batch_operation(
            "cancel-test",
            [{"op": "inspect", "container_id": c.id}],
        )
        cancel = self.mgr.cancel_batch_operation(batch["batch_id"])
        self.assertTrue(cancel["ok"])
        self.assertEqual(cancel["status"], "cancelled")

        status = self.mgr.get_batch_status(batch["batch_id"])
        self.assertEqual(status["progress"]["skipped"], 1)

    def test_batch_stop_on_error(self):
        """Stop-on-error prevents subsequent operations."""
        c1 = self._mk("int-soe1")
        c2 = self._mk("int-soe2")
        c3 = self._mk("int-soe3")

        batch = self.mgr.create_batch_operation(
            "stop-on-err",
            [
                {"op": "stop", "container_id": c1.id},  # fails (not running)
                {"op": "inspect", "container_id": c2.id},
                {"op": "inspect", "container_id": c3.id},
            ],
            stop_on_error=True,
        )
        result = self.mgr.execute_batch_operation(batch["batch_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"]["failed"], 1)
        self.assertEqual(result["progress"]["skipped"], 2)

    def test_batch_inventory(self):
        """List all batches."""
        self.mgr.create_batch_operation("b1", [{"op": "inspect", "container_id": "x"}])
        self.mgr.create_batch_operation("b2", [{"op": "inspect", "container_id": "y"}])
        result = self.mgr.list_batch_operations()
        self.assertEqual(result["count"], 2)


class TestDeploymentEnvironmentsIntegration(unittest.TestCase):
    """End-to-end deployment environment workflows."""

    def setUp(self):
        self.mgr = ContainerManager()

    def tearDown(self):
        pass  # Environments are pure data, no containers to clean

    def test_full_promotion_workflow(self):
        """Create envs → deploy → promote → verify history."""
        self.mgr.create_environment("dev")
        self.mgr.create_environment("staging", parent="dev")
        self.mgr.create_environment("prod", parent="staging")

        # Deploy to dev
        d1 = self.mgr.deploy_to_environment("dev", version="v1.0.0", notes="Initial")
        self.assertTrue(d1["ok"])

        # Deploy v2
        self.mgr.deploy_to_environment("dev", version="v1.1.0")

        # Promote to staging
        p = self.mgr.promote_between_environments("dev", "staging")
        self.assertTrue(p["ok"])

        # Promote to prod
        p2 = self.mgr.promote_between_environments("staging", "prod")
        self.assertTrue(p2["ok"])

        # Verify histories
        dev_hist = self.mgr.get_environment_history("dev")
        self.assertEqual(dev_hist["count"], 2)

        prod_hist = self.mgr.get_environment_history("prod")
        self.assertEqual(prod_hist["count"], 1)

    def test_lock_blocks_deployment(self):
        """Locking an environment prevents deployment."""
        self.mgr.create_environment("locked-env")
        self.mgr.lock_environment("locked-env")

        result = self.mgr.deploy_to_environment("locked-env")
        self.assertIn("error", result)

        self.mgr.unlock_environment("locked-env")
        result = self.mgr.deploy_to_environment("locked-env")
        self.assertTrue(result["ok"])

    def test_rollback_restores_previous(self):
        """Rollback reverts to the prior deployment."""
        self.mgr.create_environment("rb-env")
        self.mgr.deploy_to_environment("rb-env", version="v1")
        self.mgr.deploy_to_environment("rb-env", version="v2")
        self.mgr.deploy_to_environment("rb-env", version="v3")

        result = self.mgr.rollback_environment("rb-env")
        self.assertTrue(result["ok"])
        self.assertEqual(result["rolled_back_from"], "v3")
        self.assertEqual(result["rolled_back_to"], "v2")

    def test_promote_dry_run(self):
        """Dry-run promotion checks rules without deploying."""
        self.mgr.create_environment("dr-src")
        self.mgr.create_environment("dr-dst", parent="dr-src")
        self.mgr.deploy_to_environment("dr-src", version="v1")

        result = self.mgr.promote_between_environments("dr-src", "dr-dst", dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("checks", result)

        # Verify nothing was actually deployed
        dst = self.mgr.get_environment("dr-dst")
        self.assertEqual(dst["deployment_count"], 0)

    def test_environment_chain(self):
        """Multiple environments form a promotion chain."""
        for name in ["dev", "qa", "staging", "prod"]:
            parent = {"dev": None, "qa": "dev", "staging": "qa", "prod": "staging"}[name]
            self.mgr.create_environment(name, parent=parent)

        envs = self.mgr.list_environments()
        self.assertEqual(envs["count"], 4)

        for name in ["dev", "qa", "staging", "prod"]:
            env = self.mgr.get_environment(name)
            self.assertIn("promotion_rules", env)


class TestContainerVersioningIntegration(unittest.TestCase):
    """End-to-end container versioning workflows."""

    def setUp(self):
        self.mgr = ContainerManager()
        self.containers = []

    def tearDown(self):
        for c in self.containers:
            try:
                self.mgr.terminate(c)
            except Exception:
                pass

    def _mk(self, name):
        c = self.mgr.create(ContainerConfig(name=name, command=["sleep", "10"]))
        self.containers.append(c)
        return c

    def test_version_and_rollback(self):
        """Create versions, change config, rollback, verify."""
        c = self._mk("ver-1")
        self.mgr.create_version(c, notes="Initial config")

        # Change config
        c.config.limits.memory_mb = 512
        self.mgr.create_version(c, notes="Doubled memory")

        c.config.limits.memory_mb = 1024
        self.mgr.create_version(c, notes="Quadrupled memory")

        # Verify history
        hist = self.mgr.get_version_history(c.id)
        self.assertEqual(hist["total"], 3)

        # Verify active version
        active = self.mgr.get_active_version(c.id)
        self.assertEqual(active["version"], 3)

        # Diff between v1 and v3
        diff = self.mgr.diff_versions(c.id, 1, 3)
        self.assertGreater(diff["changed_count"], 0)

        # Rollback to v1
        rollback = self.mgr.rollback_version(c.id, 1)
        self.assertTrue(rollback["ok"])

        # Verify active is now v1
        active = self.mgr.get_active_version(c.id)
        self.assertEqual(active["version"], 1)

    def test_version_no_changes_detected(self):
        """Diff between identical versions shows no changes."""
        c = self._mk("ver-2")
        self.mgr.create_version(c, notes="v1")
        # Don't change anything
        self.mgr.create_version(c, notes="v2 no changes")

        diff = self.mgr.diff_versions(c.id, 1, 2)
        self.assertEqual(diff["changed_count"], 0)
        self.assertEqual(len(diff["changes"]), 0)

    def test_version_empty_history(self):
        """Empty version history returns appropriate response."""
        c = self._mk("ver-empty")
        hist = self.mgr.get_version_history(c.id)
        self.assertEqual(hist["total"], 0)

        active = self.mgr.get_active_version(c.id)
        self.assertIsNone(active["version"])


if __name__ == "__main__":
    unittest.main()
