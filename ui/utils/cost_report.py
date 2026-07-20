"""Cloud cost statement for the Reports page.

Reports is an accounting view of what the infrastructure actually costs, per
day/week/month/year, broken down by AWS service -- not a waste report. Every
figure comes from cloud_costs_raw (AWS Cost Explorer, UnblendedCost, daily,
grouped by SERVICE). All amounts USD.

Resource-level detail (per instance/volume) is intentionally absent: the
collector groups by SERVICE only, and resource-level Cost Explorer data is a
paid AWS opt-in. See the methodology note in the report.
"""

import calendar
import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

GRANULARITIES = ("day", "week", "month", "year")


def _first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _last_of_month(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def resolve_cost_period(granularity: str, anchor: date) -> Tuple[date, date, str, date, date, str]:
    """(start, end, label, prev_start, prev_end, bucket) for a granularity
    around `anchor`. `end` is inclusive. `bucket` is how the trend is sliced:
    'day' (bars per day) or 'month' (bars per month)."""
    g = granularity if granularity in GRANULARITIES else "month"

    if g == "day":
        start = end = anchor
        prev_start = prev_end = anchor - timedelta(days=1)
        label = anchor.strftime("%d %b %Y")
        bucket = "day"
    elif g == "week":
        start = anchor - timedelta(days=anchor.weekday())  # Monday
        end = start + timedelta(days=6)
        prev_start, prev_end = start - timedelta(days=7), end - timedelta(days=7)
        label = f"Week of {start.strftime('%d %b %Y')}"
        bucket = "day"
    elif g == "year":
        start, end = date(anchor.year, 1, 1), date(anchor.year, 12, 31)
        prev_start, prev_end = date(anchor.year - 1, 1, 1), date(anchor.year - 1, 12, 31)
        label = str(anchor.year)
        bucket = "month"
    else:  # month
        start, end = _first_of_month(anchor), _last_of_month(anchor)
        prev_last = start - timedelta(days=1)
        prev_start, prev_end = _first_of_month(prev_last), prev_last
        label = anchor.strftime("%B %Y")
        bucket = "day"
    return start, end, label, prev_start, prev_end, bucket


def shift_anchor(granularity: str, anchor: date, direction: int) -> date:
    """Anchor for the previous (-1) or next (+1) period at this granularity."""
    g = granularity if granularity in GRANULARITIES else "month"
    if g == "day":
        return anchor + timedelta(days=direction)
    if g == "week":
        return anchor + timedelta(days=7 * direction)
    if g == "year":
        return date(anchor.year + direction, anchor.month, 1)
    # month
    if direction < 0:
        prev = _first_of_month(anchor) - timedelta(days=1)
        return _first_of_month(prev)
    nxt = _last_of_month(anchor) + timedelta(days=1)
    return nxt


def _scalar(row: Any, default: float = 0.0) -> float:
    if row is None:
        return default
    v = list(row.values())[0] if isinstance(row, dict) else row[0]
    return float(v) if v is not None else default


def latest_cost_date(conn) -> Optional[date]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(usage_date) AS d FROM cloud_costs_raw;")
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return None
    return row["d"] if isinstance(row, dict) else row[0]


def _period_total(conn, start: date, end: date) -> float:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM cloud_costs_raw "
            "WHERE usage_date >= %s AND usage_date <= %s;",
            (start, end),
        )
        return _scalar(cur.fetchone())
    finally:
        cur.close()


def _by_service(conn, start: date, end: date) -> Dict[str, float]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT service, COALESCE(SUM(cost), 0) AS usd FROM cloud_costs_raw "
            "WHERE usage_date >= %s AND usage_date <= %s GROUP BY service;",
            (start, end),
        )
        out = {}
        for row in cur.fetchall():
            svc, usd = (row["service"], row["usd"]) if isinstance(row, dict) else (row[0], row[1])
            out[svc or "Unattributed"] = float(usd or 0)
        return out
    finally:
        cur.close()


