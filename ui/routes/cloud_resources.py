"""Live cloud resource inventory (EC2, EBS, EIP, VPC, snapshots, S3)."""

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse

from state import templates, CLOUD_REGIONS
from utils.logger import get_logger

router = APIRouter()

logger = get_logger("cloud_resources")


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

    # Fetch all resource types in parallel (snapshots per region + S3 global)
    with ThreadPoolExecutor(max_workers=len(CLOUD_REGIONS) * 5 + 1) as executor:
        ec2_futs = [executor.submit(_fetch_ec2, r) for r in CLOUD_REGIONS]
        vol_futs = [executor.submit(_fetch_volumes, r) for r in CLOUD_REGIONS]
        ip_futs = [executor.submit(_fetch_ips, r) for r in CLOUD_REGIONS]
        vpc_futs = [executor.submit(_fetch_vpcs, r) for r in CLOUD_REGIONS]
        snap_futs = [executor.submit(_fetch_snapshots, r) for r in CLOUD_REGIONS]
        rds_futs = [executor.submit(_fetch_rds, r) for r in CLOUD_REGIONS]
        rds_snap_futs = [executor.submit(_fetch_rds_snapshots, r) for r in CLOUD_REGIONS]
        s3_fut = executor.submit(_fetch_s3)

    instances = [i for f in ec2_futs for i in f.result()]
    volumes = [v for f in vol_futs for v in f.result()]
    ips = [ip for f in ip_futs for ip in f.result()]
    vpcs = [vpc for f in vpc_futs for vpc in f.result()]
    snapshots = [s for f in snap_futs for s in f.result()]
    databases = [d for f in rds_futs for d in f.result()]
    rds_snapshots = [s for f in rds_snap_futs for s in f.result()]
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

    if state_filter != "all":
        instances = [i for i in instances if i["state"] == state_filter]

    instances.sort(key=lambda x: (x["state"] != "running", x["name"]))
    volumes.sort(key=lambda x: (x["state"] != "in-use", x["region"]))
    ips.sort(key=lambda x: (not x["associated"], x["region"]))
    vpcs.sort(key=lambda x: (not x["is_default"], x["region"]))
    snapshots.sort(key=lambda x: x["start_time"] or "", reverse=True)
    databases.sort(key=lambda x: (x["status"] != "available", x["region"], x["db_id"]))
    rds_snapshots.sort(key=lambda x: x["created"] or "", reverse=True)
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
            "s3_count": len(buckets),
        },
    )
