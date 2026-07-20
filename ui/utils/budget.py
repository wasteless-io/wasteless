"""Monthly cloud budget for the Reports CFO lens (spend vs budget).

A single amount in USD, stored in budget_settings (newest row wins). Kept
tiny on purpose: the report reads the current budget and compares it to the
month's actual spend from cloud_costs_raw. All amounts USD, no conversion.
"""

from datetime import date
from typing import Optional


def get_budget(conn) -> Optional[float]:
    """Current monthly budget in USD, or None if never set."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT monthly_usd FROM budget_settings ORDER BY updated_at DESC LIMIT 1;")
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        return None
    value = row["monthly_usd"] if isinstance(row, dict) else row[0]
    return float(value)


def set_budget(conn, monthly_usd: float, updated_by: str = "settings_ui") -> None:
    """Record a new monthly budget (append-only; newest wins)."""
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO budget_settings (monthly_usd, updated_by) VALUES (%s, %s);",
            (monthly_usd, updated_by),
        )
        conn.commit()
    finally:
        cur.close()


def month_spend_usd(conn, year: int, month: int) -> float:
    """Actual AWS spend billed in a calendar month (cloud_costs_raw)."""
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    start = date(year, month, 1)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT COALESCE(SUM(cost), 0) AS usd
            FROM cloud_costs_raw
            WHERE usage_date >= %s AND usage_date < %s;
            """,
            (start, end),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    value = row["usd"] if isinstance(row, dict) else row[0]
    return float(value)
