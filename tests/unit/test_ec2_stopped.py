"""
Unit tests for the stopped-EC2 detector (src/detectors/ec2_stopped.py).

DB and AWS are mocked — no Postgres or credentials required. This
detector writes waste_detected rows the remediator later acts on, so
the focus is: EBS cost arithmetic, the region-scan fallback, and the
exact rows save()/recommend() emit (waste_type, confidence, metadata).
"""

import json
import pytest
import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from detectors.ec2_stopped import (
    EC2StoppedDetector,
    _ebs_cost,
    _fetch_ebs_cost_for_instance,
)


def _detector_with_mock_conn():
    """Bypass __init__ (env vars + real psycopg2.connect) entirely."""
    detector = EC2StoppedDetector.__new__(EC2StoppedDetector)
    detector.conn = MagicMock()
    return detector


class TestEbsCost:
    def test_known_volume_types(self):
        assert _ebs_cost(100, "gp2") == 10.00
        assert _ebs_cost(100, "gp3") == 8.00

    def test_unknown_type_falls_back_to_default(self):
        # Default is the gp2 price — the most common (and most expensive
        # of the general-purpose types), so unknown types aren't underbilled
        assert _ebs_cost(100, "weird-new-type") == 10.00


class TestFetchEbsCostForInstance:
    def _ec2_response(self, volumes=None, launch_time=None):
        instance = {
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": v["VolumeId"]}} for v in (volumes or [])]
        }
        if launch_time:
            instance["LaunchTime"] = launch_time
        return {"Reservations": [{"Instances": [instance]}]}

    def test_sums_cost_across_volumes(self):
        volumes = [
            {"VolumeId": "vol-1", "Size": 100, "VolumeType": "gp2"},
            {"VolumeId": "vol-2", "Size": 50, "VolumeType": "gp3"},
        ]
        ec2 = MagicMock()
        ec2.describe_instances.return_value = self._ec2_response(volumes)
        ec2.describe_volumes.return_value = {"Volumes": volumes}

        with patch("core.aws_clients.get_client", return_value=ec2):
            info = _fetch_ebs_cost_for_instance("i-abc", "eu-west-1")

        assert info["found"] is True
        assert info["ebs_cost"] == 14.00  # 10.00 + 4.00
        assert [v["volume_id"] for v in info["volumes"]] == ["vol-1", "vol-2"]

    def test_instance_not_in_region(self):
        ec2 = MagicMock()
        ec2.describe_instances.return_value = {"Reservations": []}

        with patch("core.aws_clients.get_client", return_value=ec2):
            info = _fetch_ebs_cost_for_instance("i-abc", "eu-west-1")

        assert info == {"found": False}

    def test_instance_without_volumes(self):
        ec2 = MagicMock()
        ec2.describe_instances.return_value = self._ec2_response(volumes=[])

        with patch("core.aws_clients.get_client", return_value=ec2):
            info = _fetch_ebs_cost_for_instance("i-abc", "eu-west-1")

        assert info["found"] is True
        assert info["ebs_cost"] == 0
        assert info["volumes"] == []

    def test_naive_launch_time_is_treated_as_utc(self):
        launch = datetime.now() - timedelta(days=45)  # tz-naive
        ec2 = MagicMock()
        ec2.describe_instances.return_value = self._ec2_response(launch_time=launch)

        with patch("core.aws_clients.get_client", return_value=ec2):
            info = _fetch_ebs_cost_for_instance("i-abc", "eu-west-1")

        assert info["age_days"] in (44, 45)

    def test_api_error_degrades_to_not_found(self):
        ec2 = MagicMock()
        ec2.describe_instances.side_effect = Exception("throttled")

        with patch("core.aws_clients.get_client", return_value=ec2):
            info = _fetch_ebs_cost_for_instance("i-abc", "eu-west-1")

        assert info == {"found": False}


