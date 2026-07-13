"""Live in-memory log viewer (debug page)."""

from contextlib import contextmanager

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from state import get_db, templates, scheduler, _aws_status

router = APIRouter()


def _last_scan_time():
    """(timestamp of the last scan run or None, db reachable?).

    collection_runs gets one row per "wasteless collect" run even when
    nothing is detected; waste_detected.updated_at is the only signal
    for manual single-detector runs. Best-effort on purpose: /logs is
    the debug page and must keep rendering when Postgres is down, so a
    failed lookup degrades to "no stamp" instead of a 500 — and doubles
    as the DB-health probe for the status dots.
    """
    try:
        with contextmanager(get_db)() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT GREATEST(
                    (SELECT MAX(updated_at) FROM waste_detected),
                    (SELECT MAX(ran_at) FROM collection_runs)
                ) as last_scan
            """)
            row = cursor.fetchone()
            cursor.close()
            return (row["last_scan"] if row else None, True)
    except Exception:
        return (None, False)


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    """Live log viewer for debugging (in-memory, current UI process)."""
    last_scan, db_ok = _last_scan_time()
    # System-health dots (moved here from the overview header): the DB
    # probe is the last-scan lookup itself; AWS reachability is the
    # cached status maintained by the sync job; scheduler is in-process.
    system_health = {
        "db": db_ok,
        "aws": _aws_status.get("reachable"),
        "scheduler": scheduler.running,
    }
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"last_scan": last_scan, "system_health": system_health},
    )


@router.get("/api/logs")
def api_logs(
    after_id: int = 0, level: str = "DEBUG", q: str = "", limit: int = Query(500, ge=1, le=2000)
):
    """Incremental poll of the in-memory log buffer.

    `after_id` is the client's cursor: only newer entries are returned.
    `level` is a minimum (DEBUG shows everything), `q` a case-insensitive
    substring match on message and logger name.
    """
    import logging as _logging
    from utils.log_buffer import get_handler

    handler = get_handler()
    if handler is None:
        return JSONResponse({"entries": [], "last_id": 0})

    min_levelno = _logging.getLevelName(level.upper())
    if not isinstance(min_levelno, int):
        raise HTTPException(status_code=400, detail=f"unknown level: {level}")

    return JSONResponse(
        handler.query(after_id=after_id, min_levelno=min_levelno, search=q, limit=limit)
    )
