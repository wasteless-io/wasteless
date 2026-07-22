"""Executive dashboard (KPIs + charts) and the JSON endpoints feeding it."""

import os
from datetime import date, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from schemas import AskQuestionRequest
from state import get_db, templates, CLOUD_REGIONS, DAYS_PER_MONTH, TREND_RANGES

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


def _short_resource_label(resource_id: str) -> str:
    """One-line label for breakdown rows: ARNs collapse to their resource
    name (an ALB ARN becomes wasteless-test-alb-1), plain ids pass through.
    The full id stays available in the row's title attribute."""
    if not resource_id.startswith("arn:"):
        return resource_id
    tail = resource_id.split(":")[-1]
    parts = [p for p in tail.split("/") if p]
    # Load balancer ARNs end in .../loadbalancer/<type>/<name>/<hash>
    if parts and parts[0] == "loadbalancer" and len(parts) >= 3:
        return parts[2]
    return parts[-1] if parts else resource_id


# AWS region -> (friendly name, ISO country id in ui/static/world-map.svg).
# The card colors that country path and hangs the shared card tooltip on
# it; regions missing here (or whose country is absent from the simplified
# map, like Bahrain) still appear in the bar list, just uncolored.
_REGION_GEO = {
    "us-east-1": ("N. Virginia", "us"),
    "us-east-2": ("Ohio", "us"),
    "us-west-1": ("N. California", "us"),
    "us-west-2": ("Oregon", "us"),
    "ca-central-1": ("Montreal", "ca"),
    "sa-east-1": ("Sao Paulo", "br"),
    "eu-west-1": ("Ireland", "ie"),
    "eu-west-2": ("London", "gb"),
    "eu-west-3": ("Paris", "fr"),
    "eu-central-1": ("Frankfurt", "de"),
    "eu-north-1": ("Stockholm", "se"),
    "eu-south-1": ("Milan", "it"),
    "ap-south-1": ("Mumbai", "in"),
    "ap-southeast-1": ("Singapore", "sg"),
    "ap-southeast-2": ("Sydney", "au"),
    "ap-northeast-1": ("Tokyo", "jp"),
    "ap-northeast-2": ("Seoul", "kr"),
    "af-south-1": ("Cape Town", "za"),
    "me-south-1": ("Bahrain", "bh"),
}


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

    Returns (trend, granularity, subtitle, rows), daily points for
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
    rows = cursor.fetchall()

    # Honesty rule: never claim a window longer than what is on screen.
    # When the history is shorter than the range key promises ("Last 12
    # months" with 7 months of data), the subtitle swaps the claim for the
    # dates actually covered, like every other chart on the page.
    if rows:
        first, last = rows[0]["date"], rows[-1]["date"]
        expected_start = date.today() - timedelta(days=trend_days)
        forecast_part = subtitle.split(" · ")[-1]
        if granularity == "month":
            if first > expected_start.replace(day=1):
                if first.year == last.year:
                    label = f"{first.strftime('%b')} – {last.strftime('%b %Y')}"
                else:
                    label = f"{first.strftime('%b %Y')} – {last.strftime('%b %Y')}"
                subtitle = f"{label} · {forecast_part}"
        elif first > expected_start + timedelta(days=1):
            if first.year == last.year:
                label = f"{first.strftime('%-d %b')} – {last.strftime('%-d %b %Y')}"
            else:
                label = f"{first.strftime('%-d %b %Y')} – {last.strftime('%-d %b %Y')}"
            subtitle = f"{label} · {forecast_part}"

    # Honesty rule twin: snapshots older than the first detection ever
    # recorded can only come from the age-based backfill, so say so when
    # the window shows any (fresh installs: the whole curve at first, then
    # the real history takes over day by day).
    if rows:
        cursor.execute("SELECT MIN(created_at)::date AS first_real FROM waste_detected")
        first_real = cursor.fetchone()["first_real"]
        if first_real is not None and rows[0]["date"] < first_real:
            # Short enough to keep the subtitle on one line; the "from
            # resource ages" detail lives in the tooltip (template side)
            subtitle += " · early history reconstructed"
    return trend, granularity, subtitle, rows


