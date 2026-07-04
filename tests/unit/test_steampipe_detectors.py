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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from detectors.nat_gateway_unused import (
    NATGatewayUnusedDetector, NAT_GATEWAY_MONTHLY_COST_EUR
)
from detectors.ebs_gp2_migration import (
    EBSGp2MigrationDetector, GP2_TO_GP3_SAVINGS_EUR_PER_GIB
)
from detectors.elb_unused import ELBUnusedDetector, ELB_MONTHLY_COST_EUR
from detectors.vpc_unused import VPCUnusedDetector
from collectors.ec2_metrics_steampipe import rows_to_records


def _bare(cls):
    """Instantiate a detector without opening a database connection."""
    return object.__new__(cls)


class TestNATGatewayMapping:

    def test_idle_gateway(self):
        items = _bare(NATGatewayUnusedDetector).map_rows([{
            'nat_gateway_id': 'nat-0abc',
            'vpc_id': 'vpc-1',
            'state': 'available',
            'region': 'eu-west-1',
            'bytes_out_30d': 0,
        }])
        item = items[0]
        assert item['resource_id'] == 'nat-0abc'
        assert item['monthly_cost'] == NAT_GATEWAY_MONTHLY_COST_EUR
        assert 'no outbound traffic' in item['action']

    def test_non_available_state(self):
        item = _bare(NATGatewayUnusedDetector).map_rows([{
            'nat_gateway_id': 'nat-1', 'state': 'failed', 'region': 'eu-west-1',
        }])[0]
        assert "in 'failed' state" in item['action']
        assert item['metadata']['bytes_out_30d'] == 0

    def test_empty_input(self):
        assert _bare(NATGatewayUnusedDetector).map_rows([]) == []


class TestGp2MigrationMapping:

    def test_savings_proportional_to_size(self):
        item = _bare(EBSGp2MigrationDetector).map_rows([{
            'volume_id': 'vol-1', 'name': 'data', 'size_gb': 100,
            'az': 'eu-west-1a', 'region': 'eu-west-1',
        }])[0]
        # 100 GiB * (0.0920 - 0.0736) = 1.84 EUR/mo
        assert item['monthly_cost'] == pytest.approx(1.84)
        assert GP2_TO_GP3_SAVINGS_EUR_PER_GIB == pytest.approx(0.0184)
        assert 'MIGRATE' in item['action']
        assert 'data (vol-1)' in item['action']

    def test_unnamed_volume(self):
        item = _bare(EBSGp2MigrationDetector).map_rows([{
            'volume_id': 'vol-2', 'name': None, 'size_gb': 10,
        }])[0]
        assert 'vol-2' in item['action']
        assert item['metadata']['name'] == ''

    def test_empty_input(self):
        assert _bare(EBSGp2MigrationDetector).map_rows([]) == []


class TestELBUnusedMapping:

    def test_cost_by_lb_type(self):
        rows = [
            {'lb_type': t, 'name': f'lb-{t}', 'arn': f'arn:{t}', 'region': 'eu-west-1'}
            for t in ('application', 'network', 'gateway', 'classic')
        ]
        items = _bare(ELBUnusedDetector).map_rows(rows)
        for item, row in zip(items, rows):
            assert item['monthly_cost'] == ELB_MONTHLY_COST_EUR[row['lb_type']]
            assert item['resource_id'] == row['arn']

    def test_classic_reason_differs(self):
        items = _bare(ELBUnusedDetector).map_rows([
            {'lb_type': 'classic', 'name': 'old-lb', 'arn': 'arn:clb'},
            {'lb_type': 'application', 'name': 'alb', 'arn': 'arn:alb'},
        ])
        assert 'no instances attached' in items[0]['action']
        assert 'no registered targets' in items[1]['action']

    def test_missing_arn_falls_back_to_name(self):
        item = _bare(ELBUnusedDetector).map_rows([
            {'lb_type': 'classic', 'name': 'legacy', 'arn': None},
        ])[0]
        assert item['resource_id'] == 'legacy'

    def test_empty_input(self):
        assert _bare(ELBUnusedDetector).map_rows([]) == []


class TestVPCUnusedMapping:

    def test_custom_vpc(self):
        item = _bare(VPCUnusedDetector).map_rows([{
            'vpc_id': 'vpc-0abc',
            'region': 'eu-west-3',
            'cidr_block': '10.0.0.0/16',
            'is_default': False,
            'name': 'sandbox',
        }])[0]
        assert item['resource_id'] == 'vpc-0abc'
        assert item['monthly_cost'] == 0.0
        assert item['confidence'] == 0.85
        assert 'DELETE' in item['action']
        assert 'sandbox (vpc-0abc)' in item['action']
        assert item['metadata']['hygiene'] is True

    def test_default_vpc_lower_confidence_and_review(self):
        item = _bare(VPCUnusedDetector).map_rows([{
            'vpc_id': 'vpc-def', 'region': 'us-east-1',
            'cidr_block': '172.31.0.0/16', 'is_default': True, 'name': '',
        }])[0]
        assert item['confidence'] == 0.60
        assert item['action'].startswith('REVIEW default VPC vpc-def')

    def test_unnamed_vpc_uses_bare_id(self):
        item = _bare(VPCUnusedDetector).map_rows([{
            'vpc_id': 'vpc-1', 'is_default': False, 'name': None,
        }])[0]
        assert 'vpc-1 in' in item['action']
        assert item['metadata']['name'] == ''

    def test_empty_input(self):
        assert _bare(VPCUnusedDetector).map_rows([]) == []


class TestBaseDetect:

    @patch('detectors.steampipe_base.run_query_file')
    def test_detect_runs_named_query_and_maps(self, mock_query):
        mock_query.return_value = [{'nat_gateway_id': 'nat-9', 'state': 'available'}]
        detector = _bare(NATGatewayUnusedDetector)
        items = detector.detect()
        mock_query.assert_called_once_with('nat_gateway_unused')
        assert items[0]['resource_id'] == 'nat-9'

    def test_map_rows_is_abstract(self):
        from detectors.steampipe_base import SteampipeWasteDetector
        with pytest.raises(NotImplementedError):
            _bare(SteampipeWasteDetector).map_rows([])


class TestMetricsRowsToRecords:

    def test_full_row(self):
        records = rows_to_records([{
            'instance_id': 'i-0abc',
            'instance_type': 't3.micro',
            'instance_name': 'web-1',
            'instance_state': 'running',
            'collection_date': '2026-07-01',
            'cpu_avg': 2.34,
            'cpu_max': 15.6,
        }])
        assert records == [
            ('i-0abc', 't3.micro', 'web-1', 'running', '2026-07-01', 2.34, 15.6)
        ]

    def test_missing_optional_fields(self):
        record = rows_to_records([{
            'instance_id': 'i-1', 'collection_date': '2026-07-01',
        }])[0]
        assert record == ('i-1', '', '', '', '2026-07-01', None, None)

    def test_empty_input(self):
        assert rows_to_records([]) == []
