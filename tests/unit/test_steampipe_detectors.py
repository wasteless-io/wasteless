"""
Unit tests for the Steampipe-native detectors (NAT gateways, gp2 migration,
unused load balancers). Detectors are instantiated without a database
connection; only map_rows() logic is exercised.
"""

import sys
import os
from unittest.mock import patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from detectors.nat_gateway_unused import NATGatewayUnusedDetector, NAT_GATEWAY_MONTHLY_COST_USD
from detectors.ebs_gp2_migration import EBSGp2MigrationDetector, GP2_TO_GP3_SAVINGS_USD_PER_GIB
from detectors.elb_unused import ELBUnusedDetector, ELB_MONTHLY_COST_USD
from detectors.vpc_unused import VPCUnusedDetector
from detectors.ami_orphan import AMIOrphanDetector, SNAPSHOT_USD_PER_GIB
from detectors.rds_stopped import RDSStoppedDetector
from detectors.rds_snapshot_orphan import RDSSnapshotOrphanDetector
from detectors.rds_idle import RDSIdleDetector
from detectors import rds_pricing
from collectors.ec2_metrics_steampipe import rows_to_records


def _bare(cls):
    """Instantiate a detector without opening a database connection."""
    return object.__new__(cls)


class TestNATGatewayMapping:

    def test_idle_gateway(self):
        items = _bare(NATGatewayUnusedDetector).map_rows(
            [
                {
                    "nat_gateway_id": "nat-0abc",
                    "vpc_id": "vpc-1",
                    "state": "available",
                    "region": "eu-west-1",
                    "bytes_out_30d": 0,
                }
            ]
        )
        item = items[0]
        assert item["resource_id"] == "nat-0abc"
        assert item["monthly_cost"] == NAT_GATEWAY_MONTHLY_COST_USD
        assert "no outbound traffic" in item["action"]

    def test_non_available_state(self):
        item = _bare(NATGatewayUnusedDetector).map_rows(
            [
                {
                    "nat_gateway_id": "nat-1",
                    "state": "failed",
                    "region": "eu-west-1",
                }
            ]
        )[0]
        assert "in 'failed' state" in item["action"]
        assert item["metadata"]["bytes_out_30d"] == 0

    def test_empty_input(self):
        assert _bare(NATGatewayUnusedDetector).map_rows([]) == []


class TestGp2MigrationMapping:

    def test_savings_proportional_to_size(self):
        item = _bare(EBSGp2MigrationDetector).map_rows(
            [
                {
                    "volume_id": "vol-1",
                    "name": "data",
                    "size_gb": 100,
                    "az": "eu-west-1a",
                    "region": "eu-west-1",
                }
            ]
        )[0]
        # 100 GiB * (0.10 - 0.08) = 2.00 USD/mo
        assert item["monthly_cost"] == pytest.approx(2.00)
        assert GP2_TO_GP3_SAVINGS_USD_PER_GIB == pytest.approx(0.02)
        assert "MIGRATE" in item["action"]
        assert "data (vol-1)" in item["action"]

    def test_unnamed_volume(self):
        item = _bare(EBSGp2MigrationDetector).map_rows(
            [
                {
                    "volume_id": "vol-2",
                    "name": None,
                    "size_gb": 10,
                }
            ]
        )[0]
        assert "vol-2" in item["action"]
        assert item["metadata"]["name"] == ""

    def test_empty_input(self):
        assert _bare(EBSGp2MigrationDetector).map_rows([]) == []


