"""Live cloud resource inventory (EC2, EBS, EIP, VPC, snapshots, NAT
gateways, load balancers, AMIs, RDS, S3)."""

import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse

from fastapi import Depends

from state import templates, CLOUD_REGIONS, get_db
from schemas import TagRequest
from utils.logger import get_logger

router = APIRouter()

logger = get_logger("cloud_resources")


# Resource-id prefix -> our resource_type label, for the audit log.
_ID_PREFIX_TYPE = {
    "i-": "ec2_instance",
    "vol-": "ebs_volume",
    "snap-": "ebs_snapshot",
    "ami-": "ami",
    "vpc-": "vpc",
    "nat-": "nat_gateway",
    "eipalloc-": "elastic_ip",
}


def _infer_type(resource_id: str) -> str:
    for prefix, label in _ID_PREFIX_TYPE.items():
        if resource_id.startswith(prefix):
            return label
    return "ec2_resource"


@router.post("/api/cloud-resources/tag")
def tag_resources(req: TagRequest, conn=Depends(get_db)):
    """Apply one tag (key=value) to the selected EC2-family resources.

    Tagging is a write op but non-destructive (metadata only): it goes
    through the write role, is grouped per region (ec2:CreateTags is a
    per-region call), and every result is written to actions_log so the
    change is auditable like any other action.
    """
    if req.key.lower().startswith("aws:"):
        raise HTTPException(
            status_code=422, detail="Tag keys starting with 'aws:' are reserved by AWS"
        )

    from collections import defaultdict

    from utils.aws_clients import get_client

    by_region: dict = defaultdict(list)
    for r in req.resources:
        by_region[r.region].append(r.id)

    cursor = conn.cursor()
    results = []
    for region, ids in by_region.items():
        try:
            ec2 = get_client("ec2", region=region, write=True)
            ec2.create_tags(Resources=ids, Tags=[{"Key": req.key, "Value": req.value}])
            ok, err = True, None
        except Exception as e:  # noqa: BLE001 - surfaced per-resource to the UI
            ok, err = False, str(e)
            logger.warning("Tagging failed in %s: %s", region, err)
        for rid in ids:
            results.append({"id": rid, "success": ok, "error": err})
            cursor.execute(
                """
                INSERT INTO actions_log
                    (resource_id, resource_type, action_type, action_status,
                     dry_run, action_date, error_message, executed_by, metadata)
                VALUES (%s, %s, 'tag', %s, false, NOW(), %s, 'inventory_ui', %s)
                """,
                (
                    rid,
                    _infer_type(rid),
                    "success" if ok else "failed",
                    err,
                    json.dumps({"key": req.key, "value": req.value, "region": region}),
                ),
            )
    conn.commit()
    return {
        "tagged": sum(1 for r in results if r["success"]),
        "total": len(results),
        "results": results,
    }


