"""
Shared app state for the wasteless UI: database config, template engine,
config manager, background-job scheduler, and cross-route constants.

Split out of what used to be a single 2223-line main.py so route modules
(ui/routes/*.py) and background jobs (ui/jobs.py) import just this instead
of importing each other — avoids circular imports between routers.
"""

import logging
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

APP_DIR = Path(__file__).parent
ENV_PATH = APP_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Database configuration.
# connect_timeout: without it, a host that DROPS packets (VPN down,
# container half-up) makes psycopg2.connect wait for the OS TCP timeout
# (~2 min) — the app or a test run just hangs in silence. 10s matches the
# backend pool (src/core/database.py).
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "wasteless"),
    "user": os.getenv("DB_USER", "wasteless"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": 10,
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
#
# Created lazily on first use, NOT at import: importing state/jobs/main
# must not require a live Postgres (test modules skip cleanly instead of
# erroring at collection; /setup can render before the DB exists). The
# operational fail-fast lives in main.py's lifespan, which pings the DB
# at startup. The lock only guards creation; getconn() stays lock-free
# afterwards (see the deadlock note above).
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=2, maxconn=40, cursor_factory=RealDictCursor, **DB_CONFIG
                )
    return _pool


# Fixed USD→EUR rate, same convention as the detectors' AWS pricing and
# src/constants.py — used for LLM costs and the Waste Rate denominator
USD_TO_EUR = float(os.getenv("USD_TO_EUR", "0.92"))

# Single monthly→daily convention (365/12): detectors price a month as
# 730 hours, so dividing by a 30-day month would overstate daily rates
# and make yearly figures disagree (×12 vs daily×365) across the UI.
DAYS_PER_MONTH = 365 / 12


def get_db():
    """Get a pooled database connection."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        # Discard any uncommitted work (or clear an aborted-transaction
        # state left by a route that raised mid-query) before the
        # connection goes back to the pool -- otherwise the next request
        # to grab it inherits an open transaction. No-op if there's
        # nothing to roll back.
        conn.rollback()
        pool.putconn(conn)


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


# DB timestamps are UTC-naive (Postgres runs in UTC inside Docker); the
# conversion happens at display time only. Staleness checks stay in SQL,
# in the same clock that stamped the rows — never compare a converted
# value against NOW().
DISPLAY_TZ = ZoneInfo("Europe/Paris")


def _localtime(dt, fmt: str = "%d %b, %H:%M") -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime(fmt)


templates.env.filters["localtime"] = _localtime

# Add config_manager to template globals for mode badge
from utils.config_manager import ConfigManager

_config_manager = ConfigManager()
templates.env.globals["get_dry_run"] = _config_manager.get_dry_run

# Every EUR figure derives from AWS USD pricing through this fixed rate;
# exposed to templates so the conversion is disclosed instead of implicit.
templates.env.globals["usd_to_eur"] = USD_TO_EUR

# AWS connectivity for the global "connect your account" banner (base.html).
# Reads the status cached by main.py's startup and the sync job — never
# calls AWS during a page render. Tri-state: True / False / None (unknown,
# banner hidden until the first check completes).
templates.env.globals["aws_reachable"] = lambda: _aws_status.get("reachable")

# Scheduler instance (jobs registered by main.py's lifespan)
scheduler = AsyncIOScheduler()

# Cached AWS reachability status (refreshed by sync job)
_aws_status: dict = {"reachable": None, "checked_at": None}


def aws_connection_configured() -> bool:
    """Une connexion AWS est-elle configurée ? Même heuristique large que
    l'atterrissage navigateur de wasteless.sh : ARNs/clés dans
    l'environnement (ui/.env est chargé au démarrage) ou credentials
    partagés crées par `aws configure`. Dans le doute, considéré configuré
    — on ne renvoie jamais un compte connecté vers /setup."""
    if os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_ACCESS_KEY_ID"):
        return True
    return (Path.home() / ".aws" / "credentials").exists()


def check_aws_reachable() -> bool:
    """Quick AWS connectivity check via STS."""
    try:
        from botocore.config import Config
        from utils.aws_clients import get_client

        # Short timeouts: this runs during startup and must never block the app
        cfg = Config(connect_timeout=3, read_timeout=3, retries={"max_attempts": 1})
        get_client("sts", config=cfg).get_caller_identity()
        return True
    except Exception as e:
        # debug, not warning: this check runs every 5 minutes and "AWS not
        # configured yet" is a normal state — but the exact reason
        # (AccessDenied vs missing credentials) must stay diagnosable.
        logging.getLogger("wasteless_ui.state").debug(f"AWS unreachable: {e}")
        return False