def build_waste_heatmap(cursor, weeks: int = 53) -> Dict[str, Any]:
    """GitHub-style daily-waste calendar for the last ~year, from the SAME
    waste_snapshots that feed the trend curve. Returns week columns (each 7
    days Mon..Sun, `None` for future days) with a 0-4 intensity level per day,
    plus the max daily value. The two views read the same data, differently:
    the curve shows the level over time, the calendar shows which days carried
    waste and how much."""
    today = date.today()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    cursor.execute(
        "SELECT snapshot_date, COALESCE(SUM(total_eur), 0) AS total "
        "FROM waste_snapshots WHERE snapshot_date >= %s GROUP BY snapshot_date",
        (start,),
    )
    by_date = {r["snapshot_date"]: float(r["total"]) for r in cursor.fetchall()}
    mx = max(by_date.values(), default=0.0)

    # Honesty (same rule as the trend subtitle): snapshots older than the
    # first real detection are age-based reconstruction, not measured waste.
    # They are marked so the calendar never passes an estimate off as a fact.
    cursor.execute("SELECT MIN(created_at)::date AS first_real FROM waste_detected")
    row = cursor.fetchone()
    first_real = row["first_real"] if row else None

    def level(v: float) -> int:
        if v <= 0 or mx <= 0:
            return 0
        r = v / mx
        return 1 if r < 0.10 else 2 if r < 0.35 else 3 if r < 0.70 else 4

    week_cols = []
    prev_month = None
    reconstructed_days = 0
    for w in range(weeks):
        monday = start + timedelta(weeks=w)
        month = None
        if monday.month != prev_month:
            month = monday.strftime("%b")
            prev_month = monday.month
        days = []
        for wd in range(7):
            day = monday + timedelta(days=wd)
            if day > today:
                days.append(None)
            else:
                v = by_date.get(day, 0.0)
                recon = first_real is not None and day < first_real and v > 0
                if recon:
                    reconstructed_days += 1
                days.append(
                    {
                        "date": day.isoformat(),
                        "value": round(v, 2),
                        "level": level(v),
                        "recon": recon,
                    }
                )
        week_cols.append({"month": month, "days": days})
    return {
        "weeks": week_cols,
        "max": round(mx, 2),
        "days_with_data": len(by_date),
        "reconstructed_days": reconstructed_days,
    }


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
                   END as accuracy_pct
            FROM savings_realized
        ),
        -- Last collection that actually spoke to AWS: same honest source
        -- as the Overview (created_at + healthy collection_runs only) --
        -- never updated_at, which advances every tick even with AWS broken
        -- because detectors re-confirm findings from DB-cached metrics.
        -- Age computed in SQL against the clock that stamped the rows.
        last_scan AS (
            SELECT last_analysis,
                   EXTRACT(EPOCH FROM (NOW()::timestamp - last_analysis)) / 3600.0
                       AS last_scan_hours
            FROM (
                SELECT GREATEST(
                    (SELECT MAX(created_at) FROM waste_detected),
                    (SELECT MAX(created_at) FROM cloud_costs_raw),
                    (SELECT MAX(ran_at) FROM collection_runs
                     WHERE failed_steps = '{}')
                ) AS last_analysis
            ) t
        )
        SELECT
            m.potential_monthly,
            s.verified_savings,
            f.failed_7d,
            p.pending_count,
            a.accuracy_pct,
            l.last_analysis,
            l.last_scan_hours
        FROM metrics m
        CROSS JOIN savings s
        CROSS JOIN failed f
        CROSS JOIN pending p
        CROSS JOIN accuracy a
        CROSS JOIN last_scan l;
    """)
    kpis = cursor.fetchone()

    # Freshness comes from the same SQL clock that stamped the rows;
    # display goes through the localtime filter (DB stamps are UTC-naive).
    last_scan_hours_ago = None
    if kpis["last_scan_hours"] is not None:
        last_scan_hours_ago = int(kpis["last_scan_hours"])

    # Daily burn rate of the active waste: feeds the AI card's ROI line
    cursor.execute(
        """
        SELECT COALESCE(SUM(monthly_waste_eur), 0) / %s as daily_burn
        FROM active_waste
    """,
        (DAYS_PER_MONTH,),
    )
    inaction_row = cursor.fetchone()

    # Active waste broken down by resource type (moved here from the report
    # 2026-07-19): current waste, biggest contributor first.
    cursor.execute("""
        SELECT resource_type,
               COUNT(*) AS cnt,
               COALESCE(SUM(monthly_waste_eur), 0) AS monthly_eur
        FROM active_waste
        GROUP BY resource_type
        ORDER BY monthly_eur DESC
        """)
    waste_by_type = cursor.fetchall()

    # AWS Spend KPI: last full calendar month from Cost Explorer data
    # (cloud_costs_raw, collected daily by cost_collector_job), same
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

    # Distinct services billed over the last 30 rolling days (the only
    # window the daily collection guarantees complete). Same figure the
    # Overview rings used to carry; the tile now lives in Financial
    # Overview.
    cursor.execute("""
        SELECT COUNT(DISTINCT service) AS n
        FROM cloud_costs_raw
        WHERE usage_date >= CURRENT_DATE - 30
        """)
    aws_service_count = int(cursor.fetchone()["n"])

    _last_full_month_end = date.today().replace(day=1) - timedelta(days=1)
    aws_spend_month = _last_full_month_end.strftime("%B %Y")
    # Named month for the MoM delta sub-label ("vs May", not "vs previous month")
    aws_spend_prev_month = (_last_full_month_end.replace(day=1) - timedelta(days=1)).strftime("%B")
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

    # Total Cost KPI: everything Cost Explorer has reported into
    # cloud_costs_raw, all months confounded. Unlike AWS Spend (last full
    # calendar month), this is the cumulative bill over the whole collected
    # window, so the sub-label always states that window (honesty rule).
    cursor.execute("""
        SELECT COALESCE(SUM(cost), 0) as total_eur,
               MIN(usage_date) as first_day,
               MAX(usage_date) as last_day,
               COUNT(*) as row_count,
               COUNT(DISTINCT usage_date) as days_covered
        FROM cloud_costs_raw
    """)
    total_row = cursor.fetchone()
    total_cost_eur = float(total_row["total_eur"]) if total_row["row_count"] > 0 else None
    total_cost_period = None
    total_cost_detail = None
    if total_cost_eur is not None:
        first, last = total_row["first_day"], total_row["last_day"]
        if first == last:
            total_cost_period = first.strftime("%-d %b")
        else:
            total_cost_period = f"{first.strftime('%-d %b')} to {last.strftime('%-d %b')}"

        # Per-service breakdown of the whole window, for the click-through
        # modal: same shape as aws_spend_detail, no date filter.
        cursor.execute("""
            SELECT service, SUM(cost) as eur
            FROM cloud_costs_raw
            GROUP BY service
            ORDER BY eur DESC
        """)
        total_cost_detail = {
            "start": first.isoformat(),
            "end": last.isoformat(),
            "days_covered": total_row["days_covered"],
            "services": [
                {
                    "service": r["service"],
                    "eur": float(r["eur"]),
                    "pct": float(r["eur"]) / total_cost_eur * 100 if total_cost_eur else 0,
                }
                for r in cursor.fetchall()
            ],
        }

    # Monthly cost by service (Cost Explorer console-style stacked chart):
    # every collected month, the 7 biggest services named, the tail folded
    # into "Other" (categorical palette ceiling), partial months starred.
    cost_chart = None
    if total_cost_eur is not None:
        cursor.execute("""
            SELECT DATE_TRUNC('month', usage_date)::date AS month,
                   service,
                   SUM(cost) AS eur,
                   COUNT(DISTINCT usage_date) AS days_covered
            FROM cloud_costs_raw
            GROUP BY 1, 2
            ORDER BY 1
        """)
        rows = cursor.fetchall()
        months = sorted({r["month"] for r in rows})
        totals_by_service: dict = {}
        for r in rows:
            totals_by_service[r["service"]] = totals_by_service.get(r["service"], 0.0) + float(
                r["eur"]
            )
        top_services = [
            s for s, _ in sorted(totals_by_service.items(), key=lambda kv: kv[1], reverse=True)[:7]
        ]
        series_names = top_services + (
            ["Other"] if len(totals_by_service) > len(top_services) else []
        )
        cells: dict = {}
        days_by_month: dict = {}
        for r in rows:
            name = r["service"] if r["service"] in top_services else "Other"
            cells[(r["month"], name)] = cells.get((r["month"], name), 0.0) + float(r["eur"])
            days_by_month[r["month"]] = max(
                days_by_month.get(r["month"], 0), int(r["days_covered"])
            )
        labels = []
        for m in months:
            next_month = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
            days_in_month = (next_month - m).days
            partial = days_by_month.get(m, 0) < days_in_month
            labels.append(m.strftime("%b") + ("*" if partial else ""))
        cost_chart = {
            "labels": labels,
            "series": [
                {
                    "name": name,
                    "data": [round(cells.get((m, name), 0.0), 2) for m in months],
                }
                for name in series_names
            ],
        }

    # Upcoming deadlines, merged and sorted: scheduled executions still in
    # their grace-period veto window (the authoritative, cancellable list
    # lives on /recommendations), plus the first Cost Explorer verification
    # expected (earliest unverified real action + the tracker's 7-day
    # minimum). Dates only; internal mechanics like accrual caps stay in
    # their own modals.
    cursor.execute("""
        SELECT r.recommendation_type, r.execute_after,
               r.estimated_monthly_savings_eur, w.resource_id
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.status = 'scheduled' AND r.execute_after IS NOT NULL
        ORDER BY r.execute_after
        LIMIT 5
    """)
    upcoming = [
        {
            "kind": "execution",
            "when": r["execute_after"],
            "action": (r["recommendation_type"] or "").replace("_", " "),
            "label": _short_resource_label(r["resource_id"]),
            "resource_id": r["resource_id"],
            "amount": float(r["estimated_monthly_savings_eur"] or 0),
        }
        for r in cursor.fetchall()
    ]
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
    next_verification = None
    if verif_row and verif_row["first_action"] is not None:
        next_verification = verif_row["first_action"] + timedelta(days=7)
        upcoming.append(
            {
                "kind": "verification",
                "when": next_verification,
                "action": None,
                "label": "first Cost Explorer verification of applied savings",
                "resource_id": None,
                "amount": None,
            }
        )
    upcoming.sort(key=lambda u: u["when"])

    # Trend: active-waste totals from waste_snapshots (stable history written
    # by detector runs + one-shot backfill)
    trend, trend_granularity, trend_subtitle, waste_trend = fetch_waste_trend(cursor, trend)
    waste_heatmap = build_waste_heatmap(cursor)

    # LLM spend over the last 30 days: totals averaged per day actually
    # covered (dividing by 30 would understate the rate when tracking just
    # started), plus a per-feature breakdown for the AI Spend card
    cursor.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) as cost_usd,
               COUNT(*) as calls,
               COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) as tokens,
               GREATEST(CURRENT_DATE - MIN(called_at::date) + 1, 1) as days_covered,
               MIN(called_at::date) as first_call,
               MAX(called_at::date) as last_call
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

    # Saved so far: what acting through wasteless returned. Each real applied
    # action (success, not dry-run, not a 'start' rollback) accrues its
    # recommendation's estimated monthly rate from the action date until
    # now, cut short by whichever comes first:
    #   - the same resource being started again (a restart undoes the saving);
    #   - the resource's PROVEN lifetime before remediation (metadata
    #     age_days when the detector recorded it, else the observation
    #     window between first detection and the action, floor 1 day).
    # The lifetime cap bounds the counterfactual by history: a NAT gateway
    # that lived one day cannot "save" a year of its price.
    cursor.execute(
        """
        WITH saving_actions AS (
            SELECT a.resource_id, a.action_date,
                   COALESCE(r.estimated_monthly_savings_eur, 0) AS monthly_rate,
                   GREATEST(
                       COALESCE((w.metadata->>'age_days')::numeric, 0),
                       EXTRACT(EPOCH FROM a.action_date - w.detection_date::timestamp)
                           / 86400.0,
                       1
                   ) AS lifetime_days
            FROM actions_log a
            LEFT JOIN recommendations r ON r.id = a.recommendation_id
            LEFT JOIN waste_detected w ON w.id = r.waste_id
            WHERE a.action_status = 'success'
              AND a.dry_run = false
              AND a.action_type <> 'start'
        ),
        accruals AS (
            SELECT s.action_date, s.monthly_rate,
                   LEAST(
                       COALESCE((SELECT MIN(u.action_date)
                                 FROM actions_log u
                                 WHERE u.resource_id = s.resource_id
                                   AND u.action_type = 'start'
                                   AND u.action_status = 'success'
                                   AND u.dry_run = false
                                   AND u.action_date > s.action_date), NOW()),
                       s.action_date + s.lifetime_days * INTERVAL '1 day'
                   ) AS accrual_end
            FROM saving_actions s
        )
        SELECT COALESCE(SUM(monthly_rate / %s
                   * EXTRACT(EPOCH FROM accrual_end - action_date) / 86400.0), 0) AS saved,
               MIN(action_date) AS since,
               COALESCE(SUM(monthly_rate)
                   FILTER (WHERE accrual_end > NOW() - INTERVAL '1 minute'), 0) AS live_monthly,
               COUNT(*) AS n_actions
        FROM accruals
        """,
        (DAYS_PER_MONTH,),
    )
    saved_row = cursor.fetchone()

    # Per-action breakdown of the same accrual, for the click-through
    # modal: which action earned what, and whether it is still counting.
    saved_detail = None
    if saved_row and float(saved_row["saved"]) > 0:
        cursor.execute(
            """
            WITH saving_actions AS (
                SELECT a.resource_id, a.action_type, a.action_date,
                       COALESCE(r.estimated_monthly_savings_eur, 0) AS monthly_rate,
                       GREATEST(
                           COALESCE((w.metadata->>'age_days')::numeric, 0),
                           EXTRACT(EPOCH FROM a.action_date - w.detection_date::timestamp)
                               / 86400.0,
                           1
                       ) AS lifetime_days
                FROM actions_log a
                LEFT JOIN recommendations r ON r.id = a.recommendation_id
                LEFT JOIN waste_detected w ON w.id = r.waste_id
                WHERE a.action_status = 'success'
                  AND a.dry_run = false
                  AND a.action_type <> 'start'
            ),
            accruals AS (
                SELECT s.resource_id, s.action_type, s.action_date, s.monthly_rate,
                       s.lifetime_days,
                       (SELECT MIN(u.action_date)
                        FROM actions_log u
                        WHERE u.resource_id = s.resource_id
                          AND u.action_type = 'start'
                          AND u.action_status = 'success'
                          AND u.dry_run = false
                          AND u.action_date > s.action_date) AS restarted_at,
                       s.action_date + s.lifetime_days * INTERVAL '1 day' AS cap_end,
                       LEAST(
                           COALESCE((SELECT MIN(u.action_date)
                                     FROM actions_log u
                                     WHERE u.resource_id = s.resource_id
                                       AND u.action_type = 'start'
                                       AND u.action_status = 'success'
                                       AND u.dry_run = false
                                       AND u.action_date > s.action_date), NOW()),
                           s.action_date + s.lifetime_days * INTERVAL '1 day'
                       ) AS accrual_end
                FROM saving_actions s
            )
            SELECT resource_id, action_type, action_date, monthly_rate,
                   lifetime_days, restarted_at, cap_end,
                   monthly_rate / %s
                       * EXTRACT(EPOCH FROM accrual_end - action_date) / 86400.0 AS accrued,
                   (accrual_end > NOW() - INTERVAL '1 minute') AS still_counting,
                   accrual_end
            FROM accruals
            ORDER BY accrued DESC, action_date DESC
            """,
            (DAYS_PER_MONTH,),
        )
        saved_total_f = float(saved_row["saved"])
        detail_actions = []
        zero_count = 0
        for r in cursor.fetchall():
            accrued = float(r["accrued"])
            if accrued <= 0:
                zero_count += 1
                continue
            stop_reason = None
            if not r["still_counting"]:
                # A stop is either the resource coming back to life, or the
                # lifetime cap: no more credit than the resource provably lived
                stop_reason = (
                    "restart"
                    if r["restarted_at"] is not None and r["restarted_at"] == r["accrual_end"]
                    else "cap"
                )
            detail_actions.append(
                {
                    "resource_id": r["resource_id"],
                    "label": _short_resource_label(r["resource_id"]),
                    "action": r["action_type"].replace("_", " "),
                    "date": r["action_date"].strftime("%b %-d"),
                    "monthly_rate": float(r["monthly_rate"]),
                    "accrued": accrued,
                    "pct": accrued / saved_total_f * 100,
                    "still_counting": r["still_counting"],
                    "lifetime_days": round(float(r["lifetime_days"] or 0)),
                    "cap_until": r["cap_end"].strftime("%b %-d, %H:%M"),
                    "stop_reason": stop_reason,
                    "stopped_on": (
                        None if r["still_counting"] else r["accrual_end"].strftime("%b %-d")
                    ),
                }
            )
        saved_detail = {"actions": detail_actions, "zero_count": zero_count}

    # Pending-decision breakdown for the Recoverable Now click-through
    # modal: what each approval would return, highest savings first.
    recoverable_detail = None
    if (kpis["pending_count"] or 0) > 0:
        cursor.execute("""
            SELECT r.recommendation_type, r.estimated_monthly_savings_eur,
                   w.resource_id, w.confidence_score
            FROM recommendations r
            JOIN waste_detected w ON w.id = r.waste_id
            WHERE r.status = 'pending'
            ORDER BY r.estimated_monthly_savings_eur DESC
        """)
        potential = float(kpis["potential_monthly"] or 0)
        recoverable_detail = {
            "actions": [
                {
                    "resource_id": r["resource_id"],
                    "label": _short_resource_label(r["resource_id"]),
                    "action": (r["recommendation_type"] or "").replace("_", " "),
                    "monthly": float(r["estimated_monthly_savings_eur"] or 0),
                    "pct": (
                        float(r["estimated_monthly_savings_eur"] or 0) / potential * 100
                        if potential
                        else 0
                    ),
                    "confidence": round(float(r["confidence_score"] or 0) * 100),
                }
                for r in cursor.fetchall()
            ]
        }

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

    saved_total = float(saved_row["saved"]) if saved_row else 0
    saved_since = saved_row["since"] if saved_row else None
    saved_monthly = float(saved_row["live_monthly"]) if saved_row else 0
    saved_daily = saved_monthly / DAYS_PER_MONTH
    saved_actions = saved_row["n_actions"] if saved_row else 0

    ai_usage = None
    ai_daily_cost = None
    ai_roi = None
    if llm_row and llm_row["calls"]:
        ai_daily_cost = float(llm_row["cost_usd"]) / llm_row["days_covered"]
        # Actual covered dates, not "last 30 days": tracking may be younger
        # than the window (honesty rule shared with the Total Cost tile)
        first, last = llm_row["first_call"], llm_row["last_call"]
        ai_period = (
            first.strftime("%-d %b")
            if first == last
            else f"{first.strftime('%-d %b')} to {last.strftime('%-d %b')}"
        )
        ai_usage = {
            "cost_eur": float(llm_row["cost_usd"]),
            "period": ai_period,
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

    daily_burn = float(inaction_row["daily_burn"]) if inaction_row else 0
    if ai_daily_cost:
        ai_roi = daily_burn / ai_daily_cost

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={
            "kpis": kpis,
            "waste_by_type": waste_by_type,
            "last_scan_hours_ago": last_scan_hours_ago,
            "waste_trend": waste_trend,
            "waste_heatmap": waste_heatmap,
            "trend_range": trend,
            "trend_granularity": trend_granularity,
            "trend_subtitle": trend_subtitle,
            "saved_total": saved_total,
            "saved_since": saved_since,
            "saved_monthly": saved_monthly,
            "saved_daily": saved_daily,
            "saved_actions": saved_actions,
            "saved_detail": saved_detail,
            "recoverable_detail": recoverable_detail,
            "ai_usage": ai_usage,
            "ai_daily_cost": ai_daily_cost,
            "ai_roi": ai_roi,
            "last_run": last_run,
            "aws_spend_eur": aws_spend_eur,
            "aws_service_count": aws_service_count,
            "aws_spend_month": aws_spend_month,
            "aws_spend_prev_month": aws_spend_prev_month,
            "aws_spend_delta_pct": aws_spend_delta_pct,
            "aws_spend_period": aws_spend_period,
            "aws_spend_detail": aws_spend_detail,
            "total_cost_eur": total_cost_eur,
            "total_cost_period": total_cost_period,
            "total_cost_detail": total_cost_detail,
            "cost_chart": cost_chart,
            "upcoming": upcoming,
            "next_verification": next_verification,
        },
    )


@router.post("/api/dashboard/cost-chat")
def chat_about_costs(body: AskQuestionRequest, conn=Depends(get_db)):
    """One-shot AI answer to a question about the customer's AWS cloud costs.

    Powers the Cost Analyst console on the dashboard (same contract as the
    Recommendations chat). Stateless; sync route because the LLM call blocks
    and must run in the threadpool.
    """
    from core.llm import LLMUnavailableError, answer_cost_question, is_enabled
    from utils.cost_report import format_cost_context

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    if not is_enabled():
        return JSONResponse(
            {
                "answer": None,
                "error": "AI insights are not configured. Set a model and API key "
                "in Settings → AI Insights (LLM)",
            },
            status_code=503,
        )
    try:
        answer = answer_cost_question(question, format_cost_context(conn), conn=conn)
    except LLMUnavailableError as e:
        return JSONResponse({"answer": None, "error": str(e)}, status_code=502)
    return JSONResponse({"answer": answer})


@router.get("/api/dashboard/trend")
def api_dashboard_trend(conn=Depends(get_db), range: str = "30d"):
    """Waste trend points for a range key, feeds the dashboard chart via AJAX."""
    cursor = conn.cursor()
    trend, granularity, subtitle, rows = fetch_waste_trend(cursor, range)
    cursor.close()
    return {
        "range": trend,
        "granularity": granularity,
        "subtitle": subtitle,
        "points": [{"date": str(r["date"]), "total": float(r["total_waste"] or 0)} for r in rows],
    }


# In-process cache for the Resources-by-Region sweep: 4 regions x ~6 AWS
# list calls is too slow (and too chatty) to run on every dashboard load.
_region_inventory_cache: Dict[str, Any] = {"at": None, "data": None}
_REGION_INVENTORY_TTL = 600  # seconds


@router.get("/api/dashboard/resources-by-region")
def api_resources_by_region():
    """Live resource counts per region for the dashboard mini-map.

    Sweeps CLOUD_REGIONS in parallel (instances, volumes, EIPs, NAT
    gateways, load balancers, RDS), cached in-process for 10 minutes so
    the dashboard itself stays a pure-Postgres page; the card loads this
    endpoint after render.
    """
    from datetime import datetime

    now = datetime.now()
    if (
        _region_inventory_cache["data"] is not None
        and (now - _region_inventory_cache["at"]).total_seconds() < _REGION_INVENTORY_TTL
    ):
        return _region_inventory_cache["data"]

    from state import check_aws_reachable

    if not check_aws_reachable():
        return {"available": False, "regions": [], "total": 0}

    from concurrent.futures import ThreadPoolExecutor

    from utils.aws_clients import get_client

    def count_region(region: str):
        counts: Dict[str, int] = {}
        try:
            ec2 = get_client("ec2", region=region)
            n = 0
            for page in ec2.get_paginator("describe_instances").paginate():
                for res in page.get("Reservations", []):
                    n += sum(
                        1 for i in res.get("Instances", []) if i["State"]["Name"] != "terminated"
                    )
            counts["instances"] = n
            counts["volumes"] = sum(
                len(p.get("Volumes", [])) for p in ec2.get_paginator("describe_volumes").paginate()
            )
            counts["elastic_ips"] = len(ec2.describe_addresses().get("Addresses", []))
            counts["nat_gateways"] = sum(
                1
                for g in ec2.describe_nat_gateways().get("NatGateways", [])
                if g.get("State") in ("available", "pending")
            )
            elbv2 = get_client("elbv2", region=region)
            counts["load_balancers"] = sum(
                len(p.get("LoadBalancers", []))
                for p in elbv2.get_paginator("describe_load_balancers").paginate()
            )
            rds = get_client("rds", region=region)
            counts["rds_instances"] = sum(
                len(p.get("DBInstances", []))
                for p in rds.get_paginator("describe_db_instances").paginate()
            )
        except Exception as e:
            print(f"Region inventory error {region}: {e}")
            return region, None
        return region, counts

    with ThreadPoolExecutor(max_workers=len(CLOUD_REGIONS)) as pool:
        results = list(pool.map(count_region, CLOUD_REGIONS))

    regions = []
    total = 0
    for region, counts in results:
        if not counts:
            continue
        n = sum(counts.values())
        if n == 0:
            continue
        total += n
        geo = _REGION_GEO.get(region)
        regions.append(
            {
                "region": region,
                "name": geo[0] if geo else region,
                "country": geo[1] if geo else None,
                "count": n,
                "breakdown": counts,
            }
        )
    for r in regions:
        r["pct"] = round(r["count"] / total * 100) if total else 0
    regions.sort(key=lambda r: -r["count"])

    data = {"available": True, "total": total, "regions": regions}
    _region_inventory_cache["at"] = now
    _region_inventory_cache["data"] = data
    return data


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
                COUNT(*) FILTER (WHERE status = 'approved_manual') as manual_todo_count,
                COUNT(*) FILTER (WHERE status = 'scheduled'
                                 AND execute_after <= NOW() + INTERVAL '48 hours')
                    as imminent_count
            FROM recommendations
        ),
        actions AS (
            SELECT COUNT(*) as success_count
            FROM actions_log
            WHERE action_status = 'success'
        )
        SELECT m.potential_savings, m.pending_count, m.manual_todo_count,
               m.imminent_count, a.success_count
        FROM metrics m CROSS JOIN actions a;
    """)
    result = cursor.fetchone()
    cursor.close()

    return {
        "potential_savings": float(result["potential_savings"]),
        "pending_count": int(result["pending_count"]),
        "manual_todo_count": int(result["manual_todo_count"]),
        "imminent_count": int(result["imminent_count"]),
        "actions_count": int(result["success_count"]),
    }
