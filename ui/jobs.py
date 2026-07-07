"""
Background jobs registered on the APScheduler instance (every 5 minutes,
see main.py's lifespan) plus the AWS execution helpers they share with the
/api/actions route (ui/routes/recommendations.py).
"""

from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from state import DB_CONFIG, SYNCABLE_STATUSES, _config_manager, _aws_status, check_aws_reachable
from utils.action_registry import execution_mode


def _sync_ec2_instance_states(cursor, instance_ids):
    """Reconcile EC2 recommendations against live instance state.

    A stop_instance/terminate_instance recommendation whose instance was
    already stopped or terminated outside wasteless (AWS console, another
    tool) is obsolete just like a vanished resource — retrying it forever
    would never resolve it. Shared by sync_aws_job (every 5 min) and the
    manual /api/sync-aws button so both resolve the same cases; this used
    to be manual-button-only, so a stopped instance behind a 'scheduled'
    or 'rejected' recommendation never cleared until someone clicked sync.

    Returns (synced_count, obsolete_count).
    """
    from utils.aws_clients import get_client

    aws_states = {}
    for region in ["eu-west-1", "eu-west-2", "eu-west-3", "us-east-1"]:
        try:
            ec2 = get_client("ec2", region=region)
            response = ec2.describe_instances(
                Filters=[{"Name": "instance-id", "Values": instance_ids}]
            )
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    aws_states[instance["InstanceId"]] = {
                        "state": instance["State"]["Name"],
                        "region": region,
                    }
        except Exception as e:
            print(f"Error checking region {region}: {e}")
            continue

    synced_count = 0
    obsolete_count = 0

    for instance_id in instance_ids:
        aws_info = aws_states.get(instance_id)

        if aws_info is None:
            cursor.execute(
                """
                UPDATE recommendations r
                SET status = 'obsolete', applied_at = NOW()
                FROM waste_detected w
                WHERE r.waste_id = w.id
                AND w.resource_id = %s
                AND r.status = ANY(%s)
            """,
                (instance_id, list(SYNCABLE_STATUSES)),
            )
            obsolete_count += cursor.rowcount
            continue

        aws_state = aws_info["state"]

        cursor.execute(
            """
            SELECT r.id, r.recommendation_type
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE w.resource_id = %s AND r.status = ANY(%s)
        """,
            (instance_id, list(SYNCABLE_STATUSES)),
        )

        for rec in cursor.fetchall():
            rec_type = rec["recommendation_type"]
            should_obsolete = (
                rec_type == "stop_instance" and aws_state in ("stopped", "terminated")
            ) or (rec_type == "terminate_instance" and aws_state == "terminated")

            if should_obsolete:
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'obsolete', applied_at = NOW()
                    WHERE id = %s
                """,
                    (rec["id"],),
                )
                obsolete_count += 1
            else:
                cursor.execute(
                    """
                    UPDATE waste_detected
                    SET metadata = jsonb_set(
                        COALESCE(metadata, '{}'::jsonb),
                        '{instance_state}',
                        %s::jsonb
                    )
                    WHERE resource_id = %s
                """,
                    (f'"{aws_state}"', instance_id),
                )
                synced_count += 1

    return synced_count, obsolete_count


def sync_aws_job():
    """Background job to sync recommendations with AWS state.

    Covers every resource type detectors can produce (EC2 instances, EBS
    volumes, Elastic IPs, snapshots, NAT gateways, load balancers): when
    the resource no longer exists, the pending recommendation is obsolete.
    EC2 instances also get the stopped/terminated-outside-wasteless check
    via _sync_ec2_instance_states (see its docstring).
    """
    from utils.aws_sync import find_vanished_resources

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        cursor = conn.cursor()

        # Open recommendations grouped by resource type — see
        # SYNCABLE_STATUSES for which statuses are checked and why.
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
        pending = {row["resource_type"]: row["ids"] for row in cursor.fetchall()}

        if not pending:
            conn.close()
            return

        instance_ids = pending.pop("ec2_instance", [])
        vanished = find_vanished_resources(pending)

        obsolete_count = 0
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

        if instance_ids:
            _, ec2_obsolete = _sync_ec2_instance_states(cursor, instance_ids)
            obsolete_count += ec2_obsolete

        conn.commit()
        conn.close()

        if obsolete_count > 0:
            print(f"Auto-sync: marked {obsolete_count} recommendations as obsolete")

    except Exception as e:
        print(f"Auto-sync error: {e}")
    finally:
        _aws_status["reachable"] = check_aws_reachable()
        from datetime import datetime as _dt

        _aws_status["checked_at"] = _dt.now()


def terraform_pr_sync_job():
    """Reconcile open Terraform remediation PRs with GitHub.

    A merged PR means the change went through the user's Terraform
    pipeline (recommendation -> approved); a closed PR is a human
    rejection (-> rejected). Still-open PRs are left alone.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        from utils.terraform_pr import sync_open_prs

        updated = sync_open_prs(conn)
        conn.commit()
        conn.close()
        if updated > 0:
            print(f"Terraform PR sync: {updated} recommendation(s) updated")
    except Exception as e:
        print(f"Terraform PR sync error: {e}")


