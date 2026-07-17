"""Shared persistence for connection settings entered in the UI.

Both the /setup page (AWS) and the Settings AI card save credentials the
same way: write BOTH env files (root .env feeds the collectors/detectors,
ui/.env feeds this process) and apply the values to the running process so
no restart is needed. Keeping the two files in sync here is the whole
point — the manual "mirror the root .env" convention lost users.
"""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent  # ui/
ROOT_DIR = APP_DIR.parent  # repo root

ENV_FILES = [ROOT_DIR / ".env", APP_DIR / ".env"]


def write_env_files(values: dict, files: list | None = None) -> None:
    """Set KEY=VALUE in every env file (`files` overrides the default pair,
    used by tests): replace the line when the key exists, append otherwise.
    Empty values are left untouched (these pages only add or update
    settings, they never unset keys)."""
    for path in files if files is not None else ENV_FILES:
        lines = path.read_text().splitlines() if path.exists() else []
        remaining = {k: v for k, v in values.items() if v}
        out = []
        for line in lines:
            key = line.split("=", 1)[0] if "=" in line else None
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
            else:
                out.append(line)
        out.extend(f"{k}={v}" for k, v in remaining.items())
        path.write_text("\n".join(out) + "\n")
        os.chmod(path, 0o600)


def apply_to_env(values: dict) -> None:
    """Export the non-empty values into this process's environment so the
    change is live immediately (boto3 chain, litellm key lookup, ...)."""
    for key, value in values.items():
        if value:
            os.environ[key] = value
