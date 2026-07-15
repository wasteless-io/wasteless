"""Recommendations page, its JSON API, the estate-wide AI chat endpoint, and
the approve/reject/dismiss/cancel/execute action endpoint."""

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg2.extras import Json

from state import get_db, templates, _config_manager, _aws_status, USD_TO_EUR, DAYS_PER_MONTH
from schemas import ActionRequest, AskQuestionRequest
from jobs import _execute_ec2_boto3
from utils.action_registry import execution_mode
from utils.logger import log_remediation_action

router = APIRouter()

# Cost Explorer service each waste resource_type is billed under — used to cap
# estimated savings at the real per-service spend (coherence cap in the
# recommendations route). Several types share "EC2 - Other" (EBS, snapshots,
# EIP, NAT), so they're capped together against that single line. A type not
# in this map is left uncapped (cap = None) rather than hidden.
# The savings cap below compares estimates against the trailing-30-day Cost
# Explorer bill. A resource younger than that window barely appears in the
# bill, so capping it would wrongly crush a correct forward-looking estimate
# (stopping a week-old idle instance really does save its full monthly cost).
# Only resources at least this old are subject to the per-service cap.
BILLING_WINDOW_DAYS = 30

RTYPE_TO_CE_SERVICE = {
    "ec2_instance": "Amazon Elastic Compute Cloud - Compute",
    "ebs_volume": "EC2 - Other",
    "ebs_snapshot": "EC2 - Other",
    "elastic_ip": "EC2 - Other",
    "nat_gateway": "EC2 - Other",
    "load_balancer": "Amazon Elastic Load Balancing",
    "vpc": "Amazon Virtual Private Cloud",
}


