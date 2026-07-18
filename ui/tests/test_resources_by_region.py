#!/usr/bin/env python3
"""Resources-by-Region mini-map: the endpoint must degrade cleanly when
AWS is unreachable (no boto3 calls, no cache poisoning) and the region
projection must map known regions inside the SVG canvas."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient

    TESTCLIENT_AVAILABLE = True
except ImportError:
    TESTCLIENT_AVAILABLE = False


class TestRegionCountryMapping(unittest.TestCase):
    def test_every_region_maps_to_a_country_present_in_the_svg(self):
        """The card colors the hosting country path: every ISO id we emit
        must exist in the shipped map asset (Bahrain is the known absentee
        of the simplified map — the region stays list-only)."""
        from routes.dashboard import _REGION_GEO

        svg = (Path(__file__).resolve().parents[1] / "static" / "world-map.svg").read_text()
        missing_ok = {"bh"}
        for code, (name, iso) in _REGION_GEO.items():
            self.assertTrue(iso.isalpha() and iso.islower(), f"{code}: bad iso '{iso}'")
            if iso in missing_ok:
                continue
            self.assertIn(f'id="{iso}"', svg, f"{code} ({name}): country '{iso}' not in the SVG")

    def test_template_paints_multi_part_country_groups(self):
        """us/se/au are <g> wrappers in the SVG: inline fill on the group
        loses to the asset's path{} style rule, so the template must also
        paint each descendant path (regression: US/Sweden/Australia stayed
        grey while single-path countries colored fine)."""
        svg = (Path(__file__).resolve().parents[1] / "static" / "world-map.svg").read_text()
        self.assertIn('<g id="us"', svg)
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
        ).read_text()
        self.assertIn("path.querySelectorAll('path')", template)

    def test_template_strips_native_svg_titles(self):
        """The asset's <title> renders as a native browser tooltip that
        shadows the per-country tooltip cards: the inlining JS must drop it."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
        ).read_text()
        self.assertIn("querySelectorAll('title')", template)


class TestSingleRegionSource(unittest.TestCase):
    def test_ui_and_pipeline_share_one_region_list(self):
        """CLOUD_REGIONS must BE constants.AWS_SCAN_REGIONS, not a copy:
        the two lists were once maintained by hand in parallel, and the
        map/detector coverage silently diverged."""
        import constants
        import state
        from utils import aws_sync

        self.assertIs(state.CLOUD_REGIONS, constants.AWS_SCAN_REGIONS)
        self.assertIs(aws_sync.SYNC_REGIONS, constants.AWS_SCAN_REGIONS)


@unittest.skipUnless(TESTCLIENT_AVAILABLE, "fastapi.testclient not installed")
class TestResourcesByRegionEndpoint(unittest.TestCase):
    def test_unreachable_aws_reports_unavailable(self):
        from main import app
        from routes import dashboard as dash

        dash._region_inventory_cache["data"] = None
        with patch("state.check_aws_reachable", return_value=False):
            client = TestClient(app)
            resp = client.get("/api/dashboard/resources-by-region")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"available": False, "regions": [], "total": 0})
        # An unavailable sweep must not be cached as if it were data
        self.assertIsNone(dash._region_inventory_cache["data"])


if __name__ == "__main__":
    unittest.main()
