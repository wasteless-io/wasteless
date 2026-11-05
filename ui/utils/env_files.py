"""Shared persistence for connection settings entered in the UI.

Both the /setup page (AWS) and the Settings AI card save credentials the
same way: write BOTH env files (root .env feeds the collectors/detectors,
ui/.env feeds this process) and apply the values to the running process so
no restart is needed. Keeping the two files in sync here is the whole
point: the manual "mirror the root .env" convention lost users.
"""

import os
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent  # ui/
ROOT_DIR = APP_DIR.parent  # repo root

ENV_FILES = [ROOT_DIR / ".env", APP_DIR / ".env"]


def _current_user_sid() -> str | None:
    """SID of the current Windows user (locale-independent, unlike names)."""
    try:
        out = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            errors="replace",  # OEM code page output; the SID itself is ASCII
            timeout=10,
            check=True,
        ).stdout
        return out.strip().split(",")[-1].strip('"') or None
    except (OSError, subprocess.SubprocessError):
        return None


def restrict_to_owner(path: Path) -> None:
    """Secrets-grade permissions: chmod 600 on POSIX. On Windows, chmod
    only toggles the read-only flag, so the equivalent is an NTFS ACL with
    inheritance cut and a single entry for the current user — the same
    icacls call install.ps1 makes."""
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    sid = _current_user_sid()
    grantee = f"*{sid}" if sid else os.environ.get("USERNAME", "")
    if not grantee:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{grantee}:F"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        # Never fail the save over this: the values are written, and files
        # under the user profile inherit ACLs that already exclude other
        # standard users.
        pass


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
        restrict_to_owner(path)


def apply_to_env(values: dict) -> None:
    """Export the non-empty values into this process's environment so the
    change is live immediately (boto3 chain, litellm key lookup, ...)."""
    for key, value in values.items():
        if value:
            os.environ[key] = value


def clear_env_keys(keys: list, files: list | None = None) -> None:
    """Remove KEY=... lines from every env file and unset them in this
    process. This is the disconnect path: write_env_files() only ever adds
    or updates (empty values are ignored), so unsetting needs its own call.
    Both files stay in sync for the same reason saving does."""
    keyset = set(keys)
    for path in files if files is not None else ENV_FILES:
        if not path.exists():
            continue
        out = [
            line
            for line in path.read_text().splitlines()
            if (line.split("=", 1)[0] if "=" in line else None) not in keyset
        ]
        path.write_text("\n".join(out) + ("\n" if out else ""))
        restrict_to_owner(path)
    for key in keyset:
        os.environ.pop(key, None)