def _trend(conn, start: date, end: date, bucket: str):
    """[(label, usd)] over the period, one point per day or per month."""
    cur = conn.cursor()
    try:
        if bucket == "month":
            cur.execute(
                "SELECT date_trunc('month', usage_date)::date AS b, "
                "COALESCE(SUM(cost), 0) AS usd FROM cloud_costs_raw "
                "WHERE usage_date >= %s AND usage_date <= %s GROUP BY b ORDER BY b;",
                (start, end),
            )
            rows = cur.fetchall()
            fmt = "%b"
        else:
            cur.execute(
                "SELECT usage_date AS b, COALESCE(SUM(cost), 0) AS usd FROM cloud_costs_raw "
                "WHERE usage_date >= %s AND usage_date <= %s GROUP BY b ORDER BY b;",
                (start, end),
            )
            rows = cur.fetchall()
            fmt = "%d %b" if (end - start).days > 7 else "%a"
        out = []
        for row in rows:
            b, usd = (row["b"], row["usd"]) if isinstance(row, dict) else (row[0], row[1])
            out.append((b.strftime(fmt), float(usd or 0)))
        return out
    finally:
        cur.close()


def _trend_scale(trend) -> float:
    """A robust y-scale for the bar chart: cap at the largest *non-spike* value
    so a single lumpy day (monthly fees, or tax settled on the 1st) does not
    flatten every other bar, while genuine day-to-day variation stays visible.
    A value is a spike only if it exceeds 4x the median; those bars are drawn
    clipped with a marker, exact value in the tooltip, so nothing is hidden."""
    vals = sorted(v for _, v in trend if v is not None)
    if not vals:
        return 0.0
    median = vals[len(vals) // 2]
    if median <= 0:  # sparse/zero-heavy data: just use the true max
        return float(vals[-1])
    non_spike = [v for v in vals if v <= median * 4]
    cap = non_spike[-1] if non_spike else vals[-1]
    return float(cap if cap > 0 else vals[-1])


def collect_cost_report(conn, granularity: str, anchor: date) -> Dict[str, Any]:
    """Full cost statement for a period. Deterministic SQL over cloud_costs_raw."""
    g = granularity if granularity in GRANULARITIES else "month"
    start, end, label, prev_start, prev_end, bucket = resolve_cost_period(g, anchor)
    last_data = latest_cost_date(conn)

    total = _period_total(conn, start, end)
    prev_total = _period_total(conn, prev_start, prev_end)
    delta = total - prev_total
    delta_pct = (delta / prev_total * 100) if prev_total > 0 else None

    cur_svc = _by_service(conn, start, end)
    prev_svc = _by_service(conn, prev_start, prev_end)
    services = []
    for svc, usd in sorted(cur_svc.items(), key=lambda kv: kv[1], reverse=True):
        p = prev_svc.get(svc, 0.0)
        services.append(
            {
                "service": svc,
                "usd": usd,
                "pct": (usd / total * 100) if total > 0 else 0.0,
                "prev_usd": p,
                "delta_pct": ((usd - p) / p * 100) if p > 0 else None,
            }
        )

    # For a day view, a single bar is useless -- show the trailing 14 days as
    # context instead.
    if g == "day":
        trend = _trend(conn, anchor - timedelta(days=13), anchor, "day")
    else:
        trend = _trend(conn, start, end, bucket)
    trend_scale = _trend_scale(trend)

    # Elapsed days with data, for a fair daily average / run-rate on a period
    # that Cost Explorer has not finished reporting.
    complete = last_data is not None and end <= last_data
    effective_end = end if complete else (last_data or start)
    if effective_end < start:
        effective_end = start
    elapsed_days = (effective_end - start).days + 1
    daily_avg = (total / elapsed_days) if elapsed_days > 0 else 0.0

    return {
        "granularity": g,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "label": label},
        "anchor": anchor.isoformat(),
        "prev_anchor": shift_anchor(g, anchor, -1).isoformat(),
        "next_anchor": shift_anchor(g, anchor, +1).isoformat(),
        "total_usd": total,
        "prev_total_usd": prev_total,
        "delta_usd": delta,
        "delta_pct": (round(delta_pct, 1) if delta_pct is not None else None),
        "services": services,
        "service_count": len(services),
        "top_service": services[0] if services else None,
        "trend": trend,
        "trend_scale": trend_scale,
        "trend_clipped": any(v > trend_scale for _, v in trend),
        "daily_avg_usd": daily_avg,
        "annual_run_rate_usd": daily_avg * 365,
        "complete": complete,
        "last_data_date": last_data.isoformat() if last_data else None,
        "has_data": total > 0,
    }