@router.get("/recommendations", response_class=HTMLResponse)
def recommendations(
    request: Request,
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0,
):
    """Recommendations management page."""
    cursor = conn.cursor()

    # WHERE clause shared by the display query (capped at 500 rows) and the
    # summary-stats query below (uncapped) — otherwise, past 500 matching
    # rows, "Savings"/"conf." in the header would silently undercount
    # against the true filtered total (and against Home's unfiltered
    # pending_eur KPI), since they'd only reflect the top-500 subset shown.
    where_clause = "WHERE r.status = 'pending'"
    params = []

    if type_filter != "All":
        where_clause += " AND r.recommendation_type = %s"
        params.append(type_filter)

    if min_savings > 0:
        where_clause += " AND r.estimated_monthly_savings_eur >= %s"
        params.append(min_savings)

    if min_confidence > 0:
        where_clause += " AND w.confidence_score >= %s"
        params.append(min_confidence)

    cursor.execute(
        f"""
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
            GREATEST(COALESCE((w.metadata->>'age_days')::numeric, 0),
                     EXTRACT(EPOCH FROM (NOW() - w.created_at)) / 86400.0) as age_days_frac,
            w.metadata->>'description' as snap_description,
            r.ai_insight
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
        ORDER BY r.estimated_monthly_savings_eur DESC LIMIT 500
    """,  # noqa: S608 — where_clause is constant fragments; values are %s params
        params if params else None,
    )
    recommendations = cursor.fetchall()

    # Summary stats: true totals across every matching row, not just the
    # 500 shown in the table below.
    cursor.execute(
        f"""
        SELECT COUNT(*) as cnt,
               COALESCE(SUM(r.estimated_monthly_savings_eur), 0) as total_savings,
               COALESCE(AVG(w.confidence_score), 0) as avg_confidence
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
    """,  # noqa: S608 — where_clause is constant fragments; values are %s params
        params if params else None,
    )
    totals = cursor.fetchone()
    total_count = totals["cnt"]
    total_savings = float(totals["total_savings"])
    avg_confidence = float(totals["avg_confidence"])

    # Coherence cap (finops, display-time PER-SERVICE cap). A global cap on the
    # total hides per-service overstatements behind big uncovered lines (EC2
    # idle estimated at ~7 € while real EC2 compute is 0.26 €, masked by a
    # 6.81 € WAF bill). So each service's estimated savings are capped at that
    # service's own real 30-day Cost Explorer spend, then summed. Same filtered
    # set as total_savings (reuses where_clause); no data is mutated; the raw
    # estimate is disclosed in the note/tooltip.
    cursor.execute(
        """
        SELECT service,
               SUM(CASE WHEN currency = 'USD' THEN cost * %s ELSE cost END) AS eur
        FROM cloud_costs_raw
        WHERE usage_date >= CURRENT_DATE - 30
        GROUP BY service
        """,
        (USD_TO_EUR,),
    )
    service_spend = {row["service"]: float(row["eur"] or 0) for row in cursor.fetchall()}

    # Age-aware split: only resources old enough to have been billed through
    # the window are capped; younger ones keep their full estimate (their
    # forward-looking savings are real even though the trailing bill barely
    # saw them yet).
    cursor.execute(
        f"""
        SELECT w.resource_type,
               COALESCE(SUM(CASE WHEN COALESCE((w.metadata->>'age_days')::numeric,
                                               CURRENT_DATE - w.detection_date, 0) >= %s
                                 THEN r.estimated_monthly_savings_eur ELSE 0 END), 0) AS est_mature,
               COALESCE(SUM(CASE WHEN COALESCE((w.metadata->>'age_days')::numeric,
                                               CURRENT_DATE - w.detection_date, 0) < %s
                                 THEN r.estimated_monthly_savings_eur ELSE 0 END), 0) AS est_young
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        {where_clause}
        GROUP BY w.resource_type
    """,  # noqa: S608 — where_clause is constant fragments; values are %s params
        [BILLING_WINDOW_DAYS, BILLING_WINDOW_DAYS] + params,
    )
    # Buckets keyed by CE service; cap None = uncapped (unmapped types and
    # everything younger than the billing window land in "__uncapped__").
    savings_buckets: dict = {"__uncapped__": {"savings": 0.0, "cap": None}}
    for row in cursor.fetchall():
        service = RTYPE_TO_CE_SERVICE.get(row["resource_type"])
        est_mature = float(row["est_mature"])
        est_young = float(row["est_young"])
        if service:
            b = savings_buckets.setdefault(
                service, {"savings": 0.0, "cap": service_spend.get(service, 0.0)}
            )
            b["savings"] += est_mature
            savings_buckets["__uncapped__"]["savings"] += est_young
        else:
            savings_buckets["__uncapped__"]["savings"] += est_mature + est_young

    savings_capped = sum(
        b["savings"] if b["cap"] is None else min(b["savings"], b["cap"])
        for b in savings_buckets.values()
    )
    # tolerance avoids a note on pure rounding noise. The raw estimate stays
    # the headline (the "normal" savings the user expects); the capped figure
    # is disclosed as a "realistic" note under it when the cap bites.
    savings_over_spend = savings_capped < total_savings - 0.005

    # "Wasted so far": what the filtered resources have already cost since
    # their creation — monthly cost prorated per day of age (365/12 days per
    # month, same convention as everywhere else). Backward-looking cumulative
    # amount, unlike Savings/mo (a forward monthly rate); notably it stays
    # honest for resources younger than the 30-day billing window, which the
    # per-service cap squashes. Aggregated over every matching row (same
    # where_clause), not just the 500 displayed.
    # Age in FRACTIONAL days — "from creation until now", per the tile's
    # promise: a resource created this morning has already consumed a few
    # cents, and integer days would show 0.00 for its whole first day.
    # GREATEST covers both directions: metadata age_days wins for resources
    # older than their detection; hours-since-detection wins for fresh ones.
    age_frac_sql = """GREATEST(
            COALESCE((w.metadata->>'age_days')::numeric, 0),
            EXTRACT(EPOCH FROM (NOW() - w.created_at)) / 86400.0
        )"""
    cursor.execute(
        f"""
        SELECT COALESCE(SUM(
            COALESCE((w.metadata->>'monthly_cost_eur')::numeric,
                     r.estimated_monthly_savings_eur, 0)
            * {age_frac_sql}
        ), 0) / %s AS wasted
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
    """,  # noqa: S608 — constant SQL fragments; values are %s params
        [DAYS_PER_MONTH] + params,
    )
    wasted_so_far_total = float(cursor.fetchone()["wasted"])

    # Per-row realism factor: scale each service's rows by min(1, spend/est) so
    # the displayed per-row Savings sum to the capped headline (a row can't
    # claim more than its share of the service's real spend). Raw estimate kept
    # on the row (savings_raw) for the tooltip. Annotating `recommendations`
    # covers every tab list — they hold the same row objects.
    savings_factor = {}
    for key, b in savings_buckets.items():
        if b["cap"] is None or b["savings"] <= 0:
            savings_factor[key] = 1.0
        else:
            savings_factor[key] = min(1.0, b["cap"] / b["savings"])

    for row in recommendations:
        service = RTYPE_TO_CE_SERVICE.get(row["resource_type"])
        mature = float(row["age_days"] or 0) >= BILLING_WINDOW_DAYS
        if service and mature:
            row["bucket_key"] = service
            factor = savings_factor.get(service, 1.0)
        else:
            # Young or unmapped: full estimate, uncapped bucket.
            row["bucket_key"] = "__uncapped__"
            factor = 1.0
        raw = float(row["estimated_monthly_savings_eur"] or 0)
        row["savings_capped"] = raw * factor
        row["savings_is_capped"] = factor < 0.9995
        # Same factor scales the row's Monthly cost so an idle resource shows a
        # realistic cost ≈ its realistic saving (no 3.56 €/mo cost next to a
        # 0.13 € saving on one line). List price kept for the tooltip. Computed
        # here in float — the metadata value is a Decimal and Decimal * float
        # raises in the template.
        mc_raw = float(row["monthly_cost"]) if row["monthly_cost"] is not None else raw
        row["monthly_cost_raw"] = mc_raw
        row["monthly_cost_capped"] = mc_raw * factor
        # Row's share of "Wasted so far" (same fractional-age formula as the
        # SQL aggregate); carried on the checkbox so live KPI deltas stay exact.
        row["wasted_so_far"] = mc_raw * float(row["age_days_frac"] or 0) / DAYS_PER_MONTH

    ec2_recs = [r for r in recommendations if r["resource_type"] == "ec2_instance"]
    # The EBS tab renders deletion semantics ("unattached", "why delete?"),
    # so it only gets delete_volume recs; gp2 migrations go to Other
    ebs_recs = [
        r
        for r in recommendations
        if r["resource_type"] == "ebs_volume" and r["recommendation_type"] == "delete_volume"
    ]
    eip_recs = [r for r in recommendations if r["resource_type"] == "elastic_ip"]
    snap_recs = [r for r in recommendations if r["resource_type"] == "ebs_snapshot"]
    rds_recs = [
        r for r in recommendations if r["resource_type"] in ("rds_instance", "rds_snapshot")
    ]
    # Catch-all so recommendations from new detectors (NAT gateways, load
    # balancers, gp2 migrations, AMIs, ...) are never silently hidden
    bucketed = {id(r) for r in ec2_recs + ebs_recs + eip_recs + snap_recs + rds_recs}
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
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'scheduled'")
    scheduled_total_count = cursor.fetchone()["n"]

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
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'pr_open'")
    pr_open_total_count = cursor.fetchone()["n"]

    # Manual-review recommendations the human confirmed but hasn't
    # necessarily deleted yet — wasteless never touches AWS for these, so
    # they need their own visible "still on you" section, not just a
    # History entry that reads like something already happened.
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.applied_at,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type,
               COALESCE(w.metadata->>'region', w.metadata->>'az', 'eu-west-1') AS region
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'approved_manual'
        ORDER BY r.applied_at
        LIMIT 100
    """)
    approved_manual_recs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'approved_manual'")
    approved_manual_total_count = cursor.fetchone()["n"]

    # Skipped (rejected) recommendations: dropped out of the pending list but
    # still counted as active waste. Surfaced here so the user can bring one
    # back to pending — re-detection never revives a 'rejected' reco on its
    # own, so without this there was no way to undo a Skip.
    cursor.execute("""
        SELECT r.id, r.recommendation_type, r.applied_at,
               r.estimated_monthly_savings_eur,
               w.resource_id, w.resource_type
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'rejected'
        ORDER BY r.applied_at DESC NULLS LAST
        LIMIT 100
    """)
    rejected_recs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) AS n FROM recommendations WHERE status = 'rejected'")
    rejected_total_count = cursor.fetchone()["n"]

    # Distinguishes "the collector never ran" from "it ran and everything got
    # resolved" — an empty pending list means very different things, and the
    # generic placeholder used to claim the collector hadn't run even when
    # waste_detected already held resolved history (dismissed/applied/
    # approved/obsolete).
    cursor.execute("SELECT EXISTS (SELECT 1 FROM waste_detected) AS exists_flag")
    has_waste_history = cursor.fetchone()["exists_flag"]

    cursor.close()

    return templates.TemplateResponse(
        request,
        "recommendations.html",
        context={
            # Server-local time (already Europe/Paris on this host), set by
            # sync_aws_job every 5 min and by POST /api/sync-aws — not a DB
            # timestamp, so no UTC conversion here.
            "aws_sync_at": _aws_status.get("checked_at"),
            "has_waste_history": has_waste_history,
            "pr_open_recs": pr_open_recs,
            "pr_open_total_count": pr_open_total_count,
            "scheduled_total_count": scheduled_total_count,
            "approved_manual_recs": approved_manual_recs,
            "approved_manual_total_count": approved_manual_total_count,
            "rejected_recs": rejected_recs,
            "rejected_total_count": rejected_total_count,
            "recommendations": recommendations,
            "ec2_recs": ec2_recs,
            "ebs_recs": ebs_recs,
            "eip_recs": eip_recs,
            "snap_recs": snap_recs,
            "rds_recs": rds_recs,
            "other_recs": other_recs,
            "scheduled_recs": scheduled_recs,
            "total_count": total_count,
            "total_savings": total_savings,
            "savings_capped": savings_capped,
            "savings_over_spend": savings_over_spend,
            "wasted_so_far_total": wasted_so_far_total,
            "savings_buckets": savings_buckets,
            "avg_confidence": avg_confidence,
            "type_filter": type_filter,
            "min_savings": min_savings,
            "min_confidence": min_confidence,
        },
    )


@router.get("/api/recommendations")
def api_recommendations(
    conn=Depends(get_db),
    type_filter: str = "All",
    min_savings: int = 0,
    min_confidence: float = 0.0,
    limit: int = Query(100, ge=1, le=500),
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

    query += " ORDER BY r.estimated_monthly_savings_eur DESC LIMIT %s"
    params.append(limit)

    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()

    return {"recommendations": results, "count": len(results)}


# CloudTrail LookupEvents est limite a ~2 req/s sur tout le compte : cache
# en memoire par ressource pour que des clics repetes (ou plusieurs onglets)
# ne consomment pas le quota. TTL court : l'historique bouge peu.
_HISTORY_CACHE: dict = {}
_HISTORY_CACHE_TTL_SECONDS = 600


@router.get("/api/recommendations/{rec_id}/resource-history")
def resource_history(rec_id: int, conn=Depends(get_db)):
    """CloudTrail history of this recommendation's resource: who created or
    modified it, and when — the context a human wants before approving.

    Uses LookupEvents (90 days of management events, no trail setup needed)
    in the resource's own region. Degrades gracefully when the readonly
    role lacks cloudtrail:LookupEvents (older onboarding stacks): the
    response says how to enable it instead of erroring.
    """
    import os
    import time

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT w.resource_id, w.metadata
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.id = %s
    """,
        (rec_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="recommendation not found")

    resource_id = row["resource_id"]
    cached = _HISTORY_CACHE.get(resource_id)
    if cached and cached[0] > time.time():
        return cached[1]

    metadata = row["metadata"] or {}
    region = metadata.get("region") or os.getenv("AWS_REGION", "eu-west-1")

    from utils.aws_clients import get_client

    try:
        cloudtrail = get_client("cloudtrail", region=region)
        response = cloudtrail.lookup_events(
            LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": resource_id}],
            MaxResults=20,
        )
        events = [
            {
                "time": event["EventTime"].isoformat(),
                "name": event.get("EventName", ""),
                "username": event.get("Username", ""),
                # EventId lets the UI deep-link to this exact event in the
                # CloudTrail console (no extra API call, URL is deterministic).
                "event_id": event.get("EventId", ""),
            }
            for event in response.get("Events", [])
        ]
        payload = {"available": True, "region": region, "events": events}
    except Exception as e:
        if "AccessDenied" in str(e):
            # Stack d'onboarding anterieure a l'ajout de la permission :
            # message actionnable plutot qu'une 500.
            payload = {
                "available": False,
                "hint": "The wasteless-readonly role lacks cloudtrail:LookupEvents "
                "— update your onboarding stack (onboarding/cloudformation or "
                "terraform) to enable resource history.",
            }
        else:
            payload = {"available": False, "hint": str(e)}

    _HISTORY_CACHE[resource_id] = (time.time() + _HISTORY_CACHE_TTL_SECONDS, payload)
    return payload


