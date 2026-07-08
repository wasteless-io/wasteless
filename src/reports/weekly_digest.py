#!/usr/bin/env python3
"""
Activity report for Wasteless — a period's FinOps activity in one message.

Every number comes from deterministic SQL over the existing tables
(waste_detected, recommendations, actions_log, savings_realized). When AI
insights are enabled (same configuration as core/llm.py), a short LLM
narrative can be prepended to the report; the narrative comments on the
numbers but the numbers themselves never come from the model. Like every
LLM feature in wasteless, it degrades silently: without litellm or a
configured model the report is purely deterministic.

The report covers an inclusive date range [start_date, end_date]. The
"pending recommendations" section is a live snapshot of the table, so it
is only meaningful when the period includes today; for past periods the
report falls back to the recommendations *created* during the period.

Usage:
    python3 src/reports/weekly_digest.py [--days 7]
    python3 src/reports/weekly_digest.py --start 2026-06-01 --end 2026-06-30
"""

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple


from core import llm

logger = logging.getLogger(__name__)

MAX_TOKENS = 300
TIMEOUT_SECONDS = 20

NARRATIVE_PROMPT = """\
You are the FinOps assistant inside wasteless, an AWS cost-waste detector.
Write the narrative introduction of the activity report below.

Report data (JSON, amounts in EUR/month unless stated): {data}

Write 3-5 short sentences, plain language, no markdown:
1. the headline of the period (the most significant number or change);
2. what deserves attention first (e.g. oldest pending recommendation,
   failed actions), if anything;
3. one concrete next step, phrased as advice, never as an instruction.
Use "you may want to review", "consider", "it could be worth checking" —
never an imperative like "delete", "stop", "do X". The reader decides;
you suggest. Never invent numbers that are not in the data above."""


def _values(row: Any) -> Tuple:
    """Normalize a DB row to a tuple of values.

    The backend pipeline uses plain cursors (tuples) while the UI connects
    with RealDictCursor (dict rows); support both so the report can be
    generated from either side.
    """
    if isinstance(row, dict):
        return tuple(row.values())
    return tuple(row)


def collect_digest_data(conn, start_date: date, end_date: date) -> Dict[str, Any]:
    """Aggregate the period's activity. Deterministic SQL only.

    The range is inclusive: [start_date, end_date].
    """
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")

    end_exclusive = end_date + timedelta(days=1)
    # UTC, not local date.today(): compared against created_at/action_date
    # columns stamped with Postgres's NOW() (UTC) -- a local-timezone
    # "today" can disagree with the DB's during the daily window where the
    # local date has rolled over but UTC hasn't yet.
    period_includes_today = end_date >= datetime.now(timezone.utc).date()

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(monthly_waste_eur), 0) AS eur
            FROM waste_detected
            WHERE created_at >= %s AND created_at < %s;
        """,
            (start_date, end_exclusive),
        )
        new_count, new_eur = _values(cursor.fetchone())

        cursor.execute(
            """
            SELECT resource_type, COUNT(*) AS n,
                   COALESCE(SUM(monthly_waste_eur), 0) AS eur
            FROM waste_detected
            WHERE created_at >= %s AND created_at < %s
            GROUP BY resource_type
            ORDER BY 3 DESC;
        """,
            (start_date, end_exclusive),
        )
        by_type = [(t, int(c), float(e)) for t, c, e in (_values(r) for r in cursor.fetchall())]

        if period_includes_today:
            # Live snapshot: what is waiting for a decision right now.
            cursor.execute("""
                SELECT COUNT(*) AS n,
                       COALESCE(SUM(estimated_monthly_savings_eur), 0) AS eur,
                       EXTRACT(DAY FROM NOW() - MIN(created_at))::int AS oldest_days
                FROM recommendations
                WHERE status = 'pending';
            """)
            pending_count, pending_eur, oldest_days = _values(cursor.fetchone())
            pending_scope = "snapshot"
        else:
            # Past period: the table only holds current statuses, so a
            # snapshot would be meaningless — report what was created then.
            cursor.execute(
                """
                SELECT COUNT(*) AS n,
                       COALESCE(SUM(estimated_monthly_savings_eur), 0) AS eur
                FROM recommendations
                WHERE created_at >= %s AND created_at < %s;
            """,
                (start_date, end_exclusive),
            )
            pending_count, pending_eur = _values(cursor.fetchone())
            oldest_days = None
            pending_scope = "created_in_period"

        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE action_status = 'success' AND NOT dry_run) AS succeeded,
                COUNT(*) FILTER (WHERE action_status = 'failed' AND NOT dry_run) AS failed,
                COUNT(*) FILTER (WHERE dry_run) AS dry_run
            FROM actions_log
            WHERE action_date >= %s AND action_date < %s;
        """,
            (start_date, end_exclusive),
        )
        succeeded, failed, dry_run = _values(cursor.fetchone())

        cursor.execute(
            """
            SELECT COALESCE(SUM(actual_savings_eur), 0) AS eur
            FROM savings_realized
            WHERE verified_at >= %s AND verified_at < %s;
        """,
            (start_date, end_exclusive),
        )
        (verified_eur,) = _values(cursor.fetchone())

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": (end_date - start_date).days + 1,
            },
            "new_waste": {
                "count": int(new_count),
                "monthly_eur": float(new_eur),
                "by_type": by_type,
            },
            "pending": {
                "count": int(pending_count),
                "monthly_eur": float(pending_eur),
                "oldest_days": int(oldest_days) if oldest_days is not None else None,
                "scope": pending_scope,
            },
            "actions": {
                "succeeded": int(succeeded),
                "failed": int(failed),
                "dry_run": int(dry_run),
            },
            "verified_savings_eur": float(verified_eur),
        }
    finally:
        cursor.close()