def format_cost_statement(report: Dict[str, Any]) -> str:
    """Plain-text (markdown) cost statement from a report dict. No LLM."""
    p = report["period"]
    lines = [
        f"Wasteless — Cloud Cost Statement ({p['label']})",
        "=" * 60,
        "",
        f"Period: {p['start']} to {p['end']} ({report['granularity']})",
        f"Total cost: {report['total_usd']:.2f} USD",
    ]
    if report["delta_pct"] is not None:
        sign = "+" if report["delta_usd"] >= 0 else ""
        lines.append(
            f"vs previous {report['granularity']}: {sign}{report['delta_usd']:.2f} USD "
            f"({sign}{report['delta_pct']}%) — was {report['prev_total_usd']:.2f} USD"
        )
    lines.append(f"Daily average: {report['daily_avg_usd']:.2f} USD")
    lines.append(f"Annual run-rate: {report['annual_run_rate_usd']:.2f} USD")
    if not report["complete"]:
        lines.append("(period not fully reported by Cost Explorer yet)")
    lines += ["", "Cost by service:"]
    for s in report["services"]:
        lines.append(f"  - {s['service']}: {s['usd']:.2f} USD ({s['pct']:.0f}%)")
    if not report["services"]:
        lines.append("  (no cost data for this period)")
    lines += [
        "",
        f"Source: AWS Cost Explorer (UnblendedCost), last updated {report['last_data_date']}.",
        "Service-level granularity; amounts in USD.",
    ]
    return "\n".join(lines)


def _kv(row):
    """First two values of a DB row (dict or tuple)."""
    if isinstance(row, dict):
        vals = list(row.values())
        return vals[0], vals[1]
    return row[0], row[1]


