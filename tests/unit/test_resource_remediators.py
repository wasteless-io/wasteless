"""
Unit tests for the generic resource remediators (gp2 migration, NAT
gateway, load balancer). AWS and the database are fully mocked; the tests
exercise the guarded remediation flow and the live waste re-verification.
"""

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.safeguards import SafeguardException
from remediators.resource_remediator import (
    Gp2MigrationRemediator,
    LoadBalancerRemediator,
    NATGatewayRemediator,
    VolumeDeleteRemediator,
    REMEDIATORS_BY_RECOMMENDATION,
)


def _bare(cls, dry_run=True):
    """Instantiate a remediator without DB/safeguards initialization."""
    r = object.__new__(cls)
    r.dry_run = dry_run
    r.safeguards = MagicMock()
    r.safeguards.is_whitelisted.return_value = False
    r.safeguards.is_auto_remediation_enabled.return_value = False
    r.conn = MagicMock()
    return r


class TestVerifyStillWasteful:

    def test_gp2_volume_still_gp2_passes(self):
        r = _bare(Gp2MigrationRemediator)
        r.verify_still_wasteful({"volume_type": "gp2"}, "vol-1", "eu-west-3")

    def test_gp3_volume_blocks(self):
        r = _bare(Gp2MigrationRemediator)
        with pytest.raises(SafeguardException) as exc_info:
            r.verify_still_wasteful({"volume_type": "gp3"}, "vol-1", "eu-west-3")
        assert "already migrated" in str(exc_info.value)

    def test_lb_with_targets_blocks(self):
        r = _bare(LoadBalancerRemediator)
        with pytest.raises(SafeguardException) as exc_info:
            r.verify_still_wasteful(
                {"lb_type": "application", "registered_targets": 3}, "arn:aws:...", "eu-west-3"
            )
        assert "not deleting" in str(exc_info.value)

    def test_lb_without_targets_passes(self):
        r = _bare(LoadBalancerRemediator)
        r.verify_still_wasteful(
            {"lb_type": "application", "registered_targets": 0}, "arn:aws:...", "eu-west-3"
        )

    def test_classic_lb_with_instances_blocks(self):
        r = _bare(LoadBalancerRemediator)
        with pytest.raises(SafeguardException):
            r.verify_still_wasteful(
                {"lb_type": "classic", "instances": ["i-1"]}, "my-clb", "eu-west-3"
            )

    def test_nat_gateway_passes(self):
        r = _bare(NATGatewayRemediator)
        r.verify_still_wasteful({"state": "available"}, "nat-1", "eu-west-3")


class TestVolumeDelete:

    def _available(self):
        return {
            "volume_id": "vol-1",
            "volume_type": "gp3",
            "size_gb": 8,
            "state": "available",
            "az": "eu-west-3a",
            "attachments": [],
            "tags": {},
        }

    def test_available_volume_passes(self):
        r = _bare(VolumeDeleteRemediator)
        r.verify_still_wasteful(self._available(), "vol-1", "eu-west-3")

    def test_attached_volume_blocks(self):
        r = _bare(VolumeDeleteRemediator)
        state = dict(self._available(), state="in-use", attachments=["i-0abc"])
        with pytest.raises(SafeguardException) as exc_info:
            r.verify_still_wasteful(state, "vol-1", "eu-west-3")
        assert "i-0abc" in str(exc_info.value)
        assert "not deleting" in str(exc_info.value)

    def test_snapshot_created_before_delete(self):
        """Snapshot-first is the whole safety story: order matters."""
        r = _bare(VolumeDeleteRemediator)
        calls = []
        ec2 = MagicMock()
        ec2.create_snapshot.side_effect = lambda **kw: (
            calls.append("snapshot"),
            {"SnapshotId": "snap-rollback1"},
        )[1]
        ec2.delete_volume.side_effect = lambda **kw: calls.append("delete")
        with patch("remediators.resource_remediator.get_client", return_value=ec2):
            r.execute_action("vol-1", "eu-west-3", self._available())
        assert calls == ["snapshot", "delete"]
        # the rollback row gets the EBS snapshot id merged in
        merged = [
            c
            for c in r.conn.cursor.return_value.execute.call_args_list
            if "rollback_snapshots" in str(c)
        ]
        assert len(merged) == 1
        assert "snap-rollback1" in str(merged[0])

    def test_rollback_snapshot_tagged(self):
        r = _bare(VolumeDeleteRemediator)
        ec2 = MagicMock()
        ec2.create_snapshot.return_value = {"SnapshotId": "snap-1"}
        with patch("remediators.resource_remediator.get_client", return_value=ec2):
            r.execute_action("vol-1", "eu-west-3", self._available())
        tags = ec2.create_snapshot.call_args.kwargs["TagSpecifications"][0]["Tags"]
        assert {"Key": "wasteless:rollback", "Value": "true"} in tags
        assert {"Key": "wasteless:source-volume", "Value": "vol-1"} in tags

    def test_dry_run_never_calls_aws(self):
        r = _bare(VolumeDeleteRemediator)
        r.get_resource_state = MagicMock(return_value=self._available())
        r.execute_action = MagicMock()
        r._get_recommendation_confidence = MagicMock(return_value=0.95)
        r._log_action = MagicMock(return_value=42)
        r._create_rollback_snapshot = MagicMock(return_value=7)
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is True
        r.execute_action.assert_not_called()

    def test_registered_for_delete_volume(self):
        assert REMEDIATORS_BY_RECOMMENDATION["delete_volume"] is VolumeDeleteRemediator