_RESOURCE_DETAILS_CACHE: dict = {}


def _date_field(label, dt):
    return {"label": label, "value": dt.isoformat(), "date": True} if dt else None


def _describe_instance(ec2, resource_id, region):
    inst = ec2.describe_instances(InstanceIds=[resource_id])["Reservations"][0]["Instances"][0]
    return (
        "Instance",
        f"#InstanceDetails:instanceId={resource_id}",
        inst.get("Tags", []),
        [
            {"label": "State", "value": inst.get("State", {}).get("Name")},
            {"label": "Type", "value": inst.get("InstanceType")},
            {
                "label": "Availability zone",
                "value": inst.get("Placement", {}).get("AvailabilityZone"),
            },
            # Honest label: LaunchTime resets on every stop/start — the true
            # creation is CloudTrail's RunInstances (the modal's Created box).
            _date_field("Last launched", inst.get("LaunchTime")),
            {"label": "Private IP", "value": inst.get("PrivateIpAddress")},
            {"label": "Public IP", "value": inst.get("PublicIpAddress")},
            {"label": "AMI", "value": inst.get("ImageId")},
            {"label": "VPC", "value": inst.get("VpcId")},
            {"label": "Subnet", "value": inst.get("SubnetId")},
            {"label": "Key pair", "value": inst.get("KeyName")},
            {"label": "Platform", "value": inst.get("PlatformDetails")},
            {"label": "Architecture", "value": inst.get("Architecture")},
        ],
    )


