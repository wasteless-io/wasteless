"""Recommendations page, its JSON API, the AI Q&A endpoint, and the
approve/reject/dismiss/cancel/execute action endpoint."""

import json
import os

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg2.extras import Json

from state import get_db, templates, _config_manager
from schemas import ActionRequest, AskQuestionRequest
from jobs import _execute_ec2_boto3
from utils.action_registry import execution_mode
from utils.logger import log_remediation_action

router = APIRouter()


@router.get("/recommendations", response_class=HTMLResponse)
def recommendations(
    request: Request,
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0,
):
    """Recommendations management page."""
    cursor = conn.cursor()

    # WHERE clause shared by the display query (capped at 500 rows) and the
    # summary-stats query below (uncapped) — otherwise, past 500 matching
    # rows, "Savings"/"conf." in the header would silently undercount
    # against the true filtered total (and against Home's unfiltered
    # pending_eur KPI), since they'd only reflect the top-500 subset shown.
    where_clause = "WHERE r.status = 'pending'"
    params = []

    if type_filter != "All":
        where_clause += " AND r.recommendation_type = %s"
        params.append(type_filter)

    if min_savings > 0:
        where_clause += " AND r.estimated_monthly_savings_eur >= %s"
        params.append(min_savings)

    if min_confidence > 0:
        where_clause += " AND w.confidence_score >= %s"
        params.append(min_confidence)

    cursor.execute(
        f"""
        SELECT
            r.id,
            r.recommendation_type,
            w.resource_id,
            w.resource_type,
            r.estimated_monthly_savings_eur,
            w.confidence_score,
            r.action_required,
            r.status,
            r.created_at,
            w.metadata->>'instance_type' as instance_type,
            (w.metadata->>'cpu_avg_7d')::numeric as cpu_avg,
            (w.metadata->>'monthly_cost_eur')::numeric as monthly_cost,
            w.metadata->>'instance_state' as instance_state,
            w.metadata->>'size_gb' as volume_size_gb,
            w.metadata->>'vol_type' as volume_type,
            COALESCE(w.metadata->>'region', w.metadata->>'az') as volume_region,
            w.metadata->>'name' as volume_name,
            w.metadata->>'public_ip' as public_ip,
            COALESCE((w.metadata->>'age_days')::integer, CURRENT_DATE - w.detection_date) as age_days,
            w.metadata->>'description' as snap_description,
            r.ai_insight
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
        ORDER BY r.estimated_monthly_savings_eur DESC LIMIT 500
    """,
        params if params else None,
    )
    recommendations = cursor.fetchall()

    # Summary stats: true totals across every matching row, not just the
    # 500 shown in the table below.
    cursor.execute(
        f"""
        SELECT COUNT(*) as cnt,
               COALESCE(SUM(r.estimated_monthly_savings_eur), 0) as total_savings,
               COALESCE(AVG(w.confidence_score), 0) as avg_confidence
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
    """,
        params if params else None,
    )
    totals = cursor.fetchone()
    total_count = totals["cnt"]
    total_savings = float(totals["total_savings"])
    avg_confidence = float(totals["avg_confidence"])

    ec2_recs = [r for r in recommendations if r["resource_type"] == "ec2_instance"]
    # The EBS tab renders deletion semantics ("unattached", "why delete?"),
    # so it only gets delete_volume recs; gp2 migrations go to Other
    ebs_recs = [
        r
        for r in recommendations
        if r["resource_type"] == "ebs_volume" and r["recommendation_type"] == "delete_volume"
    ]
    eip_recs = [r for r in recommendations if r["resource_type"] == "elastic_ip"]
    snap_recs = [r for r in recommendations if r["resource_type"] == "ebs_snapshot"]
    # Catch-all so recommendations from new detectors (NAT gateways, load
    # balancers, gp2 migrations, ...) are never silently hidden
    bucketed = {id(r) for r in ec2_recs + ebs_recs + eip_recs + snap_recs}
    other_recs = [r for r in recommendations if id(r) not in bucketed]

    # Approvals waiting out their grace period (cancellable)
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.execute_after,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type,
               CEIL(EXTRACT(EPOCH FROM r.execute_after - NOW()) / 86400)::int
                   AS days_left
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'scheduled'
        ORDER BY r.execute_after
        LIMIT 100
    """)
    scheduled_recs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'scheduled'")
    scheduled_total_count = cursor.fetchone()["n"]

    # Remediations awaiting human review as a Terraform PR
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.pr_url,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pr_open'
        ORDER BY r.estimated_monthly_savings_eur DESC
        LIMIT 100
    """)
    pr_open_recs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'pr_open'")
    pr_open_total_count = cursor.fetchone()["n"]

    # Manual-review recommendations the human confirmed but hasn't
    # necessarily deleted yet — wasteless never touches AWS for these, so
    # they need their own visible "still on you" section, not just a
    # History entry that reads like something already happened.
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.applied_at,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'approved_manual'
        ORDER BY r.applied_at
        LIMIT 100
    """)
    approved_manual_recs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'approved_manual'")
    approved_manual_total_count = cursor.fetchone()["n"]

    # Distinguishes "the collector never ran" from "it ran and everything got
    # resolved" — an empty pending list means very different things, and the
    # generic placeholder used to claim the collector hadn't run even when
    # waste_detected already held resolved history (dismissed/applied/
    # approved/obsolete).
    cursor.execute("SELECT EXISTS (SELECT 1 FROM waste_detected) AS exists_flag")
    has_waste_history = cursor.fetchone()["exists_flag"]

    cursor.close()

    return templates.TemplateResponse(
        request,
        "recommendations.html",
        context={
            "has_waste_history": has_waste_history,
            "pr_open_recs": pr_open_recs,
            "pr_open_total_count": pr_open_total_count,
            "scheduled_total_count": scheduled_total_count,
            "approved_manual_recs": approved_manual_recs,
            "approved_manual_total_count": approved_manual_total_count,
            "recommendations": recommendations,
            "ec2_recs": ec2_recs,
            "ebs_recs": ebs_recs,
            "eip_recs": eip_recs,
            "snap_recs": snap_recs,
            "other_recs": other_recs,
            "scheduled_recs": scheduled_recs,
            "total_count": total_count,
            "total_savings": total_savings,
            "avg_confidence": avg_confidence,
            "type_filter": type_filter,
            "min_savings": min_savings,
            "min_confidence": min_confidence,
        },
    )


@router.get("/api/recommendations")
def api_recommendations(
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0,
    limit: int = 100,
):
    """Get recommendations as JSON."""
    cursor = conn.cursor()

    query = """
        SELECT
            r.id,
            r.recommendation_type,
            w.resource_id,
            r.estimated_monthly_savings_eur,
            w.confidence_score,
            r.action_required,
            r.status,
            r.created_at,
            w.metadata->>'instance_type' as instance_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending'
    """
    params = []

    if type_filter != "All":
        query += " AND r.recommendation_type = %s"
        params.append(type_filter)

    if min_savings > 0:
        query += " AND r.estimated_monthly_savings_eur >= %s"
        params.append(min_savings)

    if min_confidence > 0:
        query += " AND w.confidence_score >= %s"
        params.append(min_confidence)

    query += f" ORDER BY r.estimated_monthly_savings_eur DESC LIMIT {limit}"

    cursor.execute(query, params if params else None)
    results = cursor.fetchall()
    cursor.close()

    return {"recommendations": results, "count": len(results)}


@router.post("/api/recommendations/{rec_id}/ask")
def ask_about_recommendation(rec_id: int, body: AskQuestionRequest, conn=Depends(get_db)):
    """One-shot AI answer to a question about a specific recommendation.

    Stateless (no conversation history) and scoped to this recommendation's
    own data — same guardrails as the ai_insight generation it sits next to.
    Sync route on purpose: the LLM call blocks up to 20s and must run in
    the threadpool, not on the event loop.
    """
    # src/ is a package importable from the repo root, not from ui/ — same
    # sys.path trick as ui/utils/remediator.py's backend integration.
    import sys

    backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from src.core.llm import answer_question

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.action_required, r.estimated_monthly_savings_eur,
               w.resource_type, w.confidence_score, w.metadata
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.id = %s
    """,
        (rec_id,),
    )
    row = cursor.fetchone()
    cursor.close()

    if row is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    answer = answer_question(
        question,
        row["action_required"],
        row["resource_type"],
        row["estimated_monthly_savings_eur"],
        row["confidence_score"],
        metadata,
        conn=conn,
    )
    if answer is None:
        return JSONResponse(
            {"answer": None, "error": "AI is not configured or the request failed"},
            status_code=503,
        )
    return JSONResponse({"answer": answer})