class TestRemediateFlow:

    def _remediator_with_state(self, state, dry_run=True):
        r = _bare(Gp2MigrationRemediator, dry_run=dry_run)
        r.get_resource_state = MagicMock(return_value=state)
        r.execute_action = MagicMock()
        r._get_recommendation_confidence = MagicMock(return_value=0.95)
        r._log_action = MagicMock(return_value=42)
        r._update_action_status = MagicMock()
        r._create_rollback_snapshot = MagicMock(return_value=7)
        return r

    def test_dry_run_never_calls_aws(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}})
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is True
        assert result["dry_run"] is True
        r.execute_action.assert_not_called()
        r._create_rollback_snapshot.assert_called_once()

    def test_dry_run_does_not_mark_recommendation_applied(self):
        # Nothing was actually touched on AWS: the recommendation must stay
        # out of 'applied' so it keeps counting as active waste instead of
        # silently looking remediated.
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}})
        r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        cursor = r.conn.cursor.return_value
        sql_calls = " ".join(str(c) for c in cursor.execute.call_args_list)
        assert "status = 'applied'" not in sql_calls

    def test_real_run_marks_recommendation_applied(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}}, dry_run=False)
        r.safeguards.is_auto_remediation_enabled.return_value = True
        r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        cursor = r.conn.cursor.return_value
        sql_calls = " ".join(str(c) for c in cursor.execute.call_args_list)
        assert "status = 'applied'" in sql_calls

    def test_missing_resource_fails(self):
        r = self._remediator_with_state(None)
        result = r.remediate("vol-gone", recommendation_id=1, region="eu-west-3")
        assert result["success"] is False
        assert "not found" in result["error"]
        r.execute_action.assert_not_called()

    def test_whitelisted_resource_blocked(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}})
        r.safeguards.is_whitelisted.return_value = True
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is False
        assert "whitelisted" in result["error"]
        r.execute_action.assert_not_called()

    def test_low_confidence_blocked(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}})
        r.safeguards.check_confidence_score.side_effect = SafeguardException("Confidence too low")
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is False
        r.execute_action.assert_not_called()

    def test_action_disabled_by_toggle_blocked(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}})
        r.safeguards.is_action_enabled.return_value = False
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is False
        assert "disabled by config" in result["error"]
        r.execute_action.assert_not_called()

    def test_real_run_blocked_when_auto_remediation_disabled(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}}, dry_run=False)
        r.safeguards.is_auto_remediation_enabled.return_value = False
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is False
        assert "disabled" in result["error"]
        r.execute_action.assert_not_called()

    def test_real_run_executes_when_enabled(self):
        r = self._remediator_with_state({"volume_type": "gp2", "tags": {}}, dry_run=False)
        r.safeguards.is_auto_remediation_enabled.return_value = True
        result = r.remediate("vol-1", recommendation_id=1, region="eu-west-3")
        assert result["success"] is True
        r.execute_action.assert_called_once()


class TestExecuteActions:

    @patch("remediators.resource_remediator.get_client")
    def test_gp2_migration_calls_modify_volume(self, mock_get_client):
        ec2 = MagicMock()
        mock_get_client.return_value = ec2
        _bare(Gp2MigrationRemediator).execute_action("vol-1", "eu-west-3", {})
        ec2.modify_volume.assert_called_once_with(VolumeId="vol-1", VolumeType="gp3")

    @patch("remediators.resource_remediator.get_client")
    def test_nat_deletion_calls_delete(self, mock_get_client):
        ec2 = MagicMock()
        mock_get_client.return_value = ec2
        _bare(NATGatewayRemediator).execute_action("nat-1", "eu-west-3", {})
        ec2.delete_nat_gateway.assert_called_once_with(NatGatewayId="nat-1")

    @patch("remediators.resource_remediator.get_client")
    def test_lb_deletion_routes_by_id_shape(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client
        r = _bare(LoadBalancerRemediator)

        r.execute_action("arn:aws:elasticloadbalancing:...", "eu-west-3", {})
        client.delete_load_balancer.assert_called_with(
            LoadBalancerArn="arn:aws:elasticloadbalancing:..."
        )

        r.execute_action("my-classic-lb", "eu-west-3", {})
        client.delete_load_balancer.assert_called_with(LoadBalancerName="my-classic-lb")


class TestRegistry:

    def test_all_new_recommendation_types_covered(self):
        assert set(REMEDIATORS_BY_RECOMMENDATION) == {
            "migrate_gp2_to_gp3",
            "delete_volume",
            "delete_nat_gateway",
            "delete_load_balancer",
        }

    def test_rollback_flags(self):
        assert Gp2MigrationRemediator.can_rollback is True
        assert VolumeDeleteRemediator.can_rollback is True  # snapshot-first
        assert NATGatewayRemediator.can_rollback is False
        assert LoadBalancerRemediator.can_rollback is False
