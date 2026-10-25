#!/usr/bin/env python3
"""
Tests for POST /api/collect-now (routes/sync.py): the Collect now button
of the empty Recommendations page. The endpoint is a thin fire-and-forget
wrapper around utils.collect.start_background_collection; wasteless.sh is
never actually executed here.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR))

from fastapi import HTTPException

from routes.sync import api_collect_now

import utils.collect as collect_module


class TestCollectNowEndpoint(unittest.TestCase):

    def test_started(self):
        with patch.object(collect_module, "start_background_collection", return_value=True) as sbc:
            result = api_collect_now()
        self.assertEqual(result, {"started": True})
        sbc.assert_called_once()

    def test_script_missing_is_500_with_hint(self):
        with patch.object(collect_module, "start_background_collection", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                api_collect_now()
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("wasteless.sh", ctx.exception.detail)


class TestStartBackgroundCollection(unittest.TestCase):

    def setUp(self):
        patcher = patch.object(collect_module, "IS_WINDOWS", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_launches_wasteless_collect(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "wasteless.sh"
            script.write_text("#!/bin/bash\n")
            with patch.object(collect_module.subprocess, "Popen") as popen:
                self.assertTrue(collect_module.start_background_collection(Path(tmp)))
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], [str(script), "collect"])

    def test_without_script_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(collect_module.start_background_collection(Path(tmp)))

    def test_popen_failure_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "wasteless.sh").write_text("#!/bin/bash\n")
            with patch.object(collect_module.subprocess, "Popen", side_effect=OSError("nope")):
                self.assertFalse(collect_module.start_background_collection(Path(tmp)))


class TestStartBackgroundCollectionWindows(unittest.TestCase):
    """Native Windows: no bash, the run goes through src/run_collection.py
    with the root venv's windowless interpreter."""

    def setUp(self):
        patcher = patch.object(collect_module, "IS_WINDOWS", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _make_install(root: Path, interpreter: str = "pythonw.exe") -> Path:
        (root / "src").mkdir()
        (root / "src" / "run_collection.py").write_text("")
        scripts = root / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        python = scripts / interpreter
        python.write_text("")
        return python

    def test_launches_run_collection_with_pythonw(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = self._make_install(Path(tmp))
            with patch.object(collect_module.subprocess, "Popen") as popen:
                self.assertTrue(collect_module.start_background_collection(Path(tmp)))
            cmd = popen.call_args.args[0]
            runner = Path(tmp) / "src" / "run_collection.py"
            self.assertEqual(cmd[:2], [str(python), str(runner)])
            self.assertEqual(cmd[2], "--log")
            self.assertIn("creationflags", popen.call_args.kwargs)
            self.assertNotIn("start_new_session", popen.call_args.kwargs)

    def test_falls_back_to_python_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = self._make_install(Path(tmp), "python.exe")
            with patch.object(collect_module.subprocess, "Popen") as popen:
                self.assertTrue(collect_module.start_background_collection(Path(tmp)))
            self.assertEqual(popen.call_args.args[0][0], str(python))

    def test_without_venv_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src").mkdir()
            (Path(tmp) / "src" / "run_collection.py").write_text("")
            self.assertFalse(collect_module.start_background_collection(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
