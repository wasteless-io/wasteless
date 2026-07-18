"""
Daily waste snapshots for trend charts.

waste_detected.updated_at moves on every re-scan, so aggregating it by date
does not give a stable time series. Instead, every detector run photographs
the current active waste (per resource type) into waste_snapshots, keyed by
(snapshot_date, resource_type).

The upsert makes the call idempotent: re-running any detector the same day
simply refreshes today's row with the latest totals.

On a fresh install the history is also reconstructed once from resource
ages (see backfill_waste_history), so the trend chart is never empty while
the first real week of snapshots accumulates.
"""

import logging

logger = logging.getLogger(__name__)

# Auto-backfill trigger: while fewer than this many distinct snapshot days
# exist, each snapshot call also reconstructs the past from resource ages.
MIN_HISTORY_DAYS = 7
BACKFILL_DAYS = 180


def backfill_waste_history(conn, days: int = BACKFILL_DAYS) -> int:
    """Reconstruct past waste_snapshots from resource ages (floor values).

    A resource wasting today with a known age was already wasting on each
    of the past N days, so waste at date D = sum of the active resources
    that already existed at D (metadata age_days, detection_date as
    fallback). Resources cleaned up before today are invisible: the curve
    is a floor, not an exact value. Real snapshots are never overwritten
    (ON CONFLICT DO NOTHING). Returns the number of rows inserted.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
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
        """,
            (days,),
        )
        inserted = cursor.rowcount
        conn.commit()
        if inserted:
            logger.info(f"Waste history backfilled: {inserted} snapshot row(s)")
        return inserted
    except Exception as e:
        conn.rollback()
        logger.warning(f"Waste history backfill failed (non-fatal): {e}")
        return 0
    finally:
        cursor.close()


def snapshot_active_waste(conn) -> int:
    """Photograph today's active waste per resource type into waste_snapshots.

    Aggregates the active_waste view (all types, not just the calling
    detector's), so any detector run keeps today's snapshot complete.
    Starts from every resource_type ever seen in waste_detected (not just
    those still in active_waste) so a type that drops to zero waste today
    is recorded as 0 instead of silently keeping yesterday's total forever.
    Returns the number of resource types snapshotted.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO waste_snapshots (snapshot_date, resource_type, total_eur, resource_count)
            SELECT CURRENT_DATE, rt.resource_type,
                   COALESCE(SUM(aw.monthly_waste_eur), 0), COUNT(aw.id)
            FROM (SELECT DISTINCT resource_type FROM waste_detected) rt
            LEFT JOIN active_waste aw ON aw.resource_type = rt.resource_type
            GROUP BY rt.resource_type
            ON CONFLICT (snapshot_date, resource_type) DO UPDATE SET
                total_eur      = EXCLUDED.total_eur,
                resource_count = EXCLUDED.resource_count
        """)
        count = cursor.rowcount
        conn.commit()
        logger.info(f"Waste snapshot saved for {count} resource type(s)")
    except Exception as e:
        conn.rollback()
        # A failed snapshot must never break a detection run
        logger.warning(f"Waste snapshot failed (non-fatal): {e}")
        return 0
    finally:
        cursor.close()

    # Fresh-install self-healing: while the real history is shorter than
    # MIN_HISTORY_DAYS, reconstruct the past from resource ages so the
    # trend chart has a curve from the very first scan. Cheap COUNT once
    # the history exists; never overwrites real snapshots.
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT snapshot_date) AS n FROM waste_snapshots")
        row = cursor.fetchone()
        # Callers hand us tuple- or dict-cursor connections alike
        history_days = row["n"] if isinstance(row, dict) else row[0]
        cursor.close()
        if history_days < MIN_HISTORY_DAYS:
            backfill_waste_history(conn)
    except Exception as e:
        conn.rollback()
        logger.warning(f"Waste history check failed (non-fatal): {e}")

    return count
