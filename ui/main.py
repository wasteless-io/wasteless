#!/usr/bin/env python3
"""
Wasteless.io - FastAPI Backend
==============================

Fast, lightweight API for cloud cost optimization dashboard.
Replaces Streamlit for better performance.

Author: Wasteless Team
"""

import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

# Configure logging for scheduler
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# Load environment variables
APP_DIR = Path(__file__).parent
ENV_PATH = APP_DIR / '.env'
load_dotenv(dotenv_path=ENV_PATH)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'wasteless'),
    'user': os.getenv('DB_USER', 'wasteless'),
    'password': os.getenv('DB_PASSWORD', '')
}

# Fixed USD→EUR rate, same convention as the detectors' AWS pricing and
# src/constants.py — used for LLM costs and the Waste Rate denominator
USD_TO_EUR = float(os.getenv('USD_TO_EUR', '0.92'))

# Single monthly→daily convention (365/12): detectors price a month as
# 730 hours, so dividing by a 30-day month would overstate daily rates
# and make yearly figures disagree (×12 vs daily×365) across the UI.
DAYS_PER_MONTH = 365 / 12


def get_db():
    """Get database connection."""
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def sync_aws_job():
    """Background job to sync recommendations with AWS state.

    Covers every resource type detectors can produce (EC2 instances, EBS
    volumes, Elastic IPs, snapshots, NAT gateways, load balancers): when
    the resource no longer exists, the pending recommendation is obsolete.
    """
    from utils.aws_sync import find_vanished_resources

    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        cursor = conn.cursor()

        # Open recommendations grouped by resource type. Rejected ones are
        # included: a rejected resource still counts as active waste, so its
        # disappearance must also be detected to stop counting it. Scheduled
        # ones too: a resource that vanishes during its grace period must
        # become obsolete instead of failing at execution time.
        cursor.execute("""
            SELECT w.resource_type, array_agg(DISTINCT w.resource_id) AS ids
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status IN ('pending', 'rejected', 'scheduled')
            GROUP BY w.resource_type
        """)
        pending = {row['resource_type']: row['ids'] for row in cursor.fetchall()}

        if not pending:
            conn.close()
            return

        vanished = find_vanished_resources(pending)

        obsolete_count = 0
        for resource_type, ids in vanished.items():
            cursor.execute("""
                UPDATE recommendations r
                SET status = 'obsolete', applied_at = NOW()
                FROM waste_detected w
                WHERE r.waste_id = w.id
                AND w.resource_type = %s
                AND w.resource_id = ANY(%s)
                AND r.status IN ('pending', 'rejected', 'scheduled')
            """, (resource_type, ids))
            obsolete_count += cursor.rowcount

        conn.commit()
        conn.close()

        if obsolete_count > 0:
            print(f"Auto-sync: marked {obsolete_count} recommendations as obsolete")

    except Exception as e:
        print(f"Auto-sync error: {e}")
    finally:
        from datetime import datetime as _dt
        _aws_status["reachable"] = check_aws_reachable()
        _aws_status["checked_at"] = _dt.now()


def terraform_pr_sync_job():
    """Reconcile open Terraform remediation PRs with GitHub.

    A merged PR means the change went through the user's Terraform
    pipeline (recommendation -> approved); a closed PR is a human
    rejection (-> rejected). Still-open PRs are left alone.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        from utils.terraform_pr import sync_open_prs
        updated = sync_open_prs(conn)
        conn.commit()
        conn.close()
        if updated > 0:
            print(f"Terraform PR sync: {updated} recommendation(s) updated")
    except Exception as e:
        print(f"Terraform PR sync error: {e}")


def grace_executor_job():
    """Execute scheduled approvals whose grace period has elapsed.

    Mirrors the /api/actions execution path: remediator mode goes through
    the backend safeguards pipeline, boto3 mode acts on EC2 directly.
    dry_run and per-action toggles are re-read at execution time.
    """
    try:
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.id, r.recommendation_type,
                   w.resource_id, w.resource_type, w.metadata
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'scheduled' AND r.execute_after <= NOW()
            ORDER BY r.execute_after
            LIMIT 20
        """)
        due = cursor.fetchall()
        if not due:
            conn.close()
            return

        from utils.remediator import RemediatorProxy
        dry_run = _config_manager.get_dry_run()

        for row in due:
            rec_id        = row['id']
            rec_type      = row['recommendation_type']
            instance_id   = row['resource_id']
            resource_type = row['resource_type']
            metadata      = row['metadata'] or {}
            action_type   = rec_type.replace('_instance', '').replace('_volume', '').replace('_snapshot', '')

            mode = execution_mode(rec_type)
            if mode != 'manual' and not _config_manager.get_action_enabled(rec_type):
                mode = 'manual'

            if mode == 'remediator':
                try:
                    proxy = RemediatorProxy(dry_run=dry_run)
                    result = proxy.execute_recommendations(conn, [rec_id])[0]
                    success = bool(result.get('success'))
                    error = result.get('error')
                except Exception as e:
                    success, error = False, str(e)
            elif mode == 'boto3' and not dry_run:
                success, error = _execute_ec2_boto3(instance_id, rec_type, metadata)
            else:
                # dry-run, or action disabled since approval: record only
                success, error = True, None

            cursor.execute("""
                INSERT INTO actions_log
                (resource_id, recommendation_id, resource_type, action_type,
                 action_status, dry_run, action_date, error_message, executed_by)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, 'grace_executor')
            """, (instance_id, rec_id, resource_type, action_type,
                  'success' if success else 'failed',
                  dry_run or mode == 'manual', error))

            # On failure — or if dry-run/manual meant nothing was actually
            # touched — the recommendation returns to the pending queue
            # instead of staying invisibly stuck in 'scheduled' or looking
            # remediated when it isn't.
            real_action_taken = success and not dry_run and mode != 'manual'
            cursor.execute("""
                UPDATE recommendations
                SET status = %s, applied_at = NOW(), execute_after = NULL
                WHERE id = %s
            """, ('approved' if real_action_taken else 'pending', rec_id))
            conn.commit()
            print(f"Grace executor: rec #{rec_id} ({rec_type}) → "
                  f"{'OK' if success else f'FAILED: {error}'}")

        conn.close()

    except Exception as e:
        print(f"Grace executor error: {e}")


# Scheduler instance
scheduler = AsyncIOScheduler()

# Cached AWS reachability status (refreshed by sync job)
_aws_status = {"reachable": None, "checked_at": None}