@router.get("/cloud-resources", response_class=HTMLResponse)
def cloud_resources(
    request: Request,
    tab: str = Query("ec2"),
    state_filter: str = Query("all"),
    region_filter: str = Query("all"),
):
    """Cloud resources inventory page - EC2, Volumes, Elastic IPs, VPCs."""
    try:
        import boto3  # noqa: F401 - fail fast if the AWS SDK is missing
    except ImportError as e:
        raise HTTPException(status_code=500, detail="boto3 not installed") from e
    from utils.aws_clients import get_client

    def _tag_name(tags):
        return next((t["Value"] for t in (tags or []) if t["Key"] == "Name"), "-")

    def _user_tags(tags):
        # Tags worth showing in the inventory: drop the Name tag (it has its
        # own column) and AWS-managed 'aws:' tags (noise, not user-set).
        return [
            {"key": t["Key"], "value": t.get("Value", "")}
            for t in (tags or [])
            if t["Key"] != "Name" and not t["Key"].startswith("aws:")
        ]

    def _fetch_ec2(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            # describe_instances truncates past ~1000 results without a
            # paginator: an account with more instances than that in one
            # region would silently lose the rest from this inventory.
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate():
                for r in page.get("Reservations", []):
                    for inst in r.get("Instances", []):
                        launch = inst.get("LaunchTime")
                        result.append(
                            {
                                "instance_id": inst["InstanceId"],
                                "name": _tag_name(inst.get("Tags")),
                                "tags": _user_tags(inst.get("Tags")),
                                "type": inst["InstanceType"],
                                "state": inst["State"]["Name"],
                                "region": region,
                                "launch_time": launch,
                                "public_ip": inst.get("PublicIpAddress", "-"),
                                "private_ip": inst.get("PrivateIpAddress", "-"),
                            }
                        )
            return result
        except Exception as e:
            logger.error(f"EC2 error {region}: {e}")
            return []

    def _fetch_volumes(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            # Same truncation risk as describe_instances for accounts with
            # many EBS volumes in a single region.
            paginator = ec2.get_paginator("describe_volumes")
            for page in paginator.paginate():
                for vol in page.get("Volumes", []):
                    attachments = vol.get("Attachments", [])
                    result.append(
                        {
                            "volume_id": vol["VolumeId"],
                            "name": _tag_name(vol.get("Tags")),
                            "tags": _user_tags(vol.get("Tags")),
                            "size_gb": vol["Size"],
                            "state": vol["State"],
                            "type": vol["VolumeType"],
                            "az": vol["AvailabilityZone"],
                            "region": region,
                            "encrypted": vol.get("Encrypted", False),
                            "attached_to": attachments[0]["InstanceId"] if attachments else "-",
                        }
                    )
            return result
        except Exception as e:
            logger.error(f"Volumes error {region}: {e}")
            return []

    def _fetch_ips(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            for addr in ec2.describe_addresses().get("Addresses", []):
                result.append(
                    {
                        "allocation_id": addr.get("AllocationId", "-"),
                        "tags": _user_tags(addr.get("Tags")),
                        "public_ip": addr.get("PublicIp", "-"),
                        "private_ip": addr.get("PrivateIpAddress", "-"),
                        "instance_id": addr.get("InstanceId", "-"),
                        "domain": addr.get("Domain", "-"),
                        "region": region,
                        "associated": bool(
                            addr.get("InstanceId") or addr.get("NetworkInterfaceId")
                        ),
                    }
                )
            return result
        except Exception as e:
            logger.error(f"IPs error {region}: {e}")
            return []

    def _fetch_vpcs(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            for vpc in ec2.describe_vpcs().get("Vpcs", []):
                result.append(
                    {
                        "vpc_id": vpc["VpcId"],
                        "name": _tag_name(vpc.get("Tags")),
                        "tags": _user_tags(vpc.get("Tags")),
                        "cidr": vpc["CidrBlock"],
                        "state": vpc["State"],
                        "is_default": vpc.get("IsDefault", False),
                        "region": region,
                    }
                )
            return result
        except Exception as e:
            logger.error(f"VPCs error {region}: {e}")
            return []

    def _fetch_snapshots(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            # Snapshots are the most likely of the five to exceed 1000 in a
            # single region (orphaned backups accumulate for years), so the
            # truncation risk without a paginator is the most real here.
            paginator = ec2.get_paginator("describe_snapshots")
            for page in paginator.paginate(OwnerIds=["self"]):
                for snap in page.get("Snapshots", []):
                    start = snap.get("StartTime")
                    result.append(
                        {
                            "snapshot_id": snap["SnapshotId"],
                            "tags": _user_tags(snap.get("Tags")),
                            "description": snap.get("Description") or "-",
                            "volume_id": snap.get("VolumeId") or "-",
                            "size_gb": snap.get("VolumeSize", 0),
                            "state": snap["State"],
                            "start_time": start,
                            "encrypted": snap.get("Encrypted", False),
                            "region": region,
                        }
                    )
            return result
        except Exception as e:
            logger.error(f"Snapshots error {region}: {e}")
            return []

    def _fetch_rds(region):
        try:
            rds = get_client("rds", region=region)
            result = []
            paginator = rds.get_paginator("describe_db_instances")
            for page in paginator.paginate():
                for db in page.get("DBInstances", []):
                    result.append(
                        {
                            "db_id": db["DBInstanceIdentifier"],
                            "engine": db.get("Engine", "-"),
                            "engine_version": db.get("EngineVersion", "-"),
                            "class": db.get("DBInstanceClass", "-"),
                            "status": db.get("DBInstanceStatus", "-"),
                            "size_gb": db.get("AllocatedStorage", 0),
                            "storage_type": db.get("StorageType", "-"),
                            "multi_az": db.get("MultiAZ", False),
                            "region": region,
                        }
                    )
            return result
        except Exception as e:
            logger.error(f"RDS error {region}: {e}")
            return []

    def _fetch_rds_snapshots(region):
        try:
            rds = get_client("rds", region=region)
            result = []
            # Manual snapshots only: automated ones are managed by the
            # instance retention window and would flood the inventory.
            paginator = rds.get_paginator("describe_db_snapshots")
            for page in paginator.paginate(SnapshotType="manual"):
                for snap in page.get("DBSnapshots", []):
                    result.append(
                        {
                            "snapshot_id": snap["DBSnapshotIdentifier"],
                            "db_id": snap.get("DBInstanceIdentifier", "-"),
                            "engine": snap.get("Engine", "-"),
                            "size_gb": snap.get("AllocatedStorage", 0),
                            "status": snap.get("Status", "-"),
                            "created": snap.get("SnapshotCreateTime"),
                            "region": region,
                        }
                    )
            return result
        except Exception as e:
            logger.error(f"RDS snapshots error {region}: {e}")
            return []

    def _fetch_nat_gateways(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            for nat in ec2.describe_nat_gateways().get("NatGateways", []):
                result.append(
                    {
                        "nat_id": nat["NatGatewayId"],
                        "name": _tag_name(nat.get("Tags")),
                        "tags": _user_tags(nat.get("Tags")),
                        "state": nat.get("State", "-"),
                        "vpc_id": nat.get("VpcId", "-"),
                        "subnet_id": nat.get("SubnetId", "-"),
                        "type": nat.get("ConnectivityType", "public"),
                        "created": nat.get("CreateTime"),
                        "region": region,
                    }
                )
            return result
        except Exception as e:
            logger.error(f"NAT gateways error {region}: {e}")
            return []

    def _fetch_load_balancers(region):
        result = []
        try:
            elbv2 = get_client("elbv2", region=region)
            paginator = elbv2.get_paginator("describe_load_balancers")
            for page in paginator.paginate():
                for lb in page.get("LoadBalancers", []):
                    result.append(
                        {
                            "name": lb["LoadBalancerName"],
                            "arn": lb.get("LoadBalancerArn", "-"),
                            "lb_type": lb.get("Type", "application"),
                            "scheme": lb.get("Scheme", "-"),
                            "state": (lb.get("State") or {}).get("Code", "-"),
                            "vpc_id": lb.get("VpcId", "-"),
                            "created": lb.get("CreatedTime"),
                            "region": region,
                        }
                    )
        except Exception as e:
            logger.error(f"ELBv2 error {region}: {e}")
        try:
            elb = get_client("elb", region=region)
            for lb in elb.describe_load_balancers().get("LoadBalancerDescriptions", []):
                result.append(
                    {
                        "name": lb["LoadBalancerName"],
                        "arn": "-",
                        "lb_type": "classic",
                        "scheme": lb.get("Scheme", "-"),
                        "state": "-",
                        "vpc_id": lb.get("VPCId", "-"),
                        "created": lb.get("CreatedTime"),
                        "region": region,
                    }
                )
        except Exception as e:
            logger.error(f"Classic ELB error {region}: {e}")
        return result

    def _fetch_amis(region):
        try:
            ec2 = get_client("ec2", region=region)
            result = []
            # Self-owned only: public AMIs would flood the inventory
            paginator = ec2.get_paginator("describe_images")
            for page in paginator.paginate(Owners=["self"]):
                for image in page.get("Images", []):
                    snapshot_ids = [
                        m["Ebs"]["SnapshotId"]
                        for m in image.get("BlockDeviceMappings", [])
                        if "Ebs" in m and m["Ebs"].get("SnapshotId")
                    ]
                    result.append(
                        {
                            "image_id": image["ImageId"],
                            "name": image.get("Name") or _tag_name(image.get("Tags")),
                            "tags": _user_tags(image.get("Tags")),
                            "state": image.get("State", "-"),
                            "created": image.get("CreationDate", "-"),
                            "snapshot_count": len(snapshot_ids),
                            "public": image.get("Public", False),
                            "region": region,
                        }
                    )
            return result
        except Exception as e:
            logger.error(f"AMIs error {region}: {e}")
            return []

    def _fetch_s3():
        try:
            s3 = get_client("s3")
            result = []
            for bucket in s3.list_buckets().get("Buckets", []):
                created = bucket.get("CreationDate")
                try:
                    loc = s3.get_bucket_location(Bucket=bucket["Name"])
                    region = loc.get("LocationConstraint") or "us-east-1"
                except Exception as e:
                    # Cross-account buckets deny GetBucketLocation; show the
                    # bucket anyway, but keep the reason diagnosable.
                    logger.debug(f"get_bucket_location {bucket['Name']}: {e}")
                    region = "-"
                result.append(
                    {
                        "name": bucket["Name"],
                        "created": created,
                        "region": region,
                    }
                )
            return result
        except Exception as e:
            logger.error(f"S3 error: {e}")
            return []

    # Fetch all resource types in parallel (per region + S3 global)
    with ThreadPoolExecutor(max_workers=len(CLOUD_REGIONS) * 8 + 1) as executor:
        ec2_futs = [executor.submit(_fetch_ec2, r) for r in CLOUD_REGIONS]
        vol_futs = [executor.submit(_fetch_volumes, r) for r in CLOUD_REGIONS]
        ip_futs = [executor.submit(_fetch_ips, r) for r in CLOUD_REGIONS]
        vpc_futs = [executor.submit(_fetch_vpcs, r) for r in CLOUD_REGIONS]
        snap_futs = [executor.submit(_fetch_snapshots, r) for r in CLOUD_REGIONS]
        rds_futs = [executor.submit(_fetch_rds, r) for r in CLOUD_REGIONS]
        rds_snap_futs = [executor.submit(_fetch_rds_snapshots, r) for r in CLOUD_REGIONS]
        nat_futs = [executor.submit(_fetch_nat_gateways, r) for r in CLOUD_REGIONS]
        lb_futs = [executor.submit(_fetch_load_balancers, r) for r in CLOUD_REGIONS]
        ami_futs = [executor.submit(_fetch_amis, r) for r in CLOUD_REGIONS]
        s3_fut = executor.submit(_fetch_s3)

    instances = [i for f in ec2_futs for i in f.result()]
    volumes = [v for f in vol_futs for v in f.result()]
    ips = [ip for f in ip_futs for ip in f.result()]
    vpcs = [vpc for f in vpc_futs for vpc in f.result()]
    snapshots = [s for f in snap_futs for s in f.result()]
    databases = [d for f in rds_futs for d in f.result()]
    rds_snapshots = [s for f in rds_snap_futs for s in f.result()]
    nat_gateways = [n for f in nat_futs for n in f.result()]
    load_balancers = [lb for f in lb_futs for lb in f.result()]
    amis = [a for f in ami_futs for a in f.result()]
    buckets = s3_fut.result()

    # Apply region filter (S3 not filtered — global service)
    if region_filter != "all":
        instances = [i for i in instances if i["region"] == region_filter]
        volumes = [v for v in volumes if v["region"] == region_filter]
        ips = [ip for ip in ips if ip["region"] == region_filter]
        vpcs = [vpc for vpc in vpcs if vpc["region"] == region_filter]
        snapshots = [s for s in snapshots if s["region"] == region_filter]
        databases = [d for d in databases if d["region"] == region_filter]
        rds_snapshots = [s for s in rds_snapshots if s["region"] == region_filter]
        nat_gateways = [n for n in nat_gateways if n["region"] == region_filter]
        load_balancers = [lb for lb in load_balancers if lb["region"] == region_filter]
        amis = [a for a in amis if a["region"] == region_filter]

    if state_filter != "all":
        instances = [i for i in instances if i["state"] == state_filter]

    instances.sort(key=lambda x: (x["state"] != "running", x["name"]))
    volumes.sort(key=lambda x: (x["state"] != "in-use", x["region"]))
    ips.sort(key=lambda x: (not x["associated"], x["region"]))
    vpcs.sort(key=lambda x: (not x["is_default"], x["region"]))
    snapshots.sort(key=lambda x: x["start_time"] or "", reverse=True)
    databases.sort(key=lambda x: (x["status"] != "available", x["region"], x["db_id"]))
    rds_snapshots.sort(key=lambda x: x["created"] or "", reverse=True)
    nat_gateways.sort(key=lambda x: (x["state"] != "available", x["region"], x["nat_id"]))
    load_balancers.sort(key=lambda x: (x["region"], x["name"]))
    amis.sort(key=lambda x: x["created"] or "", reverse=True)
    buckets.sort(key=lambda x: x["name"])

    return templates.TemplateResponse(
        request,
        "cloud_resources.html",
        context={
            "tab": tab,
            "instances": instances,
            "volumes": volumes,
            "ips": ips,
            "vpcs": vpcs,
            "snapshots": snapshots,
            "databases": databases,
            "rds_snapshots": rds_snapshots,
            "buckets": buckets,
            "state_filter": state_filter,
            "region_filter": region_filter,
            "regions": CLOUD_REGIONS,
            "ec2_count": len(instances),
            "running_count": sum(1 for i in instances if i["state"] == "running"),
            "stopped_count": sum(1 for i in instances if i["state"] == "stopped"),
            "vol_count": len(volumes),
            "ip_count": len(ips),
            "vpc_count": len(vpcs),
            "snap_count": len(snapshots),
            "rds_count": len(databases) + len(rds_snapshots),
            "nat_gateways": nat_gateways,
            "load_balancers": load_balancers,
            "amis": amis,
            "nat_count": len(nat_gateways),
            "lb_count": len(load_balancers),
            "ami_count": len(amis),
            "s3_count": len(buckets),
        },
    )
