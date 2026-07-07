#!/usr/bin/env python3
"""
AWS existence checks for the recommendations auto-sync job.

For every resource type wasteless can recommend on, this module answers one
question: which of these resource IDs no longer exist in AWS? The sync job
then marks the matching pending recommendations as obsolete.

Pure logic lives in find_vanished_resources(); boto3 clients are injected
through client_factory so tests can run without AWS.
"""

import logging
from typing import Callable, Dict, List, Set

logger = logging.getLogger(__name__)

SYNC_REGIONS = ["eu-west-1", "eu-west-2", "eu-west-3", "us-east-1"]


def _existing_instances(ec2, ids: List[str]) -> Set[str]:
    """Instance IDs that exist and are not terminated."""
    found = set()
    response = ec2.describe_instances(Filters=[{"Name": "instance-id", "Values": ids}])
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance["State"]["Name"] != "terminated":
                found.add(instance["InstanceId"])
    return found


def _existing_volumes(ec2, ids: List[str]) -> Set[str]:
    response = ec2.describe_volumes(Filters=[{"Name": "volume-id", "Values": ids}])
    return {v["VolumeId"] for v in response.get("Volumes", [])}


def _existing_eips(ec2, ids: List[str]) -> Set[str]:
    response = ec2.describe_addresses(Filters=[{"Name": "allocation-id", "Values": ids}])
    return {a["AllocationId"] for a in response.get("Addresses", []) if a.get("AllocationId")}


def _existing_snapshots(ec2, ids: List[str]) -> Set[str]:
    response = ec2.describe_snapshots(
        OwnerIds=["self"], Filters=[{"Name": "snapshot-id", "Values": ids}]
    )
    return {s["SnapshotId"] for s in response.get("Snapshots", [])}


def _existing_nat_gateways(ec2, ids: List[str]) -> Set[str]:
    """NAT gateway IDs that exist and are not deleted/deleting.

    Deleted NAT gateways stay visible in the API for a while, so state
    must be checked, not just presence.
    """
    response = ec2.describe_nat_gateways(Filters=[{"Name": "nat-gateway-id", "Values": ids}])
    return {
        n["NatGatewayId"]
        for n in response.get("NatGateways", [])
        if n.get("State") not in ("deleted", "deleting")
    }


def _existing_load_balancers(elbv2, elb) -> Set[str]:
    """All ALB/NLB/GWLB ARNs and Classic LB names in the region.

    Listed exhaustively (no server-side filter): describe by explicit
    ARN/name raises NotFound for missing ones instead of skipping them.
    """
    found: Set[str] = set()
    paginator = elbv2.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        found.update(lb["LoadBalancerArn"] for lb in page.get("LoadBalancers", []))
    paginator = elb.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        found.update(lb["LoadBalancerName"] for lb in page.get("LoadBalancerDescriptions", []))
    return found


def find_vanished_resources(
    pending: Dict[str, List[str]],
    regions: List[str] = SYNC_REGIONS,
    client_factory: Callable = None,
) -> Dict[str, List[str]]:
    """
    Determine which pending resources no longer exist in AWS.

    Args:
        pending: resource_type -> list of resource IDs with pending recs
        regions: regions to scan (a resource existing in any region counts)
        client_factory: boto3.client-compatible callable (injected in tests)

    Returns:
        resource_type -> list of resource IDs that vanished
    """
    if client_factory is None:
        from utils.aws_clients import get_client

        def client_factory(service, region_name=None):
            return get_client(service, region=region_name)

    checkers = {
        "ec2_instance": _existing_instances,
        "ebs_volume": _existing_volumes,
        "elastic_ip": _existing_eips,
        "ebs_snapshot": _existing_snapshots,
        "nat_gateway": _existing_nat_gateways,
    }

    vanished: Dict[str, List[str]] = {}

    for resource_type, ids in pending.items():
        if not ids:
            continue

        if resource_type != "load_balancer" and resource_type not in checkers:
            # Unknown type: skip rather than wrongly obsolete
            logger.warning(f"No sync checker for resource type " f"'{resource_type}', skipping")
            continue

        existing: Set[str] = set()
        regions_checked = 0

        for region in regions:
            try:
                if resource_type == "load_balancer":
                    existing |= _existing_load_balancers(
                        client_factory("elbv2", region_name=region),
                        client_factory("elb", region_name=region),
                    )
                else:
                    existing |= checkers[resource_type](
                        client_factory("ec2", region_name=region), ids
                    )
                regions_checked += 1
            except Exception as e:
                logger.warning(f"Sync check failed for {resource_type} " f"in {region}: {e}")

        if regions_checked < len(regions):
            # A resource in an unchecked region would look vanished; only
            # conclude when every region answered
            logger.warning(
                f"Skipping obsoletion for '{resource_type}': "
                f"only {regions_checked}/{len(regions)} regions checked"
            )
            continue

        gone = [rid for rid in ids if rid not in existing]
        if gone:
            vanished[resource_type] = gone

    return vanished