class TestELBUnusedMapping:

    def test_cost_by_lb_type(self):
        rows = [
            {"lb_type": t, "name": f"lb-{t}", "arn": f"arn:{t}", "region": "eu-west-1"}
            for t in ("application", "network", "gateway", "classic")
        ]
        items = _bare(ELBUnusedDetector).map_rows(rows)
        for item, row in zip(items, rows):
            assert item["monthly_cost"] == ELB_MONTHLY_COST_USD[row["lb_type"]]
            assert item["resource_id"] == row["arn"]

    def test_classic_reason_differs(self):
        items = _bare(ELBUnusedDetector).map_rows(
            [
                {"lb_type": "classic", "name": "old-lb", "arn": "arn:clb"},
                {"lb_type": "application", "name": "alb", "arn": "arn:alb"},
            ]
        )
        assert "no instances attached" in items[0]["action"]
        assert "no registered targets" in items[1]["action"]

    def test_missing_arn_falls_back_to_name(self):
        item = _bare(ELBUnusedDetector).map_rows(
            [
                {"lb_type": "classic", "name": "legacy", "arn": None},
            ]
        )[0]
        assert item["resource_id"] == "legacy"

    def test_no_traffic_reason_and_lower_confidence(self):
        item = _bare(ELBUnusedDetector).map_rows(
            [
                {
                    "lb_type": "application",
                    "name": "idle-alb",
                    "arn": "arn:alb",
                    "region": "eu-west-1",
                    "reason": "no_traffic",
                    "registered_targets": 3,
                }
            ]
        )[0]
        assert "no traffic in 30 days" in item["action"]
        assert "3 target(s) registered" in item["action"]
        assert item["confidence"] == 0.85
        assert item["metadata"]["reason"] == "no_traffic"
        assert item["metadata"]["registered_targets"] == 3

    def test_empty_input(self):
        assert _bare(ELBUnusedDetector).map_rows([]) == []


class TestAMIOrphanMapping:

    def test_cost_from_backing_snapshots(self):
        item = _bare(AMIOrphanDetector).map_rows(
            [
                {
                    "image_id": "ami-0abc",
                    "name": "base-image",
                    "region": "eu-west-1",
                    "platform_details": "Linux/UNIX",
                    "backing_gb": 30,
                    "snapshot_count": 2,
                    "age_days": 120,
                }
            ]
        )[0]
        assert item["resource_id"] == "ami-0abc"
        assert item["monthly_cost"] == round(30 * SNAPSHOT_USD_PER_GIB, 2)
        assert "DEREGISTER" in item["action"]
        assert "base-image (ami-0abc)" in item["action"]
        assert item["metadata"]["snapshot_count"] == 2

    def test_unnamed_ami_uses_bare_id(self):
        item = _bare(AMIOrphanDetector).map_rows(
            [{"image_id": "ami-1", "backing_gb": 0, "snapshot_count": 0, "age_days": 100}]
        )[0]
        assert "ami-1" in item["action"]
        assert item["monthly_cost"] == 0.0

    def test_empty_input(self):
        assert _bare(AMIOrphanDetector).map_rows([]) == []


class TestRDSStoppedMapping:

    def test_storage_cost_and_autorestart_note(self):
        item = _bare(RDSStoppedDetector).map_rows(
            [
                {
                    "db_instance_identifier": "db-prod",
                    "class": "db.t3.medium",
                    "engine": "postgres",
                    "allocated_storage": 100,
                    "storage_type": "gp3",
                    "multi_az": False,
                    "region": "eu-west-1",
                    "age_days": 45,
                }
            ]
        )[0]
        assert item["resource_id"] == "db-prod"
        assert item["monthly_cost"] == rds_pricing.storage_usd(100, "gp3")
        assert "auto-restarts after 7 days" in item["action"]

    def test_empty_input(self):
        assert _bare(RDSStoppedDetector).map_rows([]) == []


class TestRDSSnapshotOrphanMapping:

    def test_backup_storage_cost(self):
        item = _bare(RDSSnapshotOrphanDetector).map_rows(
            [
                {
                    "db_snapshot_identifier": "snap-manual-1",
                    "db_instance_identifier": "db-old",
                    "engine": "mysql",
                    "allocated_storage": 50,
                    "storage_type": "gp2",
                    "region": "eu-west-1",
                    "age_days": 200,
                }
            ]
        )[0]
        assert item["resource_id"] == "snap-manual-1"
        assert item["monthly_cost"] == rds_pricing.snapshot_usd(50)
        assert "db-old" in item["action"]

    def test_empty_input(self):
        assert _bare(RDSSnapshotOrphanDetector).map_rows([]) == []


