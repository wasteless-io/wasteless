"""
Background jobs registered on the APScheduler instance (every 5 minutes,
see main.py's lifespan) plus the AWS execution helpers they share with the
/api/actions route (ui/routes/recommendations.py).
"""

from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

# Deliberate direct psycopg2.connect() here (not state.get_db's pool): a
# scheduler job can hold its connection for the length of an AWS sweep, and
# parking long-lived work in the request pool would starve page loads. One
# short-lived connection per 5-min tick is cheap and self-cleaning.
import psycopg2
from psycopg2.extras import RealDictCursor

from state import DB_CONFIG, SYNCABLE_STATUSES, _config_manager, _aws_status, check_aws_reachable
from utils.action_registry import execution_mode


def _resolve_vanished(cursor: Any, resource_type: str, ids: List[str]) -> int:
    """Solde les recommandations dont la ressource n'existe plus. Partage
    par sync_aws_job et /api/sync-aws — avant cette factorisation, les deux
    portaient chacun leur copie du meme UPDATE.

    Une reco approved_manual devient 'applied' : l'humain avait valide, la
    disparition EST l'execution de sa decision (cycle manuel symetrique du
    cycle automatise). Tout autre statut syncable devient 'obsolete'
    (ressource evaporee hors process). Retourne le nombre de
    recommandations resolues (applied + obsolete confondus)."""
    cursor.execute(
        """
        UPDATE recommendations r
        SET status = CASE WHEN r.status = 'approved_manual'
                          THEN 'applied' ELSE 'obsolete' END,
            applied_at = NOW()
        FROM waste_detected w
        WHERE r.waste_id = w.id
        AND w.resource_type = %s
        AND w.resource_id = ANY(%s)
        AND r.status = ANY(%s)
    """,
        (resource_type, ids, list(SYNCABLE_STATUSES)),
    )
    return cursor.rowcount


def _sync_ec2_instance_states(cursor: Any, instance_ids: List[str]) -> Tuple[int, int]:
    """Reconcile EC2 recommendations against live instance state.

    A stop_instance/terminate_instance recommendation whose instance was
    already stopped or terminated outside wasteless (AWS console, another
    tool) is resolved just like a vanished resource — retrying it forever
    would never resolve it. Shared by sync_aws_job (every 5 min) and the
    manual /api/sync-aws button so both resolve the same cases; this used
    to be manual-button-only, so a stopped instance behind a 'scheduled'
    or 'rejected' recommendation never cleared until someone clicked sync.

    Resolution: 'applied' when the recommendation was approved_manual (the
    human decided, then executed it themselves), 'obsolete' otherwise
    (changed outside the process) — same rule as _resolve_vanished.

    Returns (synced_count, resolved_count).
    """
    from utils.aws_clients import get_client

    aws_states: Dict[str, Dict[str, str]] = {}
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
                SET status = CASE WHEN r.status = 'approved_manual'
                                  THEN 'applied' ELSE 'obsolete' END,
                    applied_at = NOW()
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
                    SET status = CASE WHEN status = 'approved_manual'
                                      THEN 'applied' ELSE 'obsolete' END,
                        applied_at = NOW()
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


def sync_aws_job() -> None:
    """Background job to sync recommendations with AWS state.

    Covers every resource type detectors can produce (EC2 instances, EBS
    volumes, Elastic IPs, snapshots, NAT gateways, load balancers, VPCs):
    when the resource no longer exists, the pending recommendation is
    obsolete. The guard test in test_aws_sync.py fails when a detector
    resource_type has no checker here.
    EC2 instances also get the stopped/terminated-outside-wasteless check
    via _sync_ec2_instance_states (see its docstring).
    """
    from utils.aws_sync import find_vanished_resources

    try:
        # closing() guarantees the connection is released on every exit path
        # (early return, exception mid-query, commit failure). Leaking it here
        # would exhaust Postgres over repeated scheduler ticks.
        with closing(psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)) as conn:
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
                return

            instance_ids = pending.pop("ec2_instance", [])
            vanished = find_vanished_resources(pending)

            resolved_count = 0
            for resource_type, ids in vanished.items():
                resolved_count += _resolve_vanished(cursor, resource_type, ids)

            if instance_ids:
                _, ec2_resolved = _sync_ec2_instance_states(cursor, instance_ids)
                resolved_count += ec2_resolved

            conn.commit()

            if resolved_count > 0:
                print(f"Auto-sync: resolved {resolved_count} recommendations (applied/obsolete)")

    except Exception as e:
        print(f"Auto-sync error: {e}")
    finally:
        _aws_status["reachable"] = check_aws_reachable()
        from datetime import datetime as _dt

        _aws_status["checked_at"] = _dt.now()


def terraform_pr_sync_job() -> None:
    """Reconcile open Terraform remediation PRs with GitHub.

    A merged PR means the change went through the user's Terraform
    pipeline (recommendation -> approved); a closed PR is a human
    rejection (-> rejected). Still-open PRs are left alone.
    """
    try:
        with closing(psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)) as conn:
            from utils.terraform_pr import sync_open_prs

            updated = sync_open_prs(conn)
            conn.commit()
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


def _execute_ec2_boto3(
    instance_id: str, rec_type: str, metadata: Optional[Dict[str, Any]]
) -> Tuple[bool, Optional[str]]:
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
        region_errors: List[str] = []

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


