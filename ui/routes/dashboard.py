"""Executive dashboard (KPIs + charts) and the JSON endpoints feeding it."""

import os
from datetime import date, timedelta

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from state import get_db, templates, DAYS_PER_MONTH, TREND_RANGES

router = APIRouter()


# Provider logo detection: keyword in the model name → logo slug in
# ui/static/providers/. First match wins across the models list.
_PROVIDER_KEYWORDS = [
    ("deepseek", "deepseek"),
    ("claude", "claude"),
    ("anthropic", "anthropic"),
    ("gpt", "openai"),
    ("openai", "openai"),
    ("ollama", "ollama"),
    ("llama", "ollama"),
    ("mistral", "mistral"),
    ("gemini", "gemini"),
]


def _llm_provider(models):
    """Logo slug for the first recognized provider, or None."""
    for model in models:
        name = (model or "").lower()
        for keyword, provider in _PROVIDER_KEYWORDS:
            if keyword in name:
                logo = os.path.join(
                    os.path.dirname(__file__), "..", "static", "providers", f"{provider}.svg"
                )
                if os.path.exists(logo):
                    return provider
    return None


def fetch_waste_trend(cursor, trend: str):
    """Waste trend points from waste_snapshots for a given range key.

    Returns (trend, granularity, subtitle, rows) — daily points for
    7d/30d/90d, monthly averages for 1y.
    """
    if trend not in TREND_RANGES:
        trend = "30d"
    trend_days, granularity, subtitle = TREND_RANGES[trend]
    if granularity == "month":
        cursor.execute(
            """
            SELECT date_trunc('month', snapshot_date)::date as date,
                   AVG(daily_total) as total_waste
            FROM (
                SELECT snapshot_date, COALESCE(SUM(total_eur), 0) as daily_total
                FROM waste_snapshots
                WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
                GROUP BY snapshot_date
            ) d
            GROUP BY 1
            ORDER BY 1
        """,
            (trend_days,),
        )
    else:
        cursor.execute(
            """
            SELECT snapshot_date as date, COALESCE(SUM(total_eur), 0) as total_waste
            FROM waste_snapshots
            WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY snapshot_date
            ORDER BY snapshot_date
        """,
            (trend_days,),
        )
    return trend, granularity, subtitle, cursor.fetchall()