def _describe_volume(ec2, resource_id, region):
    vol = ec2.describe_volumes(VolumeIds=[resource_id])["Volumes"][0]
    attachments = ", ".join(a.get("InstanceId", "?") for a in vol.get("Attachments", []))
    return (
        "Volume",
        f"#VolumeDetails:volumeId={resource_id}",
        vol.get("Tags", []),
        [
            {"label": "State", "value": vol.get("State")},
            {"label": "Size", "value": f"{vol['Size']} GiB" if vol.get("Size") else None},
            {"label": "Type", "value": vol.get("VolumeType")},
            {"label": "IOPS", "value": vol.get("Iops")},
            {"label": "Throughput", "value": vol.get("Throughput")},
            {"label": "Availability zone", "value": vol.get("AvailabilityZone")},
            _date_field("Created", vol.get("CreateTime")),
            {"label": "Attached to", "value": attachments or "— (unattached)"},
            {"label": "Encrypted", "value": "yes" if vol.get("Encrypted") else "no"},
        ],
    )


def _describe_address(ec2, resource_id, region):
    addr = ec2.describe_addresses(AllocationIds=[resource_id])["Addresses"][0]
    associated = addr.get("InstanceId") or addr.get("NetworkInterfaceId")
    return (
        "Elastic IP",
        f"#ElasticIpDetails:AllocationId={resource_id}",
        addr.get("Tags", []),
        [
            {"label": "Public IP", "value": addr.get("PublicIp")},
            {"label": "Scope", "value": addr.get("Domain")},
            {"label": "Associated to", "value": associated or "— (unassociated)"},
            {"label": "Network border group", "value": addr.get("NetworkBorderGroup")},
            {"label": "Reverse DNS", "value": addr.get("PtrRecord")},
        ],
    )


def _describe_snapshot(ec2, resource_id, region):
    snap = ec2.describe_snapshots(SnapshotIds=[resource_id])["Snapshots"][0]
    return (
        "Snapshot",
        f"#SnapshotDetails:snapshotId={resource_id}",
        snap.get("Tags", []),
        [
            {"label": "State", "value": snap.get("State")},
            {"label": "Source volume", "value": snap.get("VolumeId")},
            {
                "label": "Volume size",
                "value": f"{snap['VolumeSize']} GiB" if snap.get("VolumeSize") else None,
            },
            _date_field("Created", snap.get("StartTime")),
            {"label": "Storage tier", "value": snap.get("StorageTier")},
            {"label": "Encrypted", "value": "yes" if snap.get("Encrypted") else "no"},
            {"label": "Description", "value": snap.get("Description")},
        ],
    )


_RESOURCE_DESCRIBERS = {
    "i-": _describe_instance,
    "vol-": _describe_volume,
    "eipalloc-": _describe_address,
    "snap-": _describe_snapshot,
}