def check_aws_reachable() -> bool:
    """Quick AWS connectivity check via STS."""
    try:
        from botocore.config import Config
        from utils.aws_clients import get_client
        # Short timeouts: this runs during startup and must never block the app
        cfg = Config(connect_timeout=3, read_timeout=3, retries={'max_attempts': 1})
        get_client('sts', config=cfg).get_caller_identity()
        return True
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown."""
    # Startup: test database connection
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.close()
        print("Database connection OK")
    except Exception as e:
        print(f"Database connection failed: {e}")

    # Initial AWS connectivity check (avoids "Not checked" on first page load)
    from datetime import datetime as _dt
    _aws_status["reachable"] = check_aws_reachable()
    _aws_status["checked_at"] = _dt.now()
    print(f"AWS connectivity: {'OK' if _aws_status['reachable'] else 'not reachable'}")

    # Start scheduler for auto-sync (every 5 minutes)
    scheduler.add_job(sync_aws_job, 'interval', minutes=5, id='aws_sync')
    # Grace-period executor: applies scheduled approvals once due
    scheduler.add_job(grace_executor_job, 'interval', minutes=5, id='grace_executor')
    # Terraform PR reconciliation: merged -> approved, closed -> rejected
    scheduler.add_job(terraform_pr_sync_job, 'interval', minutes=5, id='terraform_pr_sync')
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
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

# Templates
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Add datetime to template globals for time calculations
from datetime import datetime
templates.env.globals['now'] = datetime.now

# Add config_manager to template globals for mode badge
from utils.action_registry import execution_mode
from utils.config_manager import ConfigManager
_config_manager = ConfigManager()
templates.env.globals['get_dry_run'] = _config_manager.get_dry_run

# Every EUR figure derives from AWS USD pricing through this fixed rate;
# exposed to templates so the conversion is disclosed instead of implicit.
templates.env.globals['usd_to_eur'] = USD_TO_EUR

# Live log capture for the /logs debug page (in-memory, nothing persisted)
from utils.log_buffer import install_capture
install_capture()

from utils.logger import log_remediation_action


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class ActionRequest(BaseModel):
    """Request to execute actions on recommendations."""
    recommendation_ids: List[int]
    action: str  # 'approve', 'reject', 'dismiss', 'execute'
    dry_run: bool = True


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    key: str
    value: str | int | float | bool


class AskQuestionRequest(BaseModel):
    """One-shot question about a specific recommendation."""
    question: str


# =============================================================================
# HTML PAGES
# =============================================================================

@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    """Public landing page."""
    return templates.TemplateResponse(request, "landing.html")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, conn=Depends(get_db)):
    """Home page with overview metrics."""
    cursor = conn.cursor()

    # Fetch metrics in single query
    cursor.execute("""
        WITH pending AS (
            SELECT COUNT(*) as pending_count,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) as pending_eur
            FROM recommendations
            WHERE status = 'pending'
        ),
        waste AS (
            SELECT COALESCE(SUM(monthly_waste_eur), 0) as total_waste
            FROM active_waste
        ),
        -- Reviewed-and-declined slice of the active waste (see /dashboard):
        -- kept in the total (the spend is real) but labelled apart.
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        ),
        -- Dénominateur du Waste Rate : dernier mois calendaire complet,
        -- converti en EUR (les writers stockent de l'USD Cost Explorer).
        -- Le mois courant serait un month-to-date partiel face à un waste
        -- exprimé en taux mensuel : ratio mécaniquement surévalué.
        raw_costs AS (
            SELECT COALESCE(SUM(CASE WHEN currency = 'USD' THEN cost * %s
                                     ELSE cost END), 0) as total_spend
            FROM cloud_costs_raw
            WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
              AND usage_date < DATE_TRUNC('month', CURRENT_DATE)
        ),
        savings AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as savings_realized
            FROM savings_realized
        )
        SELECT
            p.pending_count,
            p.pending_eur,
            w.total_waste,
            d.declined_count,
            d.declined_monthly,
            r.total_spend,
            s.savings_realized,
            CASE WHEN r.total_spend > 0
                THEN ROUND((w.total_waste / r.total_spend * 100)::numeric, 1)
                ELSE NULL
            END as waste_rate
        FROM pending p
        CROSS JOIN waste w
        CROSS JOIN declined d
        CROSS JOIN raw_costs r
        CROSS JOIN savings s;
    """, (USD_TO_EUR,))
    result = cursor.fetchone()

    # Waste by type (grouped) — active waste only
    cursor.execute("""
        SELECT
            resource_type,
            COUNT(*) as cnt,
            COALESCE(SUM(monthly_waste_eur), 0) as total_eur
        FROM active_waste
        GROUP BY resource_type
        ORDER BY total_eur DESC
    """)
    waste_by_type = cursor.fetchall()

    # Recent activity: mix detections + actions, sorted by time
    cursor.execute("""
        SELECT event_type, event_time, resource_type, cnt, amount, resource_id, action_status, error_message, dry_run
        FROM (
            SELECT
                'detection' as event_type,
                MAX(created_at) as event_time,
                resource_type,
                COUNT(*) as cnt,
                COALESCE(SUM(monthly_waste_eur), 0) as amount,
                NULL::varchar as resource_id,
                NULL::varchar as action_status,
                NULL::text as error_message,
                NULL::boolean as dry_run
            FROM waste_detected
            GROUP BY DATE(created_at), resource_type
            UNION ALL
            SELECT
                'action' as event_type,
                action_date as event_time,
                action_type as resource_type,
                1 as cnt,
                0 as amount,
                resource_id,
                action_status,
                error_message,
                dry_run
            FROM actions_log
        ) combined
        ORDER BY event_time DESC
        LIMIT 8
    """)
    recent_activity = cursor.fetchall()

    # Last sync time
    cursor.execute("""
        SELECT MAX(updated_at) as last_sync FROM waste_detected
    """)
    last_sync_row = cursor.fetchone()
    last_sync = last_sync_row['last_sync'] if last_sync_row else None

    # Daily / Monthly costs (active detected waste)
    cursor.execute("""
        SELECT COALESCE(SUM(monthly_waste_eur), 0) as monthly_cost
        FROM active_waste
    """)
    cost_row = cursor.fetchone()
    monthly_cost = float(cost_row['monthly_cost']) if cost_row else 0
    daily_cost = monthly_cost / DAYS_PER_MONTH

    # Trend: current waste vs the snapshot taken 7 days ago — same source
    # as the AI briefing, so the KPI delta and the prose never contradict.
    # None (no snapshot yet) means no trend to show, not a zero delta.
    cursor.execute("""
        SELECT SUM(total_eur) as week_ago_eur
        FROM waste_snapshots
        WHERE snapshot_date = CURRENT_DATE - 7
    """)
    trend_row = cursor.fetchone()
    week_ago_eur = trend_row['week_ago_eur'] if trend_row else None
    current_waste = float(result['total_waste']) if result else 0
    savings_trend = (current_waste - float(week_ago_eur)) if week_ago_eur is not None else None
    # Percentage variant for the KPI banner; None when week-ago base is 0
    # (division impossible), the template then falls back to the € delta.
    savings_trend_pct = (
        savings_trend / float(week_ago_eur) * 100
        if savings_trend is not None and float(week_ago_eur) > 0 else None
    )

    cursor.close()

    system_health = {
        "db": True,  # we got here, so DB is connected
        "aws": _aws_status.get("reachable"),
        "scheduler": scheduler.running,
    }

    from utils.reports import llm_narrative_available

    return templates.TemplateResponse(request, "index.html", context={
        "llm_enabled": llm_narrative_available(),
        "metrics": result,
        "waste_by_type": waste_by_type,
        "recent_activity": recent_activity,
        "system_health": system_health,
        "last_sync": last_sync,
        "daily_cost": daily_cost,
        "monthly_cost": monthly_cost,
        "savings_trend": savings_trend,
        "savings_trend_pct": savings_trend_pct,
    })


# Provider logo detection: keyword in the model name → logo slug in
# ui/static/providers/. First match wins across the models list.
_PROVIDER_KEYWORDS = [
    ('deepseek', 'deepseek'),
    ('claude', 'claude'),
    ('anthropic', 'anthropic'),
    ('gpt', 'openai'),
    ('openai', 'openai'),
    ('ollama', 'ollama'),
    ('llama', 'ollama'),
    ('mistral', 'mistral'),
    ('gemini', 'gemini'),
]


def _llm_provider(models):
    """Logo slug for the first recognized provider, or None."""
    for model in models:
        name = (model or '').lower()
        for keyword, provider in _PROVIDER_KEYWORDS:
            if keyword in name:
                logo = os.path.join(os.path.dirname(__file__),
                                    'static', 'providers', f'{provider}.svg')
                if os.path.exists(logo):
                    return provider
    return None


# Trend chart ranges: key → (days back, granularity, subtitle)
TREND_RANGES = {
    "7d":  (7,   "day",   "Last 7 days · 30-day linear forecast"),
    "30d": (30,  "day",   "Last 30 days · 30-day linear forecast"),
    "90d": (90,  "day",   "Last 90 days · 30-day linear forecast"),
    "1y":  (365, "month", "Last 12 months · 6-month linear forecast"),
}


def fetch_waste_trend(cursor, trend: str):
    """Waste trend points from waste_snapshots for a given range key.

    Returns (trend, granularity, subtitle, rows) — daily points for
    7d/30d/90d, monthly averages for 1y.
    """
    if trend not in TREND_RANGES:
        trend = "30d"
    trend_days, granularity, subtitle = TREND_RANGES[trend]
    if granularity == "month":
        cursor.execute("""
            SELECT date_trunc('month', snapshot_date)::date as date,
                   AVG(daily_total) as total_waste
            FROM (
                SELECT snapshot_date, COALESCE(SUM(total_eur), 0) as daily_total
                FROM waste_snapshots
                WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
                GROUP BY snapshot_date
            ) d
            GROUP BY 1
            ORDER BY 1
        """, (trend_days,))
    else:
        cursor.execute("""
            SELECT snapshot_date as date, COALESCE(SUM(total_eur), 0) as total_waste
            FROM waste_snapshots
            WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
            GROUP BY snapshot_date
            ORDER BY snapshot_date
        """, (trend_days,))
    return trend, granularity, subtitle, cursor.fetchall()


def fetch_waste_by_resource(cursor, range_key: str):
    """Waste by resource type averaged over a range window (waste_snapshots).

    Monthly rate per type = sum over the window / number of snapshot days,
    so a type absent on some days is correctly diluted instead of overstated.
    The resource count shown is the latest snapshot's count in the window.
    Returns (range_key, subtitle, rows).
    """
    if range_key not in TREND_RANGES:
        range_key = "30d"
    days = TREND_RANGES[range_key][0]
    label = {"7d": "last 7 days", "30d": "last 30 days",
             "90d": "last 90 days", "1y": "last 12 months"}[range_key]
    cursor.execute("""
        WITH win AS (
            SELECT snapshot_date, resource_type, total_eur, resource_count
            FROM waste_snapshots
            WHERE snapshot_date >= CURRENT_DATE - %s * INTERVAL '1 day'
        ),
        days AS (
            SELECT COUNT(DISTINCT snapshot_date) AS n FROM win
        ),
        latest AS (
            SELECT DISTINCT ON (resource_type) resource_type, resource_count
            FROM win
            ORDER BY resource_type, snapshot_date DESC
        )
        SELECT w.resource_type,
               SUM(w.total_eur) / NULLIF((SELECT n FROM days), 0) AS total_eur,
               l.resource_count AS cnt,
               (SELECT MIN(snapshot_date) FROM win) AS period_start,
               (SELECT MAX(snapshot_date) FROM win) AS period_end
        FROM win w
        JOIN latest l USING (resource_type)
        GROUP BY w.resource_type, l.resource_count
        ORDER BY total_eur DESC
    """, (days,))
    rows = cursor.fetchall()

    # Show the dates actually covered: fresh installs have less history
    # than the theoretical window, and "last 30 days" alone would overclaim
    subtitle = f"Avg monthly waste · {label}"
    if rows:
        start, end = rows[0]["period_start"], rows[0]["period_end"]
        if start == end:
            subtitle += f" · {end.strftime('%-d %b %Y')}"
        elif start.year == end.year:
            subtitle += f" · {start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"
        else:
            subtitle += f" · {start.strftime('%-d %b %Y')} – {end.strftime('%-d %b %Y')}"
    return range_key, subtitle, rows


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, conn=Depends(get_db), trend: str = "30d"):
    """Executive dashboard with KPIs and charts."""
    cursor = conn.cursor()

    # Fetch KPIs including new CTO metrics
    cursor.execute("""
        WITH metrics AS (
            SELECT COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_monthly
            FROM recommendations WHERE status = 'pending'
        ),
        savings AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as verified_savings
            FROM savings_realized
        ),
        waste AS (
            SELECT COUNT(*) as waste_count,
                   COALESCE(SUM(monthly_waste_eur), 0) as active_monthly
            FROM active_waste
        ),
        -- Waste the user reviewed and declined to remediate: still real
        -- spend (the resource keeps costing), but not actionable — shown
        -- separately so "Monthly Waste" never reads as a pending backlog.
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        ),
        failed AS (
            SELECT COUNT(*) as failed_7d
            FROM actions_log
            WHERE action_status = 'failed'
              AND action_date >= NOW() - INTERVAL '7 days'
        ),
        cumulative AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as total_saved
            FROM savings_realized
        ),
        pending AS (
            SELECT COUNT(*) as pending_count
            FROM recommendations
            WHERE status = 'pending'
        ),
        last_scan AS (
            SELECT MAX(updated_at) as last_analysis
            FROM waste_detected
        )
        SELECT
            m.potential_monthly,
            s.verified_savings,
            w.waste_count,
            w.active_monthly,
            d.declined_count,
            d.declined_monthly,
            f.failed_7d,
            c.total_saved as cumulative_savings,
            p.pending_count,
            l.last_analysis
        FROM metrics m
        CROSS JOIN savings s
        CROSS JOIN waste w
        CROSS JOIN declined d
        CROSS JOIN failed f
        CROSS JOIN cumulative c
        CROSS JOIN pending p
        CROSS JOIN last_scan l;
    """)
    kpis = cursor.fetchone()

    # Cost of inaction: first detection date + daily burn rate (active waste)
    cursor.execute("""
        SELECT
            MIN(detection_date) as first_detection,
            COALESCE(SUM(monthly_waste_eur), 0) / %s as daily_burn
        FROM active_waste
    """, (DAYS_PER_MONTH,))
    inaction_row = cursor.fetchone()

    # Trend: active-waste totals from waste_snapshots (stable history written
    # by detector runs + one-shot backfill)
    trend, trend_granularity, trend_subtitle, waste_trend = fetch_waste_trend(cursor, trend)

    # Waste cost by resource type, averaged over the default 30-day window
    # (same source as the trend chart so both cards agree)
    resource_range, resource_subtitle, waste_by_resource = fetch_waste_by_resource(cursor, "30d")

    # LLM spend over the last 30 days: totals averaged per day actually
    # covered (dividing by 30 would understate the rate when tracking just
    # started), plus a per-feature breakdown for the AI Spend card
    cursor.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) as cost_usd,
               COUNT(*) as calls,
               COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) as tokens,
               GREATEST(CURRENT_DATE - MIN(called_at::date) + 1, 1) as days_covered
        FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days'
    """)
    llm_row = cursor.fetchone()

    cursor.execute("""
        SELECT feature,
               COUNT(*) as calls,
               COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) as tokens,
               COALESCE(SUM(cost_usd), 0) as cost_usd
        FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days'
        GROUP BY feature
        ORDER BY cost_usd DESC
    """)
    llm_features = cursor.fetchall()

    cursor.execute("""
        SELECT DISTINCT model FROM llm_usage
        WHERE called_at >= NOW() - INTERVAL '30 days' AND model IS NOT NULL
        ORDER BY model
    """)
    llm_models = [r['model'] for r in cursor.fetchall()]

    # Already burned: cumulative EUR actually lost, one daily-rate slice per
    # snapshot day (total_eur is a monthly rate, hence /DAYS_PER_MONTH).
    # Backfilled history is a floor: resources cleaned before tracking are
    # invisible.
    cursor.execute("""
        WITH daily AS (
            SELECT snapshot_date, SUM(total_eur) / %s AS rate
            FROM waste_snapshots
            GROUP BY snapshot_date
        )
        SELECT
            (SELECT COALESCE(SUM(rate), 0) FROM daily) AS burned,
            (SELECT MIN(snapshot_date) FROM daily) AS since,
            (SELECT rate FROM daily ORDER BY snapshot_date DESC LIMIT 1) AS current_rate,
            (SELECT rate FROM daily WHERE snapshot_date <= CURRENT_DATE - 30
             ORDER BY snapshot_date DESC LIMIT 1) AS rate_30d_ago
    """, (DAYS_PER_MONTH,))
    burned_row = cursor.fetchone()

    cursor.close()

    burned_total = float(burned_row['burned']) if burned_row else 0
    burned_since = burned_row['since'] if burned_row else None
    burn_delta = None
    if burned_row and burned_row['rate_30d_ago'] is not None:
        burn_delta = float(burned_row['current_rate']) - float(burned_row['rate_30d_ago'])

    ai_usage = None
    ai_daily_cost = None
    ai_roi = None
    if llm_row and llm_row['calls']:
        ai_daily_cost = float(llm_row['cost_usd']) * USD_TO_EUR / llm_row['days_covered']
        ai_usage = {
            "cost_eur": float(llm_row['cost_usd']) * USD_TO_EUR,
            "calls": llm_row['calls'],
            "tokens": llm_row['tokens'],
            "models": llm_models,
            "provider": _llm_provider(llm_models),
            "features": [
                {
                    "feature": f['feature'],
                    "calls": f['calls'],
                    "tokens": f['tokens'],
                    "cost_eur": float(f['cost_usd']) * USD_TO_EUR,
                }
                for f in llm_features
            ],
        }

    daily_burn = float(inaction_row['daily_burn']) if inaction_row else 0
    if ai_daily_cost:
        ai_roi = daily_burn / ai_daily_cost
    first_detection = inaction_row['first_detection'] if inaction_row else None

    return templates.TemplateResponse(request, "dashboard.html", context={
        "kpis": kpis,
        "waste_trend": waste_trend,
        "trend_range": trend,
        "trend_granularity": trend_granularity,
        "trend_subtitle": trend_subtitle,
        "waste_by_resource": waste_by_resource,
        "resource_range": resource_range,
        "resource_subtitle": resource_subtitle,
        "daily_burn": daily_burn,
        "first_detection": first_detection,
        "burned_total": burned_total,
        "burned_since": burned_since,
        "burn_delta": burn_delta,
        "ai_usage": ai_usage,
        "ai_daily_cost": ai_daily_cost,
        "ai_roi": ai_roi,
    })


