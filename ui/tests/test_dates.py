#!/usr/bin/env python3
"""
Tests for utils/dates.py: `%-d`-style directives must format identically on
every OS (Windows' strftime rejects them; /dashboard used to crash there).
"""

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.dates import strftime


class TestPortableStrftime(unittest.TestCase):

    def test_unpadded_day(self):
        self.assertEqual(strftime(date(2026, 3, 5), "%b %-d"), "Mar 5")
        self.assertEqual(strftime(date(2026, 3, 25), "%-d %b %Y"), "25 Mar 2026")

    def test_unpadded_time_parts_keep_padded_ones(self):
        dt = datetime(2026, 1, 2, 7, 4, 9)
        self.assertEqual(strftime(dt, "%b %-d, %H:%M"), "Jan 2, 07:04")
        self.assertEqual(strftime(dt, "%-H:%-M:%-S"), "7:4:9")
        self.assertEqual(strftime(dt, "%-m/%-d"), "1/2")

    def test_twelve_hour_clock(self):
        self.assertEqual(strftime(datetime(2026, 1, 1, 0, 0), "%-I"), "12")
        self.assertEqual(strftime(datetime(2026, 1, 1, 15, 0), "%-I"), "3")

    def test_day_of_year(self):
        self.assertEqual(strftime(date(2026, 2, 1), "%-j"), "32")

    def test_plain_formats_unchanged(self):
        d = date(2026, 3, 5)
        self.assertEqual(strftime(d, "%d %b %Y"), d.strftime("%d %b %Y"))

    def test_jinja_filter_registered(self):
        from state import templates

        tpl = templates.env.from_string("{{ d | strftime('%b %-d') }}")
        self.assertEqual(tpl.render(d=date(2026, 3, 5)), "Mar 5")


if __name__ == "__main__":
    unittest.main()