def fetch_waste_by_resource(cursor, range_key: str):
    """Waste by resource type averaged over a range window (waste_snapshots).

    Monthly rate per type = sum over the window / number of snapshot days,
    so a type absent on some days is correctly diluted instead of overstated.
    The resource count shown is the latest snapshot's count in the window.
    Returns (range_key, subtitle, rows).
    """
    if range_key not in TREND_RANGES:
        range_key = "30d"
    days = TREND_RANGES[range_key][0]
    label = {
        "7d": "last 7 days",
        "30d": "last 30 days",
        "90d": "last 90 days",
        "1y": "last 12 months",
    }[range_key]
    cursor.execute(
        """
        WITH win AS (
            SELECT snapshot_date, resource_type, total_eur, resource_count
            FROM waste_snapshots
            WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
        ),
        days AS (
            SELECT COUNT(DISTINCT snapshot_date) AS n FROM win
        ),
        latest AS (
            SELECT DISTINCT ON (resource_type) resource_type, resource_count
            FROM win
            ORDER BY resource_type, snapshot_date DESC
        )
        SELECT w.resource_type,
               SUM(w.total_eur) / NULLIF((SELECT n FROM days), 0) AS total_eur,
               l.resource_count AS cnt,
               (SELECT MIN(snapshot_date) FROM win) AS period_start,
               (SELECT MAX(snapshot_date) FROM win) AS period_end
        FROM win w
        JOIN latest l USING (resource_type)
        GROUP BY w.resource_type, l.resource_count
        ORDER BY total_eur DESC
    """,
        (days,),
    )
    rows = cursor.fetchall()

    # Show the dates actually covered: fresh installs have less history
    # than the theoretical window, and "last 30 days" alone would overclaim
    subtitle = f"Avg monthly waste · {label}"
    if rows:
        start, end = rows[0]["period_start"], rows[0]["period_end"]
        if start == end:
            subtitle += f" · {end.strftime('%-d %b %Y')}"
        elif start.year == end.year:
            subtitle += f" · {start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"
        else:
            subtitle += f" · {start.strftime('%-d %b %Y')} – {end.strftime('%-d %b %Y')}"
    return range_key, subtitle, rows


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(get_db), trend: str = "30d"):
    """Executive dashboard with KPIs and charts."""
    cursor = conn.cursor()

    # Fetch KPIs including new CTO metrics
    cursor.execute("""
        WITH metrics AS (
            SELECT COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_monthly
            FROM recommendations WHERE status = 'pending'
        ),
        savings AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as verified_savings
            FROM savings_realized
        ),
        waste AS (
            SELECT COUNT(*) as waste_count,
                   COALESCE(SUM(monthly_waste_eur), 0) as active_monthly
            FROM active_waste
        ),
        -- Waste the user reviewed and declined to remediate: still real
        -- spend (the resource keeps costing), but not actionable — shown
        -- separately so "Monthly Waste" never reads as a pending backlog.
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        ),
        failed AS (
            SELECT COUNT(*) as failed_7d
            FROM actions_log
            WHERE action_status = 'failed'
              AND action_date >= NOW() - INTERVAL '7 days'
        ),
        pending AS (
            SELECT COUNT(*) as pending_count
            FROM recommendations
            WHERE status = 'pending'
        ),
        -- Estimation accuracy: verified savings vs what was estimated before
        -- the action, over every Cost Explorer measurement. NULL until the
        -- first verification lands.
        accuracy AS (
            SELECT CASE WHEN COALESCE(SUM(estimated_savings_eur), 0) > 0
                        THEN SUM(actual_savings_eur) / SUM(estimated_savings_eur) * 100
                   END as accuracy_pct,
                   COUNT(*) as verified_count
            FROM savings_realized
        ),
        -- Control-loop queues: what sits between "reviewed" and "applied".
        queued_auto AS (
            SELECT COUNT(*) as scheduled_count,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) as scheduled_monthly
            FROM recommendations WHERE status = 'scheduled'
        ),
        queued_manual AS (
            SELECT COUNT(*) as manual_count,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) as manual_monthly
            FROM recommendations WHERE status = 'approved_manual'
        ),
        applied AS (
            SELECT COUNT(*) as applied_count
            FROM actions_log
            WHERE action_status = 'success'
        ),
        last_scan AS (
            SELECT MAX(updated_at) as last_analysis
            FROM waste_detected
        )
        SELECT
            m.potential_monthly,
            s.verified_savings,
            w.waste_count,
            w.active_monthly,
            d.declined_count,
            d.declined_monthly,
            f.failed_7d,
            p.pending_count,
            a.accuracy_pct,
            a.verified_count,
            qa.scheduled_count,
            qa.scheduled_monthly,
            qm.manual_count,
            qm.manual_monthly,
            ap.applied_count,
            l.last_analysis
        FROM metrics m
        CROSS JOIN savings s
        CROSS JOIN waste w
        CROSS JOIN declined d
        CROSS JOIN failed f
        CROSS JOIN pending p
        CROSS JOIN accuracy a
        CROSS JOIN queued_auto qa
        CROSS JOIN queued_manual qm
        CROSS JOIN applied ap
        CROSS JOIN last_scan l;
    """)
    kpis = cursor.fetchone()

    # Cost of inaction: first detection date + daily burn rate (active waste)
    cursor.execute(
        """
        SELECT
            MIN(detection_date) as first_detection,
            COALESCE(SUM(monthly_waste_eur), 0) / %s as daily_burn
        FROM active_waste
    """,
        (DAYS_PER_MONTH,),
    )
    inaction_row = cursor.fetchone()

    # AWS Spend KPI: last full calendar month from Cost Explorer data
    # (cloud_costs_raw, collected daily by cost_collector_job) — same
    # denominator convention as home's Waste Rate: the current month would
    # be a partial month-to-date and mechanically understate the bill.
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
               COUNT(DISTINCT usage_date)
                   FILTER (WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
                                               - INTERVAL '1 month') as days_covered,
               COALESCE(SUM(cost)
                        FILTER (WHERE usage_date < DATE_TRUNC('month', CURRENT_DATE)
                                                   - INTERVAL '1 month'), 0) as prev_spend_eur,
               COUNT(*) FILTER (WHERE usage_date < DATE_TRUNC('month', CURRENT_DATE)
                                                   - INTERVAL '1 month') as prev_row_count
        FROM cloud_costs_raw
        WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '2 months'
          AND usage_date < DATE_TRUNC('month', CURRENT_DATE)
    """)
    spend_row = cursor.fetchone()
    aws_spend_eur = (
        float(spend_row["spend_eur"]) if spend_row and spend_row["row_count"] > 0 else None
    )
    aws_spend_month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%B %Y")
    # Exact days covered by the collection inside that month: a fresh install
    # only has data from its first collection day, and "June 2026" alone
    # would overclaim (same honesty rule as the resource chart's subtitle).
    # The sub-label reads "June · 17–30 collected", so the period is
    # day numbers only; the month name comes from aws_spend_month.
    aws_spend_period = None
    aws_spend_detail = None
    if aws_spend_eur is not None:
        start, end = spend_row["period_start"], spend_row["period_end"]
        if start == end:
            aws_spend_period = start.strftime("%-d")
        else:
            aws_spend_period = f"{start.strftime('%-d')}–{end.strftime('%-d')}"

        # Per-service breakdown of the same window, for the click-through
        # modal: where the figure comes from, service by service.
        cursor.execute("""
            SELECT service,
                   SUM(cost) as eur
            FROM cloud_costs_raw
            WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
              AND usage_date < DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY service
            ORDER BY eur DESC
        """)
        aws_spend_detail = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days_covered": spend_row["days_covered"],
            "services": [
                {
                    "service": r["service"],
                    "eur": float(r["eur"]),
                    "pct": float(r["eur"]) / aws_spend_eur * 100 if aws_spend_eur else 0,
                }
                for r in cursor.fetchall()
            ],
        }
    # Month-over-month delta, only when both full months have data: a partial
    # first month of collection would fake a huge increase.
    aws_spend_delta_pct = None
    if (
        aws_spend_eur is not None
        and spend_row["prev_row_count"] > 0
        and float(spend_row["prev_spend_eur"]) > 0
    ):
        prev = float(spend_row["prev_spend_eur"])
        aws_spend_delta_pct = (aws_spend_eur - prev) / prev * 100

    # Next best actions: the three highest-value pending recommendations,
    # with the evidence the reviewer needs (confidence, age). Age mirrors the
    # "wasted so far" convention: metadata age_days when older, else
    # days-since-created.
    cursor.execute("""
        SELECT r.id, r.action_required, r.recommendation_type,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type, w.waste_type, w.confidence_score,
               GREATEST(
                   COALESCE((w.metadata->>'age_days')::numeric, 0),
                   EXTRACT(EPOCH FROM (NOW() - w.created_at)) / 86400.0
               )::int as age_days
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending'
        ORDER BY r.estimated_monthly_savings_eur DESC NULLS LAST
        LIMIT 3
    """)
    next_actions = cursor.fetchall()

    # Trend: active-waste totals from waste_snapshots (stable history written
    # by detector runs + one-shot backfill)
    trend, trend_granularity, trend_subtitle, waste_trend = fetch_waste_trend(cursor, trend)

    # Waste cost by resource type, averaged over the default 30-day window
    # (same source as the trend chart so both cards agree)
    resource_range, resource_subtitle, waste_by_resource = fetch_waste_by_resource(cursor, "30d")

    # LLM spend over the last 30 days: totals averaged per day actually
    # covered (dividing by 30 would understate the rate when tracking just
    # started), plus a per-feature breakdown for the AI Spend card
    cursor.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) as cost_usd,
               COUNT(*) as calls,
               COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) as tokens,
               GREATEST(CURRENT_DATE - MIN(called_at::date) + 1, 1) as days_covered
        FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days'
    """)
    llm_row = cursor.fetchone()

    cursor.execute("""
        SELECT feature,
               COUNT(*) as calls,
               COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) as tokens,
               COALESCE(SUM(cost_usd), 0) as cost_usd
        FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days'
        GROUP BY feature
        ORDER BY cost_usd DESC
    """)
    llm_features = cursor.fetchall()

    cursor.execute("""
        SELECT DISTINCT model FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days' AND model IS NOT NULL
        ORDER BY model
    """)
    llm_models = [r["model"] for r in cursor.fetchall()]

    # Already burned: cumulative EUR actually lost, one daily-rate slice per
    # snapshot day (total_eur is a monthly rate, hence /DAYS_PER_MONTH).
    # Backfilled history is a floor: resources cleaned before tracking are
    # invisible.
    cursor.execute(
        """
        WITH daily AS (
            SELECT snapshot_date, SUM(total_eur) / %s AS rate
            FROM waste_snapshots
            GROUP BY snapshot_date
        )
        SELECT
            (SELECT COALESCE(SUM(rate), 0) FROM daily) AS burned,
            (SELECT MIN(snapshot_date) FROM daily) AS since,
            (SELECT rate FROM daily ORDER BY snapshot_date DESC LIMIT 1) AS current_rate,
            (SELECT rate FROM daily WHERE snapshot_date <= CURRENT_DATE - 30
             ORDER BY snapshot_date DESC LIMIT 1) AS rate_30d_ago
    """,
        (DAYS_PER_MONTH,),
    )
    burned_row = cursor.fetchone()

    # Last collect run: flags a banner when steampipe was missing and
    # steps 7-10 (elb/nat/vpc/gp2 detectors) got skipped -- otherwise that
    # warning only ever reached ~/.wasteless.log, never this page.
    cursor.execute("""
        SELECT full_run, skipped_steps, ran_at
        FROM collection_runs
        ORDER BY ran_at DESC
        LIMIT 1
    """)
    last_run = cursor.fetchone()

    cursor.close()

    burned_total = float(burned_row["burned"]) if burned_row else 0
    burned_since = burned_row["since"] if burned_row else None
    burn_delta = None
    if burned_row and burned_row["rate_30d_ago"] is not None:
        burn_delta = float(burned_row["current_rate"]) - float(burned_row["rate_30d_ago"])

    ai_usage = None
    ai_daily_cost = None
    ai_roi = None
    if llm_row and llm_row["calls"]:
        ai_daily_cost = float(llm_row["cost_usd"]) / llm_row["days_covered"]
        ai_usage = {
            "cost_eur": float(llm_row["cost_usd"]),
            "calls": llm_row["calls"],
            "tokens": llm_row["tokens"],
            "models": llm_models,
            "provider": _llm_provider(llm_models),
            "features": [
                {
                    "feature": f["feature"],
                    "calls": f["calls"],
                    "tokens": f["tokens"],
                    "cost_eur": float(f["cost_usd"]),
                }
                for f in llm_features
            ],
        }

    # Waste as a share of the real bill, computed here because the SQL
    # values come back as Decimal and the spend as float
    waste_pct_of_bill = None
    if aws_spend_eur and float(kpis["active_monthly"] or 0) > 0:
        waste_pct_of_bill = float(kpis["active_monthly"]) / aws_spend_eur * 100

    daily_burn = float(inaction_row["daily_burn"]) if inaction_row else 0
    if ai_daily_cost:
        ai_roi = daily_burn / ai_daily_cost
    first_detection = inaction_row["first_detection"] if inaction_row else None

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={
            "kpis": kpis,
            "waste_trend": waste_trend,
            "trend_range": trend,
            "trend_granularity": trend_granularity,
            "trend_subtitle": trend_subtitle,
            "waste_by_resource": waste_by_resource,
            "resource_range": resource_range,
            "resource_subtitle": resource_subtitle,
            "daily_burn": daily_burn,
            "first_detection": first_detection,
            "burned_total": burned_total,
            "burned_since": burned_since,
            "burn_delta": burn_delta,
            "ai_usage": ai_usage,
            "ai_daily_cost": ai_daily_cost,
            "ai_roi": ai_roi,
            "last_run": last_run,
            "aws_spend_eur": aws_spend_eur,
            "aws_spend_month": aws_spend_month,
            "aws_spend_delta_pct": aws_spend_delta_pct,
            "aws_spend_period": aws_spend_period,
            "aws_spend_detail": aws_spend_detail,
            "waste_pct_of_bill": waste_pct_of_bill,
            "next_actions": next_actions,
        },
    )


