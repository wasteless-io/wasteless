"""
Shared app state for the wasteless UI: database config, template engine,
config manager, background-job scheduler, and cross-route constants.

Split out of what used to be a single 2223-line main.py so route modules
(ui/routes/*.py) and background jobs (ui/jobs.py) import just this instead
of importing each other — avoids circular imports between routers.
"""

import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

APP_DIR = Path(__file__).parent
ENV_PATH = APP_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "wasteless"),
    "user": os.getenv("DB_USER", "wasteless"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# Route handlers are sync `def`s (FastAPI runs them in a threadpool so
# blocking psycopg2 calls don't stall the event loop -- see git history
# for the stress test that found routes were all `async def` with zero
# `await`, serializing every request on one thread). Without pooling here,
# concurrent requests each open a raw connection and a load test at
# concurrency=200 hit Postgres's max_connections=100 ("sorry, too many
# clients already").
#
# maxconn=40 keeps real usage (a handful of concurrent users + the 5-min
# background jobs) comfortably pooled while leaving headroom under
# Postgres's max_connections=100 for the backend's own pool
# (src/core/database.py, up to 10) and jobs.py's occasional direct
# connects. Verified with ab: concurrency 10-30 (the realistic ceiling for
# a self-hosted single-team tool) -> 0 failures, ~50-250ms/request, down
# from multi-second before the async/sync fix. Synthetic concurrency of
# 200 does start hitting the pool limit (expected backpressure, not a
# crash: the server recovers and keeps serving once the spike passes).
#
# A tried alternative (maxconn=20 + a threading.Semaphore to queue instead
# of raising PoolError when exhausted) deadlocked under load instead:
# ThreadedConnectionPool.getconn() holds its own internal lock, and
# pairing it with a second blocking primitive across the same worker
# threads froze all of them. Don't reintroduce that pattern without
# testing it under concurrency again.
_pool = pg_pool.ThreadedConnectionPool(
    minconn=2, maxconn=40, cursor_factory=RealDictCursor, **DB_CONFIG
)

# Fixed USD→EUR rate, same convention as the detectors' AWS pricing and
# src/constants.py — used for LLM costs and the Waste Rate denominator
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))

# Single monthly→daily convention (365/12): detectors price a month as
# 730 hours, so dividing by a 30-day month would overstate daily rates
# and make yearly figures disagree (×12 vs daily×365) across the UI.
DAYS_PER_MONTH = 365 / 12


def get_db():
    """Get a pooled database connection."""
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        # Discard any uncommitted work (or clear an aborted-transaction
        # state left by a route that raised mid-query) before the
        # connection goes back to the pool -- otherwise the next request
        # to grab it inherits an open transaction. No-op if there's
        # nothing to roll back.
        conn.rollback()
        _pool.putconn(conn)


# Statuses whose resource might still vanish out from under us and needs
# checking: pending/rejected (still active waste), scheduled (grace period
# in flight), pr_open (Terraform PR still open), approved_manual (human
# confirmed a manual-review recommendation but hasn't necessarily deleted
# the resource yet — no automated action ever touches it). Not dismissed/
# obsolete/applied/approved — those are already terminal. Both the
# background job and the manual "Sync AWS" button use this same list so
# they can't drift apart and cover different scopes under the same "sync"
# name.
SYNCABLE_STATUSES = ("pending", "rejected", "scheduled", "pr_open", "approved_manual")

# Trend chart ranges: key → (days back, granularity, subtitle)
TREND_RANGES = {
    "7d": (7, "day", "Last 7 days · 30-day linear forecast"),
    "30d": (30, "day", "Last 30 days · 30-day linear forecast"),
    "90d": (90, "day", "Last 90 days · 30-day linear forecast"),
    "1y": (365, "month", "Last 12 months · 6-month linear forecast"),
}

CLOUD_REGIONS = ["eu-west-1", "eu-west-2", "eu-west-3", "us-east-1"]

# Templates
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Add datetime to template globals for time calculations
templates.env.globals["now"] = datetime.now

# Add config_manager to template globals for mode badge
from utils.config_manager import ConfigManager

_config_manager = ConfigManager()
templates.env.globals["get_dry_run"] = _config_manager.get_dry_run

# Every EUR figure derives from AWS USD pricing through this fixed rate;
# exposed to templates so the conversion is disclosed instead of implicit.
templates.env.globals["usd_to_eur"] = USD_TO_EUR

# Scheduler instance (jobs registered by main.py's lifespan)
scheduler = AsyncIOScheduler()

# Cached AWS reachability status (refreshed by sync job)
_aws_status: dict = {"reachable": None, "checked_at": None}


def check_aws_reachable() -> bool:
    """Quick AWS connectivity check via STS."""
    try:
        from botocore.config import Config
        from utils.aws_clients import get_client

        # Short timeouts: this runs during startup and must never block the app
        cfg = Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 1})
        get_client("sts", config=cfg).get_caller_identity()
        return True
    except Exception:
        return False
