"""
Unit tests for the idle-EC2 detector (src/detectors/ec2_idle.py).

DB is mocked — no Postgres required. The two pieces that matter most:
the confidence formula (its datapoint caps are what keep thin
detections below the 0.80 auto-remediation safeguard) and the
confidence→recommendation routing (stop vs downsize, never terminate).
Parameter validation is already covered in test_validation.py.
"""

import json
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from detectors.ec2_idle import (
    DEFAULT_INSTANCE_COST_USD,
    EC2IdleDetector,
)


def _detector_with_mock_conn():
    """Bypass __init__ (env vars + real psycopg2.connect) entirely."""
    detector = EC2IdleDetector.__new__(EC2IdleDetector)
    detector.conn = MagicMock()
    return detector


def _metric_row(
    instance_id="i-idle1",
    cpu_avg=1.0,
    datapoints=7,
    instance_type="t3.medium",
    region="eu-west-1",
):
    # SELECT order: id, type, state, cpu_avg, cpu_max, cpu_min, datapoints, region
    return (instance_id, instance_type, "running", cpu_avg, cpu_avg + 2.0, 0.1, datapoints, region)


def _detect(rows, cpu_threshold=5.0, days=7):
    detector = _detector_with_mock_conn()
    cursor = detector.conn.cursor.return_value
    cursor.fetchall.return_value = rows
    return detector.detect_idle_instances(cpu_threshold=cpu_threshold, days=days)


class TestInstanceMonthlyCost:
    def test_known_type(self):
        detector = _detector_with_mock_conn()
        assert detector.get_instance_monthly_cost("t3.medium") == 30.91

    def test_unknown_type_falls_back_to_default(self):
        detector = _detector_with_mock_conn()
        assert detector.get_instance_monthly_cost("z9.mega") == DEFAULT_INSTANCE_COST_USD

    def test_empty_type_falls_back_to_default(self):
        detector = _detector_with_mock_conn()
        assert detector.get_instance_monthly_cost("") == DEFAULT_INSTANCE_COST_USD

    def test_current_gen_burstable_families_are_priced(self):
        """t4g/t3a were missing from the table: every t4g.micro was costed
        at the 54.35 default, a 9x overestimate that inflated savings."""
        detector = _detector_with_mock_conn()
        assert detector.get_instance_monthly_cost("t4g.micro") == 6.72
        assert detector.get_instance_monthly_cost("t3a.micro") == 7.45

    def test_unknown_type_stamps_pricing_fallback_in_metadata(self):
        """A defaulted cost is a guess, not a measurement: the metadata must
        say so, so the UI can flag it instead of passing it off as priced."""
        waste = _detect([_metric_row(instance_type="z9.mega")])
        meta = waste[0]["metadata"]
        assert meta["pricing_fallback"] is True
        assert meta["pricing_source"] == "static_default_unknown_type"

    def test_known_type_keeps_the_priced_source_stamp(self):
        waste = _detect([_metric_row(instance_type="t4g.micro")])
        meta = waste[0]["metadata"]
        assert "pricing_fallback" not in meta
        assert meta["pricing_source"] == "aws_on_demand_static"


class TestConfidenceScore:
    def test_zero_cpu_full_window_gives_max_confidence(self):
        waste = _detect([_metric_row(cpu_avg=0.0, datapoints=7)])
        assert waste[0]["confidence_score"] == 1.0

    def test_confidence_decreases_as_cpu_approaches_threshold(self):
        waste = _detect([_metric_row(cpu_avg=4.0, datapoints=7)])
        assert waste[0]["confidence_score"] == 0.20  # 1 - 4/5

    def test_thin_data_is_capped_below_the_auto_remediation_bar(self):
        """A zero-CPU average from 1-2 datapoints proves little. The 0.70
        cap must keep it below the 0.80 min_confidence_score safeguard —
        if this cap disappears, single-datapoint instances become
        eligible for automatic stop."""
        waste = _detect([_metric_row(cpu_avg=0.0, datapoints=2)])
        assert waste[0]["confidence_score"] == 0.70

    def test_partial_window_is_capped_at_085(self):
        waste = _detect([_metric_row(cpu_avg=0.0, datapoints=5)], days=7)
        assert waste[0]["confidence_score"] == 0.85

    def test_caps_do_not_raise_low_confidence(self):
        waste = _detect([_metric_row(cpu_avg=4.0, datapoints=2)])
        assert waste[0]["confidence_score"] == 0.20  # already below cap


