#!/usr/bin/env python3
"""
Unit tests for _grace_execution_status (ui/main.py), the decision helper
that grace_executor_job uses to resolve a recommendation after attempting
a scheduled action.

Regression covered: a resource deleted outside wasteless during the grace
period used to send the recommendation back to 'pending' on execution
failure, so grace_executor_job (every 5 minutes) retried it forever
without ever resolving. It must land on 'obsolete' instead, same terminal
state sync_aws_job would apply on its own.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobs import _grace_execution_status


class TestGraceExecutionStatus(unittest.TestCase):

    def test_real_success_is_approved(self):
        self.assertEqual(
            _grace_execution_status(True, None, dry_run=False, mode="remediator"), "approved"
        )

    def test_real_success_boto3_is_approved(self):
        self.assertEqual(
            _grace_execution_status(True, None, dry_run=False, mode="boto3"), "approved"
        )

    def test_resource_gone_via_remediator_wording_is_obsolete(self):
        error = "ebs_volume vol-x1 not found in eu-west-3"
        self.assertEqual(
            _grace_execution_status(False, error, dry_run=False, mode="remediator"), "obsolete"
        )

    def test_resource_gone_via_boto3_wording_is_obsolete(self):
        error = "Resource i-x1 not found in any region"
        self.assertEqual(
            _grace_execution_status(False, error, dry_run=False, mode="boto3"), "obsolete"
        )

    def test_generic_failure_returns_to_pending_not_obsolete(self):
        error = "Errors: eu-west-3: ClientError: throttled"
        self.assertEqual(
            _grace_execution_status(False, error, dry_run=False, mode="remediator"), "pending"
        )

    def test_dry_run_never_marks_approved(self):
        # Nothing was actually touched: must not look remediated.
        self.assertEqual(
            _grace_execution_status(True, None, dry_run=True, mode="remediator"), "pending"
        )

    def test_no_error_message_defaults_to_pending(self):
        self.assertEqual(
            _grace_execution_status(False, None, dry_run=False, mode="remediator"), "pending"
        )

    def test_action_disabled_since_approval_returns_to_pending(self):
        # execution_mode() is a static mapping (ui/utils/action_registry.py):
        # a rec_type never changes mode on its own. The only way
        # grace_executor_job sees mode='manual' for a *scheduled* item (which
        # required mode != 'manual' to be scheduled in the first place) is the
        # per-action toggle being disabled during the grace window. Nothing
        # ran, so this must land back on 'pending' for a human to decide —
        # not 'approved', which is reserved for the immediate manual-review
        # approval path in /api/actions, a different scenario.
        self.assertEqual(
            _grace_execution_status(True, None, dry_run=False, mode="manual"), "pending"
        )


if __name__ == "__main__":
    unittest.main()
