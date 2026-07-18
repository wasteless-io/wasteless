"""
Unit tests for the daily waste snapshot helper (src/core/snapshots.py).
The database is mocked; only the commit/rollback contract is exercised.
(The fresh-install backfill trigger has its own suite in
test_snapshots_backfill.py.)
"""

import sys
import os
from unittest.mock import MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.snapshots import MIN_HISTORY_DAYS, snapshot_active_waste


def _conn_with_cursor(cursor):
    """Every cursor the module opens is the same mock; the history check
    reads an established history so the backfill branch stays quiet."""
    cursor.fetchone.return_value = (MIN_HISTORY_DAYS,)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


class TestSnapshotActiveWaste:

    def test_commits_and_returns_rowcount(self):
        cursor = MagicMock()
        cursor.rowcount = 3
        conn = _conn_with_cursor(cursor)

        assert snapshot_active_waste(conn) == 3
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_upsert_targets_waste_snapshots(self):
        cursor = MagicMock()
        cursor.rowcount = 1
        conn = _conn_with_cursor(cursor)

        snapshot_active_waste(conn)
        sql = cursor.execute.call_args_list[0][0][0]
        assert "INSERT INTO waste_snapshots" in sql
        assert "FROM waste_detected" in sql
        assert "LEFT JOIN active_waste" in sql
        assert "ON CONFLICT (snapshot_date, resource_type) DO UPDATE" in sql

    def test_failure_is_non_fatal(self):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("db down")
        conn = _conn_with_cursor(cursor)

        assert snapshot_active_waste(conn) == 0
        conn.rollback.assert_called()
        conn.commit.assert_not_called()
