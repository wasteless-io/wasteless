#!/usr/bin/env python3
"""
Daily CTO briefing for Wasteless — one AI-written status message per day.

Every number comes from deterministic SQL over the existing tables; the
LLM only writes the prose around them, in more depth than the report
narrative (trend vs yesterday, decisions waiting with their age, failures,
one prioritized next step). Like every LLM feature in wasteless, it
degrades silently: without litellm or a configured model there is simply
no briefing.

The briefing is cached one row per day in daily_briefings (see
sql/migrations/daily_briefings.sql), so the LLM is called at most once a
day regardless of page loads — or again on explicit refresh.

Usage:
    python3 src/reports/daily_briefing.py            # print today's briefing
    python3 src/reports/daily_briefing.py --refresh  # force regeneration
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

# Allow running as a script: python3 src/reports/daily_briefing.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from constants import USD_TO_EUR
from core import llm

logger = logging.getLogger(__name__)

MAX_TOKENS = 700
TIMEOUT_SECONDS = 30
TOP_PENDING_LIMIT = 5

BRIEFING_PROMPT = """\
You are the FinOps assistant inside wasteless, an AWS cost-waste detector.
Write today's briefing for the CTO from the data below. The dashboard
already shows the headline numbers; your job is the reading of them, not
the recitation.

Briefing data (JSON, amounts in EUR/month unless stated): {data}

Write 2 short paragraphs of plain text (no markdown, no headings, no
bullet lists), 80-130 words in total:
1. Situation — total active waste, how it moved versus yesterday and
   versus 7 days ago (waste_trend), and what dominates it.
2. Recommendation — the single most valuable next step and the concrete
   amount it recovers, phrased as advice ("you may want to review",
   "consider", "worth checking") — never as an instruction ("delete",
   "stop", "do X"). The reader decides; you suggest.

Mention these ONLY when their condition holds, otherwise not a word
about them (not even to say they are fine, empty, or unavailable):
- pending approvals: only if pending.count > 0
- failed actions: only if actions_7d.failed > 0
- scan freshness: only if last_scan_hours_ago > 24
- waste rate / total spend: only if waste_rate_pct is a number
- verified savings: only if verified_savings_eur > 0

Hard rules:
- Never invent numbers that are not in the data.
- If a value is null, zero, or an empty list, OMIT it entirely. Never
  write that data is unavailable, null, or that there is nothing to
  report — silence is the correct way to report an empty state.
- Write amounts like "9.57 €" and durations in natural units ("20
  minutes ago", "3 days"), never decimal hours.
- Factual and direct, no filler, no greetings."""


def collect_briefing_data(conn) -> Dict[str, Any]:
    """Aggregate today's situation. Deterministic SQL only.

    Richer than the report digest: current state + short-term deltas from
    waste_snapshots, top pending recommendations, and scan freshness.
    """
    cursor = conn.cursor()

    def one(query, params=()):
        cursor.execute(query, params)
        row = cursor.fetchone()
        return tuple(row.values()) if isinstance(row, dict) else tuple(row)

    def many(query, params=()):
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [tuple(r.values()) if isinstance(r, dict) else tuple(r)
                for r in rows]

    try:
        total_eur, total_count = one("""
            SELECT COALESCE(SUM(monthly_waste_eur), 0) AS eur, COUNT(*) AS n
            FROM active_waste;
        """)

        by_type = [(t, int(c), float(e)) for t, c, e in many("""
            SELECT resource_type, COUNT(*) AS n,
                   COALESCE(SUM(monthly_waste_eur), 0) AS eur
            FROM active_waste
            GROUP BY resource_type
            ORDER BY 3 DESC;
        """)]

        # Short-term trend from the daily snapshots (may be empty early on)
        yesterday_eur, week_ago_eur = one("""
            SELECT
                (SELECT SUM(total_eur) FROM waste_snapshots
                 WHERE snapshot_date = CURRENT_DATE - 1) AS yesterday_eur,
                (SELECT SUM(total_eur) FROM waste_snapshots
                 WHERE snapshot_date = CURRENT_DATE - 7) AS week_ago_eur;
        """)

        pending_count, pending_eur, oldest_days = one("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) AS eur,
                   EXTRACT(DAY FROM NOW() - MIN(created_at))::int AS oldest_days
            FROM recommendations
            WHERE status = 'pending';
        """)

        top_pending = [
            {'action': a, 'resource_type': t, 'monthly_eur': float(e),
             'waiting_days': int(d)}
            for a, t, e, d in many("""
                SELECT r.action_required, w.resource_type,
                       r.estimated_monthly_savings_eur,
                       EXTRACT(DAY FROM NOW() - r.created_at)::int AS waiting_days
                FROM recommendations r
                JOIN waste_detected w ON w.id = r.waste_id
                WHERE r.status = 'pending'
                ORDER BY r.estimated_monthly_savings_eur DESC
                LIMIT %s;
            """, (TOP_PENDING_LIMIT,))
        ]

        succeeded_7d, failed_7d, dry_run_7d = one("""
            SELECT
                COUNT(*) FILTER (WHERE action_status = 'success' AND NOT dry_run) AS succeeded,
                COUNT(*) FILTER (WHERE action_status = 'failed' AND NOT dry_run) AS failed,
                COUNT(*) FILTER (WHERE dry_run) AS dry_run
            FROM actions_log
            WHERE action_date >= CURRENT_DATE - 7;
        """)

        savings_total, = one("""
            SELECT COALESCE(SUM(actual_savings_eur), 0) AS eur FROM savings_realized;
        """)

        # Waste rate needs Cost Explorer data, which may not be collected.
        # Denominator: last complete calendar month, converted to EUR (the
        # writers store USD) — the current month would be a partial
        # month-to-date against a monthly waste rate.
        month_spend, = one("""
            SELECT COALESCE(SUM(CASE WHEN currency = 'USD' THEN cost * %s
                                     ELSE cost END), 0) AS eur
            FROM cloud_costs_raw
            WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
              AND usage_date < DATE_TRUNC('month', CURRENT_DATE);
        """, (USD_TO_EUR,))

        last_scan_hours, = one("""
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(updated_at))) / 3600 AS hours
            FROM waste_detected;
        """)

        return {
            'date': date.today().isoformat(),
            'active_waste': {
                'monthly_eur': float(total_eur),
                'count': int(total_count),
                'by_type': by_type,
            },
            'waste_trend': {
                'yesterday_eur': float(yesterday_eur) if yesterday_eur is not None else None,
                'week_ago_eur': float(week_ago_eur) if week_ago_eur is not None else None,
            },
            'pending': {
                'count': int(pending_count),
                'monthly_eur': float(pending_eur),
                'oldest_days': int(oldest_days) if oldest_days is not None else None,
                'top': top_pending,
            },
            'actions_7d': {
                'succeeded': int(succeeded_7d),
                'failed': int(failed_7d),
                'dry_run': int(dry_run_7d),
            },
            'verified_savings_eur': float(savings_total),
            'last_month_spend_eur': float(month_spend) if month_spend else None,
            'waste_rate_pct': round(float(total_eur) / float(month_spend) * 100, 1)
                              if month_spend else None,
            'last_scan_hours_ago': round(float(last_scan_hours), 1)
                                   if last_scan_hours is not None else None,
        }
    finally:
        cursor.close()


