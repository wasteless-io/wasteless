#!/usr/bin/env python3
"""
Tests for the execution-mode registry (utils/action_registry.py).

Three layers:
1. Registry semantics — declared modes are valid, unknown types fall
   back to 'manual' (the safe default).
2. Guard test — every detector's recommendation_type MUST be declared
   in EXECUTION_MODES. This is the test that fails when a new detector
   is added without consciously choosing how its approval executes
   (the delete_vpc bug: approving fell into the boto3 branch and failed
   with a misleading "not found in any region").
3. Approve flow — approving a manual-review type in production mode
   records the decision (approved + manual flag) without any AWS call.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

UI_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = UI_DIR.parent / 'src'
sys.path.insert(0, str(UI_DIR))

from utils.action_registry import EXECUTION_MODES, execution_mode

VALID_MODES = {'boto3', 'remediator', 'manual'}

# Types created inline by the historical (non-Steampipe) detectors:
# ec2_idle, ec2_stopped, ebs_orphan, eip_orphan, snapshot_orphan.
HISTORICAL_TYPES = {
    'stop_instance', 'terminate_instance', 'downsize_instance',
    'delete_volume', 'delete_snapshot', 'release_ip',
}


class TestRegistrySemantics(unittest.TestCase):

    def test_all_declared_modes_are_valid(self):
        for rec_type, mode in EXECUTION_MODES.items():
            self.assertIn(mode, VALID_MODES,
                          f"{rec_type} has invalid mode '{mode}'")

    def test_unknown_type_defaults_to_manual(self):
        self.assertEqual(execution_mode('brand_new_type'), 'manual')

    def test_known_types(self):
        self.assertEqual(execution_mode('stop_instance'), 'boto3')
        self.assertEqual(execution_mode('migrate_gp2_to_gp3'), 'remediator')
        self.assertEqual(execution_mode('delete_vpc'), 'manual')


class TestDetectorGuard(unittest.TestCase):
    """A recommendation type reaching the UI without a declared execution
    mode is exactly how the delete_vpc approval bug happened. Fail fast."""

    def test_historical_detector_types_are_declared(self):
        undeclared = HISTORICAL_TYPES - set(EXECUTION_MODES)
        self.assertEqual(undeclared, set(),
                         f"Historical types missing from EXECUTION_MODES: "
                         f"{undeclared}")

    def test_steampipe_detector_types_are_declared(self):
        import importlib
        import pkgutil

        sys.path.insert(0, str(REPO_SRC))
        try:
            from detectors.steampipe_base import SteampipeWasteDetector
            for mod in pkgutil.iter_modules([str(REPO_SRC / 'detectors')]):
                importlib.import_module(f'detectors.{mod.name}')

            def all_subclasses(cls):
                subs = set(cls.__subclasses__())
                return subs.union(*(all_subclasses(s) for s in subs))

            detectors = all_subclasses(SteampipeWasteDetector)
            self.assertGreater(len(detectors), 0,
                               "No Steampipe detector found — path issue?")
            for cls in detectors:
                rec_type = cls.recommendation_type
                if not rec_type:  # abstract intermediates
                    continue
                self.assertIn(
                    rec_type, EXECUTION_MODES,
                    f"{cls.__name__} introduces recommendation type "
                    f"'{rec_type}' — declare it in "
                    f"ui/utils/action_registry.py (default choice: 'manual')")
        finally:
            sys.path.remove(str(REPO_SRC))


class TestApproveFlow(unittest.TestCase):
    """Approve via /api/actions with a mocked DB: manual-review types in
    production mode must record the decision without touching AWS."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main
        cls.main = main
        # No context manager: lifespan (scheduler, sync job) must not run
        cls.client = TestClient(main.app)

    def _approve(self, rec_type, resource_type, resource_id, dry_run):
        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.fetchone.return_value = {
            'resource_id': resource_id,
            'resource_type': resource_type,
            'recommendation_type': rec_type,
            'metadata': {'region': 'eu-west-3'},
        }
        self.main.app.dependency_overrides[self.main.get_db] = lambda: conn
        try:
            with patch.object(self.main._config_manager, 'get_dry_run',
                              return_value=dry_run):
                response = self.client.post('/api/actions', json={
                    'recommendation_ids': [42],
                    'action': 'approve',
                    'dry_run': dry_run,
                })
        finally:
            self.main.app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 200)
        return response.json()['results'][0], cursor

    def test_manual_types_approved_in_production_without_aws(self):
        manual_types = [t for t, m in EXECUTION_MODES.items()
                        if m == 'manual']
        self.assertGreater(len(manual_types), 0)
        for rec_type in manual_types:
            with self.subTest(rec_type=rec_type):
                result, cursor = self._approve(
                    rec_type, 'vpc', 'res-1', dry_run=False)
                self.assertTrue(result['success'],
                                f"{rec_type}: {result.get('error')}")
                self.assertTrue(result['manual'])
                self.assertNotIn('error', result)
                # decision recorded as 'approved_manual', not 'approved':
                # nothing touched AWS, the resource still counts as active
                # waste until the human deletes it and a sync confirms it.
                updates = [c for c in cursor.execute.call_args_list
                           if 'UPDATE recommendations' in str(c)]
                self.assertTrue(any('approved_manual' in str(c.args[1])
                                    for c in updates))

    def test_manual_approval_logged_as_dry_run(self):
        result, cursor = self._approve(
            'delete_vpc', 'vpc', 'vpc-1', dry_run=False)
        log_calls = [c for c in cursor.execute.call_args_list
                     if 'actions_log' in str(c)]
        self.assertEqual(len(log_calls), 1)
        # dry_run column (6th param) must be True: nothing touched AWS
        params = log_calls[0].args[1]
        self.assertTrue(params[5])

    def test_boto3_type_approved_in_dry_run(self):
        result, _ = self._approve(
            'stop_instance', 'ec2_instance', 'i-1', dry_run=True)
        self.assertTrue(result['success'])
        self.assertFalse(result['manual'])

    def test_disabled_toggle_degrades_automated_type_to_manual(self):
        # stop_instance is 'boto3' in the registry; with its per-action
        # toggle off, production approval must not attempt any AWS call
        with patch.object(self.main._config_manager, 'get_action_enabled',
                          return_value=False):
            result, cursor = self._approve(
                'stop_instance', 'ec2_instance', 'i-1', dry_run=False)
        self.assertTrue(result['success'], result.get('error'))
        self.assertTrue(result['manual'])
        # logged as dry-run: nothing touched AWS
        log_calls = [c for c in cursor.execute.call_args_list
                     if 'actions_log' in str(c)]
        self.assertTrue(log_calls[0].args[1][5])


if __name__ == '__main__':
    unittest.main()
