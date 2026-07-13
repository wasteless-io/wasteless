"""Action history and audit trail page."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from state import get_db, templates

router = APIRouter()


@router.get("/history", response_class=HTMLResponse)
def history(
    request: Request,
    conn=Depends(get_db),
    status_filter: str = "All",
    action_filter: str = "All",
    days_back: int = 30,
):
    """Action history and audit trail."""
    cursor = conn.cursor()

    # Dry-run actions are simulations, not history: they never appear
    # here. NULL dry_run (legacy rows) counts as a real action.
    where_clause = (
        "WHERE a.action_date >= NOW() - INTERVAL '%s days'"
        " AND NOT COALESCE(a.dry_run, FALSE)"
    )
    params = [days_back]

    if status_filter != "All":
        where_clause += " AND a.action_status = %s"
        params.append(status_filter)

    if action_filter != "All":
        where_clause += " AND a.action_type = %s"
        params.append(action_filter)

    # Total matching the same filters, uncapped — the table itself stays
    # capped at 100 rows below, but the header must say so honestly rather
    # than silently implying those 100 are everything.
    # S608: where_clause is built from constant fragments only — every
    # user-supplied value goes through %s params.
    cursor.execute(
        f"SELECT COUNT(*) AS n FROM actions_log a {where_clause}",  # noqa: S608
        tuple(params),
    )
    total_count = cursor.fetchone()["n"]

    cursor.execute(
        f"""
        SELECT
            a.id,
            a.resource_id,
            a.resource_type,
            a.action_type,
            a.action_status,
            a.dry_run,
            a.action_date,
            a.error_message,
            a.executed_by
        FROM actions_log a
        {where_clause}
        ORDER BY a.action_date DESC LIMIT 100
    """,  # noqa: S608 — where_clause is constant fragments; values are %s params
        tuple(params),
    )
    actions = cursor.fetchall()

    # Summary. Anything that isn't success/failed (pending, blocked, ...)
    # is bucketed as "other" so the three counts always add up to the
    # total shown — a status this doesn't yet know about still gets
    # counted somewhere instead of silently vanishing from the header.
    success_count = sum(1 for a in actions if a["action_status"] == "success")
    failed_count = sum(1 for a in actions if a["action_status"] == "failed")
    other_count = len(actions) - success_count - failed_count

    cursor.close()

    return templates.TemplateResponse(
        request,
        "history.html",
        context={
            "actions": actions,
            "total_count": total_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "other_count": other_count,
            "status_filter": status_filter,
            "action_filter": action_filter,
            "days_back": days_back,
        },
    )
