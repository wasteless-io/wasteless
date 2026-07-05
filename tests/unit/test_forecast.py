"""
Tests de forecast avancé (couche 2 de l'audit FinOps).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import (
    FinOpsInvariantError,
    flags_budget_overrun,
    forecast_after_remediation,
    linear_forecast,
)


def test_linear_forecast_matches_elapsed_days():
    # Exemple du document : 31 000 / 15 * 31 ≈ 64 066,67
    assert linear_forecast(31000, days_elapsed=15, days_in_month=31) == \
        pytest.approx(64066.67, abs=0.01)
    # Exercice 5 : 15 000 au jour 10 sur 30 jours -> 45 000, pas 30 000
    assert linear_forecast(15000, days_elapsed=10, days_in_month=30) == \
        pytest.approx(45000)


def test_forecast_requires_days_elapsed_and_days_in_month():
    with pytest.raises(FinOpsInvariantError):
        linear_forecast(15000, days_elapsed=0, days_in_month=30)
    with pytest.raises(FinOpsInvariantError):
        linear_forecast(15000, days_elapsed=10, days_in_month=0)
    with pytest.raises(FinOpsInvariantError):
        linear_forecast(15000, days_elapsed=35, days_in_month=30)


def test_forecast_after_remediation_equals_forecast_minus_validated_low_risk_savings():
    # Exercice 17 : 50 000 - 6 000 = 44 000, pas 41 000
    result = forecast_after_remediation(
        forecast=50000, validated_low_risk_savings=6000, current_spend_mtd=30000)
    assert result == 44000


def test_forecast_after_remediation_never_below_current_spend():
    with pytest.raises(FinOpsInvariantError):
        forecast_after_remediation(
            forecast=50000, validated_low_risk_savings=40000,
            current_spend_mtd=45000)  # 10 000 < 45 000 déjà dépensés
    # Cas limite : égal au spend déjà réalisé, accepté
    assert forecast_after_remediation(
        forecast=50000, validated_low_risk_savings=5000,
        current_spend_mtd=45000) == 45000


def test_forecast_flags_budget_overrun():
    assert flags_budget_overrun(forecast=65000, budget=60000) is True
    assert flags_budget_overrun(forecast=55000, budget=60000) is False
    assert flags_budget_overrun(forecast=60000, budget=60000) is False
