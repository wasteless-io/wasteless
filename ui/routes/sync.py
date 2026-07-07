"""Manual AWS sync endpoint (mirrors the sync_aws_job background job)."""

from fastapi import APIRouter, Depends, HTTPException

from state import get_db, SYNCABLE_STATUSES
from jobs import _sync_ec2_instance_states

router = APIRouter()


@router.post("/api/sync-aws")
async def api_sync_aws(conn=Depends(get_db)):
    """Synchronize recommendations with current AWS instance states."""
    import traceback

    try:
        import boto3  # noqa: F401 -- availability check
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"boto3 not installed: {e}")

    try:
        cursor = conn.cursor()

        # Pending recommendations grouped by resource type: only EC2
        # instances go through the state logic below; other types are
        # existence-checked with the proper API (an EIP id would never be
        # found by describe_instances and used to be wrongly obsoleted)
        cursor.execute(
            """
            SELECT w.resource_type, array_agg(DISTINCT w.resource_id) AS ids
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = ANY(%s)
            GROUP BY w.resource_type
        """,
            (list(SYNCABLE_STATUSES),),
        )
        pending_by_type = {row["resource_type"]: row["ids"] for row in cursor.fetchall()}
        pending_instances = pending_by_type.pop("ec2_instance", [])

        if not pending_instances and not pending_by_type:
            return {"synced": 0, "obsolete": 0, "message": "No pending recommendations"}

        total_checked = len(pending_instances) + sum(len(ids) for ids in pending_by_type.values())

        # Non-EC2 resources: obsolete recommendations whose resource is gone
        obsolete_count = 0
        if pending_by_type:
            from utils.aws_sync import find_vanished_resources

            vanished = find_vanished_resources(pending_by_type)
            for resource_type, ids in vanished.items():
                cursor.execute(
                    """
                    UPDATE recommendations r
                    SET status = 'obsolete', applied_at = NOW()
                    FROM waste_detected w
                    WHERE r.waste_id = w.id
                    AND w.resource_type = %s
                    AND w.resource_id = ANY(%s)
                    AND r.status = ANY(%s)
                """,
                    (resource_type, ids, list(SYNCABLE_STATUSES)),
                )
                obsolete_count += cursor.rowcount

        # EC2 instances: same state-based reconciliation as sync_aws_job,
        # so a manual click never resolves more (or less) than the auto job.
        synced_count = 0
        if pending_instances:
            synced_count, ec2_obsolete = _sync_ec2_instance_states(cursor, pending_instances)
            obsolete_count += ec2_obsolete

        conn.commit()

        return {
            "synced": synced_count,
            "obsolete": obsolete_count,
            "total_checked": total_checked,
            "message": f"Synced {synced_count} instances, marked {obsolete_count} as obsolete",
        }

    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"Sync AWS error: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
