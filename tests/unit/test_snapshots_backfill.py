"""Fresh-install trend history: the age-based backfill and its trigger.

The reconstruction itself is plain SQL (floor semantics, never overwrites
real snapshots); what needs guarding is the trigger contract in
snapshot_active_waste — backfill while the real history is shorter than
MIN_HISTORY_DAYS, never once it exists — and the age parsing detectors
rely on to feed it.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core.snapshots import MIN_HISTORY_DAYS, snapshot_active_waste
from detectors.steampipe_base import age_days_from


class TestAgeDaysFrom:
    def test_iso_with_zulu_suffix(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=42)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert age_days_from(ts) in (41, 42)

    def test_iso_with_offset(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert age_days_from(ts) in (9, 10)

    def test_naive_timestamp_is_treated_as_utc(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        assert age_days_from(ts) in (4, 5)

    def test_future_date_clamps_to_zero(self):
        ts = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        assert age_days_from(ts) == 0

    def test_absent_or_garbage_is_none(self):
        assert age_days_from(None) is None
        assert age_days_from("") is None
        assert age_days_from("not-a-date") is None


def _conn_with_history(history_days):
    """Connection whose COUNT(DISTINCT snapshot_date) returns history_days;
    every cursor it hands out is kept so the test can inspect the SQL."""
    conn = MagicMock(name="connection")
    cursors = []

    def make_cursor():
        cursor = MagicMock(name="cursor")
        cursor.fetchone.return_value = (history_days,)
        cursor.rowcount = 3
        cursors.append(cursor)
        return cursor

    conn.cursor.side_effect = make_cursor
    return conn, cursors


def _all_sql(cursors):
    return "\n".join(call.args[0] for cursor in cursors for call in cursor.execute.call_args_list)


class TestBackfillTrigger:
    def test_backfills_while_history_is_short(self):
        conn, cursors = _conn_with_history(MIN_HISTORY_DAYS - 1)
        snapshot_active_waste(conn)
        assert "generate_series" in _all_sql(
            cursors
        ), "a near-empty history must trigger the age-based reconstruction"

    def test_does_not_backfill_once_history_exists(self):
        conn, cursors = _conn_with_history(MIN_HISTORY_DAYS)
        snapshot_active_waste(conn)
        assert "generate_series" not in _all_sql(
            cursors
        ), "an established history must never be backfilled again"