@router.get("/api/recommendations/{rec_id}/resource-details")
def resource_details(rec_id: int, conn=Depends(get_db)):
    """Live characteristics of this recommendation's resource (EC2 instance,
    EBS volume, Elastic IP or snapshot, dispatched on the id prefix) — the
    recap shown when clicking a resource id, alongside the CloudTrail
    creation info the resource-history endpoint already provides.

    Same degrade-gracefully contract as resource-history: available=False
    plus an actionable hint instead of a 500.
    """
    import os
    import time

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT w.resource_id, w.resource_type, w.metadata
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.id = %s
    """,
        (rec_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="recommendation not found")
    resource_id = row["resource_id"]
    describe = next(
        (fn for prefix, fn in _RESOURCE_DESCRIBERS.items() if resource_id.startswith(prefix)),
        None,
    )
    if describe is None:
        raise HTTPException(status_code=400, detail="no details view for this resource type")

    cached = _RESOURCE_DETAILS_CACHE.get(resource_id)
    if cached and cached[0] > time.time():
        return cached[1]

    metadata = row["metadata"] or {}
    region = metadata.get("region") or os.getenv("AWS_REGION", "eu-west-1")

    from utils.aws_clients import get_client

    try:
        ec2 = get_client("ec2", region=region)
        kind, console_fragment, tags, fields = describe(ec2, resource_id, region)
        payload = {
            "available": True,
            "region": region,
            "kind": kind,
            "console_url": (
                f"https://console.aws.amazon.com/ec2/v2/home?region={region}{console_fragment}"
            ),
            "name": next((t["Value"] for t in tags if t["Key"] == "Name"), None),
            "fields": [f for f in fields if f and f.get("value") not in (None, "")],
            "tags": [{"key": t["Key"], "value": t["Value"]} for t in tags],
        }
    except Exception as e:
        if "NotFound" in str(e) or ".Malformed" in str(e):
            hint = "Resource not found on AWS — it may have been deleted since detection."
        elif "AccessDenied" in str(e) or "UnauthorizedOperation" in str(e):
            hint = (
                "The wasteless-readonly role lacks the ec2:Describe* permission "
                "for this resource in this region — check your onboarding stack."
            )
        else:
            hint = str(e)
        payload = {"available": False, "hint": hint}

    _RESOURCE_DETAILS_CACHE[resource_id] = (time.time() + _HISTORY_CACHE_TTL_SECONDS, payload)
    return payload


def _deletion_status(ec2, resource_id):
    """Live check: is this resource actually gone from AWS?

    Returns (deleted, state):
    - deleted True  → confirmed gone (describe raised *.NotFound, or — for an
      instance — its state is 'terminated', which lingers ~1h post-terminate);
    - deleted False → still there; state is its current lifecycle state so the
      UI can say "still running/available/…";
    - raises on anything else (access denied, throttling) so the caller can
      report "couldn't verify" rather than wrongly confirming a deletion.

    Never returns True on an ambiguous error — we only mark a to-do done when
    we can positively see the resource is gone.
    """
    try:
        if resource_id.startswith("i-"):
            reservations = ec2.describe_instances(InstanceIds=[resource_id])["Reservations"]
            if not reservations or not reservations[0]["Instances"]:
                return True, None
            state = reservations[0]["Instances"][0]["State"]["Name"]
            return (state == "terminated"), state
        if resource_id.startswith("vol-"):
            vols = ec2.describe_volumes(VolumeIds=[resource_id])["Volumes"]
            return (not vols), (vols[0]["State"] if vols else None)
        if resource_id.startswith("eipalloc-"):
            addrs = ec2.describe_addresses(AllocationIds=[resource_id])["Addresses"]
            if not addrs:
                return True, None
            return False, ("associated" if addrs[0].get("InstanceId") else "allocated")
        if resource_id.startswith("snap-"):
            snaps = ec2.describe_snapshots(SnapshotIds=[resource_id])["Snapshots"]
            return (not snaps), (snaps[0]["State"] if snaps else None)
    except Exception as e:
        # The *.NotFound error IS the confirmation the resource was deleted.
        if "NotFound" in str(e):
            return True, None
        raise
    return None, None


@router.get("/api/recommendations/manual-todos")
def manual_todos(conn=Depends(get_db)):
    """Manual-review recommendations the user marked as to-do (status
    approved_manual) — JSON for the shared notifications popover, which lives
    on every page and so can't read the recommendations route's template
    context. Newest first, capped like the pending list."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.id, r.recommendation_type,
               r.estimated_monthly_savings_eur,
               w.resource_id,
               COALESCE(w.metadata->>'region', w.metadata->>'az', 'eu-west-1') AS region
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.status = 'approved_manual'
        ORDER BY r.applied_at DESC
        LIMIT 5
    """)
    return {
        "manual_todos": [
            {
                "id": row["id"],
                "recommendation_type": row["recommendation_type"],
                "resource_id": row["resource_id"],
                "estimated_monthly_savings_eur": float(row["estimated_monthly_savings_eur"] or 0),
                "region": row["region"],
            }
            for row in cursor.fetchall()
        ]
    }


@router.get("/api/recommendations/{rec_id}/deletion-status")
def deletion_status(rec_id: int, conn=Depends(get_db)):
    """Whether this to-do's resource has actually been deleted on AWS — gates
    the "Done" button so a resource can't be marked done while it's still
    running. Live (uncached): the user just acted in the console.
    """
    import os

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT w.resource_id,
               COALESCE(w.metadata->>'region', w.metadata->>'az') AS region
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.id = %s
    """,
        (rec_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="recommendation not found")
    resource_id = row["resource_id"]
    region = row["region"] or os.getenv("AWS_REGION", "eu-west-1")
    # az value ('eu-west-1a') → region ('eu-west-1') for the API client.
    if region and region[-1].isalpha():
        region = region[:-1]

    from utils.aws_clients import get_client

    try:
        ec2 = get_client("ec2", region=region)
        deleted, state = _deletion_status(ec2, resource_id)
        return {"deleted": deleted, "state": state}
    except Exception as e:
        if "AccessDenied" in str(e) or "UnauthorizedOperation" in str(e):
            hint = "The wasteless-readonly role can't describe this resource — can't verify."
        else:
            hint = f"Couldn't verify on AWS: {e}"
        return {"deleted": None, "hint": hint}


