"""
Tests de sécurité du workflow de remédiation (couche 3 de l'audit FinOps) :
aucune action ne doit pouvoir sauter les états detected -> ... -> executed
sans les garde-fous associés (approval, owner, rollback, audit log).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import (
    FinOpsInvariantError,
    validate_approval_required,
    validate_realized_savings_status,
)


def test_destructive_action_requires_approval():
    remediation = {
        'recommendation_id': 'rec-004', 'resource_id': 'vol-bbb222',
        'recommended_action': 'delete', 'environment': 'dev',
        'risk': 'low', 'approval_required': False,
    }
    with pytest.raises(FinOpsInvariantError):
        validate_approval_required(
            remediation['recommended_action'], remediation['environment'],
            remediation['risk'], remediation['approval_required'])
    assert validate_approval_required(
        remediation['recommended_action'], remediation['environment'],
        remediation['risk'], True) is True


def test_realized_savings_requires_executed_status():
    remediation = {
        'recommendation_id': 'rec-001', 'status': 'approved',
        'realized_monthly_saving': 420,
    }
    with pytest.raises(FinOpsInvariantError):
        validate_realized_savings_status(
            remediation['status'], remediation['realized_monthly_saving'])
    assert validate_realized_savings_status('executed', 420) == 420
    assert validate_realized_savings_status('approved', 0) == 0