@app.get("/api/dashboard/trend")
async def api_dashboard_trend(conn=Depends(get_db), range: str = "30d"):
    """Waste trend points for a range key — feeds the dashboard chart via AJAX."""
    cursor = conn.cursor()
    trend, granularity, subtitle, rows = fetch_waste_trend(cursor, range)
    cursor.close()
    return {
        "range": trend,
        "granularity": granularity,
        "subtitle": subtitle,
        "points": [
            {"date": str(r["date"]), "total": float(r["total_waste"] or 0)}
            for r in rows
        ],
    }


@app.get("/api/dashboard/waste-by-resource")
async def api_dashboard_waste_by_resource(conn=Depends(get_db), range: str = "30d"):
    """Waste by resource type for a range key — feeds the bar chart via AJAX."""
    cursor = conn.cursor()
    range_key, subtitle, rows = fetch_waste_by_resource(cursor, range)
    cursor.close()
    return {
        "range": range_key,
        "subtitle": subtitle,
        "items": [
            {
                "resource_type": r["resource_type"],
                "total": float(r["total_eur"] or 0),
                "count": r["cnt"] or 0,
            }
            for r in rows
        ],
    }


@app.get("/recommendations", response_class=HTMLResponse)
async def recommendations(
    request: Request,
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0
):
    """Recommendations management page."""
    cursor = conn.cursor()

    # Build query with filters
    query = """
        SELECT
            r.id,
            r.recommendation_type,
            w.resource_id,
            w.resource_type,
            r.estimated_monthly_savings_eur,
            w.confidence_score,
            r.action_required,
            r.status,
            r.created_at,
            w.metadata->>'instance_type' as instance_type,
            (w.metadata->>'cpu_avg_7d')::numeric as cpu_avg,
            (w.metadata->>'monthly_cost_eur')::numeric as monthly_cost,
            w.metadata->>'instance_state' as instance_state,
            w.metadata->>'size_gb' as volume_size_gb,
            w.metadata->>'vol_type' as volume_type,
            COALESCE(w.metadata->>'region', w.metadata->>'az') as volume_region,
            w.metadata->>'name' as volume_name,
            w.metadata->>'public_ip' as public_ip,
            COALESCE((w.metadata->>'age_days')::integer, CURRENT_DATE - w.detection_date) as age_days,
            w.metadata->>'description' as snap_description,
            r.ai_insight
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending'
    """
    params = []

    if type_filter != "All":
        query += " AND r.recommendation_type = %s"
        params.append(type_filter)

    if min_savings > 0:
        query += " AND r.estimated_monthly_savings_eur >= %s"
        params.append(min_savings)

    if min_confidence > 0:
        query += " AND w.confidence_score >= %s"
        params.append(min_confidence)

    query += " ORDER BY r.estimated_monthly_savings_eur DESC LIMIT 500"

    cursor.execute(query, params if params else None)
    recommendations = cursor.fetchall()

    # Summary stats
    total_savings = sum(r['estimated_monthly_savings_eur'] or 0 for r in recommendations)
    avg_confidence = sum(r['confidence_score'] or 0 for r in recommendations) / len(recommendations) if recommendations else 0

    ec2_recs  = [r for r in recommendations if r['resource_type'] == 'ec2_instance']
    # The EBS tab renders deletion semantics ("unattached", "why delete?"),
    # so it only gets delete_volume recs; gp2 migrations go to Other
    ebs_recs  = [r for r in recommendations if r['resource_type'] == 'ebs_volume'
                 and r['recommendation_type'] == 'delete_volume']
    eip_recs  = [r for r in recommendations if r['resource_type'] == 'elastic_ip']
    snap_recs = [r for r in recommendations if r['resource_type'] == 'ebs_snapshot']
    # Catch-all so recommendations from new detectors (NAT gateways, load
    # balancers, gp2 migrations, ...) are never silently hidden
    bucketed = {id(r) for r in ec2_recs + ebs_recs + eip_recs + snap_recs}
    other_recs = [r for r in recommendations if id(r) not in bucketed]

    # Approvals waiting out their grace period (cancellable)
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.execute_after,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type,
               CEIL(EXTRACT(EPOCH FROM r.execute_after - NOW()) / 86400)::int
                   AS days_left
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'scheduled'
        ORDER BY r.execute_after
        LIMIT 100
    """)
    scheduled_recs = cursor.fetchall()

    # Remediations awaiting human review as a Terraform PR
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.pr_url,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pr_open'
        ORDER BY r.estimated_monthly_savings_eur DESC
        LIMIT 100
    """)
    pr_open_recs = cursor.fetchall()

    # Distinguishes "the collector never ran" from "it ran and everything got
    # resolved" — an empty pending list means very different things, and the
    # generic placeholder used to claim the collector hadn't run even when
    # waste_detected already held resolved history (dismissed/applied/
    # approved/obsolete).
    cursor.execute("SELECT EXISTS (SELECT 1 FROM waste_detected) AS exists_flag")
    has_waste_history = cursor.fetchone()['exists_flag']

    cursor.close()

    return templates.TemplateResponse(request, "recommendations.html", context={
        "has_waste_history": has_waste_history,
        "pr_open_recs": pr_open_recs,
        "recommendations": recommendations,
        "ec2_recs": ec2_recs,
        "ebs_recs": ebs_recs,
        "eip_recs": eip_recs,
        "snap_recs": snap_recs,
        "other_recs": other_recs,
        "scheduled_recs": scheduled_recs,
        "total_savings": total_savings,
        "avg_confidence": avg_confidence,
        "type_filter": type_filter,
        "min_savings": min_savings,
        "min_confidence": min_confidence
    })


