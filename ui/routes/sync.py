"""Manual AWS sync endpoint (mirrors the sync_aws_job background job)."""

from fastapi import APIRouter, Depends, HTTPException

from datetime import datetime

from state import get_db, SYNCABLE_STATUSES, _aws_status
from jobs import _resolve_vanished, _sync_ec2_instance_states
from utils.logger import get_logger

router = APIRouter()

logger = get_logger("sync")


@router.post("/api/sync-aws")
def api_sync_aws(conn=Depends(get_db)):
    """Synchronize recommendations with current AWS instance states."""
    import traceback

    try:
        import boto3  # noqa: F401 -- availability check
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"boto3 not installed: {e}") from e

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

        # Non-EC2 resources: resolve recommendations whose resource is gone
        # (approved_manual -> applied, others -> obsolete; see
        # jobs._resolve_vanished, shared with the background job)
        resolved_count = 0
        if pending_by_type:
            from utils.aws_sync import find_vanished_resources

            vanished = find_vanished_resources(pending_by_type)
            for resource_type, ids in vanished.items():
                resolved_count += _resolve_vanished(cursor, resource_type, ids)

        # EC2 instances: same state-based reconciliation as sync_aws_job,
        # so a manual click never resolves more (or less) than the auto job.
        synced_count = 0
        if pending_instances:
            synced_count, ec2_resolved = _sync_ec2_instance_states(cursor, pending_instances)
            resolved_count += ec2_resolved

        conn.commit()

        # Same stamp as sync_aws_job: the "Last sync" subtitle under the
        # Sync AWS button reads _aws_status, a manual click must move it
        # exactly like the 5-minute job does. Reaching this line means
        # the AWS calls above succeeded.
        _aws_status["reachable"] = True
        _aws_status["checked_at"] = datetime.now()

        return {
            "synced": synced_count,
            "obsolete": resolved_count,
            "total_checked": total_checked,
            "message": f"Synced {synced_count} instances, resolved {resolved_count} "
            "(applied/obsolete)",
        }

    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error(f"Sync AWS error: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}") from e


@router.post("/api/collect-now")
def api_collect_now():
    """Fire-and-forget full collection run (wasteless.sh collect).

    Behind the "Collect now" button on the empty Recommendations page:
    detection, unlike /api/sync-aws which only reconciles EXISTING
    recommendations with AWS. The collect lock in wasteless.sh makes an
    overlap with the 5-minute loop (or a double click) harmless."""
    from utils.collect import start_background_collection

    if not start_background_collection():
        raise HTTPException(
            status_code=500, detail="could not start the collection (wasteless.sh not found)"
        )
    logger.info("Manual collection started from the UI (collect-now)")
    return {"started": True}
