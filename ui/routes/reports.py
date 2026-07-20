"""Activity reports (download, AI narrative) and the daily AI briefing."""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from schemas import BudgetRequest
from state import get_db, templates

router = APIRouter()


def _resolve_report_period(month, start, end, days):
    """Shared filter resolution for the report routes (400 on bad input)."""
    from utils.reports import resolve_period

    try:
        return resolve_period(month=month, start=start, end=end, days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _resolve_anchor(conn, at: Optional[str]) -> date:
    """Anchor date for the cost period: an explicit ?at=YYYY-MM-DD, else the
    latest day Cost Explorer has reported (so the page opens on real data)."""
    from utils.cost_report import latest_cost_date

    if at:
        try:
            return date.fromisoformat(at)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"bad date: {at}") from e
    return latest_cost_date(conn) or date.today()


def _budget_for(conn, report) -> dict:
    """Monthly budget amount and, for a month period, the actual (from the cost
    total) it is measured against. Budget only frames a calendar month."""
    from utils.budget import get_budget

    is_month = report["granularity"] == "month"
    return {
        "amount": get_budget(conn),  # None when never set
        "is_month": is_month,
        "label": report["period"]["label"],
        "actual": report["total_usd"] if is_month else None,
    }


def _cost_context(conn, g: str, at: Optional[str]):
    from utils.cost_report import collect_cost_report

    report = collect_cost_report(conn, g, _resolve_anchor(conn, at))
    return report, _budget_for(conn, report)


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    conn=Depends(get_db),
    g: str = "month",
    at: Optional[str] = None,
):
    """On-screen preview of the cloud cost statement (accounting view of what
    the infrastructure costs per day/week/month/year, by service). The PDF at
    /reports/print is the primary deliverable."""
    report, budget = _cost_context(conn, g, at)
    return templates.TemplateResponse(
        request,
        "reports.html",
        context={"report": report, "budget": budget, "generated_at": datetime.now()},
    )


@router.get("/reports/print", response_class=HTMLResponse)
def reports_print(
    request: Request,
    conn=Depends(get_db),
    g: str = "month",
    at: Optional[str] = None,
):
    """Standalone, print-optimised cost statement (no app shell).

    The full /reports page cannot be printed reliably: its fixed sidebar,
    `overflow: clip` wrapper and `100vh` layout break Chrome's paged-media
    paginator (blank output). This renders the same figures self-contained.
    Add ?auto=1 to open the browser print dialog on load.
    """
    report, budget = _cost_context(conn, g, at)
    return templates.TemplateResponse(
        request,
        "reports_print.html",
        context={"report": report, "budget": budget, "generated_at": datetime.now()},
    )


@router.post("/api/reports/budget")
def set_report_budget(req: BudgetRequest, conn=Depends(get_db)):
    """Set the monthly cloud budget (USD) the cost statement measures against."""
    from utils.budget import set_budget

    set_budget(conn, req.monthly_usd, updated_by="reports_ui")
    return JSONResponse({"monthly_usd": req.monthly_usd})


@router.get("/api/reports/download")
def reports_download(
    conn=Depends(get_db),
    g: str = "month",
    at: Optional[str] = None,
):
    """Download the cost statement as Markdown. Deterministic content only."""
    from utils.cost_report import format_cost_statement

    report, _ = _cost_context(conn, g, at)
    content = format_cost_statement(report)
    filename = f"wasteless-cost_{report['period']['start']}_{report['period']['end']}.md"
    return PlainTextResponse(
        content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/reports/narrative")
def reports_narrative(
    conn=Depends(get_db),
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None,
):
    """Generate the AI narrative for a report period, on demand.

    Sync route on purpose: the LLM call blocks up to 20s and must run in
    the threadpool, not on the event loop.
    """
    from utils.reports import collect_digest_data, generate_narrative

    start_date, end_date = _resolve_report_period(month, start, end, days)
    narrative = generate_narrative(collect_digest_data(conn, start_date, end_date), conn=conn)
    return JSONResponse({"narrative": narrative})


@router.get("/api/briefing/today")
def briefing_today(conn=Depends(get_db), refresh: bool = False):
    """Today's AI briefing for the home page, cached one row per day.

    The AI only comments; every number in the briefing data is computed
    from the database. Sync route on purpose: the LLM call blocks up to
    30s on a cache miss and must run in the threadpool, not on the event
    loop. Returns {"briefing": null} when the LLM is disabled or fails —
    the card hides itself.
    """
    from utils.reports import get_or_create_briefing

    briefing = get_or_create_briefing(conn, refresh=refresh)
    if not briefing:
        return JSONResponse({"briefing": None})
    return JSONResponse(
        {
            "briefing": briefing["content"],
            "model": briefing["model"],
            "generated_at": briefing["created_at"].isoformat() if briefing["created_at"] else None,
            "cached": briefing["cached"],
        }
    )