def _fmt_rec_line(r) -> str:
    """One pending recommendation as a single `key=value | …` line for the
    estate chat prompt. Every column the UI shows is included; type-specific
    fields that are NULL for this row are dropped so the model only ever sees
    facts that exist (nothing to hallucinate around)."""
    parts = [
        str(r["action_required"]),
        str(r["resource_type"]),
        str(r["resource_id"]),
        f"savings={float(r['savings']):.2f} EUR/mo",
        f"confidence={float(r['confidence']) * 100:.0f}%",
    ]
    if r["instance_type"]:
        parts.append(f"type={r['instance_type']}")
    if r["instance_state"]:
        parts.append(f"state={r['instance_state']}")
    if r["cpu_avg"] is not None:
        parts.append(f"avg_cpu_7d={float(r['cpu_avg']):.1f}%")
    if r["datapoints"] is not None:
        parts.append(f"datapoints={r['datapoints']}")
    if r["observation_days"] is not None:
        parts.append(f"observation_days={r['observation_days']}")
    if r["monthly_cost"] is not None:
        parts.append(f"monthly_cost={float(r['monthly_cost']):.2f} EUR/mo")
    if r["volume_size_gb"]:
        parts.append(f"size_gb={r['volume_size_gb']}")
    if r["volume_type"]:
        parts.append(f"volume_type={r['volume_type']}")
    if r["region"]:
        parts.append(f"region={r['region']}")
    if r["public_ip"]:
        parts.append(f"public_ip={r['public_ip']}")
    if r["age_days"] is not None:
        parts.append(f"age_days={r['age_days']}")
    if r["resource_name"]:
        parts.append(f"name={r['resource_name']}")
    if r["snap_description"]:
        parts.append(f"description={r['snap_description']}")
    return "- " + " | ".join(parts)


