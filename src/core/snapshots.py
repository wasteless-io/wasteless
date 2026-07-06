"""
Daily waste snapshots for trend charts.

waste_detected.updated_at moves on every re-scan, so aggregating it by date
does not give a stable time series. Instead, every detector run photographs
the current active waste (per resource type) into waste_snapshots, keyed by
(snapshot_date, resource_type).

The upsert makes the call idempotent: re-running any detector the same day
simply refreshes today's row with the latest totals.
"""

import logging

logger = logging.getLogger(__name__)


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
        return count
    except Exception as e:
        conn.rollback()
        # A failed snapshot must never break a detection run
        logger.warning(f"Waste snapshot failed (non-fatal): {e}")
        return 0
    finally:
        cursor.close()
