"""
Unit tests for the Windows-native collection pipeline (src/run_collection.py).
Detector scripts and the database are mocked — nothing touches AWS.
"""

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import run_collection
from run_collection import STEAMPIPE_STEPS, acquire_lock, pid_alive, release_lock


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0)


class TestPidAlive:
    def test_current_process_is_alive(self):
        assert pid_alive(os.getpid())

    def test_non_positive_pid_is_dead(self):
        assert not pid_alive(0)
        assert not pid_alive(-1)


class TestLock:
    def test_second_acquire_fails_while_held(self, tmp_path):
        lock = tmp_path / "lock.d"
        assert acquire_lock(lock)
        assert not acquire_lock(lock)
        release_lock(lock)
        assert not lock.exists()

    def test_stale_lock_is_reclaimed(self, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text("0")  # holder long gone
        assert acquire_lock(lock)
        assert (lock / "pid").read_text() == str(os.getpid())

    def test_garbage_pid_file_is_reclaimed(self, tmp_path):
        lock = tmp_path / "lock.d"
        lock.mkdir()
        (lock / "pid").write_text("not-a-pid")
        assert acquire_lock(lock)


class TestPipeline:
    def _run(self, steampipe_path, run_side_effect=_ok):
        out = io.StringIO()
        with (
            patch.object(run_collection.shutil, "which", return_value=steampipe_path),
            patch.object(run_collection.subprocess, "run", side_effect=run_side_effect) as run,
            patch.object(run_collection, "record_run") as record,
        ):
            assert run_collection.run_pipeline(out) == 0
        return run, record, out.getvalue()

    def test_without_steampipe_skips_its_steps(self):
        run, record, output = self._run(None)
        scripts = [Path(c.args[0][1]).stem for c in run.call_args_list]
        assert "elb_unused" not in scripts
        assert scripts[-1] == "aws_collector"
        skipped, failed = record.call_args.args
        assert skipped == [Path(s).stem for _, s in STEAMPIPE_STEPS]
        assert failed == []
        assert "steampipe CLI not found" in output

    def test_with_steampipe_runs_all_steps(self):
        run, record, _ = self._run("/usr/bin/steampipe")
        # 14 detector/collector steps + the daily cost collection
        assert run.call_count == 15
        assert record.call_args.args == ([], [])

    def test_failed_step_is_recorded_and_run_continues(self):
        def fail_ec2_idle(cmd, **kwargs):
            code = 1 if cmd[1].endswith("ec2_idle.py") else 0
            return subprocess.CompletedProcess(args=cmd, returncode=code)

        run, record, _ = self._run(None, fail_ec2_idle)
        _, failed = record.call_args.args
        assert failed == ["ec2_idle"]
        assert run.call_count == 7  # 6 boto3 steps + costs, none aborted

    def test_children_run_in_utf8_mode_from_repo_root(self):
        run, _, _ = self._run(None)
        kwargs = run.call_args_list[0].kwargs
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["cwd"] == run_collection.ROOT_DIR

    def test_db_failure_does_not_crash_the_run(self):
        out = io.StringIO()
        with (
            patch.object(run_collection.shutil, "which", return_value=None),
            patch.object(run_collection.subprocess, "run", side_effect=_ok),
            patch.object(run_collection, "record_run", side_effect=RuntimeError("db down")),
        ):
            assert run_collection.run_pipeline(out) == 0
        assert "could not record collection_runs status" in out.getvalue()
