#!/usr/bin/python3
"""
Manual backfill of waste_snapshots from resource creation dates.

Thin wrapper around core.snapshots.backfill_waste_history, the canonical
implementation (also triggered automatically by snapshot_active_waste on
fresh installs while fewer than MIN_HISTORY_DAYS of real snapshots exist).
Run this by hand to reconstruct deeper history or after purging the table.

Known limit: resources cleaned up before today are invisible, so the
backfilled curve is a floor, not an exact value. Real snapshots written by
detectors (same-day rows) are never overwritten: ON CONFLICT DO NOTHING.

Usage:
    python3 scripts/backfill_waste_snapshots.py

Env vars:
    BACKFILL_DAYS  How far back to reconstruct (default: 180)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2
from dotenv import load_dotenv

load_dotenv()

from core.snapshots import backfill_waste_history

BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "180"))


def main():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=10,
    )
    inserted = backfill_waste_history(conn, days=BACKFILL_DAYS)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date)
        FROM waste_snapshots
    """)
    first, last, days = cursor.fetchone()
    cursor.close()
    conn.close()

    print(f"Backfill done: {inserted} row(s) inserted")
    print(f"waste_snapshots now spans {first} → {last} ({days} day(s))")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Backfill failed: {e}", file=sys.stderr)
        sys.exit(1)
