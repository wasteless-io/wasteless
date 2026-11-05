"""Fire-and-forget launcher for a full collection run (wasteless.sh collect).

Shared by the /setup save (first collection right after connecting AWS) and
the /api/collect-now endpoint (Collect now button on the empty
Recommendations page). The collect lock inside wasteless.sh makes an
overlap with the 5-minute loop harmless, and a failure here must never
fail the caller.

Native Windows has no bash: there the run goes through src/run_collection.py
(same steps, same lock) with the root venv's windowless interpreter.
"""

import os
import subprocess
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("collect")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # repo root

IS_WINDOWS = os.name == "nt"

# Windows process flags: no console window, not killed with the UI.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _windows_command(root: Path) -> list | None:
    runner = root / "src" / "run_collection.py"
    scripts = root / "venv" / "Scripts"
    candidates = (scripts / "pythonw.exe", scripts / "python.exe")
    python = next((p for p in candidates if p.exists()), None)
    if python is None or not runner.exists():
        return None
    log_file = Path.home() / ".wasteless.log"
    return [str(python), str(runner), "--log", str(log_file)]


def start_background_collection(root_dir: Path | None = None) -> bool:
    """Launch `wasteless.sh collect` (src/run_collection.py on Windows)
    detached; True when the process started. `root_dir` overrides the repo
    root (used by tests)."""
    root = root_dir if root_dir is not None else ROOT_DIR
    if IS_WINDOWS:
        cmd = _windows_command(root)
        if cmd is None:
            return False
        popen_kwargs: dict = {"creationflags": _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP}
    else:
        script = root / "wasteless.sh"
        if not script.exists():
            return False
        cmd = [str(script), "collect"]
        popen_kwargs = {"start_new_session": True}
    try:
        subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
        return True
    except Exception as e:
        logger.warning(f"Background collection not started: {e}")
        return False