def cost_analyst(conn, months_back: int = 6) -> Dict[str, Any]:
    """Diagnostic read of the cost trend from cloud_costs_raw: monthly totals,
    month-over-month move, the top service driver, an anomaly (spike) flag, and
    the annual run-rate. Deterministic -- the AI only comments this, it never
    invents a figure. All amounts USD."""
    last = latest_cost_date(conn) or date.today()
    idx = last.year * 12 + (last.month - 1) - (months_back - 1)
    window_start = date(idx // 12, idx % 12 + 1, 1)

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date_trunc('month', usage_date)::date AS m, COALESCE(SUM(cost),0) AS usd "
            "FROM cloud_costs_raw WHERE usage_date >= %s AND usage_date <= %s GROUP BY m ORDER BY m;",
            (window_start, last),
        )
        months = []
        for row in cur.fetchall():
            mo, usd = _kv(row)
            months.append(
                {
                    "month": mo,
                    "label": mo.strftime("%b %Y"),
                    "short": mo.strftime("%b"),
                    "usd": float(usd),
                }
            )

        cur.execute(
            "SELECT service, COALESCE(SUM(cost),0) usd FROM cloud_costs_raw "
            "WHERE usage_date >= %s AND usage_date <= %s GROUP BY service ORDER BY 2 DESC;",
            (window_start, last),
        )
        svcs = [(_kv(r)) for r in cur.fetchall()]

        # Peak month + its drivers.
        peak = max(months, key=lambda x: x["usd"]) if months else None
        peak_drivers = []
        if peak:
            cur.execute(
                "SELECT service, COALESCE(SUM(cost),0) usd FROM cloud_costs_raw "
                "WHERE usage_date >= %s AND usage_date < (%s::date + INTERVAL '1 month') "
                "GROUP BY service ORDER BY 2 DESC LIMIT 2;",
                (peak["month"], peak["month"]),
            )
            peak_drivers = [
                {"service": s, "usd": float(u)} for s, u in (_kv(r) for r in cur.fetchall())
            ]

        # Annual run-rate from the trailing 30 days of billed spend.
        cur.execute(
            "SELECT COALESCE(SUM(cost),0) FROM cloud_costs_raw "
            "WHERE usage_date > %s::date - INTERVAL '30 days' AND usage_date <= %s;",
            (last, last),
        )
        last30 = _scalar(cur.fetchone())
    finally:
        cur.close()

    total = sum(m["usd"] for m in months)
    current = months[-1] if months else None
    previous = months[-2] if len(months) >= 2 else None
    # The current month is partial when Cost Explorer has not reported through
    # its last day: a raw month-over-month % would then be misleading, so we
    # flag it and the readout says "so far" and drops the comparison.
    current_partial = bool(current and last < _last_of_month(current["month"]))
    mom = None
    if current and previous and previous["usd"] > 0 and not current_partial:
        mom = round((current["usd"] - previous["usd"]) / previous["usd"] * 100, 1)

    top = None
    if svcs and total > 0:
        s, u = svcs[0]
        top = {"service": s, "usd": float(u), "pct": round(float(u) / total * 100)}

    # Spike: a month whose cost is >= 2x the median of the *other* months.
    is_spike = False
    peak_ratio = None
    if peak and len(months) >= 3:
        others = sorted(m["usd"] for m in months if m is not peak)
        median = others[len(others) // 2] if others else 0
        if median > 0:
            peak_ratio = round(peak["usd"] / median, 1)
            is_spike = peak["usd"] >= 2 * median

    return {
        "months": [{"label": m["label"], "short": m["short"], "usd": m["usd"]} for m in months],
        "window_months": len(months),
        "current": {"label": current["label"], "usd": current["usd"]} if current else None,
        "current_partial": current_partial,
        "previous": {"label": previous["label"], "usd": previous["usd"]} if previous else None,
        "mom_delta_pct": mom,
        "top_service": top,
        "service_count": len(svcs),
        "peak": (
            {
                "label": peak["label"],
                "usd": peak["usd"],
                "ratio": peak_ratio,
                "drivers": peak_drivers,
                "is_spike": is_spike,
            }
            if peak
            else None
        ),
        "annual_run_rate_usd": float(last30) * 365 / 30,
        "last_data_date": last.isoformat(),
        "has_data": total > 0,
    }


def format_cost_analyst(d: Dict[str, Any]) -> str:
    """Deterministic analyst readout (always available, even without the LLM).
    Short sentences: movement, drivers, projection, anomaly."""
    if not d.get("has_data"):
        return "No cost recorded yet. Cost data is collected daily from AWS Cost Explorer (24-48h lag)."
    parts = []
    cur = d["current"]
    if cur:
        if d.get("current_partial"):
            s = f"{cur['label']} spend so far is ${cur['usd']:.2f} (partial month)"
        else:
            s = f"{cur['label']} spend is ${cur['usd']:.2f}"
            if d["mom_delta_pct"] is not None and d["previous"]:
                sign = "+" if d["mom_delta_pct"] >= 0 else ""
                s += f", {sign}{d['mom_delta_pct']}% vs {d['previous']['label']} (${d['previous']['usd']:.2f})"
        parts.append(s + ".")
    pk = d.get("peak")
    if pk and pk["is_spike"]:
        drv = ", ".join(x["service"] for x in pk["drivers"]) or "a few services"
        parts.append(
            f"{pk['label']} stands out at ${pk['usd']:.2f} (~{pk['ratio']}x the median), driven by {drv}."
        )
    top = d.get("top_service")
    if top:
        parts.append(
            f"Largest cost driver over {d['window_months']} months: {top['service']} (${top['usd']:.2f}, {top['pct']}%)."
        )
    parts.append(
        f"At the current run-rate, annual spend projects to ${d['annual_run_rate_usd']:.0f}."
    )
    return " ".join(parts)


_ANALYST_PROMPT = """You are the FinOps cost analyst inside wasteless, reading a
customer's AWS Cost Explorer data. Write a short spoken-aloud commentary on the
cost trend below.

Data (JSON, amounts in USD): {data}

Write 2 to 4 short sentences, plain language, no markdown. Cover, in order and
only if present in the data:
1. the current month's movement (if current_partial is true, say "so far" and
   do NOT state a month-over-month %);
2. the single biggest cost driver (top_service);
3. any anomaly: a spike month (peak.is_spike) and what drove it (peak.drivers);
4. the annual run-rate projection.
Never invent a number that is not in the data above. Do not give instructions;
you describe what the spend is doing."""


def generate_cost_narrative(data: Dict[str, Any], conn=None) -> Optional[str]:
    """LLM commentary on the cost trend, or None (never raises). The AI only
    comments; every figure comes from `data` (deterministic SQL)."""
    from core import llm

    if not llm.is_enabled():
        return None
    try:
        import json
        import os

        import litellm

        response = litellm.completion(
            model=os.getenv(llm.MODEL_ENV_VAR),
            messages=[
                {
                    "role": "user",
                    "content": _ANALYST_PROMPT.format(data=json.dumps(data, default=str)),
                }
            ],
            max_tokens=220,
            temperature=0.2,
            timeout=20,
        )
        llm.record_usage(conn, "cost_analyst", response)
        text = response.choices[0].message.content
        return text.strip() if text else None
    except Exception as e:  # noqa: BLE001 - degrade silently like every LLM feature
        logger.warning("Cost analyst narrative failed (continuing without): %s", e)
        return None


def format_cost_context(conn) -> str:
    """A compact, deterministic block of the customer's cost figures, fed to
    the LLM cost Q&A. Every number here comes from cloud_costs_raw."""
    d = cost_analyst(conn)
    lines = ["Monthly totals (most recent months):"]
    for m in d["months"]:
        lines.append(f"  {m['label']}: ${m['usd']:.2f}")
    if d["current"]:
        cur = d["current"]
        partial = " (PARTIAL month, spend so far)" if d["current_partial"] else ""
        lines.append(f"Current month: {cur['label']} = ${cur['usd']:.2f}{partial}")
    if d["previous"] and d["mom_delta_pct"] is not None:
        lines.append(
            f"Month-over-month: {d['mom_delta_pct']}% vs {d['previous']['label']} "
            f"(${d['previous']['usd']:.2f})"
        )
    if d["top_service"]:
        t = d["top_service"]
        lines.append(
            f"Top cost driver over the window: {t['service']} = ${t['usd']:.2f} ({t['pct']}%)"
        )
    if d["peak"] and d["peak"]["is_spike"]:
        p = d["peak"]
        drv = ", ".join(x["service"] for x in p["drivers"])
        lines.append(
            f"Anomaly: {p['label']} spiked to ${p['usd']:.2f} (~{p['ratio']}x the median), "
            f"driven by {drv}."
        )
    lines.append(f"Annual run-rate projection: ${d['annual_run_rate_usd']:.0f}")
    lines.append(f"Cost data last updated: {d['last_data_date']} (Cost Explorer lags 24-48h).")

    # Full by-service breakdown over the same window as the monthly trend.
    if d["months"]:
        start = date.fromisoformat(_month_start_iso(d["months"][0]))
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT service, COALESCE(SUM(cost),0) usd FROM cloud_costs_raw "
                "WHERE usage_date >= %s GROUP BY service ORDER BY 2 DESC;",
                (start,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        if rows:
            lines.append("Cost by service (window total):")
            for row in rows:
                svc, usd = _kv(row)
                lines.append(f"  {svc}: ${float(usd):.2f}")
    return "\n".join(lines)


def _month_start_iso(month_entry) -> str:
    """First-of-month ISO date for a months[] entry (label like 'Feb 2026')."""
    from datetime import datetime

    return datetime.strptime(month_entry["label"], "%b %Y").date().replace(day=1).isoformat()
