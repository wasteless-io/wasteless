#!/usr/bin/env python3
"""The sync job's fallback-pricing signal: pending recommendations whose
cost came from a static-table default must surface as a WARNING on /logs,
once per count change (a 5-minute tick repeating itself would drown the
buffer)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobs


def _cursor(n):
    cur = MagicMock()
    cur.fetchone.return_value = {"n": n}
    return cur


class TestFallbackPricingSignal(unittest.TestCase):
    def setUp(self):
        jobs._fallback_priced_last = None

    def test_warns_on_new_count_then_stays_silent(self):
        with self.assertLogs("wasteless_ui.jobs", level="WARNING") as logs:
            jobs._warn_default_priced(_cursor(3))
        self.assertIn("3 pending recommendation(s)", logs.output[0])
        with self.assertNoLogs("wasteless_ui.jobs", level="INFO"):
            jobs._warn_default_priced(_cursor(3))

    def test_zero_at_boot_stays_silent(self):
        with self.assertNoLogs("wasteless_ui.jobs", level="INFO"):
            jobs._warn_default_priced(_cursor(0))

    def test_recovery_to_zero_logs_info(self):
        with self.assertLogs("wasteless_ui.jobs", level="WARNING"):
            jobs._warn_default_priced(_cursor(2))
        with self.assertLogs("wasteless_ui.jobs", level="INFO") as logs:
            jobs._warn_default_priced(_cursor(0))
        self.assertIn("No pending recommendations left", logs.output[0])


if __name__ == "__main__":
    unittest.main()
