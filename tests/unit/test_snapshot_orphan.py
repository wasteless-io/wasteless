"""
Unit tests for the old-snapshot detector (src/detectors/snapshot_orphan.py).

DB and AWS are mocked — no Postgres or credentials required. The
protection filters matter most here: a snapshot backing a registered
AMI or managed by AWS Backup/DLM must never be recommended for
deletion, whatever its age.
"""

import json
import pytest
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from detectors.snapshot_orphan import (
    SnapshotOrphanDetector,
    _fetch_ami_snapshot_ids,
    _fetch_old_snapshots,
    _snapshot_monthly_cost,
)


def _detector_with_mock_conn():
    """Bypass __init__ (env vars + real psycopg2.connect) entirely."""
    detector = SnapshotOrphanDetector.__new__(SnapshotOrphanDetector)
    detector.conn = MagicMock()
    return detector


def _snapshot(snap_id, age_days, size_gb=100, tags=None, **extra):
    snap = {
        "SnapshotId": snap_id,
        "StartTime": datetime.now(timezone.utc) - timedelta(days=age_days, hours=1),
        "VolumeSize": size_gb,
        "State": "completed",
        "Description": "",
        "VolumeId": "vol-1",
        "Encrypted": False,
    }
    if tags is not None:
        snap["Tags"] = tags
    snap.update(extra)
    return snap


class TestSnapshotMonthlyCost:
    def test_cost_per_gib(self):
        assert _snapshot_monthly_cost(100) == 4.60
        assert _snapshot_monthly_cost(0) == 0.0


class TestFetchAmiSnapshotIds:
    def test_collects_snapshot_ids_from_image_mappings(self):
        ec2 = MagicMock()
        ec2.describe_images.return_value = {
            "Images": [
                {
                    "BlockDeviceMappings": [
                        {"Ebs": {"SnapshotId": "snap-ami1"}},
                        {"VirtualName": "ephemeral0"},  # no Ebs key
                    ]
                },
                {"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-ami2"}}]},
            ]
        }
        assert _fetch_ami_snapshot_ids(ec2) == {"snap-ami1", "snap-ami2"}

    def test_no_images(self):
        ec2 = MagicMock()
        ec2.describe_images.return_value = {"Images": []}
        assert _fetch_ami_snapshot_ids(ec2) == set()


class TestFetchOldSnapshots:
    def _run(self, snapshots, images=None):
        ec2 = MagicMock()
        ec2.describe_images.return_value = {"Images": images or []}
        ec2.describe_snapshots.return_value = {"Snapshots": snapshots}
        with patch("core.aws_clients.get_client", return_value=ec2):
            return _fetch_old_snapshots("eu-west-1")

    def test_old_snapshot_is_flagged_with_age_and_cost(self):
        result, ami_ids = self._run([_snapshot("snap-old", age_days=120)])

        assert ami_ids == set()
        assert len(result) == 1
        assert result[0]["snapshot_id"] == "snap-old"
        assert result[0]["age_days"] == 120
        assert result[0]["monthly_cost"] == 4.60
        assert result[0]["region"] == "eu-west-1"

    def test_young_snapshot_is_ignored(self):
        result, _ = self._run([_snapshot("snap-young", age_days=30)])
        assert result == []

    def test_ami_backed_snapshot_is_protected(self):
        """A snapshot backing a registered AMI must never be flagged —
        deleting it would break the AMI."""
        images = [{"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-ami"}}]}]
        result, ami_ids = self._run([_snapshot("snap-ami", age_days=400)], images=images)

        assert result == []
        assert ami_ids == {"snap-ami"}

    def test_aws_backup_managed_snapshot_is_protected(self):
        tags = [{"Key": "aws:backup:source-resource", "Value": "arn:..."}]
        result, _ = self._run([_snapshot("snap-backup", age_days=400, tags=tags)])
        assert result == []

    def test_dlm_managed_snapshot_is_protected(self):
        tags = [{"Key": "aws:dlm:lifecycle-policy-id", "Value": "policy-1"}]
        result, _ = self._run([_snapshot("snap-dlm", age_days=400, tags=tags)])
        assert result == []

    def test_naive_start_time_is_treated_as_utc(self):
        snap = _snapshot("snap-naive", age_days=120)
        snap["StartTime"] = snap["StartTime"].replace(tzinfo=None)
        result, _ = self._run([snap])
        assert len(result) == 1

    def test_api_error_degrades_to_empty(self):
        with patch("core.aws_clients.get_client", side_effect=Exception("no creds")):
            result, ami_ids = _fetch_old_snapshots("eu-west-1")
        assert result == []
        assert ami_ids == set()


