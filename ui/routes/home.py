"""Landing page and the home/overview page (/, /landing)."""

from datetime import date, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from state import (
    get_db,
    templates,
    DAYS_PER_MONTH,
    aws_connection_configured,
)

router = APIRouter()


@router.get("/landing", response_class=HTMLResponse)
def landing(request: Request):
    """Public landing page."""
    return templates.TemplateResponse(request, "landing.html")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, conn=Depends(get_db), welcome: str = ""):
    """Home page with overview metrics.

    `welcome=1` is set by the /setup success hand-off: the page then greets
    the user once (the banner's script cleans the URL so a refresh does
    not repeat it)."""
    # Premier lancement : tant qu'AWS n'est pas connecte, la home n'affiche
    # que des zeros — on emmene l'utilisateur au wizard, comme l'ouverture
    # navigateur de wasteless.sh. Seule la racine redirige : les autres
    # pages restent accessibles en direct.
    if not aws_connection_configured():
        return RedirectResponse("/setup", status_code=302)

    cursor = conn.cursor()

    # Fetch metrics in single query
    cursor.execute("""
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
        -- montants bruts tels que facturés (USD, aucune conversion).
        -- Le mois courant serait un month-to-date partiel face à un waste
        -- exprimé en taux mensuel : ratio mécaniquement surévalué.
        raw_costs AS (
            SELECT COALESCE(SUM(cost), 0) as total_spend
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
    """)
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

    # Last collection time — shown in the KPI banner footer and drives
    # the onboarding hint. collection_runs gets one row per "wasteless
    # collect" run even when nothing is detected; waste_detected's
    # created_at covers manual single-detector runs (which don't record
    # a collection_runs row) through the findings they insert.
    # Two traps this query avoids: a run whose steps die on AWS
    # AccessDenied still inserts a collection_runs row (hence the
    # failed_steps filter), and updated_at moves every tick even with
    # AWS broken because detectors re-confirm findings from DB-cached
    # metrics (hence created_at, not updated_at).
    # Staleness is decided in SQL, against the same clock that stamped
    # the rows (Postgres runs in Docker and may not share the host's
    # timezone). 15 min = 3 missed ticks of the 5-minute loop.
    cursor.execute("""
        SELECT last_sync, last_sync < NOW() - INTERVAL '15 minutes' as stale
        FROM (
            SELECT GREATEST(
                (SELECT MAX(created_at) FROM waste_detected),
                (SELECT MAX(created_at) FROM cloud_costs_raw),
                (SELECT MAX(ran_at) FROM collection_runs
                 WHERE failed_steps = '{}')
            ) as last_sync
        ) t
    """)
    last_sync_row = cursor.fetchone()
    last_sync = last_sync_row["last_sync"] if last_sync_row else None
    collection_stale = bool(last_sync_row["stale"]) if last_sync_row else False

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
    # (division impossible), the template then falls back to the $ delta.
    savings_trend_pct = (
        savings_trend / float(week_ago_eur) * 100
        if savings_trend is not None and float(week_ago_eur) > 0
        else None
    )

    # Real bill from Cost Explorer (cloud_costs_raw, collected daily by
    # cost_collector_job): last 30 days rolling — the only window the
    # 30-day collection guarantees complete (a calendar month can have a
    # hole if collection started mid-month). Raw amounts as billed (USD).
    cursor.execute("""
        SELECT COALESCE(SUM(cost), 0) as spend_eur,
               COALESCE(SUM(cost), 0) as spend_usd,
               COUNT(DISTINCT service) as service_count,
               COUNT(*) as row_count
        FROM cloud_costs_raw
        WHERE usage_date >= CURRENT_DATE - 30
    """)
    spend_row = cursor.fetchone()
    aws_spend_30d_eur = (
        float(spend_row["spend_eur"]) if spend_row and spend_row["row_count"] > 0 else None
    )
    aws_spend_30d_usd = float(spend_row["spend_usd"]) if spend_row else 0.0
    aws_service_count = int(spend_row["service_count"]) if spend_row else 0

    # ---- Financial Overview tiles (shared partial _financial_tiles.html):
    # the same five figures as /dashboard. The queries below mirror
    # routes/dashboard.py; the parity test
    # (ui/tests/test_financial_tiles_parity.py) renders both pages and
    # fails if the two ever show different values. ----
    cursor.execute("""
        SELECT COALESCE(SUM(cost)
                        FILTER (WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
                                                    - INTERVAL '1 month'), 0) as spend_eur,
               COUNT(*) FILTER (WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
                                                    - INTERVAL '1 month') as row_count,
               MIN(usage_date) FILTER (WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
                                                           - INTERVAL '1 month') as period_start,
               MAX(usage_date) FILTER (WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
                                                           - INTERVAL '1 month') as period_end,
               COALESCE(SUM(cost)
                        FILTER (WHERE usage_date < DATE_TRUNC('month', CURRENT_DATE)
                                                   - INTERVAL '1 month'), 0) as prev_spend_eur,
               COUNT(*) FILTER (WHERE usage_date < DATE_TRUNC('month', CURRENT_DATE)
                                                   - INTERVAL '1 month') as prev_row_count
        FROM cloud_costs_raw
        WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '2 months'
          AND usage_date < DATE_TRUNC('month', CURRENT_DATE)
    """)
    fin_spend = cursor.fetchone()
    aws_spend_eur = float(fin_spend["spend_eur"]) if fin_spend["row_count"] > 0 else None
    _last_full_month_end = date.today().replace(day=1) - timedelta(days=1)
    aws_spend_month = _last_full_month_end.strftime("%B %Y")
    aws_spend_prev_month = (_last_full_month_end.replace(day=1) - timedelta(days=1)).strftime("%B")
    aws_spend_period = None
    # Partial = the collected days don't span the whole calendar month (a
    # fresh install only has data from its first collection). The tile then
    # keeps the honest "June · 22–30 collected" sub-label, the tooltip drops
    # its "the whole bill" claim, and the MoM delta below is suppressed so a
    # 9-day month never fakes a plunge against a full previous month.
    aws_spend_partial = False
    if aws_spend_eur is not None:
        start, end = fin_spend["period_start"], fin_spend["period_end"]
        aws_spend_period = (
            start.strftime("%-d")
            if start == end
            else f"{start.strftime('%-d')}–{end.strftime('%-d')}"
        )
        aws_spend_partial = (
            start > _last_full_month_end.replace(day=1) or end < _last_full_month_end
        )
    aws_spend_delta_pct = None
    if (
        aws_spend_eur is not None
        and not aws_spend_partial
        and fin_spend["prev_row_count"] > 0
        and float(fin_spend["prev_spend_eur"]) > 0
    ):
        prev = float(fin_spend["prev_spend_eur"])
        aws_spend_delta_pct = (aws_spend_eur - prev) / prev * 100

    cursor.execute("""
        SELECT COALESCE(SUM(cost), 0) as total_eur,
               MIN(usage_date) as first_day,
               MAX(usage_date) as last_day,
               COUNT(*) as row_count
        FROM cloud_costs_raw
    """)
    total_row = cursor.fetchone()
    total_cost_eur = float(total_row["total_eur"]) if total_row["row_count"] > 0 else None
    total_cost_period = None
    if total_cost_eur is not None:
        first, last = total_row["first_day"], total_row["last_day"]
        total_cost_period = (
            first.strftime("%-d %b")
            if first == last
            else f"{first.strftime('%-d %b')} to {last.strftime('%-d %b')}"
        )

    cursor.execute("""
        SELECT CASE WHEN COALESCE(SUM(estimated_savings_eur), 0) > 0
                    THEN SUM(actual_savings_eur) / SUM(estimated_savings_eur) * 100
               END as accuracy_pct
        FROM savings_realized
    """)
    _accuracy = cursor.fetchone()["accuracy_pct"]
    fin_kpis = {
        "potential_monthly": float(result["pending_eur"] or 0),
        "pending_count": result["pending_count"],
        "verified_savings": float(result["savings_realized"] or 0),
        "accuracy_pct": float(_accuracy) if _accuracy is not None else None,
    }

    cursor.execute("""
        SELECT MIN(a.action_date) AS first_action
        FROM actions_log a
        LEFT JOIN savings_realized s ON s.recommendation_id = a.recommendation_id
        WHERE a.action_status = 'success'
          AND a.dry_run = false
          AND a.action_type IN ('stop', 'terminate', 'downsize')
          AND s.id IS NULL
    """)
    verif_row = cursor.fetchone()
    next_verification = (
        verif_row["first_action"] + timedelta(days=7)
        if verif_row and verif_row["first_action"] is not None
        else None
    )

    # Coherence guard (finops invariant, wired to live data). Detector
    # cost/savings estimates are list-price based and independent from the
    # metered Cost Explorer bill, so total active waste can exceed real
    # spend — arithmetically impossible ("on ne peut pas gaspiller plus
    # qu'on ne dépense", src/core/finops_invariants). Rather than let the
    # invariant raise (500) or silently show waste_rate > 100 %, we run it
    # and surface any violation as a banner so the incoherence is visible.
    from core.finops_invariants import waste_percentage, FinOpsInvariantError

    total_waste_monthly = float(result["total_waste"] or 0)
    waste_exceeds_spend = None
    if aws_spend_30d_eur is not None and aws_spend_30d_eur > 0:
        try:
            waste_percentage(total_waste_monthly, aws_spend_30d_eur)
        except FinOpsInvariantError:
            # spend > 0 and waste >= 0 are guaranteed above, so the only
            # way this raises is waste > spend — the case worth flagging.
            waste_exceeds_spend = {
                "waste": total_waste_monthly,
                "spend": aws_spend_30d_eur,
            }

    # Monthly average over ALL collected history (rows accumulate beyond
    # the 30-day collection window as days pass): daily average scaled to
    # a month, same 365/12 convention as everywhere else. Feeds the
    # above/below-average arrow next to the AWS Spend value — only shown
    # once the delta is meaningful (>1%), so the early weeks (history ≈
    # the displayed window, delta mechanically ~0) show no arrow.
    aws_spend_vs_avg = None
    aws_spend_avg_eur = None
    cursor.execute("""
        SELECT COALESCE(SUM(cost), 0) as total_eur,
               MAX(usage_date) - MIN(usage_date) + 1 as days_covered
        FROM cloud_costs_raw
    """)
    avg_row = cursor.fetchone()
    if avg_row and avg_row["days_covered"] and aws_spend_30d_eur is not None:
        aws_spend_avg_eur = float(avg_row["total_eur"]) / avg_row["days_covered"] * DAYS_PER_MONTH
        if aws_spend_avg_eur > 0:
            delta_pct = (aws_spend_30d_eur - aws_spend_avg_eur) / aws_spend_avg_eur
            if delta_pct > 0.01:
                aws_spend_vs_avg = "above"
            elif delta_pct < -0.01:
                aws_spend_vs_avg = "below"

    # Decision queue: the three pending recommendations with the biggest
    # savings, shown under the Daily Briefing (prototype validated
    # 2026-07-19). Same source as Recoverable Now so the two always agree.
    cursor.execute("""
        SELECT w.resource_id,
               r.recommendation_type,
               r.estimated_monthly_savings_eur AS savings,
               w.metadata->>'region' AS region,
               w.metadata->>'instance_type' AS instance_type,
               (w.metadata->>'cpu_avg_7d')::numeric AS cpu_avg
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.status = 'pending'
        ORDER BY r.estimated_monthly_savings_eur DESC, r.id
        LIMIT 3
    """)
    decision_queue = cursor.fetchall()

    cursor.close()

    from utils.reports import llm_narrative_available

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "llm_enabled": llm_narrative_available(),
            "metrics": result,
            "waste_by_type": waste_by_type,
            "recent_activity": recent_activity,
            "last_sync": last_sync,
            "collection_stale": collection_stale,
            "daily_cost": daily_cost,
            "monthly_cost": monthly_cost,
            "savings_trend": savings_trend,
            "savings_trend_pct": savings_trend_pct,
            "aws_spend_30d_eur": aws_spend_30d_eur,
            "aws_spend_30d_usd": aws_spend_30d_usd,
            "aws_spend_avg_eur": aws_spend_avg_eur,
            "aws_spend_vs_avg": aws_spend_vs_avg,
            "aws_service_count": aws_service_count,
            # Shared Financial Overview tiles (_financial_tiles.html)
            "kpis": fin_kpis,
            "next_verification": next_verification,
            "total_cost_eur": total_cost_eur,
            "total_cost_period": total_cost_period,
            "aws_spend_eur": aws_spend_eur,
            "aws_spend_month": aws_spend_month,
            "aws_spend_prev_month": aws_spend_prev_month,
            "aws_spend_delta_pct": aws_spend_delta_pct,
            "aws_spend_period": aws_spend_period,
            "aws_spend_partial": aws_spend_partial,
            "decision_queue": decision_queue,
            "waste_exceeds_spend": waste_exceeds_spend,
            "welcome": welcome == "1",
        },
    )
