#!/usr/bin/env python3
"""The Overview (/) and /dashboard share the Financial Overview tiles via
the _financial_tiles.html partial, but each route computes the figures with
its own (mirrored) queries. This test renders both pages and compares the
tile values so the two can never silently drift apart."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient

    TESTCLIENT_AVAILABLE = True
except ImportError:
    TESTCLIENT_AVAILABLE = False


def _tiles(html: str):
    """(label, value) pairs of the fin-tiles block, in display order."""
    block = re.search(r'class="grid grid-5 fin-tiles"(.*?)\n</div>', html, re.S)
    if not block:
        return None
    values = re.findall(r'<div class="kpi-value[^"]*">(.*?)</div>', block.group(1), re.S)
    labels = re.findall(r'<div class="kpi-label">(.*?)</div>', block.group(1), re.S)
    # Strip markup: only the rendered text matters for parity
    values = [re.sub(r"<[^>]+>", "", v).strip() for v in values]
    return list(zip(labels, values))


@unittest.skipUnless(TESTCLIENT_AVAILABLE, "fastapi.testclient not installed")
class TestFinancialTilesParity(unittest.TestCase):
    def test_overview_and_dashboard_show_identical_tiles(self):
        from main import app

        client = TestClient(app)
        home = client.get("/", follow_redirects=False)
        if home.status_code != 200:
            self.skipTest("home redirects (AWS not configured in this environment)")
        dash = client.get("/dashboard")
        self.assertEqual(dash.status_code, 200)

        home_tiles = _tiles(home.text)
        dash_tiles = _tiles(dash.text)
        self.assertIsNotNone(home_tiles, "fin-tiles block missing on /")
        self.assertIsNotNone(dash_tiles, "fin-tiles block missing on /dashboard")
        self.assertEqual(len(home_tiles), 5)
        self.assertEqual(
            home_tiles,
            dash_tiles,
            "the two pages render different Financial Overview figures: "
            "their mirrored queries have drifted (see routes/home.py)",
        )


if __name__ == "__main__":
    unittest.main()