class TestRDSIdleMapping:

    def test_full_instance_cost_and_multi_az(self):
        single = _bare(RDSIdleDetector).map_rows(
            [
                {
                    "db_instance_identifier": "db-idle",
                    "class": "db.m5.large",
                    "engine": "postgres",
                    "allocated_storage": 100,
                    "storage_type": "gp3",
                    "multi_az": False,
                    "max_conn_14d": 0,
                    "region": "eu-west-1",
                }
            ]
        )[0]
        assert single["monthly_cost"] == rds_pricing.instance_usd("db.m5.large", False, 100, "gp3")
        assert "0 connections in 14 days" in single["action"]
        # multi-AZ roughly doubles the compute portion → strictly more expensive
        multi = _bare(RDSIdleDetector).map_rows(
            [
                {
                    "db_instance_identifier": "db-idle-ha",
                    "class": "db.m5.large",
                    "allocated_storage": 100,
                    "storage_type": "gp3",
                    "multi_az": True,
                }
            ]
        )[0]
        assert multi["monthly_cost"] > single["monthly_cost"]

    def test_unknown_class_uses_default(self):
        item = _bare(RDSIdleDetector).map_rows(
            [
                {
                    "db_instance_identifier": "db-x",
                    "class": "db.weird.42xlarge",
                    "allocated_storage": 20,
                }
            ]
        )[0]
        expected = rds_pricing.instance_usd("db.weird.42xlarge", False, 20, "gp2")
        assert item["monthly_cost"] == expected
        assert expected > 0

    def test_empty_input(self):
        assert _bare(RDSIdleDetector).map_rows([]) == []


class TestVPCUnusedMapping:

    def test_custom_vpc(self):
        item = _bare(VPCUnusedDetector).map_rows(
            [
                {
                    "vpc_id": "vpc-0abc",
                    "region": "eu-west-3",
                    "cidr_block": "10.0.0.0/16",
                    "is_default": False,
                    "name": "sandbox",
                }
            ]
        )[0]
        assert item["resource_id"] == "vpc-0abc"
        assert item["monthly_cost"] == 0.0
        assert item["confidence"] == 0.85
        assert "DELETE" in item["action"]
        assert "sandbox (vpc-0abc)" in item["action"]
        assert item["metadata"]["hygiene"] is True

    def test_default_vpc_lower_confidence_and_review(self):
        item = _bare(VPCUnusedDetector).map_rows(
            [
                {
                    "vpc_id": "vpc-def",
                    "region": "us-east-1",
                    "cidr_block": "172.31.0.0/16",
                    "is_default": True,
                    "name": "",
                }
            ]
        )[0]
        assert item["confidence"] == 0.60
        assert item["action"].startswith("REVIEW default VPC vpc-def")

    def test_unnamed_vpc_uses_bare_id(self):
        item = _bare(VPCUnusedDetector).map_rows(
            [
                {
                    "vpc_id": "vpc-1",
                    "is_default": False,
                    "name": None,
                }
            ]
        )[0]
        assert "vpc-1 in" in item["action"]
        assert item["metadata"]["name"] == ""

    def test_empty_input(self):
        assert _bare(VPCUnusedDetector).map_rows([]) == []


class TestBaseDetect:

    @patch("detectors.steampipe_base.run_query_file")
    def test_detect_runs_named_query_and_maps(self, mock_query):
        mock_query.return_value = [{"nat_gateway_id": "nat-9", "state": "available"}]
        detector = _bare(NATGatewayUnusedDetector)
        items = detector.detect()
        mock_query.assert_called_once_with("nat_gateway_unused")
        assert items[0]["resource_id"] == "nat-9"

    def test_map_rows_is_abstract(self):
        from detectors.steampipe_base import SteampipeWasteDetector

        with pytest.raises(NotImplementedError):
            _bare(SteampipeWasteDetector).map_rows([])


class TestMetricsRowsToRecords:

    def test_full_row(self):
        records = rows_to_records(
            [
                {
                    "instance_id": "i-0abc",
                    "instance_type": "t3.micro",
                    "instance_name": "web-1",
                    "instance_state": "running",
                    "collection_date": "2026-07-01",
                    "cpu_avg": 2.34,
                    "cpu_max": 15.6,
                }
            ]
        )
        assert records == [("i-0abc", "t3.micro", "web-1", "running", "2026-07-01", 2.34, 15.6)]

    def test_missing_optional_fields(self):
        record = rows_to_records(
            [
                {
                    "instance_id": "i-1",
                    "collection_date": "2026-07-01",
                }
            ]
        )[0]
        assert record == ("i-1", "", "", "", "2026-07-01", None, None)

    def test_empty_input(self):
        assert rows_to_records([]) == []
