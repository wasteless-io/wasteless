"""
Unit tests for the orphaned-EBS detector (src/detectors/ebs_orphan.py).

DB and AWS are mocked — no Postgres or credentials required. Focus:
parsing describe_volumes into priced records, and the exact
waste_detected/recommendations rows emitted.
"""

import json
import pytest
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from detectors.ebs_orphan import (
    EBSOrphanDetector,
    _fetch_orphaned_volumes,
    _volume_monthly_cost,
)


def _detector_with_mock_conn():
    """Bypass __init__ (env vars + real psycopg2.connect) entirely."""
    detector = EBSOrphanDetector.__new__(EBSOrphanDetector)
    detector.conn = MagicMock()
    return detector


class TestVolumeMonthlyCost:
    def test_known_volume_types(self):
        assert _volume_monthly_cost(100, "gp3") == 7.36
        assert _volume_monthly_cost(100, "io1") == 11.50

    def test_unknown_type_falls_back_to_gp2_price(self):
        assert _volume_monthly_cost(100, "weird-new-type") == 9.20


class TestFetchOrphanedVolumes:
    def _run(self, volumes):
        ec2 = MagicMock()
        ec2.describe_volumes.return_value = {"Volumes": volumes}
        with patch("core.aws_clients.get_client", return_value=ec2):
            return _fetch_orphaned_volumes("eu-west-1")

    def test_parses_volume_with_name_tag_and_age(self):
        create_time = datetime.now(timezone.utc) - timedelta(days=30, hours=1)
        result = self._run(
            [
                {
                    "VolumeId": "vol-orphan1",
                    "Size": 100,
                    "VolumeType": "gp3",
                    "AvailabilityZone": "eu-west-1a",
                    "Encrypted": True,
                    "CreateTime": create_time,
                    "Tags": [
                        {"Key": "Name", "Value": "old-data"},
                        {"Key": "Team", "Value": "infra"},
                    ],
                }
            ]
        )

        assert len(result) == 1
        vol = result[0]
        assert vol["volume_id"] == "vol-orphan1"
        assert vol["name"] == "old-data"
        assert vol["monthly_cost"] == 7.36
        assert vol["age_days"] == 30
        assert vol["encrypted"] is True

    def test_volume_without_tags_or_create_time(self):
        result = self._run([{"VolumeId": "vol-bare", "Size": 10, "VolumeType": "gp2"}])

        assert result[0]["name"] == ""
        assert result[0]["age_days"] is None
        assert result[0]["monthly_cost"] == 0.92

    def test_naive_create_time_is_treated_as_utc(self):
        # Build the naive datetime from UTC so the expected age doesn't
        # depend on the machine's local timezone
        create_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10, hours=1)
        result = self._run(
            [
                {
                    "VolumeId": "vol-naive",
                    "Size": 10,
                    "VolumeType": "gp2",
                    "CreateTime": create_time,
                }
            ]
        )
        assert result[0]["age_days"] == 10

    def test_api_error_degrades_to_empty(self):
        with patch("core.aws_clients.get_client", side_effect=Exception("no creds")):
            assert _fetch_orphaned_volumes("eu-west-1") == []


class TestSave:
    def _volume(self):
        return {
            "volume_id": "vol-orphan1",
            "name": "old-data",
            "size_gb": 100,
            "vol_type": "gp3",
            "az": "eu-west-1a",
            "region": "eu-west-1",
            "encrypted": False,
            "monthly_cost": 7.36,
            "age_days": 30,
        }

    def test_waste_row_shape(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchone.return_value = (42,)

        assert detector.save([self._volume()]) == [42]

        params = cursor.execute.call_args[0][1]
        assert params[3] == "vol-orphan1"  # resource_id
        assert params[4] == "ebs_volume"  # resource_type
        assert params[5] == "orphaned_volume"  # waste_type
        assert params[6] == 7.36  # monthly_waste_eur
        assert params[7] == 0.95  # confidence — state=available is unambiguous

        metadata = json.loads(params[8])
        assert metadata["vol_type"] == "gp3"
        assert "pricing_source" in metadata  # stamp_pricing provenance

    def test_db_error_rolls_back_and_raises(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.execute.side_effect = Exception("connection lost")

        with pytest.raises(RuntimeError):
            detector.save([self._volume()])

        detector.conn.rollback.assert_called_once()

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.save([]) == []
        detector.conn.cursor.assert_not_called()


class TestRecommend:
    def test_creates_delete_recommendation(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = {"size_gb": 100, "vol_type": "gp3", "region": "eu-west-1", "name": "old-data"}
        cursor.fetchall.return_value = [(42, "vol-orphan1", 7.36, meta)]

        count = detector.recommend([42])

        assert count == 1
        params = cursor.execute.call_args_list[-1][0][1]
        assert params[0] == 42
        assert params[1] == "delete_volume"
        assert "old-data" in params[2]
        assert params[3] == 7.36
        assert params[4] == "pending"

    def test_metadata_as_json_string_is_parsed(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = json.dumps({"size_gb": 100, "vol_type": "gp3", "region": "eu-west-1"})
        cursor.fetchall.return_value = [(42, "vol-orphan1", 7.36, meta)]

        assert detector.recommend([42]) == 1

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.recommend([]) == 0
        detector.conn.cursor.assert_not_called()