class TestWasteEstimate:
    def test_waste_is_cost_times_idle_ratio(self):
        waste = _detect([_metric_row(cpu_avg=2.0, instance_type="t3.medium")])
        # 28.44 * (1 - 2/100) = 27.87
        assert waste[0]["monthly_waste_eur"] == 30.29

    def test_metadata_carries_detection_context(self):
        waste = _detect([_metric_row(cpu_avg=1.0, datapoints=7)], cpu_threshold=5.0, days=7)
        meta = waste[0]["metadata"]
        assert meta["threshold_used"] == 5.0
        assert meta["observation_days"] == 7
        assert meta["datapoints"] == 7
        assert meta["detection_method"] == "cloudwatch_cpu_avg"
        assert "pricing_source" in meta  # stamp_pricing provenance

    def test_metadata_region_comes_from_metrics_row(self):
        # The remediator reads metadata->>'region' before falling back to
        # AWS_REGION: the detection must carry the region the metrics
        # were collected in, not the env var of the moment.
        waste = _detect([_metric_row(region="us-east-1")])
        assert waste[0]["metadata"]["region"] == "us-east-1"

    def test_metadata_region_falls_back_to_env_for_legacy_rows(self, monkeypatch):
        # Rows collected before multi-region support have region NULL —
        # they all came from AWS_REGION.
        monkeypatch.setenv("AWS_REGION", "eu-west-3")
        waste = _detect([_metric_row(region=None)])
        assert waste[0]["metadata"]["region"] == "eu-west-3"

    def test_no_idle_instances(self):
        assert _detect([]) == []


class TestGenerateRecommendations:
    def _recommend(self, confidence, cpu_avg=1.0):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        meta = {"cpu_avg_7d": cpu_avg}
        cursor.fetchall.return_value = [(42, "i-idle1", confidence, 27.87, meta)]
        count = detector.generate_recommendations([42])
        assert count == 1
        return cursor.execute.call_args_list[-1][0][1]

    def test_high_confidence_recommends_stop(self):
        params = self._recommend(confidence=0.95)
        assert params[1] == "stop_instance"
        assert "terminate manually" in params[2]  # terminate stays a manual follow-up

    def test_medium_confidence_recommends_stop_off_hours(self):
        params = self._recommend(confidence=0.70)
        assert params[1] == "stop_instance"
        assert "off-hours" in params[2]

    def test_low_confidence_recommends_downsize_not_stop(self):
        """Below 0.60 the signal is too weak to stop anything — the
        recommendation must degrade to a resize, never an outage."""
        params = self._recommend(confidence=0.40)
        assert params[1] == "downsize_instance"

    def test_status_and_savings_are_propagated(self):
        params = self._recommend(confidence=0.95)
        assert params[3] == 27.87
        assert params[4] == "pending"

    def test_missing_waste_record_is_skipped(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchall.return_value = []

        assert detector.generate_recommendations([42]) == 0

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.generate_recommendations([]) == 0
        detector.conn.cursor.assert_not_called()


class TestSaveWasteDetected:
    def test_waste_row_shape(self):
        detector = _detector_with_mock_conn()
        cursor = detector.conn.cursor.return_value
        cursor.fetchone.return_value = (42,)

        waste = {
            "resource_id": "i-idle1",
            "resource_type": "ec2_instance",
            "waste_type": "idle_compute",
            "monthly_waste_eur": 27.87,
            "confidence_score": 0.80,
            "metadata": {"cpu_avg_7d": 1.0},
        }
        assert detector.save_waste_detected([waste]) == [42]

        params = cursor.execute.call_args[0][1]
        assert params[3] == "i-idle1"
        assert params[5] == "idle_compute"
        assert params[7] == 0.80
        assert json.loads(params[8]) == {"cpu_avg_7d": 1.0}

    def test_empty_input_is_a_noop(self):
        detector = _detector_with_mock_conn()
        assert detector.save_waste_detected([]) == []
        detector.conn.cursor.assert_not_called()
