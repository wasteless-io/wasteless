#!/usr/bin/env python3
"""
Unit tests for the Terraform PR integration — approval routing,
PR reconciliation, and ConfigManager fields.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from utils.config_manager import ConfigManager, ConfigValidationError
from utils import terraform_pr as tf_pr


def _fake_config(enabled=True, threshold=50.0, required_types=None):
    config = MagicMock()
    config.repo = "acme/infra"
    config.requires_pr = MagicMock(
        side_effect=lambda rtype, savings: (
            enabled and (rtype in (required_types or []) or savings >= threshold)
        )
    )
    return config


def _row(savings=100.0, resource_type="nat_gateway"):
    return {
        "resource_id": "nat-0abc123",
        "resource_type": resource_type,
        "recommendation_type": "delete_nat_gateway",
        "action_required": "NAT gateway unused for 30 days",
        "estimated_monthly_savings_eur": savings,
        "confidence_score": 0.92,
        "metadata": {},
    }


class TestMaybeOpenPR(unittest.TestCase):

    def setUp(self):
        self.conn = MagicMock()
        self.cursor = MagicMock()
        self.conn.cursor.return_value = self.cursor

    def test_below_threshold_not_routed(self):
        with patch.object(tf_pr, "_load_backend_config", return_value=_fake_config(threshold=50.0)):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(savings=3.65), dry_run=True)
        self.assertIsNone(result)

    def test_config_unavailable_falls_back(self):
        with patch.object(tf_pr, "_load_backend_config", side_effect=RuntimeError("no backend")):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(), dry_run=True)
        self.assertIsNone(result)

    def test_unmanaged_resource_falls_back(self):
        remediator = MagicMock()
        remediator.propose_removal.return_value = None
        with (
            patch.object(tf_pr, "_load_backend_config", return_value=_fake_config()),
            patch("src.remediators.terraform_pr.TerraformPRRemediator", return_value=remediator),
        ):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(), dry_run=True)
        self.assertIsNone(result)

    def test_routed_real_run_stores_pr_url(self):
        proposal = MagicMock(
            pr_url="https://github.com/acme/infra/pull/7",
            branch="wasteless/remove-nat-0abc123",
            address="aws_nat_gateway.unused",
        )
        remediator = MagicMock()
        remediator.propose_removal.return_value = proposal
        with (
            patch.object(tf_pr, "_load_backend_config", return_value=_fake_config()),
            patch("src.remediators.terraform_pr.TerraformPRRemediator", return_value=remediator),
        ):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(), dry_run=False)

        self.assertTrue(result["success"])
        self.assertTrue(result["terraform_pr"])
        self.assertEqual(result["pr_url"], proposal.pr_url)
        sql_calls = " ".join(str(c) for c in self.cursor.execute.call_args_list)
        self.assertIn("pr_open", sql_calls)
        self.assertIn("actions_log", sql_calls)

    def test_dry_run_does_not_change_status(self):
        proposal = MagicMock(pr_url=None, branch="b", address="a")
        remediator = MagicMock()
        remediator.propose_removal.return_value = proposal
        with (
            patch.object(tf_pr, "_load_backend_config", return_value=_fake_config()),
            patch("src.remediators.terraform_pr.TerraformPRRemediator", return_value=remediator),
        ):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(), dry_run=True)

        self.assertTrue(result["success"])
        sql_calls = " ".join(str(c) for c in self.cursor.execute.call_args_list)
        self.assertNotIn("pr_open", sql_calls)
        self.assertIn("actions_log", sql_calls)

    def test_unsafe_change_surfaces_error_without_fallback(self):
        from src.remediators.terraform_pr import TerraformPRError

        remediator = MagicMock()
        remediator.propose_removal.side_effect = TerraformPRError("dangling references")
        with (
            patch.object(tf_pr, "_load_backend_config", return_value=_fake_config()),
            patch("src.remediators.terraform_pr.TerraformPRRemediator", return_value=remediator),
        ):
            result = tf_pr.maybe_open_pr(self.conn, 1, _row(), dry_run=False)

        self.assertIsNotNone(result)  # routed: no silent API fallback
        self.assertFalse(result["success"])
        self.assertIn("dangling", result["error"])


class TestSyncOpenPRs(unittest.TestCase):

    def _conn_with_prs(self, prs):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = prs
        cursor.rowcount = 1
        conn.cursor.return_value = cursor
        return conn, cursor

    def _gh(self, state):
        return MagicMock(returncode=0, stdout=json.dumps({"state": state}))

    def test_merged_pr_approves(self):
        conn, cursor = self._conn_with_prs([{"id": 1, "pr_url": "https://github.com/a/b/pull/1"}])
        with patch.object(tf_pr.subprocess, "run", return_value=self._gh("MERGED")):
            updated = tf_pr.sync_open_prs(conn)
        self.assertEqual(updated, 1)
        sql = str(cursor.execute.call_args_list[-1])
        self.assertIn("approved", sql)

    def test_closed_pr_rejects(self):
        conn, cursor = self._conn_with_prs([{"id": 1, "pr_url": "https://github.com/a/b/pull/1"}])
        with patch.object(tf_pr.subprocess, "run", return_value=self._gh("CLOSED")):
            updated = tf_pr.sync_open_prs(conn)
        self.assertEqual(updated, 1)
        sql = str(cursor.execute.call_args_list[-1])
        self.assertIn("rejected", sql)

    def test_open_pr_untouched(self):
        conn, cursor = self._conn_with_prs([{"id": 1, "pr_url": "https://github.com/a/b/pull/1"}])
        with patch.object(tf_pr.subprocess, "run", return_value=self._gh("OPEN")):
            updated = tf_pr.sync_open_prs(conn)
        self.assertEqual(updated, 0)

    def test_gh_failure_is_non_fatal(self):
        conn, cursor = self._conn_with_prs([{"id": 1, "pr_url": "https://github.com/a/b/pull/1"}])
        with patch.object(
            tf_pr.subprocess, "run", return_value=MagicMock(returncode=1, stderr="boom")
        ):
            updated = tf_pr.sync_open_prs(conn)
        self.assertEqual(updated, 0)


class TestTerraformPRConfigFields(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.safe_dump({"auto_remediation": {"enabled": False}}, self.tmp)
        self.tmp.close()
        self.manager = ConfigManager(config_path=self.tmp.name)

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_set_and_persist_repo(self):
        self.assertTrue(self.manager.set_terraform_pr_field("repo", "acme/infra"))
        fresh = ConfigManager(config_path=self.tmp.name)
        self.assertEqual(fresh.get_terraform_pr().get("repo"), "acme/infra")

    def test_invalid_repo_rejected(self):
        with self.assertRaises(ConfigValidationError):
            self.manager.set_terraform_pr_field("repo", "not-a-repo")

    def test_negative_threshold_rejected(self):
        with self.assertRaises(ConfigValidationError):
            self.manager.set_terraform_pr_field("pr_threshold_eur", -1)

    def test_unknown_field_rejected(self):
        with self.assertRaises(ConfigValidationError):
            self.manager.set_terraform_pr_field("nope", 1)


if __name__ == "__main__":
    unittest.main()