def format_digest(data: Dict[str, Any]) -> str:
    """Plain-text (markdown-friendly) report from the data. No LLM involved."""
    period = data["period"]
    new = data["new_waste"]
    pending = data["pending"]
    actions = data["actions"]

    lines = [
        f"Wasteless — activity report ({period['start']} to {period['end']})",
        "=" * 60,
        "",
        f"New waste detected: {new['count']} resource(s), " f"{new['monthly_eur']:.2f} EUR/month",
    ]
    for resource_type, count, eur in new["by_type"]:
        lines.append(f"  - {resource_type}: {count} ({eur:.2f} EUR/month)")

    if pending["scope"] == "snapshot":
        lines += [
            "",
            f"Pending recommendations: {pending['count']} "
            f"({pending['monthly_eur']:.2f} EUR/month of potential savings)",
        ]
        if pending["oldest_days"] is not None and pending["count"] > 0:
            lines.append(f"  Oldest has been waiting {pending['oldest_days']} day(s).")
    else:
        lines += [
            "",
            f"Recommendations created in the period: {pending['count']} "
            f"({pending['monthly_eur']:.2f} EUR/month of potential savings)",
        ]

    lines += [
        "",
        f"Actions executed: {actions['succeeded']} succeeded, "
        f"{actions['failed']} failed ({actions['dry_run']} dry-run)",
        f"Savings verified this period: {data['verified_savings_eur']:.2f} EUR",
    ]
    return "\n".join(lines)


def generate_narrative(data: Dict[str, Any], conn=None) -> Optional[str]:
    """LLM narrative for the report, or None (never raises)."""
    if not llm.is_enabled():
        return None

    try:
        import litellm

        response = litellm.completion(
            model=os.getenv(llm.MODEL_ENV_VAR),
            messages=[
                {
                    "role": "user",
                    "content": NARRATIVE_PROMPT.format(data=json.dumps(data, default=str)),
                }
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            timeout=TIMEOUT_SECONDS,
        )
        llm.record_usage(conn, "narrative", response)
        narrative = response.choices[0].message.content
        return narrative.strip() if narrative else None
    except Exception as e:
        logger.warning(f"Report narrative generation failed (continuing without): {e}")
        return None


def build_digest(conn, start_date: date, end_date: date) -> str:
    """Full report: LLM narrative when available, then the numbers."""
    data = collect_digest_data(conn, start_date, end_date)
    digest = format_digest(data)
    narrative = generate_narrative(data, conn=conn)
    if narrative:
        digest = f"{narrative}\n\n{digest}"
    return digest


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    parser = argparse.ArgumentParser(description="Print a Wasteless activity report")
    parser.add_argument(
        "--days", type=int, default=7, help="Period ending today, in days (default: 7)"
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="Period start (YYYY-MM-DD); overrides --days",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Period end (YYYY-MM-DD, default: today)",
    )
    args = parser.parse_args()

    end_date = args.end or datetime.now(timezone.utc).date()
    start_date = args.start or end_date - timedelta(days=args.days - 1)

    from core.database import get_connection

    with get_connection() as conn:
        print(build_digest(conn, start_date, end_date))


if __name__ == "__main__":
    main()
