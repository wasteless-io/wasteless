#!/usr/bin/env python3
"""
Steampipe query wrapper for Wasteless

Runs SQL queries against AWS through Steampipe (https://steampipe.io) and
returns rows as dicts. Steampipe exposes the whole AWS API as PostgreSQL
tables, so a new detector's collection layer is just a SQL file instead of
boto3 describe_* calls.

Prerequisites (one-time):
    brew install turbot/tap/steampipe
    steampipe plugin install aws

Multi-region is configured once in ~/.steampipe/config/aws.spc
(e.g. regions = ["eu-west-*", "us-east-1"]) — queries then aggregate all
regions automatically, no per-region loop needed.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Directory holding the detection queries (sql/steampipe/*.sql)
QUERIES_DIR = Path(__file__).resolve().parents[2] / 'sql' / 'steampipe'

DEFAULT_TIMEOUT_SECONDS = 120


class SteampipeError(Exception):
    """Raised when a Steampipe query fails."""
    pass


class SteampipeNotInstalledError(SteampipeError):
    """Raised when the steampipe binary is not on PATH."""
    pass


def is_available() -> bool:
    """Return True if the steampipe binary is on PATH."""
    return shutil.which('steampipe') is not None


def run_query(sql: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> List[Dict[str, Any]]:
    """
    Run a SQL query through Steampipe and return rows as dicts.

    Args:
        sql: SQL text (queries Steampipe's AWS tables, e.g. aws_ebs_volume)
        timeout: Max seconds to wait (API-backed scans can be slow)

    Raises:
        SteampipeNotInstalledError: If steampipe is not installed
        SteampipeError: On query failure, timeout, or unparseable output
    """
    if not is_available():
        raise SteampipeNotInstalledError(
            "steampipe binary not found on PATH. "
            "Install it with: brew install turbot/tap/steampipe "
            "&& steampipe plugin install aws"
        )

    try:
        result = subprocess.run(
            ['steampipe', 'query', sql, '--output', 'json'],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise SteampipeError(f"Steampipe query timed out after {timeout}s") from e

    if result.returncode != 0:
        raise SteampipeError(
            f"Steampipe query failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise SteampipeError(
            f"Could not parse Steampipe output as JSON: {result.stdout[:200]}"
        ) from e

    # Steampipe >= 0.21 wraps rows in {"rows": [...]}; older versions
    # return a bare JSON array.
    if isinstance(payload, dict):
        rows = payload.get('rows', [])
    else:
        rows = payload

    if not isinstance(rows, list):
        raise SteampipeError(
            f"Unexpected Steampipe output shape: {type(rows).__name__}"
        )

    logger.info(f"Steampipe query returned {len(rows)} row(s)")
    return rows


def run_query_file(name: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> List[Dict[str, Any]]:
    """
    Run a named query from sql/steampipe/<name>.sql.

    Args:
        name: Query file name without extension (e.g. 'ebs_orphan')
        timeout: Max seconds to wait

    Raises:
        SteampipeError: If the query file does not exist or the query fails
    """
    path = QUERIES_DIR / f'{name}.sql'
    if not path.is_file():
        raise SteampipeError(f"Query file not found: {path}")
    return run_query(path.read_text(), timeout=timeout)
