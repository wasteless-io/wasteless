"""Live in-memory log viewer (debug page)."""

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from state import templates

router = APIRouter()


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Live log viewer for debugging (in-memory, current UI process)."""
    return templates.TemplateResponse(request, "logs.html")


@router.get("/api/logs")
async def api_logs(
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
