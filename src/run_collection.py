#!/usr/bin/python3
"""
Collection & detection pipeline — Windows-native counterpart of
`wasteless.sh collect`.

Native Windows has no bash, so wasteless.ps1 (`wasteless collect`), the
Task Scheduler entry (`wasteless schedule`) and the UI's Collect now button
run this module with the root venv's interpreter. The scheduled task uses
pythonw.exe (no console window flashing every 5 minutes), which is why all
output can be appended to a log file with --log instead of stdout.

Keep the step list in sync with _collect in wasteless.sh: same steps, same
lock directory, same collection_runs row, so the UI can't tell which
launcher ran.

Usage:
    venv\\Scripts\\python.exe src\\run_collection.py [--log FILE] [--every SECONDS]
"""

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent  # repo root

# Same lock as wasteless.sh: a directory, because mkdir is atomic.
COLLECT_LOCK_DIR = Path.home() / ".wasteless-collect.lock.d"

BOTO3_STEPS = [
    ("Collecting CloudWatch metrics...", "src/collectors/aws_cloudwatch.py"),
    ("Detecting idle EC2 instances...", "src/detectors/ec2_idle.py"),
    ("Detecting stopped EC2 instances...", "src/detectors/ec2_stopped.py"),
    ("Detecting orphaned EBS volumes...", "src/detectors/ebs_orphan.py"),
    ("Detecting unassociated Elastic IPs...", "src/detectors/eip_orphan.py"),
    ("Detecting old EBS snapshots...", "src/detectors/snapshot_orphan.py"),
]

STEAMPIPE_STEPS = [
    ("Detecting unused load balancers (Steampipe)...", "src/detectors/elb_unused.py"),
    ("Detecting unused NAT gateways (Steampipe)...", "src/detectors/nat_gateway_unused.py"),
    ("Detecting unused VPCs (Steampipe)...", "src/detectors/vpc_unused.py"),
    (
        "Detecting gp2->gp3 migration candidates (Steampipe)...",
        "src/detectors/ebs_gp2_migration.py",
    ),
    ("Detecting orphaned AMIs (Steampipe)...", "src/detectors/ami_orphan.py"),
    ("Detecting stopped RDS instances (Steampipe)...", "src/detectors/rds_stopped.py"),
    ("Detecting idle RDS instances (Steampipe)...", "src/detectors/rds_idle.py"),
    ("Detecting old RDS snapshots (Steampipe)...", "src/detectors/rds_snapshot_orphan.py"),
]


def pid_alive(pid: int) -> bool:
    """Is `pid` a running process? Never use os.kill(pid, 0) for this on
    Windows: there, os.kill calls TerminateProcess for any signal value."""
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(lock_dir: Path = COLLECT_LOCK_DIR) -> bool:
    """Take the collect lock; False when another live run holds it."""
    try:
        lock_dir.mkdir()
    except FileExistsError:
        try:
            holder = int((lock_dir / "pid").read_text().strip())
        except (OSError, ValueError):
            holder = 0
        if pid_alive(holder):
            return False
        # Stale lock (holder died without cleaning up) — reclaim it. Losing
        # this second race just means yielding to the winner.
        shutil.rmtree(lock_dir, ignore_errors=True)
        try:
            lock_dir.mkdir()
        except FileExistsError:
            return False
    (lock_dir / "pid").write_text(str(os.getpid()))
    return True


def release_lock(lock_dir: Path = COLLECT_LOCK_DIR) -> None:
    shutil.rmtree(lock_dir, ignore_errors=True)


def record_run(skipped: list, failed: list) -> None:
    """One collection_runs row per run, exactly like wasteless.sh."""
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from core.database import execute_query

    try:
        execute_query(
            "INSERT INTO collection_runs (full_run, skipped_steps, failed_steps) "
            "VALUES (%s, %s, %s)",
            (len(skipped) == 0, skipped, failed),
        )
    except Exception:
        # Database not migrated yet (no failed_steps column): still record.
        execute_query(
            "INSERT INTO collection_runs (full_run, skipped_steps) VALUES (%s, %s)",
            (len(skipped) == 0, skipped),
        )


def run_pipeline(out) -> int:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")

    def say(msg: str) -> None:
        print(msg, file=out, flush=True)

    def run(script: str, *args: str) -> bool:
        result = subprocess.run(
            [sys.executable, script, *args],
            cwd=ROOT_DIR,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        return result.returncode == 0

    say("")
    say(f"WasteLess - Collect & Detect ({datetime.now():%Y-%m-%d %H:%M:%S})")
    say("")

    has_steampipe = shutil.which("steampipe") is not None
    steps = BOTO3_STEPS + (STEAMPIPE_STEPS if has_steampipe else [])
    total = len(BOTO3_STEPS) + len(STEAMPIPE_STEPS)
    failed = []
    for i, (label, script) in enumerate(steps, start=1):
        say(f"[{i}/{total}] {label}")
        if run(script):
            say("[OK] Done")
        else:
            say("[WARN] Step failed - continuing")
            failed.append(Path(script).stem)
        say("")

    skipped = []
    if not has_steampipe:
        say(f"[WARN] steampipe CLI not found - skipping steps {len(BOTO3_STEPS) + 1}-{total}")
        say("  (load balancers, NAT gateways, VPCs, gp2 migration, AMIs, RDS).")
        say("")
        skipped = [Path(script).stem for _, script in STEAMPIPE_STEPS]

    # Cost Explorer, self-guarded to once a day; kept out of failed_steps
    # for the same reason as in wasteless.sh (see the comment there).
    say("[costs] Collecting AWS costs (Cost Explorer, once/day)...")
    if run("src/aws_collector.py", "--daily"):
        say("[OK] Done")
    else:
        say("[WARN] Cost collection failed - will retry next tick")
    say("")

    try:
        record_run(skipped, failed)
    except Exception as e:
        say(f"[WARN] could not record collection_runs status: {e}")

    say("Done! Open http://localhost:8888/recommendations to review.")
    say("")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--log", help="append all output to this file instead of stdout")
    parser.add_argument(
        "--every",
        type=int,
        metavar="SECONDS",
        help="repeat forever with this pause between runs (in-process loop used by "
        "`wasteless start` when no scheduled task is installed)",
    )
    args = parser.parse_args()

    while True:
        run_once(args.log)
        if not args.every:
            return 0
        time.sleep(args.every)


def run_once(log: str | None) -> None:
    if not acquire_lock():
        return
    try:
        if log:
            with open(log, "a", encoding="utf-8") as out:
                run_pipeline(out)
        else:
            run_pipeline(sys.stdout)
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
