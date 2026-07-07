#!/usr/bin/env python3
"""
Reports integration for Wasteless UI
=====================================

Bridges the UI to the backend report module (src/reports/weekly_digest.py)
and resolves the date-range filters of the Reports page (presets, month
picker, free range) into a concrete [start_date, end_date] period.
"""

import calendar
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

# Add backend src/ to sys.path to import backend modules
# Path structure: <repo>/ui/utils/ -> go up 2 levels -> <repo>/src
BACKEND_SRC_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if BACKEND_SRC_PATH not in sys.path:
    sys.path.insert(0, BACKEND_SRC_PATH)

# The LLM configuration (WASTELESS_LLM_MODEL + provider key) lives in the
# root .env, not ui/.env — load it without overriding the UI environment.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BACKEND_SRC_PATH, "..", ".env"), override=False)

from core.llm import is_enabled as llm_narrative_available  # noqa: E402
from reports.daily_briefing import get_or_create_briefing  # noqa: E402
from reports.weekly_digest import (  # noqa: E402
    collect_digest_data,
    format_digest,
    generate_narrative,
)

__all__ = [
    "collect_digest_data",
    "format_digest",
    "generate_narrative",
    "get_or_create_briefing",
    "llm_narrative_available",
    "resolve_period",
    "report_filename",
]

MAX_PERIOD_DAYS = 366


def resolve_period(
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
) -> Tuple[date, date]:
    """Resolve the page filters into an inclusive (start_date, end_date).

    Precedence: month > explicit start/end > days. Defaults to the last
    7 days. Raises ValueError on malformed input, reversed or oversized
    ranges (the route maps it to a 400).
    """
    if month:
        year_str, _, month_str = month.partition("-")
        year, month_num = int(year_str), int(month_str)
        if not 1 <= month_num <= 12:
            raise ValueError(f"invalid month: {month!r}")
        last_day = calendar.monthrange(year, month_num)[1]
        return date(year, month_num, 1), date(year, month_num, last_day)

    if start or end:
        if not (start and end):
            raise ValueError("start and end must be provided together")
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    else:
        period_days = days if days is not None else 7
        if period_days < 1:
            raise ValueError(f"days must be >= 1, got {period_days}")
        # UTC, not local date.today(): collect_digest_data() filters
        # waste_detected.created_at against this, and that column is
        # stamped with Postgres's NOW() (UTC). A local-timezone "today"
        # silently excludes the last few hours' waste for any deployment
        # ahead of UTC, worst during the ~1-2h window each day where the
        # local date has rolled over but UTC hasn't yet.
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=period_days - 1)

    if end_date < start_date:
        raise ValueError(f"end date {end_date} is before start date {start_date}")
    if (end_date - start_date).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f"period is limited to {MAX_PERIOD_DAYS} days")
    return start_date, end_date


def report_filename(start_date: date, end_date: date, extension: str = "md") -> str:
    """Download filename for a report period."""
    return f"wasteless-report_{start_date.isoformat()}_{end_date.isoformat()}.{extension}"
