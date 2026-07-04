#!/usr/bin/python3
"""
One-shot backfill of waste_snapshots from resource creation dates.

For each currently-active waste resource we know how long it has existed
(metadata->>'age_days', with detection_date as fallback). A resource that is
wasting today was already wasting yesterday, so past snapshots can be
reconstructed as: waste at date D = sum of active resources already created
at D.

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

import psycopg2
from dotenv import load_dotenv

load_dotenv()

BACKFILL_DAYS = int(os.getenv('BACKFILL_DAYS', '180'))


def main():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', '5432')),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=10
    )
    cursor = conn.cursor()

    cursor.execute("""
        WITH aged AS (
            SELECT resource_type,
                   monthly_waste_eur,
                   CURRENT_DATE - COALESCE((metadata->>'age_days')::int,
                                           CURRENT_DATE - detection_date) AS created_on
            FROM active_waste
        )
        INSERT INTO waste_snapshots (snapshot_date, resource_type, total_eur, resource_count)
        SELECT d::date, a.resource_type,
               COALESCE(SUM(a.monthly_waste_eur), 0), COUNT(*)
        FROM generate_series(CURRENT_DATE - %s * INTERVAL '1 day',
                             CURRENT_DATE - INTERVAL '1 day',
                             INTERVAL '1 day') AS d
        JOIN aged a ON a.created_on <= d::date
        GROUP BY d::date, a.resource_type
        ON CONFLICT (snapshot_date, resource_type) DO NOTHING
    """, (BACKFILL_DAYS,))
    inserted = cursor.rowcount
    conn.commit()

    cursor.execute("""
        SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date)
        FROM waste_snapshots
    """)
    first, last, days = cursor.fetchone()
    cursor.close()
    conn.close()

    print(f"Backfill done: {inserted} row(s) inserted")
    print(f"waste_snapshots now spans {first} → {last} ({days} day(s))")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Backfill failed: {e}", file=sys.stderr)
        sys.exit(1)
