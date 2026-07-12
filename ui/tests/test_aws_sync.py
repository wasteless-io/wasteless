"""
Unit tests for utils.aws_sync — AWS existence checks behind the
recommendations auto-sync job. boto3 is fully mocked.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.aws_sync import SYNCABLE_RESOURCE_TYPES, find_vanished_resources


def _paginator(pages):
    p = MagicMock()
    p.paginate.return_value = pages
    return p


def make_client_factory(
    instances=None,
    volumes=None,
    addresses=None,
    snapshots=None,
    nat_gateways=None,
    vpcs=None,
    elbv2_arns=None,
    classic_names=None,
    fail_regions=None,
):
    """Build a boto3.client-compatible factory serving canned responses."""
    fail_regions = fail_regions or []

    def factory(service, region_name=None):
        if region_name in fail_regions:
            raise ConnectionError(f"simulated outage in {region_name}")

        client = MagicMock()
        if service == "ec2":
            client.describe_instances.return_value = {
                "Reservations": [{"Instances": instances or []}]
            }
            client.describe_volumes.return_value = {"Volumes": volumes or []}
            client.describe_addresses.return_value = {"Addresses": addresses or []}
            client.describe_snapshots.return_value = {"Snapshots": snapshots or []}
            client.describe_nat_gateways.return_value = {"NatGateways": nat_gateways or []}
            client.describe_vpcs.return_value = {"Vpcs": vpcs or []}
        elif service == "elbv2":
            client.get_paginator.return_value = _paginator(
                [{"LoadBalancers": [{"LoadBalancerArn": a} for a in (elbv2_arns or [])]}]
            )
        elif service == "elb":
            client.get_paginator.return_value = _paginator(
                [
                    {
                        "LoadBalancerDescriptions": [
                            {"LoadBalancerName": n} for n in (classic_names or [])
                        ]
                    }
                ]
            )
        return client

    return factory


REGIONS = ["eu-west-1", "eu-west-3"]


class TestFindVanishedResources(unittest.TestCase):

    def test_existing_resources_not_vanished(self):
        factory = make_client_factory(
            volumes=[{"VolumeId": "vol-1"}],
            nat_gateways=[{"NatGatewayId": "nat-1", "State": "available"}],
        )
        vanished = find_vanished_resources(
            {"ebs_volume": ["vol-1"], "nat_gateway": ["nat-1"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {})

    def test_missing_resources_vanished(self):
        factory = make_client_factory()  # AWS returns nothing anywhere
        vanished = find_vanished_resources(
            {"ebs_volume": ["vol-gone"], "elastic_ip": ["eipalloc-gone"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(
            vanished,
            {
                "ebs_volume": ["vol-gone"],
                "elastic_ip": ["eipalloc-gone"],
            },
        )

    def test_terminated_instance_vanished(self):
        factory = make_client_factory(
            instances=[
                {"InstanceId": "i-dead", "State": {"Name": "terminated"}},
                {"InstanceId": "i-alive", "State": {"Name": "running"}},
            ]
        )
        vanished = find_vanished_resources(
            {"ec2_instance": ["i-dead", "i-alive"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {"ec2_instance": ["i-dead"]})

    def test_existing_vpc_not_vanished(self):
        factory = make_client_factory(vpcs=[{"VpcId": "vpc-alive"}])
        vanished = find_vanished_resources(
            {"vpc": ["vpc-alive"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {})

    def test_deleted_vpc_vanished(self):
        """A destroyed VPC must obsolete its unused_vpc recommendation —
        before the vpc checker existed, it stayed pending forever."""
        factory = make_client_factory(vpcs=[{"VpcId": "vpc-alive"}])
        vanished = find_vanished_resources(
            {"vpc": ["vpc-alive", "vpc-gone"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {"vpc": ["vpc-gone"]})

    def test_every_detector_resource_type_has_a_checker(self):
        """Guard: each resource_type the detectors write to waste_detected
        must be syncable, or its recommendations survive the resource
        forever (how the missing vpc checker went unnoticed). When adding
        a detector with a new resource_type, add a checker in aws_sync.py
        and extend this list."""
        detector_resource_types = {
            "ec2_instance",  # ec2_idle, ec2_stopped
            "ebs_volume",  # ebs_orphan, ebs_gp2_migration
            "elastic_ip",  # eip_orphan
            "ebs_snapshot",  # snapshot_orphan
            "nat_gateway",  # nat_gateway_unused
            "load_balancer",  # elb_unused
            "vpc",  # vpc_unused
        }
        missing = detector_resource_types - SYNCABLE_RESOURCE_TYPES
        self.assertEqual(missing, set(), f"resource types without a sync checker: {missing}")

    def test_deleted_nat_gateway_vanished(self):
        factory = make_client_factory(
            nat_gateways=[
                {"NatGatewayId": "nat-old", "State": "deleted"},
            ]
        )
        vanished = find_vanished_resources(
            {"nat_gateway": ["nat-old"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {"nat_gateway": ["nat-old"]})

    def test_load_balancers_by_arn_and_name(self):
        factory = make_client_factory(
            elbv2_arns=["arn:aws:elasticloadbalancing:...:alb-alive"],
            classic_names=["classic-alive"],
        )
        vanished = find_vanished_resources(
            {
                "load_balancer": [
                    "arn:aws:elasticloadbalancing:...:alb-alive",
                    "classic-alive",
                    "arn:aws:elasticloadbalancing:...:alb-gone",
                ]
            },
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(
            vanished,
            {
                "load_balancer": ["arn:aws:elasticloadbalancing:...:alb-gone"],
            },
        )

    def test_region_failure_skips_type(self):
        """If any region cannot be checked, never conclude 'vanished'."""
        factory = make_client_factory(fail_regions=["eu-west-3"])
        vanished = find_vanished_resources(
            {"ebs_volume": ["vol-maybe"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {})

    def test_unknown_type_skipped(self):
        factory = make_client_factory()
        vanished = find_vanished_resources(
            {"quantum_bucket": ["qb-1"]},
            regions=REGIONS,
            client_factory=factory,
        )
        self.assertEqual(vanished, {})

    def test_empty_pending(self):
        vanished = find_vanished_resources(
            {},
            regions=REGIONS,
            client_factory=make_client_factory(),
        )
        self.assertEqual(vanished, {})


if __name__ == "__main__":
    unittest.main()
