#!/usr/bin/env python3
"""
Bridge test: runs the node --test suite covering the pure forecast math of
the dashboard trend chart (ui/static/trend_forecast.js), so the JS
invariants are checked by the regular Python test run. Skips when node is
not installed. Also pins the template-to-module wiring: the dashboard page
must load the module and call it instead of re-inlining the regression.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[1]


class TestTrendForecastJS(unittest.TestCase):

    def test_node_suite_passes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        # Enumerate the files rather than passing the directory: node --test
        # only globs directories from its own cwd, an absolute directory path
        # gets treated as a single (nonexistent) test file.
        test_files = sorted(str(p) for p in (UI_DIR / "tests" / "js").glob("*.test.js"))
        self.assertTrue(test_files, "no JS test files found")
        result = subprocess.run(
            [node, "--test", *test_files],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode, 0, f"node --test failed:\n{result.stdout}\n{result.stderr}"
        )

    def test_dashboard_template_uses_the_module(self):
        template = (UI_DIR / "templates" / "dashboard.html").read_text()
        self.assertIn("/static/trend_forecast.js", template)
        self.assertIn("trendForecast.computeForecast", template)
        # The regression must not creep back inline: the half-life weighting
        # only exists in the module
        self.assertNotIn("halfLife", template)

    def test_module_is_served(self):
        try:
            from fastapi.testclient import TestClient

            import sys

            sys.path.insert(0, str(UI_DIR))
            from main import app
        except ImportError as e:
            self.skipTest(f"fastapi test client unavailable ({e})")
        resp = TestClient(app).get("/static/trend_forecast.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("computeForecast", resp.text)


if __name__ == "__main__":
    unittest.main()
