"""Activity reports (download, AI narrative) and the daily AI briefing."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from state import get_db, templates

router = APIRouter()


def _resolve_report_period(month, start, end, days):
    """Shared filter resolution for the report routes (400 on bad input)."""
    from utils.reports import resolve_period
    try:
        return resolve_period(month=month, start=start, end=end, days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    conn=Depends(get_db),
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None
):
    """Activity report over a date range, with download and AI summary."""
    from utils.reports import collect_digest_data, llm_narrative_available
    start_date, end_date = _resolve_report_period(month, start, end, days)
    report = collect_digest_data(conn, start_date, end_date)

    return templates.TemplateResponse(request, "reports.html", context={
        "report": report,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "month": month or "",
        "llm_enabled": llm_narrative_available(),
        "generated_at": datetime.now(),
    })


@router.get("/api/reports/download")
def reports_download(
    conn=Depends(get_db),
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None
):
    """Download the report as Markdown. Deterministic content only."""
    from utils.reports import collect_digest_data, format_digest, report_filename
    start_date, end_date = _resolve_report_period(month, start, end, days)
    content = format_digest(collect_digest_data(conn, start_date, end_date))
    filename = report_filename(start_date, end_date)
    return PlainTextResponse(content, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


@router.post("/api/reports/narrative")
def reports_narrative(
    conn=Depends(get_db),
    month: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: Optional[int] = None
):
    """Generate the AI narrative for a report period, on demand.

    Sync route on purpose: the LLM call blocks up to 20s and must run in
    the threadpool, not on the event loop.
    """
    from utils.reports import collect_digest_data, generate_narrative
    start_date, end_date = _resolve_report_period(month, start, end, days)
    narrative = generate_narrative(collect_digest_data(conn, start_date, end_date),
                                   conn=conn)
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
    return JSONResponse({
        "briefing": briefing["content"],
        "model": briefing["model"],
        "generated_at": briefing["created_at"].isoformat()
                        if briefing["created_at"] else None,
        "cached": briefing["cached"],
    })
