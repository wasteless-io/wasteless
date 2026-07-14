"""Unit tests for _fmt_rec_line (routes/recommendations.py).

This is the function that renders each pending recommendation into the one
line the estate-chat LLM sees. The coherence guarantee it enforces:
- every column the UI shows is passed through (so the model can answer from
  real data instead of inventing figures);
- type-specific fields that are NULL for a row are DROPPED, so the model is
  never shown a placeholder it could hallucinate a value around;
- numbers are formatted verbatim (confidence 0.70 -> 70%, cpu -> one decimal).
"""

import sys
import unittest
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from routes.recommendations import _fmt_rec_line

# Every key _fmt_rec_line reads, defaulted to None; a fixture overrides only
# the fields that exist for its resource type (mirrors the NULLs the SQL
# returns for columns belonging to other types).
_BASE = {
    "action_required": None,
    "resource_type": None,
    "resource_id": None,
    "savings": 0,
    "confidence": 0,
    "instance_type": None,
    "instance_state": None,
    "cpu_avg": None,
    "datapoints": None,
    "observation_days": None,
    "monthly_cost": None,
    "volume_size_gb": None,
    "volume_type": None,
    "region": None,
    "public_ip": None,
    "age_days": None,
    "resource_name": None,
    "snap_description": None,
}


def _row(**overrides):
    return {**_BASE, **overrides}


class TestFmtRecLine(unittest.TestCase):

    def test_ec2_row_includes_confidence_drivers(self):
        # The exact case that made the LLM hallucinate: it must now receive
        # cpu, datapoints and observation_days so it can explain the 70% cap.
        line = _fmt_rec_line(
            _row(
                action_required="Stop",
                resource_type="ec2_instance",
                resource_id="i-08f6957acf8070f1d",
                savings=3.55,
                confidence=0.70,
                instance_type="t3.nano",
                instance_state="running",
                cpu_avg=0.2,
                datapoints=2,
                observation_days=7,
                monthly_cost=3.56,
                region="eu-west-1",
            )
        )
        self.assertIn("Stop | ec2_instance | i-08f6957acf8070f1d", line)
        self.assertIn("savings=3.55 EUR/mo", line)
        self.assertIn("confidence=70%", line)  # 0.70 -> 70%, verbatim
        self.assertIn("avg_cpu_7d=0.2%", line)  # one decimal
        self.assertIn("datapoints=2", line)
        self.assertIn("observation_days=7", line)
        self.assertIn("type=t3.nano", line)
        self.assertIn("state=running", line)
        self.assertIn("monthly_cost=3.56 EUR/mo", line)
        self.assertIn("region=eu-west-1", line)

    def test_null_fields_are_dropped(self):
        # An Elastic IP has no CPU/datapoints/instance_type: none of those
        # keys may appear, or the model could invent a value for them.
        line = _fmt_rec_line(
            _row(
                action_required="Release",
                resource_type="elastic_ip",
                resource_id="eipalloc-123",
                savings=3.60,
                confidence=0.95,
                public_ip="52.1.2.3",
                region="eu-west-1",
                age_days=45,
            )
        )
        self.assertIn("public_ip=52.1.2.3", line)
        self.assertIn("age_days=45", line)
        for absent in ("avg_cpu_7d", "datapoints", "observation_days", "type=", "state="):
            self.assertNotIn(absent, line)

    def test_zero_values_are_kept_not_treated_as_absent(self):
        # datapoints=0 / age_days=0 are real facts, not missing data — the
        # `is not None` guards must keep them (a truthiness check would drop
        # them and hand the model a silent gap).
        line = _fmt_rec_line(
            _row(
                action_required="Stop",
                resource_type="ec2_instance",
                resource_id="i-zero",
                savings=0.0,
                confidence=0.0,
                cpu_avg=0.0,
                datapoints=0,
                age_days=0,
            )
        )
        self.assertIn("datapoints=0", line)
        self.assertIn("age_days=0", line)
        self.assertIn("avg_cpu_7d=0.0%", line)
        self.assertIn("savings=0.00 EUR/mo", line)


if __name__ == "__main__":
    unittest.main()