@app.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    conn=Depends(get_db),
    status_filter: str = "All",
    action_filter: str = "All",
    days_back: int = 30
):
    """Action history and audit trail."""
    cursor = conn.cursor()

    query = """
        SELECT
            a.id,
            a.resource_id,
            a.resource_type,
            a.action_type,
            a.action_status,
            a.dry_run,
            a.action_date,
            a.error_message,
            a.executed_by
        FROM actions_log a
        WHERE a.action_date >= NOW() - INTERVAL '%s days'
    """
    params = [days_back]

    if status_filter != "All":
        query += " AND a.action_status = %s"
        params.append(status_filter)

    if action_filter != "All":
        query += " AND a.action_type = %s"
        params.append(action_filter)

    query += " ORDER BY a.action_date DESC LIMIT 100"

    cursor.execute(query, tuple(params))
    actions = cursor.fetchall()

    # Summary. Anything that isn't success/failed (pending, blocked, ...)
    # is bucketed as "other" so the three counts always add up to the
    # total shown — a status this doesn't yet know about still gets
    # counted somewhere instead of silently vanishing from the header.
    success_count = sum(1 for a in actions if a['action_status'] == 'success')
    failed_count = sum(1 for a in actions if a['action_status'] == 'failed')
    other_count = len(actions) - success_count - failed_count

    cursor.close()

    return templates.TemplateResponse(request, "history.html", context={
        "actions": actions,
        "success_count": success_count,
        "failed_count": failed_count,
        "other_count": other_count,
        "status_filter": status_filter,
        "action_filter": action_filter,
        "days_back": days_back
    })


