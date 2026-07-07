"""Executive dashboard (KPIs + charts) and the JSON endpoints feeding it."""

import os

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from state import get_db, templates, USD_TO_EUR, DAYS_PER_MONTH, TREND_RANGES

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
async def dashboard(request: Request, conn=Depends(get_db), trend: str = "30d"):
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
        cumulative AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as total_saved
            FROM savings_realized
        ),
        pending AS (
            SELECT COUNT(*) as pending_count
            FROM recommendations
            WHERE status = 'pending'
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
            c.total_saved as cumulative_savings,
            p.pending_count,
            l.last_analysis
        FROM metrics m
        CROSS JOIN savings s
        CROSS JOIN waste w
        CROSS JOIN declined d
        CROSS JOIN failed f
        CROSS JOIN cumulative c
        CROSS JOIN pending p
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
        ai_daily_cost = float(llm_row["cost_usd"]) * USD_TO_EUR / llm_row["days_covered"]
        ai_usage = {
            "cost_eur": float(llm_row["cost_usd"]) * USD_TO_EUR,
            "calls": llm_row["calls"],
            "tokens": llm_row["tokens"],
            "models": llm_models,
            "provider": _llm_provider(llm_models),
            "features": [
                {
                    "feature": f["feature"],
                    "calls": f["calls"],
                    "tokens": f["tokens"],
                    "cost_eur": float(f["cost_usd"]) * USD_TO_EUR,
                }
                for f in llm_features
            ],
        }

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
        },
    )


@router.get("/api/dashboard/trend")
async def api_dashboard_trend(conn=Depends(get_db), range: str = "30d"):
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
async def api_dashboard_waste_by_resource(conn=Depends(get_db), range: str = "30d"):
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
async def api_metrics(conn=Depends(get_db)):
    """Get dashboard metrics as JSON."""
    cursor = conn.cursor()
    cursor.execute("""
        WITH metrics AS (
            SELECT
                COALESCE(SUM(estimated_monthly_savings_eur)
                         FILTER (WHERE status = 'pending'), 0) as potential_savings,
                COUNT(*) FILTER (WHERE status = 'pending') as pending_count
            FROM recommendations
        ),
        actions AS (
            SELECT COUNT(*) as success_count
            FROM actions_log
            WHERE action_status = 'success'
        )
        SELECT m.potential_savings, m.pending_count, a.success_count
        FROM metrics m CROSS JOIN actions a;
    """)
    result = cursor.fetchone()
    cursor.close()

    return {
        "potential_savings": float(result["potential_savings"]),
        "pending_count": int(result["pending_count"]),
        "actions_count": int(result["success_count"]),
    }