class TestSaveConfidence:
    """Confidence grows with age: 0.60 at the 90-day threshold, +0.001/day,
    capped at 0.90 — an old snapshot is never 'certain' waste the way an
    explicitly stopped instance is."""

    def _saved_confidence(self, age_days):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchone.return_value = (1,)
        snap = {
            "snapshot_id": "snap-1",
            "description": "",
            "volume_id": "vol-1",
            "size_gb": 100,
            "state": "completed",
            "start_time": "2026-01-01T00:00:00+00:00",
            "age_days": age_days,
            "encrypted": False,
            "region": "eu-west-1",
            "monthly_cost": 4.60,
        }
        detector.save([snap])
        return cursor.execute.call_args[0][1][7]

    def test_confidence_at_threshold(self):
        assert self._saved_confidence(90) == 0.60

    def test_confidence_grows_with_age(self):
        assert self._saved_confidence(190) == 0.70

    def test_confidence_is_capped(self):
        assert self._saved_confidence(2000) == 0.90

    def test_waste_row_shape(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchone.return_value = (42,)
        snap = {
            "snapshot_id": "snap-1",
            "description": "backup",
            "volume_id": "vol-1",
            "size_gb": 100,
            "state": "completed",
            "start_time": "2026-01-01T00:00:00+00:00",
            "age_days": 120,
            "encrypted": True,
            "region": "eu-west-1",
            "monthly_cost": 4.60,
        }

        assert detector.save([snap]) == [42]
        params = cursor.execute.call_args[0][1]
        assert params[3] == "snap-1"  # resource_id
        assert params[4] == "ebs_snapshot"  # resource_type
        assert params[5] == "old_snapshot"  # waste_type
        assert params[6] == 4.60  # monthly_waste_eur

        metadata = json.loads(params[8])
        assert metadata["encrypted"] is True
        assert "pricing_source" in metadata  # stamp_pricing provenance

    def test_db_error_rolls_back_and_raises(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.execute.side_effect = Exception("connection lost")

        with pytest.raises(RuntimeError):
            self_snap = {
                "snapshot_id": "snap-1",
                "description": "",
                "volume_id": "",
                "size_gb": 1,
                "state": "completed",
                "start_time": "2026-01-01T00:00:00+00:00",
                "age_days": 100,
                "encrypted": False,
                "region": "eu-west-1",
                "monthly_cost": 0.05,
            }
            detector.save([self_snap])

        detector.conn.rollback.assert_called_once()


class TestRecommend:
    def test_creates_delete_recommendation(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = {
            "size_gb": 100,
            "age_days": 120,
            "region": "eu-west-1",
            "description": "old db backup",
        }
        cursor.fetchall.return_value = [(42, "snap-1", 4.60, meta)]

        count = detector.recommend([42])

        assert count == 1
        params = cursor.execute.call_args_list[-1][0][1]
        assert params[0] == 42
        assert params[1] == "delete_snapshot"
        assert "old db backup" in params[2]
        assert params[3] == 4.60
        assert params[4] == "pending"

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.recommend([]) == 0
        detector.conn.cursor.assert_not_called()


class TestMarkAmiBackedObsolete:
    def test_marks_pending_recommendations_obsolete(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.rowcount = 2

        assert detector.mark_ami_backed_obsolete({"snap-a", "snap-b"}) == 2
        detector.conn.commit.assert_called_once()

    def test_empty_set_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.mark_ami_backed_obsolete(set()) == 0
        detector.conn.cursor.assert_not_called()

    def test_db_error_is_swallowed_with_rollback(self):
        """Cleanup is best-effort: a failure here must not abort the
        detection run that follows."""
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.execute.side_effect = Exception("connection lost")

        assert detector.mark_ami_backed_obsolete({"snap-a"}) == 0
        detector.conn.rollback.assert_called_once()