class TestDetect:
    def test_handles_plain_tuple_rows(self):
        """Regression: cursor rows are tuples (no RealDictCursor is
        configured) — detect() used dict-style access and crashed with
        TypeError as soon as one stopped instance existed."""
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchall.return_value = [("i-stopped1", "t3.micro", 7)]

        ebs_info = {
            "found": True,
            "region": "eu-west-1",
            "ebs_cost": 9.20,
            "volumes": [{"volume_id": "vol-1", "size_gb": 100, "vol_type": "gp2", "cost": 9.20}],
            "age_days": 90,
        }
        with patch("detectors.ec2_stopped._fetch_ebs_cost_for_instance", return_value=ebs_info):
            results = detector.detect()

        assert len(results) == 1
        assert results[0]["instance_id"] == "i-stopped1"
        assert results[0]["instance_type"] == "t3.micro"
        assert results[0]["datapoints"] == 7
        assert results[0]["ebs_cost"] == 9.20

    def test_instance_not_found_in_any_region_is_skipped(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchall.return_value = [("i-gone", "t3.micro", 7)]

        with patch(
            "detectors.ec2_stopped._fetch_ebs_cost_for_instance",
            return_value={"found": False},
        ):
            results = detector.detect()

        assert results == []

    def test_zero_ebs_cost_is_not_reported(self):
        """An instance with no attached volumes wastes nothing — it must
        not produce a terminate recommendation."""
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchall.return_value = [("i-novol", "t3.micro", 7)]

        ebs_info = {
            "found": True,
            "region": "eu-west-1",
            "ebs_cost": 0,
            "volumes": [],
            "age_days": 90,
        }
        with patch("detectors.ec2_stopped._fetch_ebs_cost_for_instance", return_value=ebs_info):
            results = detector.detect()

        assert results == []

    def test_no_stopped_instances(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchall.return_value = []

        assert detector.detect() == []


class TestSave:
    def _instance(self):
        return {
            "instance_id": "i-stopped1",
            "instance_type": "t3.micro",
            "datapoints": 7,
            "region": "eu-west-1",
            "ebs_cost": 9.20,
            "volumes": [{"volume_id": "vol-1", "size_gb": 100, "vol_type": "gp2", "cost": 9.20}],
            "age_days": 90,
        }

    def test_waste_row_shape(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchone.return_value = (42,)

        waste_ids = detector.save([self._instance()])

        assert waste_ids == [42]
        params = cursor.execute.call_args[0][1]
        assert params[3] == "i-stopped1"  # resource_id
        assert params[4] == "ec2_instance"  # resource_type
        assert params[5] == "stopped_instance"  # waste_type
        assert params[6] == 9.20  # monthly_waste_eur
        assert params[7] == 0.95  # confidence — explicit state, not inferred

        metadata = json.loads(params[8])
        assert metadata["days_stopped"] == 7
        assert metadata["volumes"][0]["volume_id"] == "vol-1"
        assert "pricing_source" in metadata  # stamp_pricing provenance

    def test_db_error_rolls_back_and_raises(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.execute.side_effect = Exception("connection lost")

        with pytest.raises(RuntimeError):
            detector.save([self._instance()])

        detector.conn.rollback.assert_called_once()

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.save([]) == []
        detector.conn.cursor.assert_not_called()


class TestRecommend:
    def test_creates_terminate_recommendation(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = {
            "instance_type": "t3.micro",
            "region": "eu-west-1",
            "days_stopped": 7,
            "volumes": [{"volume_id": "vol-1"}],
        }
        cursor.fetchall.return_value = [(42, "i-stopped1", 9.20, meta)]

        count = detector.recommend([42])

        assert count == 1
        insert_call = cursor.execute.call_args_list[-1]
        params = insert_call[0][1]
        assert params[0] == 42  # waste_id
        assert params[1] == "terminate_instance"
        assert "snapshot the volumes first" in params[2]
        assert params[3] == 9.20  # estimated_monthly_savings_eur
        assert params[4] == "pending"

    def test_metadata_as_json_string_is_parsed(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = json.dumps({"instance_type": "t3.micro", "region": "eu-west-1"})
        cursor.fetchall.return_value = [(42, "i-stopped1", 9.20, meta)]

        assert detector.recommend([42]) == 1

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.recommend([]) == 0
        detector.conn.cursor.assert_not_called()
