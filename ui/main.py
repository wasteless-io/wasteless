#!/usr/bin/env python3
"""
Wasteless.io - FastAPI Backend
==============================

Fast, lightweight API for cloud cost optimization dashboard.
Replaces Streamlit for better performance.

Author: Wasteless Team
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
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


def get_db():
    """Get database connection."""
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def sync_aws_job():
    """Background job to sync recommendations with AWS state."""
    try:
        import boto3
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        cursor = conn.cursor()

        # Get pending recommendations — EC2 instances only
        cursor.execute("""
            SELECT DISTINCT w.resource_id
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'pending'
              AND w.resource_type = 'ec2_instance'
        """)
        pending_instances = [row['resource_id'] for row in cursor.fetchall()]

        if not pending_instances:
            conn.close()
            return

        # Check AWS for instance states
        regions = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']
        aws_states = {}

        for region in regions:
            try:
                ec2 = boto3.client('ec2', region_name=region)
                response = ec2.describe_instances(
                    Filters=[{'Name': 'instance-id', 'Values': pending_instances}]
                )
                for reservation in response.get('Reservations', []):
                    for instance in reservation.get('Instances', []):
                        aws_states[instance['InstanceId']] = instance['State']['Name']
            except Exception:
                continue

        # Mark obsolete recommendations
        obsolete_count = 0
        for instance_id in pending_instances:
            state = aws_states.get(instance_id)

            if state is None or state == 'terminated':
                cursor.execute("""
                    UPDATE recommendations r
                    SET status = 'obsolete', applied_at = NOW()
                    FROM waste_detected w
                    WHERE r.waste_id = w.id
                    AND w.resource_id = %s
                    AND r.status = 'pending'
                """, (instance_id,))
                obsolete_count += cursor.rowcount

        conn.commit()
        conn.close()

        if obsolete_count > 0:
            print(f"🔄 Auto-sync: marked {obsolete_count} recommendations as obsolete")

    except Exception as e:
        print(f"⚠️ Auto-sync error: {e}")
    finally:
        from datetime import datetime as _dt
        _aws_status["reachable"] = check_aws_reachable()
        _aws_status["checked_at"] = _dt.now()


# Scheduler instance
scheduler = AsyncIOScheduler()

# Cached AWS reachability status (refreshed by sync job)
_aws_status = {"reachable": None, "checked_at": None}


def check_aws_reachable() -> bool:
    """Quick AWS connectivity check via STS."""
    try:
        import boto3
        boto3.client('sts').get_caller_identity()
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
        print("✅ Database connection OK")
    except Exception as e:
        print(f"⚠️ Database connection failed: {e}")

    # Start scheduler for auto-sync (every 5 minutes)
    scheduler.add_job(sync_aws_job, 'interval', minutes=5, id='aws_sync')
    scheduler.start()
    print("🔄 Auto-sync scheduler started (every 5 min)")

    yield

    # Shutdown
    scheduler.shutdown()
    print("👋 Shutting down...")


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
from utils.config_manager import ConfigManager
_config_manager = ConfigManager()
templates.env.globals['get_dry_run'] = _config_manager.get_dry_run


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class ActionRequest(BaseModel):
    """Request to execute actions on recommendations."""
    recommendation_ids: List[int]
    action: str  # 'approve', 'reject', 'execute'
    dry_run: bool = True


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    key: str
    value: str | int | float | bool


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
            SELECT COUNT(*) as pending_count
            FROM recommendations
            WHERE status = 'pending'
        ),
        waste AS (
            SELECT COALESCE(SUM(monthly_waste_eur), 0) as total_waste
            FROM waste_detected
        ),
        raw_costs AS (
            SELECT COALESCE(SUM(cost), 0) as total_spend
            FROM cloud_costs_raw
            WHERE usage_date >= DATE_TRUNC('month', CURRENT_DATE)
        ),
        savings AS (
            SELECT COALESCE(SUM(actual_savings_eur), 0) as savings_realized
            FROM savings_realized
        )
        SELECT
            p.pending_count,
            w.total_waste,
            r.total_spend,
            s.savings_realized,
            CASE WHEN r.total_spend > 0
                THEN ROUND((w.total_waste / r.total_spend * 100)::numeric, 1)
                ELSE NULL
            END as waste_rate
        FROM pending p
        CROSS JOIN waste w
        CROSS JOIN raw_costs r
        CROSS JOIN savings s;
    """)
    result = cursor.fetchone()

    # Waste by type (grouped)
    cursor.execute("""
        SELECT
            resource_type,
            COUNT(*) as cnt,
            COALESCE(SUM(monthly_waste_eur), 0) as total_eur
        FROM waste_detected
        GROUP BY resource_type
        ORDER BY total_eur DESC
    """)
    waste_by_type = cursor.fetchall()

    # Recent activity: mix detections + actions, sorted by time
    cursor.execute("""
        SELECT event_type, event_time, resource_type, cnt, amount, resource_id, action_status, error_message
        FROM (
            SELECT
                'detection' as event_type,
                MAX(created_at) as event_time,
                resource_type,
                COUNT(*) as cnt,
                COALESCE(SUM(monthly_waste_eur), 0) as amount,
                NULL::varchar as resource_id,
                NULL::varchar as action_status,
                NULL::text as error_message
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
                error_message
            FROM actions_log
        ) combined
        ORDER BY event_time DESC
        LIMIT 8
    """)
    recent_activity = cursor.fetchall()

    # Pending breakdown by resource type
    cursor.execute("""
        SELECT w.resource_type, COUNT(*) as cnt
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending'
        GROUP BY w.resource_type
        ORDER BY cnt DESC
    """)
    pending_breakdown = cursor.fetchall()

    # Last sync time
    cursor.execute("""
        SELECT MAX(updated_at) as last_sync FROM waste_detected
    """)
    last_sync_row = cursor.fetchone()
    last_sync = last_sync_row['last_sync'] if last_sync_row else None

    # Daily / Monthly costs (detected waste)
    cursor.execute("""
        SELECT
            COALESCE(SUM(monthly_waste_eur), 0) as monthly_cost,
            COALESCE(SUM(monthly_waste_eur), 0) / 30.0 as daily_cost
        FROM waste_detected
    """)
    cost_row = cursor.fetchone()
    daily_cost = float(cost_row['daily_cost']) if cost_row else 0
    monthly_cost = float(cost_row['monthly_cost']) if cost_row else 0

    # Trend: compare current vs 7 days ago (savings potential)
    cursor.execute("""
        SELECT COALESCE(SUM(estimated_monthly_savings_eur), 0) as savings_week_ago
        FROM recommendations
        WHERE created_at <= NOW() - INTERVAL '7 days'
    """)
    trend_row = cursor.fetchone()
    savings_week_ago = float(trend_row['savings_week_ago']) if trend_row else 0
    current_savings = float(result['total_waste']) if result else 0
    savings_trend = current_savings - savings_week_ago  # positive = more waste found

    cursor.close()

    system_health = {
        "db": True,  # we got here, so DB is connected
        "aws": _aws_status.get("reachable"),
        "scheduler": scheduler.running,
    }

    return templates.TemplateResponse(request, "index.html", context={
        "metrics": result,
        "waste_by_type": waste_by_type,
        "recent_activity": recent_activity,
        "system_health": system_health,
        "pending_breakdown": pending_breakdown,
        "last_sync": last_sync,
        "daily_cost": daily_cost,
        "monthly_cost": monthly_cost,
        "savings_trend": savings_trend,
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, conn=Depends(get_db)):
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
            SELECT COUNT(*) as waste_count FROM waste_detected
        ),
        actions AS (
            SELECT
                COUNT(CASE WHEN action_status='success' THEN 1 END)::float /
                NULLIF(COUNT(*), 0) * 100 as success_rate
            FROM actions_log
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
            COALESCE(a.success_rate, 0) as success_rate,
            c.total_saved as cumulative_savings,
            p.pending_count,
            l.last_analysis
        FROM metrics m
        CROSS JOIN savings s
        CROSS JOIN waste w
        CROSS JOIN actions a
        CROSS JOIN cumulative c
        CROSS JOIN pending p
        CROSS JOIN last_scan l;
    """)
    kpis = cursor.fetchone()

    # Cost of inaction: first detection date + daily burn rate
    cursor.execute("""
        SELECT
            MIN(detection_date) as first_detection,
            COALESCE(SUM(monthly_waste_eur), 0) / 30.0 as daily_burn
        FROM waste_detected
    """)
    inaction_row = cursor.fetchone()

    # Sparkline: daily totals last 30 days
    cursor.execute("""
        SELECT DATE(updated_at) as date, COALESCE(SUM(monthly_waste_eur), 0) as total_waste
        FROM waste_detected
        WHERE updated_at >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(updated_at)
        ORDER BY date
    """)
    waste_trend = cursor.fetchall()

    # Age distribution: resources grouped by how long they've been detected
    cursor.execute("""
        SELECT
            CASE
                WHEN CURRENT_DATE - detection_date <= 30 THEN '< 30 days'
                WHEN CURRENT_DATE - detection_date <= 90 THEN '31-90 days'
                ELSE '90+ days'
            END as age_bucket,
            COUNT(*) as cnt,
            COALESCE(SUM(monthly_waste_eur), 0) as total_eur,
            MIN(CURRENT_DATE - detection_date) as min_age
        FROM waste_detected
        GROUP BY age_bucket
        ORDER BY min_age
    """)
    age_distribution = cursor.fetchall()

    # Waste cost by resource type (kept for ROI summary)
    cursor.execute("""
        SELECT resource_type, SUM(monthly_waste_eur) as total_eur
        FROM waste_detected
        GROUP BY resource_type
        ORDER BY total_eur DESC
    """)
    waste_by_resource = cursor.fetchall()

    cursor.close()

    daily_burn = float(inaction_row['daily_burn']) if inaction_row else 0
    first_detection = inaction_row['first_detection'] if inaction_row else None

    return templates.TemplateResponse(request, "dashboard.html", context={
        "kpis": kpis,
        "waste_trend": waste_trend,
        "waste_by_resource": waste_by_resource,
        "age_distribution": age_distribution,
        "daily_burn": daily_burn,
        "first_detection": first_detection,
    })


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
            w.metadata->>'description' as snap_description
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
    ebs_recs  = [r for r in recommendations if r['resource_type'] == 'ebs_volume']
    eip_recs  = [r for r in recommendations if r['resource_type'] == 'elastic_ip']
    snap_recs = [r for r in recommendations if r['resource_type'] == 'ebs_snapshot']

    cursor.close()

    return templates.TemplateResponse(request, "recommendations.html", context={
        "recommendations": recommendations,
        "ec2_recs": ec2_recs,
        "ebs_recs": ebs_recs,
        "eip_recs": eip_recs,
        "snap_recs": snap_recs,
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
            a.action_type,
            a.action_status,
            a.dry_run,
            a.action_date,
            a.error_message,
            a.executed_by,
            r.estimated_monthly_savings_eur
        FROM actions_log a
        LEFT JOIN recommendations r ON a.recommendation_id = r.id
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

    # Summary
    success_count = sum(1 for a in actions if a['action_status'] == 'success')
    failed_count = sum(1 for a in actions if a['action_status'] == 'failed')
    total_savings = sum(a['estimated_monthly_savings_eur'] or 0 for a in actions)

    cursor.close()

    return templates.TemplateResponse(request, "history.html", context={
        "actions": actions,
        "success_count": success_count,
        "failed_count": failed_count,
        "total_savings": total_savings,
        "status_filter": status_filter,
        "action_filter": action_filter,
        "days_back": days_back
    })


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

    return templates.TemplateResponse(request, "settings.html", context={
        "config": config,
        "stats": stats
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
        import boto3
    except ImportError:
        raise HTTPException(status_code=500, detail="boto3 not installed")

    def _tag_name(tags):
        return next((t['Value'] for t in (tags or []) if t['Key'] == 'Name'), '-')

    def _fetch_ec2(region):
        try:
            ec2 = boto3.client('ec2', region_name=region)
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
            ec2 = boto3.client('ec2', region_name=region)
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
            ec2 = boto3.client('ec2', region_name=region)
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
            ec2 = boto3.client('ec2', region_name=region)
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
            ec2 = boto3.client('ec2', region_name=region)
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
            s3 = boto3.client('s3')
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
                COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_savings,
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
                results.append({
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "rejected"
                })

            elif action_request.action in ("approve", "execute"):
                # Get resource info
                cursor.execute("""
                    SELECT w.resource_id, w.resource_type, r.recommendation_type, w.metadata
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
                    if not dry_run:
                        try:
                            import boto3
                            regions = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']
                            # Use stored region if available
                            stored_region = metadata.get('region')
                            if stored_region:
                                regions = [stored_region] + [r for r in regions if r != stored_region]
                            executed = False
                            region_errors = []

                            for region in regions:
                                try:
                                    ec2 = boto3.client('ec2', region_name=region)

                                    # EC2 instance actions only
                                    if rec_type in ('stop_instance', 'terminate_instance'):
                                        response = ec2.describe_instances(
                                            Filters=[{'Name': 'instance-id', 'Values': [instance_id]}]
                                        )
                                        if not response['Reservations']:
                                            continue
                                        instance_state = response['Reservations'][0]['Instances'][0]['State']['Name']
                                        if instance_state in ['terminated', 'shutting-down']:
                                            executed = True
                                            break
                                        if rec_type == 'stop_instance':
                                            ec2.stop_instances(InstanceIds=[instance_id])
                                            print(f"✅ Stopped instance {instance_id} in {region}")
                                        elif rec_type == 'terminate_instance':
                                            ec2.terminate_instances(InstanceIds=[instance_id])
                                            print(f"✅ Terminated instance {instance_id} in {region}")
                                        executed = True
                                        break

                                except Exception as e:
                                    region_errors.append(f"{region}: {type(e).__name__}: {e}")
                                    continue

                            if not executed:
                                aws_success = False
                                if region_errors:
                                    aws_error = "Errors: " + " | ".join(region_errors)
                                else:
                                    aws_error = f"Resource {instance_id} not found in any region"

                        except ImportError:
                            aws_success = False
                            aws_error = "boto3 not installed"
                        except Exception as e:
                            aws_success = False
                            aws_error = str(e)

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
                        dry_run,
                        aws_error
                    ))

                    # Update recommendation status
                    new_status = 'approved' if (dry_run or aws_success) else 'pending'
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
        elif update.key == "dry_run":
            success = config_manager.set_dry_run(update.value)
        else:
            success = config_manager.update_protection_rule(update.key, update.value)

        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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

        # Get all pending recommendations with their instance IDs
        cursor.execute("""
            SELECT DISTINCT w.resource_id
            FROM recommendations r
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE r.status = 'pending'
        """)
        pending_instances = [row['resource_id'] for row in cursor.fetchall()]

        if not pending_instances:
            return {"synced": 0, "obsolete": 0, "message": "No pending recommendations"}

        # Query AWS for instance states (check multiple regions)
        regions_to_check = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']
        aws_states = {}

        for region in regions_to_check:
            try:
                ec2 = boto3.client('ec2', region_name=region)
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

        # Update recommendations based on AWS state
        obsolete_count = 0
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
                    AND r.status = 'pending'
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
            "total_checked": len(pending_instances),
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
