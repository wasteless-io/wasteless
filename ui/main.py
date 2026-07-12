#!/usr/bin/env python3
"""
Wasteless.io - FastAPI Backend
==============================

Fast, lightweight API for cloud cost optimization dashboard.
Replaces Streamlit for better performance.

Routes live in ui/routes/ (one module per page/domain), background jobs in
ui/jobs.py, and shared app state (DB config, templates, scheduler, config
manager) in ui/state.py. This file only assembles them: create the app,
mount static files, register the scheduled jobs, include the routers.

Author: Wasteless Team
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg2
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from state import (
    APP_DIR,
    DB_CONFIG,
    get_db,  # noqa: F401 -- re-exported for ui/tests `from main import ...`
    scheduler,
    _aws_status,
    _config_manager,  # noqa: F401 -- re-exported for ui/tests `from main import ...`
    check_aws_reachable,
)
from jobs import (
    sync_aws_job,
    terraform_pr_sync_job,
    grace_executor_job,
    _grace_execution_status,  # noqa: F401 -- re-exported for ui/tests `from main import ...`
    _sync_ec2_instance_states,  # noqa: F401 -- re-exported for ui/tests `from main import ...`
)
from routes import (
    home,
    dashboard,
    recommendations,
    history,
    reports,
    logs,
    settings,
    setup,
    cloud_resources,
    sync,
)

# Configure logging for scheduler
logging.getLogger("apscheduler").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Optional Sentry error tracking — no-op without SENTRY_DSN
    # (see src/core/observability.py). Covers the UI routes and the
    # APScheduler jobs, which run in this process.
    from utils.observability import init_sentry

    init_sentry(component="ui")

    # Startup: test database connection
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        print("Database connection OK")
    except Exception as e:
        print(f"Database connection failed: {e}")

    # Initial AWS connectivity check (avoids "Not checked" on first page load)
    _aws_status["reachable"] = check_aws_reachable()
    _aws_status["checked_at"] = datetime.now()
    print(f"AWS connectivity: {'OK' if _aws_status['reachable'] else 'not reachable'}")

    # Start scheduler for auto-sync (every 5 minutes)
    scheduler.add_job(sync_aws_job, "interval", minutes=5, id="aws_sync")
    # Grace-period executor: applies scheduled approvals once due
    scheduler.add_job(grace_executor_job, "interval", minutes=5, id="grace_executor")
    # Terraform PR reconciliation: merged -> approved, closed -> rejected
    scheduler.add_job(terraform_pr_sync_job, "interval", minutes=5, id="terraform_pr_sync")
    scheduler.start()
    print("Auto-sync scheduler started (every 5 min)")

    yield

    # Shutdown
    scheduler.shutdown()
    print("Shutting down...")


# Create FastAPI app
app = FastAPI(
    title="Wasteless.io",
    description="Cloud Cost Optimization Platform",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

# Anti-CSRF/DNS-rebinding : les methodes d'ecriture doivent viser un hote de
# confiance (voir ui/utils/security.py — protection d'attente du token d'auth)
from utils.security import block_cross_origin_writes

app.middleware("http")(block_cross_origin_writes)

# Live log capture for the /logs debug page (in-memory, nothing persisted)
from utils.log_buffer import install_capture

install_capture()

# Routers, one per page/domain (see ui/routes/*.py)
app.include_router(home.router)
app.include_router(dashboard.router)
app.include_router(recommendations.router)
app.include_router(history.router)
app.include_router(reports.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(setup.router)
app.include_router(cloud_resources.router)
app.include_router(sync.router)


# =============================================================================
# RUN SERVER
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("STREAMLIT_SERVER_PORT", "8888"))
    # Loopback by default: the API has no authentication and its POST
    # endpoints execute real AWS actions. Exposing it beyond localhost
    # (WASTELESS_HOST=0.0.0.0) is an explicit operator decision.
    host = os.getenv("WASTELESS_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