@router.get("/api/dashboard/trend")
def api_dashboard_trend(conn=Depends(get_db), range: str = "30d"):
    """Waste trend points for a range key — feeds the dashboard chart via AJAX."""
    cursor = conn.cursor()
    trend, granularity, subtitle, rows = fetch_waste_trend(cursor, range)
    cursor.close()
    return {
        "range": trend,
        "granularity": granularity,
        "subtitle": subtitle,
        "points": [{"date": str(r["date"]), "total": float(r["total_waste"] or 0)} for r in rows],
    }


@router.get("/api/dashboard/waste-by-resource")
def api_dashboard_waste_by_resource(conn=Depends(get_db), range: str = "30d"):
    """Waste by resource type for a range key — feeds the bar chart via AJAX."""
    cursor = conn.cursor()
    range_key, subtitle, rows = fetch_waste_by_resource(cursor, range)
    cursor.close()
    return {
        "range": range_key,
        "subtitle": subtitle,
        "items": [
            {
                "resource_type": r["resource_type"],
                "total": float(r["total_eur"] or 0),
                "count": r["cnt"] or 0,
            }
            for r in rows
        ],
    }


@router.get("/api/metrics")
def api_metrics(conn=Depends(get_db)):
    """Get dashboard metrics as JSON."""
    cursor = conn.cursor()
    cursor.execute("""
        WITH metrics AS (
            SELECT
                COALESCE(SUM(estimated_monthly_savings_eur)
                         FILTER (WHERE status = 'pending'), 0) as potential_savings,
                COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                COUNT(*) FILTER (WHERE status = 'approved_manual') as manual_todo_count
            FROM recommendations
        ),
        actions AS (
            SELECT COUNT(*) as success_count
            FROM actions_log
            WHERE action_status = 'success'
        )
        SELECT m.potential_savings, m.pending_count, m.manual_todo_count, a.success_count
        FROM metrics m CROSS JOIN actions a;
    """)
    result = cursor.fetchone()
    cursor.close()

    return {
        "potential_savings": float(result["potential_savings"]),
        "pending_count": int(result["pending_count"]),
        "manual_todo_count": int(result["manual_todo_count"]),
        "actions_count": int(result["success_count"]),
    }