def _resolve_report_period(month, start, end, days):
    """Shared filter resolution for the report routes (400 on bad input)."""
    from utils.reports import resolve_period
    try:
        return resolve_period(month=month, start=start, end=end, days=days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/reports", response_class=HTMLResponse)
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


@app.get("/api/reports/download")
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


@app.post("/api/reports/narrative")
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


@app.get("/api/briefing/today")
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


@app.post("/api/recommendations/{rec_id}/ask")
def ask_about_recommendation(rec_id: int, body: AskQuestionRequest, conn=Depends(get_db)):
    """One-shot AI answer to a question about a specific recommendation.

    Stateless (no conversation history) and scoped to this recommendation's
    own data — same guardrails as the ai_insight generation it sits next to.
    Sync route on purpose: the LLM call blocks up to 20s and must run in
    the threadpool, not on the event loop.
    """
    # src/ is a package importable from the repo root, not from ui/ — same
    # sys.path trick as ui/utils/remediator.py's backend integration.
    import sys
    backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from src.core.llm import answer_question

    question = (body.question or '').strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.action_required, r.estimated_monthly_savings_eur,
               w.resource_type, w.confidence_score, w.metadata
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.id = %s
    """, (rec_id,))
    row = cursor.fetchone()
    cursor.close()

    if row is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    metadata = row['metadata'] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    answer = answer_question(
        question, row['action_required'], row['resource_type'],
        row['estimated_monthly_savings_eur'], row['confidence_score'],
        metadata, conn=conn,
    )
    if answer is None:
        return JSONResponse(
            {"answer": None, "error": "AI is not configured or the request failed"},
            status_code=503,
        )
    return JSONResponse({"answer": answer})


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Live log viewer for debugging (in-memory, current UI process)."""
    return templates.TemplateResponse(request, "logs.html")


@app.get("/api/logs")
async def api_logs(
    after_id: int = 0,
    level: str = "DEBUG",
    q: str = "",
    limit: int = Query(500, ge=1, le=2000)
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

    return JSONResponse(handler.query(
        after_id=after_id, min_levelno=min_levelno, search=q, limit=limit))


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, conn=Depends(get_db)):
    """Settings and configuration page."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()
    config = config_manager.load_config()

    # Database stats
    cursor = conn.cursor()
    cursor.execute("""
        WITH counts AS (
            SELECT
                (SELECT COUNT(*) FROM ec2_metrics) as ec2_metrics,
                (SELECT COUNT(*) FROM waste_detected) as waste_detected,
                (SELECT COUNT(*) FROM recommendations) as recommendations,
                (SELECT COUNT(*) FROM actions_log) as actions_log,
                (SELECT COUNT(*) FROM savings_realized) as savings_realized
        )
        SELECT * FROM counts;
    """)
    stats = cursor.fetchone()
    cursor.close()

    from utils.action_registry import EXECUTION_MODES
    automatable_actions = [
        {"type": t, "mode": m} for t, m in EXECUTION_MODES.items()
        if m in ('boto3', 'remediator')
    ]

    return templates.TemplateResponse(request, "settings.html", context={
        "config": config,
        "stats": stats,
        "automatable_actions": automatable_actions
    })


CLOUD_REGIONS = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']


@app.get("/cloud-resources", response_class=HTMLResponse)
async def cloud_resources(
    request: Request,
    tab: str = Query("ec2"),
    state_filter: str = Query("all"),
    region_filter: str = Query("all")
):
    """Cloud resources inventory page - EC2, Volumes, Elastic IPs, VPCs."""
    try:
        import boto3  # noqa: F401 - fail fast if the AWS SDK is missing
    except ImportError:
        raise HTTPException(status_code=500, detail="boto3 not installed")
    from utils.aws_clients import get_client

    def _tag_name(tags):
        return next((t['Value'] for t in (tags or []) if t['Key'] == 'Name'), '-')

    def _fetch_ec2(region):
        try:
            ec2 = get_client('ec2', region=region)
            result = []
            for r in ec2.describe_instances().get('Reservations', []):
                for inst in r.get('Instances', []):
                    launch = inst.get('LaunchTime')
                    result.append({
                        'instance_id': inst['InstanceId'],
                        'name': _tag_name(inst.get('Tags')),
                        'type': inst['InstanceType'],
                        'state': inst['State']['Name'],
                        'region': region,
                        'launch_time': launch,
                        'public_ip': inst.get('PublicIpAddress', '-'),
                        'private_ip': inst.get('PrivateIpAddress', '-'),
                    })
            return result
        except Exception as e:
            print(f"EC2 error {region}: {e}")
            return []

    def _fetch_volumes(region):
        try:
            ec2 = get_client('ec2', region=region)
            result = []
            for vol in ec2.describe_volumes().get('Volumes', []):
                attachments = vol.get('Attachments', [])
                result.append({
                    'volume_id': vol['VolumeId'],
                    'name': _tag_name(vol.get('Tags')),
                    'size_gb': vol['Size'],
                    'state': vol['State'],
                    'type': vol['VolumeType'],
                    'az': vol['AvailabilityZone'],
                    'region': region,
                    'encrypted': vol.get('Encrypted', False),
                    'attached_to': attachments[0]['InstanceId'] if attachments else '-',
                })
            return result
        except Exception as e:
            print(f"Volumes error {region}: {e}")
            return []

    def _fetch_ips(region):
        try:
            ec2 = get_client('ec2', region=region)
            result = []
            for addr in ec2.describe_addresses().get('Addresses', []):
                result.append({
                    'allocation_id': addr.get('AllocationId', '-'),
                    'public_ip': addr.get('PublicIp', '-'),
                    'private_ip': addr.get('PrivateIpAddress', '-'),
                    'instance_id': addr.get('InstanceId', '-'),
                    'domain': addr.get('Domain', '-'),
                    'region': region,
                    'associated': bool(addr.get('InstanceId') or addr.get('NetworkInterfaceId')),
                })
            return result
        except Exception as e:
            print(f"IPs error {region}: {e}")
            return []

    def _fetch_vpcs(region):
        try:
            ec2 = get_client('ec2', region=region)
            result = []
            for vpc in ec2.describe_vpcs().get('Vpcs', []):
                result.append({
                    'vpc_id': vpc['VpcId'],
                    'name': _tag_name(vpc.get('Tags')),
                    'cidr': vpc['CidrBlock'],
                    'state': vpc['State'],
                    'is_default': vpc.get('IsDefault', False),
                    'region': region,
                })
            return result
        except Exception as e:
            print(f"VPCs error {region}: {e}")
            return []

    def _fetch_snapshots(region):
        try:
            ec2 = get_client('ec2', region=region)
            result = []
            for snap in ec2.describe_snapshots(OwnerIds=['self']).get('Snapshots', []):
                start = snap.get('StartTime')
                result.append({
                    'snapshot_id': snap['SnapshotId'],
                    'description': snap.get('Description') or '-',
                    'volume_id': snap.get('VolumeId') or '-',
                    'size_gb': snap.get('VolumeSize', 0),
                    'state': snap['State'],
                    'start_time': start,
                    'encrypted': snap.get('Encrypted', False),
                    'region': region,
                })
            return result
        except Exception as e:
            print(f"Snapshots error {region}: {e}")
            return []

    def _fetch_s3():
        try:
            s3 = get_client('s3')
            result = []
            for bucket in s3.list_buckets().get('Buckets', []):
                created = bucket.get('CreationDate')
                try:
                    loc = s3.get_bucket_location(Bucket=bucket['Name'])
                    region = loc.get('LocationConstraint') or 'us-east-1'
                except Exception:
                    region = '-'
                result.append({
                    'name': bucket['Name'],
                    'created': created,
                    'region': region,
                })
            return result
        except Exception as e:
            print(f"S3 error: {e}")
            return []

    # Fetch all resource types in parallel (snapshots per region + S3 global)
    with ThreadPoolExecutor(max_workers=len(CLOUD_REGIONS) * 5 + 1) as executor:
        ec2_futs  = [executor.submit(_fetch_ec2,        r) for r in CLOUD_REGIONS]
        vol_futs  = [executor.submit(_fetch_volumes,    r) for r in CLOUD_REGIONS]
        ip_futs   = [executor.submit(_fetch_ips,        r) for r in CLOUD_REGIONS]
        vpc_futs  = [executor.submit(_fetch_vpcs,       r) for r in CLOUD_REGIONS]
        snap_futs = [executor.submit(_fetch_snapshots,  r) for r in CLOUD_REGIONS]
        s3_fut    =  executor.submit(_fetch_s3)

    instances = [i   for f in ec2_futs  for i   in f.result()]
    volumes   = [v   for f in vol_futs  for v   in f.result()]
    ips       = [ip  for f in ip_futs   for ip  in f.result()]
    vpcs      = [vpc for f in vpc_futs  for vpc in f.result()]
    snapshots = [s   for f in snap_futs for s   in f.result()]
    buckets   = s3_fut.result()

    # Apply region filter (S3 not filtered — global service)
    if region_filter != 'all':
        instances = [i for i in instances if i['region'] == region_filter]
        volumes   = [v for v in volumes   if v['region'] == region_filter]
        ips       = [ip for ip in ips     if ip['region'] == region_filter]
        vpcs      = [vpc for vpc in vpcs  if vpc['region'] == region_filter]
        snapshots = [s for s in snapshots if s['region'] == region_filter]

    if state_filter != 'all':
        instances = [i for i in instances if i['state'] == state_filter]

    instances.sort(key=lambda x: (x['state'] != 'running', x['name']))
    volumes.sort(key=lambda x: (x['state'] != 'in-use', x['region']))
    ips.sort(key=lambda x: (not x['associated'], x['region']))
    vpcs.sort(key=lambda x: (not x['is_default'], x['region']))
    snapshots.sort(key=lambda x: x['start_time'] or '', reverse=True)
    buckets.sort(key=lambda x: x['name'])

    return templates.TemplateResponse(request, "cloud_resources.html", context={
        "tab": tab,
        "instances": instances,
        "volumes": volumes,
        "ips": ips,
        "vpcs": vpcs,
        "snapshots": snapshots,
        "buckets": buckets,
        "state_filter": state_filter,
        "region_filter": region_filter,
        "regions": CLOUD_REGIONS,
        "ec2_count":  len(instances),
        "running_count": sum(1 for i in instances if i['state'] == 'running'),
        "stopped_count": sum(1 for i in instances if i['state'] == 'stopped'),
        "vol_count":  len(volumes),
        "ip_count":   len(ips),
        "vpc_count":  len(vpcs),
        "snap_count": len(snapshots),
        "s3_count":   len(buckets),
    })


# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.get("/api/metrics")
async def api_metrics(conn=Depends(get_db)):
    """Get dashboard metrics as JSON."""
    cursor = conn.cursor()
    cursor.execute("""
        WITH metrics AS (
            SELECT
                COALESCE(SUM(estimated_monthly_savings_eur)
                         FILTER (WHERE status = 'pending'), 0) as potential_savings,
                COUNT(*) FILTER (WHERE status = 'pending') as pending_count
            FROM recommendations
        ),
        actions AS (
            SELECT COUNT(*) as success_count
            FROM actions_log
            WHERE action_status = 'success'
        )
        SELECT m.potential_savings, m.pending_count, a.success_count
        FROM metrics m CROSS JOIN actions a;
    """)
    result = cursor.fetchone()
    cursor.close()

    return {
        "potential_savings": float(result['potential_savings']),
        "pending_count": int(result['pending_count']),
        "actions_count": int(result['success_count'])
    }


@app.get("/api/recommendations")
async def api_recommendations(
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0,
    limit: int = 100
):
    """Get recommendations as JSON."""
    cursor = conn.cursor()

    query = """
        SELECT
            r.id,
            r.recommendation_type,
            w.resource_id,
            r.estimated_monthly_savings_eur,
            w.confidence_score,
            r.action_required,
            r.status,
            r.created_at,
            w.metadata->>'instance_type' as instance_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending'
    """
    params = []

    if type_filter != "All":
        query += " AND r.recommendation_type = %s"
        params.append(type_filter)

    if min_savings > 0:
        query += " AND r.estimated_monthly_savings_eur >= %s"
        params.append(min_savings)

    if min_confidence > 0:
        query += " AND w.confidence_score >= %s"
        params.append(min_confidence)

    query += f" ORDER BY r.estimated_monthly_savings_eur DESC LIMIT {limit}"

    cursor.execute(query, params if params else None)
    results = cursor.fetchall()
    cursor.close()

    return {"recommendations": results, "count": len(results)}


def _execute_ec2_boto3(instance_id, rec_type, metadata):
    """Stop/terminate an EC2 instance via boto3, trying likely regions.

    Returns (success, error_message). Shared by the approval API and the
    grace-period executor job.
    """
    try:
        from utils.aws_clients import get_client
        regions = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']
        # Use stored region if available
        stored_region = (metadata or {}).get('region')
        if stored_region:
            regions = [stored_region] + [r for r in regions if r != stored_region]
        region_errors = []

        for region in regions:
            try:
                # Stop/terminate: remediation context, use the write role
                ec2 = get_client('ec2', region=region, write=True)

                # EC2 instance actions only
                if rec_type in ('stop_instance', 'terminate_instance'):
                    response = ec2.describe_instances(
                        Filters=[{'Name': 'instance-id', 'Values': [instance_id]}]
                    )
                    if not response['Reservations']:
                        continue
                    instance_state = response['Reservations'][0]['Instances'][0]['State']['Name']
                    if instance_state in ['terminated', 'shutting-down']:
                        return True, None
                    if rec_type == 'stop_instance':
                        ec2.stop_instances(InstanceIds=[instance_id])
                        print(f"Stopped instance {instance_id} in {region}")
                    elif rec_type == 'terminate_instance':
                        ec2.terminate_instances(InstanceIds=[instance_id])
                        print(f"Terminated instance {instance_id} in {region}")
                    return True, None

            except Exception as e:
                region_errors.append(f"{region}: {type(e).__name__}: {e}")
                continue

        if region_errors:
            return False, "Errors: " + " | ".join(region_errors)
        return False, f"Resource {instance_id} not found in any region"

    except ImportError:
        return False, "boto3 not installed"
    except Exception as e:
        return False, str(e)


@app.post("/api/actions")
async def api_execute_actions(action_request: ActionRequest, conn=Depends(get_db)):
    """Execute actions on recommendations."""
    cursor = conn.cursor()
    results = []

    for rec_id in action_request.recommendation_ids:
        try:
            if action_request.action == "reject":
                # Reject recommendation
                cursor.execute("""
                    UPDATE recommendations
                    SET status = 'rejected', applied_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (rec_id,))
                result = cursor.fetchone()
                reject_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "rejected"
                }
                results.append(reject_result)
                log_remediation_action("reject", [rec_id], reject_result, dry_run=False)

            elif action_request.action == "dismiss":
                # Permanently stop counting this item as active waste
                # (unlike reject, it drops out of active_waste for good).
                cursor.execute("""
                    UPDATE recommendations
                    SET status = 'dismissed', applied_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (rec_id,))
                result = cursor.fetchone()
                dismiss_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "dismissed"
                }
                results.append(dismiss_result)
                log_remediation_action("dismiss", [rec_id], dismiss_result, dry_run=False)

            elif action_request.action == "cancel":
                # Cancel a scheduled execution during its grace period
                cursor.execute("""
                    UPDATE recommendations
                    SET status = 'pending', execute_after = NULL
                    WHERE id = %s AND status = 'scheduled'
                    RETURNING id
                """, (rec_id,))
                result = cursor.fetchone()
                results.append({
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "cancelled",
                    **({} if result else {"error": "not in scheduled state"})
                })

            elif action_request.action in ("approve", "execute"):
                # Get resource info
                cursor.execute("""
                    SELECT w.resource_id, w.resource_type, r.recommendation_type,
                           w.metadata, w.confidence_score,
                           r.estimated_monthly_savings_eur, r.action_required
                    FROM recommendations r
                    JOIN waste_detected w ON r.waste_id = w.id
                    WHERE r.id = %s
                """, (rec_id,))
                row = cursor.fetchone()

                if row:
                    instance_id   = row['resource_id']
                    resource_type = row['resource_type']
                    rec_type      = row['recommendation_type']
                    metadata      = row['metadata'] or {}
                    action_type   = rec_type.replace('_instance', '').replace('_volume', '').replace('_snapshot', '')
                    aws_success   = True
                    aws_error     = None

                    # Execute real AWS action if NOT in dry-run mode (read from config, ignore client value)
                    dry_run = _config_manager.get_dry_run()

                    # GitOps routing: recommendations above the terraform_pr
                    # threshold (or of a PR-required type) become a Terraform
                    # PR instead of an AWS action. Not-Terraform-managed
                    # resources return None and take the normal path below.
                    from utils.terraform_pr import maybe_open_pr
                    pr_result = maybe_open_pr(conn, rec_id, row, dry_run)
                    if pr_result is not None:
                        results.append(pr_result)
                        continue

                    # Execution mode comes from the central registry
                    # (ui/utils/action_registry.py) — the guard test forces
                    # every detector's recommendation type to be declared there
                    mode = execution_mode(rec_type)

                    # Per-action opt-out (Settings > Automated actions):
                    # a disabled automated action degrades to manual review —
                    # the decision is recorded, AWS is not touched
                    if mode != 'manual' and not _config_manager.get_action_enabled(rec_type):
                        mode = 'manual'

                    # Grace period: a real approval is scheduled, not executed.
                    # The grace_executor_job applies it once execute_after is
                    # reached, unless cancelled meanwhile. Dry-run and manual
                    # decisions stay immediate (nothing to delay).
                    grace_days = _config_manager.get_grace_period_days()
                    if grace_days > 0 and not dry_run and mode != 'manual':
                        cursor.execute("""
                            UPDATE recommendations
                            SET status = 'scheduled',
                                execute_after = NOW() + make_interval(days => %s)
                            WHERE id = %s AND status = 'pending'
                            RETURNING execute_after
                        """, (grace_days, rec_id))
                        scheduled = cursor.fetchone()
                        if scheduled is None:
                            results.append({
                                "recommendation_id": rec_id,
                                "success": False,
                                "error": "not in pending state"
                            })
                            continue
                        cursor.execute("""
                            INSERT INTO actions_log
                            (resource_id, recommendation_id, resource_type,
                             action_type, action_status, dry_run, action_date, metadata)
                            VALUES (%s, %s, %s, %s, 'pending', false, NOW(), %s)
                        """, (instance_id, rec_id, resource_type, action_type,
                              Json({'grace_period_days': grace_days,
                                    'execute_after': scheduled['execute_after'].isoformat()})))
                        results.append({
                            "recommendation_id": rec_id,
                            "instance_id": instance_id,
                            "success": True,
                            "scheduled": True,
                            "execute_after": scheduled['execute_after'].isoformat(),
                            "action": rec_type
                        })
                        continue

                    # Backend remediators (safeguards + rollback snapshot +
                    # live waste re-verification), in dry-run and real mode alike
                    if mode == 'remediator':
                        try:
                            from utils.remediator import RemediatorProxy
                            proxy = RemediatorProxy(dry_run=dry_run)
                            result = proxy.execute_recommendations(conn, [rec_id])[0]
                            result['action'] = rec_type
                        except Exception as e:
                            result = {
                                'recommendation_id': rec_id,
                                'instance_id': instance_id,
                                'success': False,
                                'error': str(e),
                                'action': rec_type,
                            }
                        results.append(result)
                        continue

                    # The boto3 block below only automates EC2 stop/terminate.
                    # Every other type is manual-review: approving records the
                    # human decision, execution stays manual — attempting AWS
                    # calls here would fail with a misleading "not found".
                    manual_review = mode != 'boto3'
                    if not dry_run and not manual_review:
                        aws_success, aws_error = _execute_ec2_boto3(
                            instance_id, rec_type, metadata)

                    # Log action
                    action_status = 'success' if (dry_run or aws_success) else 'failed'
                    cursor.execute("""
                        INSERT INTO actions_log
                        (resource_id, recommendation_id, resource_type, action_type, action_status, dry_run, action_date, error_message)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                        RETURNING id
                    """, (
                        instance_id,
                        rec_id,
                        resource_type,
                        action_type,
                        action_status,
                        # manual approvals never touch AWS: log them as dry-run
                        dry_run or manual_review,
                        aws_error
                    ))

                    # Update recommendation status. A dry-run touches no AWS
                    # resource: leaving the status untouched (still 'pending')
                    # keeps it counted as active waste instead of looking
                    # remediated when nothing was actually done. Manual review
                    # is a real human decision either way, so it always
                    # records as approved.
                    if manual_review:
                        new_status = 'approved'
                    elif dry_run:
                        new_status = None
                    else:
                        new_status = 'approved' if aws_success else 'pending'

                    if new_status is not None:
                        cursor.execute("""
                            UPDATE recommendations
                            SET status = %s, applied_at = NOW()
                            WHERE id = %s
                        """, (new_status, rec_id))

                    result_entry = {
                        "recommendation_id": rec_id,
                        "instance_id": instance_id,
                        "success": dry_run or aws_success,
                        "dry_run": dry_run,
                        "manual": manual_review,
                        "action": rec_type
                    }
                    if aws_error:
                        result_entry["error"] = aws_error
                    results.append(result_entry)
                else:
                    results.append({
                        "recommendation_id": rec_id,
                        "success": False,
                        "error": "Recommendation not found"
                    })

        except Exception as e:
            results.append({
                "recommendation_id": rec_id,
                "success": False,
                "error": str(e)
            })

    conn.commit()
    cursor.close()

    return {"results": results}


@app.post("/api/config")
async def api_update_config(update: ConfigUpdate):
    """Update configuration value."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()

    try:
        if update.key == "auto_remediation_enabled":
            success = config_manager.set_auto_remediation_enabled(update.value)
        elif update.key == "dry_run_days":
            success = config_manager.set_dry_run_days(update.value)
        elif update.key == "grace_period_days":
            success = config_manager.set_grace_period_days(update.value)
        elif update.key == "dry_run":
            success = config_manager.set_dry_run(update.value)
        elif update.key.startswith("terraform_pr:"):
            field = update.key[len("terraform_pr:"):]
            success = config_manager.set_terraform_pr_field(field, update.value)
        elif update.key.startswith("action:"):
            action_type = update.key[len("action:"):]
            from utils.action_registry import EXECUTION_MODES
            if EXECUTION_MODES.get(action_type) not in ('boto3', 'remediator'):
                raise HTTPException(
                    status_code=400,
                    detail=f"'{action_type}' is not an automatable action type")
            success = config_manager.set_action_enabled(
                action_type, bool(update.value))
        else:
            success = config_manager.update_protection_rule(update.key, update.value)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class PolicyImport(BaseModel):
    """Policy-as-code import request (YAML text)."""
    yaml_text: str


@app.get("/api/policies/export")
def api_policies_export():
    """Download the current remediation policy as versionable YAML."""
    from utils.policies import export_policy_yaml
    from utils.config_manager import ConfigManager

    content = export_policy_yaml(ConfigManager().load_config())
    filename = f"wasteless-policies_{datetime.now().strftime('%Y-%m-%d')}.yaml"
    return PlainTextResponse(content, media_type="application/x-yaml", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


@app.post("/api/policies/import")
def api_policies_import(payload: PolicyImport):
    """Validate and apply a policy YAML document (rejects unknown keys)."""
    from utils.policies import parse_policy_yaml
    from utils.config_manager import ConfigManager, ConfigValidationError

    try:
        config = parse_policy_yaml(payload.yaml_text)
    except ConfigValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not ConfigManager().save_config(config):
        raise HTTPException(status_code=500, detail="failed to write the policy file")
    return {"success": True, "sections": sorted(config.keys())}


@app.post("/api/whitelist")
async def api_whitelist(instance_id: str, action: str = "add"):
    """Add or remove instance from whitelist."""
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager()

    try:
        if action == "add":
            success = config_manager.add_instance_to_whitelist(instance_id)
        else:
            success = config_manager.remove_instance_from_whitelist(instance_id)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sync-aws")
async def api_sync_aws(conn=Depends(get_db)):
    """Synchronize recommendations with current AWS instance states."""
    import traceback

    try:
        import boto3
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"boto3 not installed: {e}")

    try:
        cursor = conn.cursor()

        # Pending recommendations grouped by resource type: only EC2
        # instances go through the state logic below; other types are
        # existence-checked with the proper API (an EIP id would never be
        # found by describe_instances and used to be wrongly obsoleted)
        cursor.execute("""
            SELECT w.resource_type, array_agg(DISTINCT w.resource_id) AS ids
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status IN ('pending', 'rejected')
            GROUP BY w.resource_type
        """)
        pending_by_type = {row['resource_type']: row['ids']
                           for row in cursor.fetchall()}
        pending_instances = pending_by_type.pop('ec2_instance', [])

        if not pending_instances and not pending_by_type:
            return {"synced": 0, "obsolete": 0, "message": "No pending recommendations"}

        total_checked = len(pending_instances) + sum(
            len(ids) for ids in pending_by_type.values())

        # Non-EC2 resources: obsolete recommendations whose resource is gone
        obsolete_count = 0
        if pending_by_type:
            from utils.aws_sync import find_vanished_resources
            vanished = find_vanished_resources(pending_by_type)
            for resource_type, ids in vanished.items():
                cursor.execute("""
                    UPDATE recommendations r
                    SET status = 'obsolete', applied_at = NOW()
                    FROM waste_detected w
                    WHERE r.waste_id = w.id
                    AND w.resource_type = %s
                    AND w.resource_id = ANY(%s)
                    AND r.status IN ('pending', 'rejected')
                """, (resource_type, ids))
                obsolete_count += cursor.rowcount

        # Query AWS for instance states (check multiple regions)
        regions_to_check = (['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']
                            if pending_instances else [])
        aws_states = {}

        for region in regions_to_check:
            try:
                from utils.aws_clients import get_client
                ec2 = get_client('ec2', region=region)
                # Use filters instead of InstanceIds to avoid errors for non-existent instances
                response = ec2.describe_instances(
                    Filters=[{'Name': 'instance-id', 'Values': pending_instances}]
                )
                for reservation in response.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        aws_states[instance['InstanceId']] = {
                            'state': instance['State']['Name'],
                            'region': region
                        }
            except Exception as e:
                # Log error but continue with other regions
                print(f"Error checking region {region}: {e}")
                continue

        # Update EC2 recommendations based on AWS state
        synced_count = 0

        for instance_id in pending_instances:
            aws_info = aws_states.get(instance_id)

            if aws_info is None:
                # Instance doesn't exist - mark as obsolete
                cursor.execute("""
                    UPDATE recommendations r
                    SET status = 'obsolete', applied_at = NOW()
                    FROM waste_detected w
                    WHERE r.waste_id = w.id
                    AND w.resource_id = %s
                    AND r.status IN ('pending', 'rejected')
                """, (instance_id,))
                obsolete_count += cursor.rowcount
            else:
                aws_state = aws_info['state']

                # Check if recommendation is still valid
                cursor.execute("""
                    SELECT r.id, r.recommendation_type
                    FROM recommendations r
                    JOIN waste_detected w ON r.waste_id = w.id
                    WHERE w.resource_id = %s AND r.status = 'pending'
                """, (instance_id,))

                for rec in cursor.fetchall():
                    rec_type = rec['recommendation_type']
                    should_obsolete = False

                    # Stop recommendation but instance already stopped/terminated
                    if rec_type == 'stop_instance' and aws_state in ('stopped', 'terminated'):
                        should_obsolete = True
                    # Terminate recommendation but instance already terminated
                    elif rec_type == 'terminate_instance' and aws_state == 'terminated':
                        should_obsolete = True

                    if should_obsolete:
                        cursor.execute("""
                            UPDATE recommendations
                            SET status = 'obsolete', applied_at = NOW()
                            WHERE id = %s
                        """, (rec['id'],))
                        obsolete_count += 1
                    else:
                        # Update the stored state in waste_detected metadata
                        cursor.execute("""
                            UPDATE waste_detected
                            SET metadata = jsonb_set(
                                COALESCE(metadata, '{}'::jsonb),
                                '{instance_state}',
                                %s::jsonb
                            )
                            WHERE resource_id = %s
                        """, (f'"{aws_state}"', instance_id))
                        synced_count += 1

        conn.commit()

        return {
            "synced": synced_count,
            "obsolete": obsolete_count,
            "total_checked": total_checked,
            "message": f"Synced {synced_count} instances, marked {obsolete_count} as obsolete"
        }

    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"Sync AWS error: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# =============================================================================
# RUN SERVER
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('STREAMLIT_SERVER_PORT', '8888'))
    uvicorn.run(app, host="0.0.0.0", port=port)
