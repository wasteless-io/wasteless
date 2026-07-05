"""
Tests d'audit read-only face à un vrai compte AWS (couche 5 de l'audit
FinOps). Ces tests utilisent des chiffres de référence simulés (Cost
Explorer, liste d'appels API) plutôt qu'un compte réel : ils valident le
mécanisme de tolérance et de garde-fou, pas une intégration live.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import (
    FinOpsInvariantError,
    validate_read_only_audit,
    validate_within_tolerance_pct,
)


def test_cost_explorer_total_matches_wasteless_total_within_tolerance():
    cost_explorer_total = 50200
    wasteless_total = 49800
    # Écart réel ~0.8%, sous la tolérance documentée de 2%
    assert validate_within_tolerance_pct(
        wasteless_total, cost_explorer_total, tolerance_pct=2,
        label='cost_explorer_total') < 2

    # Écart de 10% doit être rejeté à la même tolérance
    with pytest.raises(FinOpsInvariantError):
        validate_within_tolerance_pct(
            45000, cost_explorer_total, tolerance_pct=2,
            label='cost_explorer_total')


def test_no_write_api_called_during_audit():
    audit_api_calls = [
        'ce:GetCostAndUsage', 'ec2:DescribeInstances',
        'ec2:DescribeVolumes', 'cloudwatch:GetMetricData',
    ]
    forbidden_write_actions = {
        'ec2:StopInstances', 'ec2:TerminateInstances', 'ec2:DeleteVolume',
        'ec2:ReleaseAddress', 'rds:DeleteDBInstance', 'rds:ModifyDBInstance',
    }
    assert validate_read_only_audit(audit_api_calls, forbidden_write_actions) is True

    with pytest.raises(FinOpsInvariantError):
        validate_read_only_audit(
            audit_api_calls + ['ec2:TerminateInstances'], forbidden_write_actions)
