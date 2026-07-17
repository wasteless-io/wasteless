"""Fire-and-forget launcher for a full collection run (wasteless.sh collect).

Shared by the /setup save (first collection right after connecting AWS) and
the /api/collect-now endpoint (Collect now button on the empty
Recommendations page). The collect lock inside wasteless.sh makes an
overlap with the 5-minute loop harmless, and a failure here must never
fail the caller.
"""

import subprocess
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("collect")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # repo root


def start_background_collection(root_dir: Path | None = None) -> bool:
    """Launch `wasteless.sh collect` detached; True when the process
    started. `root_dir` overrides the repo root (used by tests)."""
    root = root_dir if root_dir is not None else ROOT_DIR
    script = root / "wasteless.sh"
    if not script.exists():
        return False
    try:
        subprocess.Popen(
            [str(script), "collect"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        logger.warning(f"Background collection not started: {e}")
        return False