def grace_executor_job() -> None:
    """Execute scheduled approvals whose grace period has elapsed.

    Mirrors the /api/actions execution path: remediator mode goes through
    the backend safeguards pipeline, boto3 mode acts on EC2 directly.
    dry_run and per-action toggles are re-read at execution time.
    """
    conn = None
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

    except Exception as e:
        print(f"Grace executor error: {e}")
    finally:
        # Guarantee the connection is released on every path (early return,
        # exception, commit failure) so scheduler ticks don't leak connections.
        if conn is not None:
            conn.close()


def cost_collector_job() -> None:
    """Collecte quotidienne Cost Explorer → cloud_costs_raw.

    Alimente le dénominateur du Waste Rate (home + dashboard) et le KPI
    AWS Spend. Programmé sur un intervalle court mais s'auto-limite :
    l'API Cost Explorer est facturée 0,01 $ par requête, donc le job sort
    sans appeler AWS tant que les données d'hier sont déjà en base
    (~1 requête payée par jour). Même seuil de bruit (> 0,01 $) et même
    upsert que src/aws_collector.py, la version script manuelle.
    """
    import os
    from datetime import date, timedelta

    from psycopg2.extras import execute_values

    from utils.aws_clients import get_client

    try:
        with closing(psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)) as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT MAX(usage_date) AS latest FROM cloud_costs_raw WHERE provider = 'aws'"
            )
            row = cursor.fetchone()
            yesterday = date.today() - timedelta(days=1)
            if row and row["latest"] is not None and row["latest"] >= yesterday:
                return  # fresh enough — don't pay for another CE call

            # 180 days (Cost Explorer console's 6-month default), so the
            # dashboard's Total Cost matches the console figure instead of
            # starting at the install date. The upsert makes the daily
            # re-fetch idempotent; a long window can paginate, each page
            # is one billed request.
            end = date.today()
            start = end - timedelta(days=180)
            ce = get_client("ce", region=os.getenv("AWS_REGION", "eu-west-1"))
            results_by_time: List[Dict[str, Any]] = []
            next_token: Optional[str] = None
            while True:
                kwargs: Dict[str, Any] = {
                    "TimePeriod": {"Start": str(start), "End": str(end)},
                    "Granularity": "DAILY",
                    "Metrics": ["UnblendedCost"],
                    "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
                }
                if next_token:
                    kwargs["NextPageToken"] = next_token
                response = ce.get_cost_and_usage(**kwargs)
                results_by_time.extend(response.get("ResultsByTime", []))
                next_token = response.get("NextPageToken")
                if not next_token:
                    break

            account_id = os.getenv("AWS_ACCOUNT_ID", "unknown")
            region = os.getenv("AWS_REGION", "unknown")
            values: List[Tuple[Any, ...]] = []
            for day in results_by_time:
                usage_date = day["TimePeriod"]["Start"]
                for group in day.get("Groups", []):
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if cost > 0.01:
                        values.append(
                            (
                                "aws",
                                account_id,
                                group["Keys"][0],
                                None,
                                usage_date,
                                cost,
                                "USD",
                                region,
                                None,
                            )
                        )

            if not values:
                return

            execute_values(
                cursor,
                """
                INSERT INTO cloud_costs_raw
                (provider, account_id, service, resource_id, usage_date,
                 cost, currency, region, raw_data)
                VALUES %s
                ON CONFLICT ON CONSTRAINT uq_cloud_costs
                DO UPDATE SET cost = EXCLUDED.cost, currency = EXCLUDED.currency
                """,
                values,
            )
            conn.commit()
            print(f"Cost Explorer collect: {len(values)} rows upserted ({start} → {end})")

    except Exception as e:
        print(f"Cost Explorer collect error: {e}")


def savings_verifier_job() -> None:
    """Vérification des économies réelles → savings_realized (le chaînon
    Applied → Verified de la control loop).

    Compare via Cost Explorer le coût réel avant/après chaque action
    appliquée, au plus tôt 7 jours après l'action (en dessous le signal
    n'est pas significatif). S'auto-limite comme cost_collector_job : sort
    sans importer le tracker ni toucher AWS tant qu'aucune action éligible
    (réelle, non vérifiée, > 7 jours) n'attend. Chaque vérification coûte
    ~2 requêtes CE (0,01 $ pièce) et n'est faite qu'une fois : la ligne
    savings_realized écrite retire l'action de l'éligibilité.
    """
    try:
        with closing(psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)) as conn:
            cursor = conn.cursor()
            # Miroir du WHERE de verify_all_unverified_actions: le garde
            # d'éligibilité doit rester aligné sur ce que le tracker fera.
            cursor.execute("""
                SELECT COUNT(*) AS n
                FROM actions_log a
                LEFT JOIN savings_realized s ON s.recommendation_id = a.recommendation_id
                WHERE a.action_status = 'success'
                  AND a.dry_run = false
                  AND a.action_type IN ('stop', 'terminate', 'downsize')
                  AND s.id IS NULL
                  AND a.action_date < NOW() - INTERVAL '7 days'
            """)
            row = cursor.fetchone()
            if not row or (row["n"] or 0) == 0:
                return  # nothing eligible: no CE call
    except Exception as e:
        print(f"Savings verifier: eligibility check error: {e}")
        return

    try:
        from trackers.savings_tracker import SavingsTracker

        tracker = SavingsTracker()
        results = tracker.verify_all_unverified_actions(min_days_elapsed=7)
        if results:
            print(f"Savings verifier: {len(results)} action(s) verified via Cost Explorer")
    except ImportError as e:
        print(f"Savings verifier: trackers package not installed (re-run pip install -e .): {e}")
    except Exception as e:
        print(f"Savings verifier error: {e}")
