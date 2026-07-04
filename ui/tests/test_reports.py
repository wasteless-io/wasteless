#!/usr/bin/env python3
"""
Unit tests for utils/reports.py — date-range resolution and backend bridge
for the Reports page.
"""

import unittest
import sys
from datetime import date, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.reports import resolve_period, report_filename, MAX_PERIOD_DAYS


class TestResolvePeriod(unittest.TestCase):

    def test_default_is_last_7_days(self):
        start, end = resolve_period()
        self.assertEqual(end, date.today())
        self.assertEqual((end - start).days + 1, 7)

    def test_days_preset(self):
        start, end = resolve_period(days=30)
        self.assertEqual(end, date.today())
        self.assertEqual((end - start).days + 1, 30)

    def test_month(self):
        start, end = resolve_period(month='2026-06')
        self.assertEqual(start, date(2026, 6, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_month_leap_february(self):
        start, end = resolve_period(month='2024-02')
        self.assertEqual(end, date(2024, 2, 29))

    def test_month_takes_precedence_over_range(self):
        start, end = resolve_period(month='2026-06',
                                    start='2026-01-01', end='2026-01-31')
        self.assertEqual(start, date(2026, 6, 1))

    def test_explicit_range(self):
        start, end = resolve_period(start='2026-06-10', end='2026-06-20')
        self.assertEqual(start, date(2026, 6, 10))
        self.assertEqual(end, date(2026, 6, 20))

    def test_single_day_range(self):
        start, end = resolve_period(start='2026-06-10', end='2026-06-10')
        self.assertEqual(start, end)

    def test_start_without_end_rejected(self):
        with self.assertRaises(ValueError):
            resolve_period(start='2026-06-10')

    def test_reversed_range_rejected(self):
        with self.assertRaises(ValueError):
            resolve_period(start='2026-06-20', end='2026-06-10')

    def test_malformed_date_rejected(self):
        with self.assertRaises(ValueError):
            resolve_period(start='junk', end='2026-06-10')

    def test_malformed_month_rejected(self):
        with self.assertRaises(ValueError):
            resolve_period(month='2026-13')
        with self.assertRaises(ValueError):
            resolve_period(month='junk')

    def test_oversized_range_rejected(self):
        end = date.today()
        start = end - timedelta(days=MAX_PERIOD_DAYS)
        with self.assertRaises(ValueError):
            resolve_period(start=start.isoformat(), end=end.isoformat())

    def test_zero_days_rejected(self):
        with self.assertRaises(ValueError):
            resolve_period(days=0)


class TestReportFilename(unittest.TestCase):

    def test_filename_contains_period(self):
        name = report_filename(date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(name, 'wasteless-report_2026-06-01_2026-06-30.md')


class TestBackendBridge(unittest.TestCase):

    def test_backend_functions_importable(self):
        from utils.reports import (collect_digest_data, format_digest,
                                   generate_narrative, llm_narrative_available)
        self.assertTrue(callable(collect_digest_data))
        self.assertTrue(callable(format_digest))
        self.assertTrue(callable(generate_narrative))
        self.assertTrue(callable(llm_narrative_available))


if __name__ == '__main__':
    unittest.main()