def _grace_execution_status(success: bool, error: Optional[str], dry_run: bool, mode: str) -> str:
    """The recommendation's next status after a grace-period execution attempt.

    - real success (not dry-run, not manual) -> approved: genuinely done.
    - resource gone (deleted outside wasteless during the grace period) ->
      obsolete: retrying forever every 5 minutes would never resolve it,
      same terminal state sync_aws_job would apply on its own.
    - anything else (failure, dry-run, manual) -> pending: back in the
      queue for a human to reconsider, nothing was actually touched.
    """
    if success and not dry_run and mode != "manual":
        return "approved"
    if not success and error and "not found" in error:
        return "obsolete"
    return "pending"


def _execute_ec2_boto3(instance_id, rec_type, metadata):
    """Stop/terminate an EC2 instance via boto3, trying likely regions.

    Returns (success, error_message). Shared by the approval API and the
    grace-period executor job.
    """
    try:
        from utils.aws_clients import get_client

        regions = ["eu-west-1", "eu-west-2", "eu-west-3", "us-east-1"]
        # Use stored region if available
        stored_region = (metadata or {}).get("region")
        if stored_region:
            regions = [stored_region] + [r for r in regions if r != stored_region]
        region_errors = []

        for region in regions:
            try:
                # Stop/terminate: remediation context, use the write role
                ec2 = get_client("ec2", region=region, write=True)

                # EC2 instance actions only
                if rec_type in ("stop_instance", "terminate_instance"):
                    response = ec2.describe_instances(
                        Filters=[{"Name": "instance-id", "Values": [instance_id]}]
                    )
                    if not response["Reservations"]:
                        continue
                    instance_state = response["Reservations"][0]["Instances"][0]["State"]["Name"]
                    if instance_state in ["terminated", "shutting-down"]:
                        return True, None
                    if rec_type == "stop_instance":
                        ec2.stop_instances(InstanceIds=[instance_id])
                        print(f"Stopped instance {instance_id} in {region}")
                    elif rec_type == "terminate_instance":
                        ec2.terminate_instances(InstanceIds=[instance_id])
                        print(f"Terminated instance {instance_id} in {region}")
                    return True, None

            except Exception as e:
                region_errors.append(f"{region}: {type(e).__name__}: {e}")
                continue

        if region_errors:
            return False, "Errors: " + " | ".join(region_errors)
        return False, f"Resource {instance_id} not found in any region"

    except ImportError:
        return False, "boto3 not installed"
    except Exception as e:
        return False, str(e)


def grace_executor_job():
    """Execute scheduled approvals whose grace period has elapsed.

    Mirrors the /api/actions execution path: remediator mode goes through
    the backend safeguards pipeline, boto3 mode acts on EC2 directly.
    dry_run and per-action toggles are re-read at execution time.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.id, r.recommendation_type,
                   w.resource_id, w.resource_type, w.metadata
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'scheduled' AND r.execute_after <= NOW()
            ORDER BY r.execute_after
            LIMIT 20
        """)
        due = cursor.fetchall()
        if not due:
            conn.close()
            return

        from utils.remediator import RemediatorProxy

        dry_run = _config_manager.get_dry_run()

        for row in due:
            rec_id = row["id"]
            rec_type = row["recommendation_type"]
            instance_id = row["resource_id"]
            resource_type = row["resource_type"]
            metadata = row["metadata"] or {}
            action_type = (
                rec_type.replace("_instance", "").replace("_volume", "").replace("_snapshot", "")
            )

            mode = execution_mode(rec_type)
            if mode != "manual" and not _config_manager.get_action_enabled(rec_type):
                mode = "manual"

            if mode == "remediator":
                try:
                    proxy = RemediatorProxy(dry_run=dry_run)
                    result = proxy.execute_recommendations(conn, [rec_id])[0]
                    success = bool(result.get("success"))
                    error = result.get("error")
                except Exception as e:
                    success, error = False, str(e)
            elif mode == "boto3" and not dry_run:
                success, error = _execute_ec2_boto3(instance_id, rec_type, metadata)
            else:
                # dry-run, or action disabled since approval: record only
                success, error = True, None

            if not dry_run and not success:
                from utils.notifications import notify_action_failure

                notify_action_failure(rec_type, instance_id, error)

            cursor.execute(
                """
                INSERT INTO actions_log
                (resource_id, recommendation_id, resource_type, action_type,
                 action_status, dry_run, action_date, error_message, executed_by)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, 'grace_executor')
            """,
                (
                    instance_id,
                    rec_id,
                    resource_type,
                    action_type,
                    "success" if success else "failed",
                    dry_run or mode == "manual",
                    error,
                ),
            )

            # See _grace_execution_status: approved on real success,
            # obsolete if the resource vanished during the grace period,
            # pending otherwise (nothing actually touched, human decides).
            new_status = _grace_execution_status(success, error, dry_run, mode)
            cursor.execute(
                """
                UPDATE recommendations
                SET status = %s, applied_at = NOW(), execute_after = NULL
                WHERE id = %s
            """,
                (new_status, rec_id),
            )
            conn.commit()
            print(
                f"Grace executor: rec #{rec_id} ({rec_type}) → "
                f"{'OK' if success else f'FAILED: {error}'}"
            )

        conn.close()

    except Exception as e:
        print(f"Grace executor error: {e}")