@router.post("/api/recommendations/chat")
def chat_about_recommendations(body: AskQuestionRequest, conn=Depends(get_db)):
    """One-shot AI answer to a question about ALL pending recommendations.

    Powers the chat in the summary tile (not scoped to one row). Stateless;
    sync route because the LLM call blocks and must run in the threadpool.
    """
    from core.llm import answer_estate_question

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    cursor = conn.cursor()
    # Pull every column the recommendations table shows (plus the confidence
    # drivers cpu/datapoints/observation_days) so the model can answer from
    # real per-row data instead of inventing figures. Fields are type-specific
    # and mostly NULL outside their resource type; _fmt_rec_line drops NULLs.
    cursor.execute("""
        SELECT r.action_required,
               COALESCE(r.estimated_monthly_savings_eur, 0) AS savings,
               w.resource_type, w.resource_id,
               COALESCE(w.confidence_score, 0) AS confidence,
               w.metadata->>'instance_type' AS instance_type,
               w.metadata->>'instance_state' AS instance_state,
               (w.metadata->>'cpu_avg_7d')::numeric AS cpu_avg,
               (w.metadata->>'datapoints')::int AS datapoints,
               (w.metadata->>'observation_days')::int AS observation_days,
               (w.metadata->>'monthly_cost_eur')::numeric AS monthly_cost,
               w.metadata->>'size_gb' AS volume_size_gb,
               w.metadata->>'vol_type' AS volume_type,
               COALESCE(w.metadata->>'region', w.metadata->>'az') AS region,
               w.metadata->>'name' AS resource_name,
               w.metadata->>'public_ip' AS public_ip,
               COALESCE((w.metadata->>'age_days')::integer,
                        CURRENT_DATE - w.detection_date) AS age_days,
               GREATEST(COALESCE((w.metadata->>'age_days')::numeric, 0),
                        EXTRACT(EPOCH FROM (NOW() - w.created_at)) / 86400.0)
                   AS age_days_frac,
               w.metadata->>'description' AS snap_description
        FROM recommendations r
        JOIN waste_detected w ON w.id = r.waste_id
        WHERE r.status = 'pending'
        ORDER BY r.estimated_monthly_savings_eur DESC NULLS LAST
        LIMIT 100
        """)
    rows = cursor.fetchall()
    cursor.close()

    count = len(rows)
    total_savings = sum(float(r["savings"]) for r in rows)
    avg_conf = (sum(float(r["confidence"]) for r in rows) / count * 100) if count else 0.0
    lines = "\n".join(_fmt_rec_line(r) for r in rows)

    # Same per-service coherence cap the summary tile shows ("realistic ·
    # X € (capped to real spend)") — computed here too so the model can
    # explain that figure instead of denying it exists.
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT service,
               SUM(CASE WHEN currency = 'USD' THEN cost * %s ELSE cost END) AS eur
        FROM cloud_costs_raw
        WHERE usage_date >= CURRENT_DATE - 30
        GROUP BY service
        """,
        (USD_TO_EUR,),
    )
    service_spend = {r["service"]: float(r["eur"] or 0) for r in cursor.fetchall()}
    cursor.close()
    # Age-aware, like the page: only resources older than the billing window
    # are capped against their service's real spend.
    bucket_est: dict = {}
    uncapped_total = 0.0
    for r in rows:
        service = RTYPE_TO_CE_SERVICE.get(r["resource_type"])
        if service and float(r["age_days"] or 0) >= BILLING_WINDOW_DAYS:
            bucket_est[service] = bucket_est.get(service, 0.0) + float(r["savings"])
        else:
            uncapped_total += float(r["savings"])
    capped_total = uncapped_total + sum(
        min(est, service_spend.get(service, 0.0)) for service, est in bucket_est.items()
    )
    capped_savings = f"{capped_total:.2f}" if capped_total < total_savings - 0.005 else None

    # Mirror of the "Wasted so far" tile (same formula), so the chat can
    # explain that figure too.
    wasted_total = sum(
        float(r["monthly_cost"] if r["monthly_cost"] is not None else r["savings"])
        * float(r["age_days_frac"] or 0)
        / DAYS_PER_MONTH
        for r in rows
    )

    answer = answer_estate_question(
        question,
        count,
        f"{total_savings:.2f}",
        f"{avg_conf:.0f}",
        lines,
        conn=conn,
        capped_savings=capped_savings,
        wasted_so_far=f"{wasted_total:.2f}",
    )
    if answer is None:
        return JSONResponse(
            {"answer": None, "error": "AI is not configured or the request failed"},
            status_code=503,
        )
    return JSONResponse({"answer": answer})


@router.post("/api/actions")
def api_execute_actions(action_request: ActionRequest, conn=Depends(get_db)):
    """Execute actions on recommendations."""
    cursor = conn.cursor()
    results = []

    for rec_id in action_request.recommendation_ids:
        try:
            if action_request.action == "reject":
                # Reject/skip a recommendation. Allowed from 'pending' (the tabs'
                # Skip button) and from 'approved_manual' (the To-do bell's Skip:
                # the user marked it to-do but changed their mind — it goes to
                # Skipped, still counted as waste, restorable). Still barred from
                # resolved states (approved/applied/obsolete/pr_open/dismissed)
                # so a stray call can't resurrect an already-handled resource as
                # active waste.
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'rejected', applied_at = NOW()
                    WHERE id = %s AND status IN ('pending', 'approved_manual')
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                reject_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "rejected",
                    **({} if result else {"error": "not in a skippable state"}),
                }
                results.append(reject_result)
                log_remediation_action("reject", [rec_id], reject_result, dry_run=False)

            elif action_request.action == "dismiss":
                # Permanently stop counting this item as active waste
                # (unlike reject, it drops out of active_waste for good).
                # Also allowed from 'approved_manual': the human confirmed a
                # manual-review recommendation but can still change their
                # mind before actually deleting anything on AWS.
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'dismissed', applied_at = NOW()
                    WHERE id = %s AND status IN ('pending', 'approved_manual')
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                dismiss_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "dismissed",
                    **({} if result else {"error": "not in pending state"}),
                }
                results.append(dismiss_result)
                log_remediation_action("dismiss", [rec_id], dismiss_result, dry_run=False)

            elif action_request.action == "cancel":
                # Cancel a scheduled execution during its grace period
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'pending', execute_after = NULL
                    WHERE id = %s AND status = 'scheduled'
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                if result is not None:
                    # Close out the log entry the scheduling created: left
                    # at 'pending' forever otherwise, History would show a
                    # migration that looks eternally in-flight even though
                    # it was called off.
                    cursor.execute(
                        """
                        UPDATE actions_log
                        SET action_status = 'cancelled', updated_at = NOW()
                        WHERE recommendation_id = %s AND action_status = 'pending'
                    """,
                        (rec_id,),
                    )
                results.append(
                    {
                        "recommendation_id": rec_id,
                        "success": result is not None,
                        "action": "cancelled",
                        **({} if result else {"error": "not in scheduled state"}),
                    }
                )

            elif action_request.action == "restore":
                # Un-skip: send a rejected recommendation back to the pending
                # queue so the user can act on it again. Restricted to
                # 'rejected' so it can never resurrect an already-resolved
                # item (applied/approved/obsolete/dismissed) as fresh waste.
                cursor.execute(
                    """
                    UPDATE recommendations
                    SET status = 'pending', applied_at = NULL
                    WHERE id = %s AND status = 'rejected'
                    RETURNING id
                """,
                    (rec_id,),
                )
                result = cursor.fetchone()
                restore_result = {
                    "recommendation_id": rec_id,
                    "success": result is not None,
                    "action": "restored",
                    **({} if result else {"error": "not in rejected state"}),
                }
                results.append(restore_result)
                log_remediation_action("restore", [rec_id], restore_result, dry_run=False)

            elif action_request.action in ("approve", "execute"):
                # Get resource info
                cursor.execute(
                    """
                    SELECT w.resource_id, w.resource_type, r.recommendation_type,
                           w.metadata, w.confidence_score,
                           r.estimated_monthly_savings_eur, r.action_required
                    FROM recommendations r
                    JOIN waste_detected w ON r.waste_id = w.id
                    WHERE r.id = %s
                """,
                    (rec_id,),
                )
                row = cursor.fetchone()

                if row:
                    instance_id = row["resource_id"]
                    resource_type = row["resource_type"]
                    rec_type = row["recommendation_type"]
                    metadata = row["metadata"] or {}
                    action_type = (
                        rec_type.replace("_instance", "")
                        .replace("_volume", "")
                        .replace("_snapshot", "")
                    )
                    aws_success = True
                    aws_error = None

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
                    if mode != "manual" and not _config_manager.get_action_enabled(rec_type):
                        mode = "manual"

                    # Grace period: a real approval is scheduled, not executed.
                    # The grace_executor_job applies it once execute_after is
                    # reached, unless cancelled meanwhile. Dry-run and manual
                    # decisions stay immediate (nothing to delay).
                    grace_days = _config_manager.get_grace_period_days()
                    if grace_days > 0 and not dry_run and mode != "manual":
                        cursor.execute(
                            """
                            UPDATE recommendations
                            SET status = 'scheduled',
                                execute_after = NOW() + make_interval(days => %s)
                            WHERE id = %s AND status = 'pending'
                            RETURNING execute_after
                        """,
                            (grace_days, rec_id),
                        )
                        scheduled = cursor.fetchone()
                        if scheduled is None:
                            results.append(
                                {
                                    "recommendation_id": rec_id,
                                    "success": False,
                                    "error": "not in pending state",
                                }
                            )
                            continue
                        cursor.execute(
                            """
                            INSERT INTO actions_log
                            (resource_id, recommendation_id, resource_type,
                             action_type, action_status, dry_run, action_date, metadata)
                            VALUES (%s, %s, %s, %s, 'pending', false, NOW(), %s)
                        """,
                            (
                                instance_id,
                                rec_id,
                                resource_type,
                                action_type,
                                Json(
                                    {
                                        "grace_period_days": grace_days,
                                        "execute_after": scheduled["execute_after"].isoformat(),
                                    }
                                ),
                            ),
                        )
                        results.append(
                            {
                                "recommendation_id": rec_id,
                                "instance_id": instance_id,
                                "success": True,
                                "scheduled": True,
                                "execute_after": scheduled["execute_after"].isoformat(),
                                "action": rec_type,
                            }
                        )
                        continue

                    # Backend remediators (safeguards + rollback snapshot +
                    # live waste re-verification), in dry-run and real mode alike
                    if mode == "remediator":
                        try:
                            from utils.remediator import RemediatorProxy

                            proxy = RemediatorProxy(dry_run=dry_run)
                            result = proxy.execute_recommendations(conn, [rec_id])[0]
                            result["action"] = rec_type
                        except Exception as e:
                            result = {
                                "recommendation_id": rec_id,
                                "instance_id": instance_id,
                                "success": False,
                                "error": str(e),
                                "action": rec_type,
                            }
                        if not dry_run and not result.get("success"):
                            from utils.notifications import notify_action_failure

                            notify_action_failure(rec_type, instance_id, result.get("error"))
                        results.append(result)
                        continue

                    # The boto3 block below only automates EC2 stop/terminate.
                    # Every other type is manual-review: approving records the
                    # human decision, execution stays manual — attempting AWS
                    # calls here would fail with a misleading "not found".
                    manual_review = mode != "boto3"
                    if not dry_run and not manual_review:
                        aws_success, aws_error = _execute_ec2_boto3(instance_id, rec_type, metadata)
                        if not aws_success:
                            from utils.notifications import notify_action_failure

                            notify_action_failure(rec_type, instance_id, aws_error)

                    # Log action
                    action_status = "success" if (dry_run or aws_success) else "failed"
                    cursor.execute(
                        """
                        INSERT INTO actions_log
                        (resource_id, recommendation_id, resource_type, action_type, action_status, dry_run, action_date, error_message)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                        RETURNING id
                    """,
                        (
                            instance_id,
                            rec_id,
                            resource_type,
                            action_type,
                            action_status,
                            # manual approvals never touch AWS: log them as dry-run
                            dry_run or manual_review,
                            aws_error,
                        ),
                    )

                    # Update recommendation status.
                    # Manual review is checked FIRST: "Mark as to-do" is a
                    # personal reminder that never touches AWS, so it's recorded
                    # as 'approved_manual' even under dry-run — dry-run only
                    # suppresses real AWS actions, and a to-do list of manual
                    # deletions is exactly the intended dry-run/manual workflow
                    # (nothing touched the resource; the human still deletes it
                    # themselves, so it stays counted in active_waste until a
                    # sync confirms it's gone). Automated types (boto3/
                    # remediator) stay a uniform no-op in dry-run: they DO touch
                    # AWS, so under dry-run they simulate and don't re-status.
                    if manual_review:
                        new_status = "approved_manual"
                    elif dry_run:
                        new_status = None
                    else:
                        new_status = "approved" if aws_success else "pending"

                    if new_status is not None:
                        cursor.execute(
                            """
                            UPDATE recommendations
                            SET status = %s, applied_at = NOW()
                            WHERE id = %s
                        """,
                            (new_status, rec_id),
                        )

                    result_entry = {
                        "recommendation_id": rec_id,
                        "instance_id": instance_id,
                        "success": dry_run or aws_success,
                        # A manual to-do is a real bookkeeping change, not a
                        # simulated no-op — don't flag it as dry-run, so the UI
                        # removes the row / reloads and doesn't say "no change".
                        "dry_run": dry_run and not manual_review,
                        "manual": manual_review,
                        "action": rec_type,
                    }
                    if aws_error:
                        result_entry["error"] = aws_error
                    results.append(result_entry)
                else:
                    results.append(
                        {
                            "recommendation_id": rec_id,
                            "success": False,
                            "error": "Recommendation not found",
                        }
                    )

        except Exception as e:
            results.append({"recommendation_id": rec_id, "success": False, "error": str(e)})

    conn.commit()
    cursor.close()

    return {"results": results}
