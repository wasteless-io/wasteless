"""Landing page and the home/overview page (/, /landing)."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from state import (
    get_db,
    templates,
    USD_TO_EUR,
    DAYS_PER_MONTH,
    scheduler,
    _aws_status,
    aws_connection_configured,
)

router = APIRouter()


@router.get("/landing", response_class=HTMLResponse)
def landing(request: Request):
    """Public landing page."""
    return templates.TemplateResponse(request, "landing.html")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn=Depends(get_db)):
    """Home page with overview metrics."""
    # Premier lancement : tant qu'AWS n'est pas connecte, la home n'affiche
    # que des zeros — on emmene l'utilisateur au wizard, comme l'ouverture
    # navigateur de wasteless.sh. Seule la racine redirige : les autres
    # pages restent accessibles en direct.
    if not aws_connection_configured():
        return RedirectResponse("/setup", status_code=302)

    cursor = conn.cursor()

    # Fetch metrics in single query
    cursor.execute(
        """
        WITH pending AS (
            SELECT COUNT(*) as pending_count,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) as pending_eur
            FROM recommendations
            WHERE status = 'pending'
        ),
        waste AS (
            SELECT COALESCE(SUM(monthly_waste_eur), 0) as total_waste
            FROM active_waste
        ),
        -- Reviewed-and-declined slice of the active waste (see /dashboard):
        -- kept in the total (the spend is real) but labelled apart.
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        ),
        -- Dénominateur du Waste Rate : dernier mois calendaire complet,
        -- converti en EUR (les writers stockent de l'USD Cost Explorer).
        -- Le mois courant serait un month-to-date partiel face à un waste
        -- exprimé en taux mensuel : ratio mécaniquement surévalué.
        raw_costs AS (
            SELECT COALESCE(SUM(CASE WHEN currency = 'USD' THEN cost * %s
                                     ELSE cost END), 0) as total_spend
            FROM cloud_costs_raw
            WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
              AND usage_date < DATE_TRUNC('month', CURRENT_DATE)
        ),
        savings AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as savings_realized
            FROM savings_realized
        )
        SELECT
            p.pending_count,
            p.pending_eur,
            w.total_waste,
            d.declined_count,
            d.declined_monthly,
            r.total_spend,
            s.savings_realized,
            CASE WHEN r.total_spend > 0
                THEN ROUND((w.total_waste / r.total_spend * 100)::numeric, 1)
                ELSE NULL
            END as waste_rate
        FROM pending p
        CROSS JOIN waste w
        CROSS JOIN declined d
        CROSS JOIN raw_costs r
        CROSS JOIN savings s;
    """,
        (USD_TO_EUR,),
    )
    result = cursor.fetchone()

    # Waste by type (grouped) — active waste only
    cursor.execute("""
        SELECT
            resource_type,
            COUNT(*) as cnt,
            COALESCE(SUM(monthly_waste_eur), 0) as total_eur
        FROM active_waste
        GROUP BY resource_type
        ORDER BY total_eur DESC
    """)
    waste_by_type = cursor.fetchall()

    # Recent activity: mix detections + actions, sorted by time
    cursor.execute("""
        SELECT event_type, event_time, resource_type, cnt, amount, resource_id, action_status, error_message, dry_run
        FROM (
            SELECT
                'detection' as event_type,
                MAX(created_at) as event_time,
                resource_type,
                COUNT(*) as cnt,
                COALESCE(SUM(monthly_waste_eur), 0) as amount,
                NULL::varchar as resource_id,
                NULL::varchar as action_status,
                NULL::text as error_message,
                NULL::boolean as dry_run
            FROM waste_detected
            GROUP BY DATE(created_at), resource_type
            UNION ALL
            SELECT
                'action' as event_type,
                action_date as event_time,
                action_type as resource_type,
                1 as cnt,
                0 as amount,
                resource_id,
                action_status,
                error_message,
                dry_run
            FROM actions_log
        ) combined
        ORDER BY event_time DESC
        LIMIT 20
    """)
    recent_activity = cursor.fetchall()

    # Last sync time
    cursor.execute("""
        SELECT MAX(updated_at) as last_sync FROM waste_detected
    """)
    last_sync_row = cursor.fetchone()
    last_sync = last_sync_row["last_sync"] if last_sync_row else None

    # Daily / Monthly costs (active detected waste)
    cursor.execute("""
        SELECT COALESCE(SUM(monthly_waste_eur), 0) as monthly_cost
        FROM active_waste
    """)
    cost_row = cursor.fetchone()
    monthly_cost = float(cost_row["monthly_cost"]) if cost_row else 0
    daily_cost = monthly_cost / DAYS_PER_MONTH

    # Trend: current waste vs the snapshot taken 7 days ago — same source
    # as the AI briefing, so the KPI delta and the prose never contradict.
    # None (no snapshot yet) means no trend to show, not a zero delta.
    cursor.execute("""
        SELECT SUM(total_eur) as week_ago_eur
        FROM waste_snapshots
        WHERE snapshot_date = CURRENT_DATE - 7
    """)
    trend_row = cursor.fetchone()
    week_ago_eur = trend_row["week_ago_eur"] if trend_row else None
    current_waste = float(result["total_waste"]) if result else 0
    savings_trend = (current_waste - float(week_ago_eur)) if week_ago_eur is not None else None
    # Percentage variant for the KPI banner; None when week-ago base is 0
    # (division impossible), the template then falls back to the € delta.
    savings_trend_pct = (
        savings_trend / float(week_ago_eur) * 100
        if savings_trend is not None and float(week_ago_eur) > 0
        else None
    )

    cursor.close()

    system_health = {
        "db": True,  # we got here, so DB is connected
        "aws": _aws_status.get("reachable"),
        "scheduler": scheduler.running,
    }

    from utils.reports import llm_narrative_available

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "llm_enabled": llm_narrative_available(),
            "metrics": result,
            "waste_by_type": waste_by_type,
            "recent_activity": recent_activity,
            "system_health": system_health,
            "last_sync": last_sync,
            "daily_cost": daily_cost,
            "monthly_cost": monthly_cost,
            "savings_trend": savings_trend,
            "savings_trend_pct": savings_trend_pct,
        },
    )