def build_briefing_prompt(data: Dict[str, Any]) -> str:
    return BRIEFING_PROMPT.format(data=json.dumps(data, default=str))


def generate_briefing(data: Dict[str, Any], conn=None) -> Optional[str]:
    """LLM briefing text from the data, or None (never raises)."""
    if not llm.is_enabled():
        return None

    try:
        import litellm
        response = litellm.completion(
            model=os.getenv(llm.MODEL_ENV_VAR),
            messages=[{'role': 'user', 'content': build_briefing_prompt(data)}],
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            timeout=TIMEOUT_SECONDS,
        )
        llm.record_usage(conn, 'briefing', response)
        briefing = response.choices[0].message.content
        return briefing.strip() if briefing else None
    except Exception as e:
        logger.warning(f"Daily briefing generation failed (continuing without): {e}")
        return None


def get_or_create_briefing(conn, refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Today's briefing from cache, generating it once when missing.

    Returns {'content', 'model', 'created_at', 'cached'} or None when the
    LLM is disabled or generation failed (never raises).
    """
    cursor = None
    try:
        cursor = conn.cursor()
        if not refresh:
            cursor.execute("""
                SELECT content, model, created_at FROM daily_briefings
                WHERE briefing_date = CURRENT_DATE;
            """)
            row = cursor.fetchone()
            if row:
                content, model, created_at = (tuple(row.values())
                                              if isinstance(row, dict) else tuple(row))
                return {'content': content, 'model': model,
                        'created_at': created_at, 'cached': True}

        if not llm.is_enabled():
            return None

        data = collect_briefing_data(conn)
        content = generate_briefing(data, conn=conn)
        if not content:
            return None

        model = os.getenv(llm.MODEL_ENV_VAR)
        cursor.execute("""
            INSERT INTO daily_briefings (briefing_date, content, model)
            VALUES (CURRENT_DATE, %s, %s)
            ON CONFLICT (briefing_date)
            DO UPDATE SET content = EXCLUDED.content,
                          model = EXCLUDED.model,
                          created_at = NOW()
            RETURNING created_at;
        """, (content, model))
        row = cursor.fetchone()
        created_at = (tuple(row.values()) if isinstance(row, dict) else tuple(row))[0]
        conn.commit()
        return {'content': content, 'model': model,
                'created_at': created_at, 'cached': False}

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"Daily briefing failed (continuing without): {e}")
        return None
    finally:
        if cursor is not None:
            cursor.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    parser = argparse.ArgumentParser(description="Print today's CTO briefing")
    parser.add_argument('--refresh', action='store_true',
                        help="Regenerate even if today's briefing is cached")
    args = parser.parse_args()

    from core.database import get_connection
    with get_connection() as conn:
        briefing = get_or_create_briefing(conn, refresh=args.refresh)
        if briefing:
            print(briefing['content'])
        else:
            print("No briefing available (LLM disabled or generation failed).",
                  file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