@router.post("/api/actions")
def api_execute_actions(action_request: ActionRequest, conn=Depends(get_db)):
    """Execute actions on recommendations."""
    cursor = conn.cursor()
    results = []

    for rec_id in action_request.recommendation_ids:
        try:
            if action_request.action == "reject":
                # Reject recommendation. Restricted to 'pending' — the only
                # state the UI ever exposes these buttons for — so a stray
                # or future call can't silently overwrite a resolved status
                # (approved/applied/obsolete/pr_open) with 'rejected' and
                # make an already-remediated resource reappear as active waste.
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'rejected', applied_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                reject_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "rejected",
                    **({} if result else {"error": "not in pending state"}),
                }
                results.append(reject_result)
                log_remediation_action("reject", [rec_id], reject_result, dry_run=False)

            elif action_request.action == "dismiss":
                # Permanently stop counting this item as active waste
                # (unlike reject, it drops out of active_waste for good).
                # Also allowed from 'approved_manual': the human confirmed a
                # manual-review recommendation but can still change their
                # mind before actually deleting anything on AWS.
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'dismissed', applied_at = NOW()
                    WHERE id = %s AND status IN ('pending', 'approved_manual')
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                dismiss_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "dismissed",
                    **({} if result else {"error": "not in pending state"}),
                }
                results.append(dismiss_result)
                log_remediation_action("dismiss", [rec_id], dismiss_result, dry_run=False)

            elif action_request.action == "cancel":
                # Cancel a scheduled execution during its grace period
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'pending', execute_after = NULL
                    WHERE id = %s AND status = 'scheduled'
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                if result is not None:
                    # Close out the log entry the scheduling created: left
                    # at 'pending' forever otherwise, History would show a
                    # migration that looks eternally in-flight even though
                    # it was called off.
                    cursor.execute(
                        """
                        UPDATE actions_log
                        SET action_status = 'cancelled', updated_at = NOW()
                        WHERE recommendation_id = %s AND action_status = 'pending'
                    """,
                        (rec_id,),
                    )
                results.append(
                    {
                        "recommendation_id": rec_id,
                        "success": result is not None,
                        "action": "cancelled",
                        **({} if result else {"error": "not in scheduled state"}),
                    }
                )

            elif action_request.action in ("approve", "execute"):
                # Get resource info
                cursor.execute(
                    """
                    SELECT w.resource_id, w.resource_type, r.recommendation_type,
                           w.metadata, w.confidence_score,
                           r.estimated_monthly_savings_eur, r.action_required
                    FROM recommendations r
                    JOIN waste_detected w ON r.waste_id = w.id
                    WHERE r.id = %s
                """,
                    (rec_id,),
                )
                row = cursor.fetchone()

                if row:
                    instance_id = row["resource_id"]
                    resource_type = row["resource_type"]
                    rec_type = row["recommendation_type"]
                    metadata = row["metadata"] or {}
                    action_type = (
                        rec_type.replace("_instance", "")
                        .replace("_volume", "")
                        .replace("_snapshot", "")
                    )
                    aws_success = True
                    aws_error = None

                    # Execute real AWS action if NOT in dry-run mode (read from config, ignore client value)
                    dry_run = _config_manager.get_dry_run()

                    # GitOps routing: recommendations above the terraform_pr
                    # threshold (or of a PR-required type) become a Terraform
                    # PR instead of an AWS action. Not-Terraform-managed
                    # resources return None and take the normal path below.
                    from utils.terraform_pr import maybe_open_pr

                    pr_result = maybe_open_pr(conn, rec_id, row, dry_run)
                    if pr_result is not None:
                        results.append(pr_result)
                        continue

                    # Execution mode comes from the central registry
                    # (ui/utils/action_registry.py) — the guard test forces
                    # every detector's recommendation type to be declared there
                    mode = execution_mode(rec_type)

                    # Per-action opt-out (Settings > Automated actions):
                    # a disabled automated action degrades to manual review —
                    # the decision is recorded, AWS is not touched
                    if mode != "manual" and not _config_manager.get_action_enabled(rec_type):
                        mode = "manual"

                    # Grace period: a real approval is scheduled, not executed.
                    # The grace_executor_job applies it once execute_after is
                    # reached, unless cancelled meanwhile. Dry-run and manual
                    # decisions stay immediate (nothing to delay).
                    grace_days = _config_manager.get_grace_period_days()
                    if grace_days > 0 and not dry_run and mode != "manual":
                        cursor.execute(
                            """
                            UPDATE recommendations
                            SET status = 'scheduled',
                                execute_after = NOW() + make_interval(days => %s)
                            WHERE id = %s AND status = 'pending'
                            RETURNING execute_after
                        """,
                            (grace_days, rec_id),
                        )
                        scheduled = cursor.fetchone()
                        if scheduled is None:
                            results.append(
                                {
                                    "recommendation_id": rec_id,
                                    "success": False,
                                    "error": "not in pending state",
                                }
                            )
                            continue
                        cursor.execute(
                            """
                            INSERT INTO actions_log
                            (resource_id, recommendation_id, resource_type,
                             action_type, action_status, dry_run, action_date, metadata)
                            VALUES (%s, %s, %s, %s, 'pending', false, NOW(), %s)
                        """,
                            (
                                instance_id,
                                rec_id,
                                resource_type,
                                action_type,
                                Json(
                                    {
                                        "grace_period_days": grace_days,
                                        "execute_after": scheduled["execute_after"].isoformat(),
                                    }
                                ),
                            ),
                        )
                        results.append(
                            {
                                "recommendation_id": rec_id,
                                "instance_id": instance_id,
                                "success": True,
                                "scheduled": True,
                                "execute_after": scheduled["execute_after"].isoformat(),
                                "action": rec_type,
                            }
                        )
                        continue

                    # Backend remediators (safeguards + rollback snapshot +
                    # live waste re-verification), in dry-run and real mode alike
                    if mode == "remediator":
                        try:
                            from utils.remediator import RemediatorProxy

                            proxy = RemediatorProxy(dry_run=dry_run)
                            result = proxy.execute_recommendations(conn, [rec_id])[0]
                            result["action"] = rec_type
                        except Exception as e:
                            result = {
                                "recommendation_id": rec_id,
                                "instance_id": instance_id,
                                "success": False,
                                "error": str(e),
                                "action": rec_type,
                            }
                        if not dry_run and not result.get("success"):
                            from utils.notifications import notify_action_failure

                            notify_action_failure(rec_type, instance_id, result.get("error"))
                        results.append(result)
                        continue

                    # The boto3 block below only automates EC2 stop/terminate.
                    # Every other type is manual-review: approving records the
                    # human decision, execution stays manual — attempting AWS
                    # calls here would fail with a misleading "not found".
                    manual_review = mode != "boto3"
                    if not dry_run and not manual_review:
                        aws_success, aws_error = _execute_ec2_boto3(instance_id, rec_type, metadata)
                        if not aws_success:
                            from utils.notifications import notify_action_failure

                            notify_action_failure(rec_type, instance_id, aws_error)

                    # Log action
                    action_status = "success" if (dry_run or aws_success) else "failed"
                    cursor.execute(
                        """
                        INSERT INTO actions_log
                        (resource_id, recommendation_id, resource_type, action_type, action_status, dry_run, action_date, error_message)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                        RETURNING id
                    """,
                        (
                            instance_id,
                            rec_id,
                            resource_type,
                            action_type,
                            action_status,
                            # manual approvals never touch AWS: log them as dry-run
                            dry_run or manual_review,
                            aws_error,
                        ),
                    )

                    # Update recommendation status. A dry-run touches no AWS
                    # resource: leaving the status untouched (still 'pending')
                    # keeps it counted as active waste instead of looking
                    # remediated when nothing was actually done. Manual review
                    # is a real human decision either way, so it always
                    # records the decision — but as 'approved_manual', not
                    # 'approved': nothing has touched the resource yet (the
                    # human still has to delete it themselves), so it must
                    # stay counted in active_waste (see the view's comment)
                    # until sync confirms it's actually gone, same principle
                    # as the dry-run case just below.
                    if manual_review:
                        new_status = "approved_manual"
                    elif dry_run:
                        new_status = None
                    else:
                        new_status = "approved" if aws_success else "pending"

                    if new_status is not None:
                        cursor.execute(
                            """
                            UPDATE recommendations
                            SET status = %s, applied_at = NOW()
                            WHERE id = %s
                        """,
                            (new_status, rec_id),
                        )

                    result_entry = {
                        "recommendation_id": rec_id,
                        "instance_id": instance_id,
                        "success": dry_run or aws_success,
                        "dry_run": dry_run,
                        "manual": manual_review,
                        "action": rec_type,
                    }
                    if aws_error:
                        result_entry["error"] = aws_error
                    results.append(result_entry)
                else:
                    results.append(
                        {
                            "recommendation_id": rec_id,
                            "success": False,
                            "error": "Recommendation not found",
                        }
                    )

        except Exception as e:
            results.append({"recommendation_id": rec_id, "success": False, "error": str(e)})

    conn.commit()
    cursor.close()

    return {"results": results}
