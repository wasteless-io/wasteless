"""
Tests de claims CTO-safe (couche 2 de l'audit FinOps : cohérence
communication produit/commerciale).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.finops_invariants import (
    FinOpsInvariantError,
    validate_annualized_claim,
    validate_claim_wording,
    validate_low_risk_claim,
    validate_up_to_claim,
)


def test_guaranteed_savings_not_allowed_for_potential_savings():
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording("€93,600/year in guaranteed savings", savings_type="potential")
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording("Save €7,800/month instantly", savings_type="potential")
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording("Automatic savings with no risk", savings_type="potential")
    # Formulation autorisée
    assert (
        validate_claim_wording(
            "Up to €93,600/year in potential savings identified", savings_type="potential"
        )
        is not None
    )


def test_realized_savings_claim_requires_completed_actions():
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording(
            "€8,000/month in realized savings", savings_type="realized", completed_actions=0
        )
    assert (
        validate_claim_wording(
            "€8,000/month in realized savings", savings_type="realized", completed_actions=3
        )
        is not None
    )


def test_annualized_claim_requires_monthly_basis():
    with pytest.raises(FinOpsInvariantError):
        validate_annualized_claim(90000, monthly_basis=None)
    with pytest.raises(FinOpsInvariantError):
        validate_annualized_claim(120000, monthly_basis=7500)  # 7500*12=90000
    assert validate_annualized_claim(90000, monthly_basis=7500) == 90000


def test_up_to_claim_requires_dataset_support():
    # Exercice 3 : 150 % de réduction affiché, dataset ne supporte que 100 %
    with pytest.raises(FinOpsInvariantError):
        validate_up_to_claim(18000, dataset_max_value=12000)
    assert validate_up_to_claim(7500, dataset_max_value=7500) == 7500
    assert validate_up_to_claim(5000, dataset_max_value=7500) == 5000


def test_low_risk_claim_only_uses_low_risk_recommendations():
    recommendations = [
        {"resource_id": "i-1", "potential_saving": 3000, "risk": "low"},
        {"resource_id": "i-2", "potential_saving": 2500, "risk": "medium"},
        {"resource_id": "i-3", "potential_saving": 800, "risk": "low"},
    ]
    # Total réel low-risk : 3000 + 800 = 3800
    with pytest.raises(FinOpsInvariantError):
        validate_low_risk_claim(5000, recommendations)  # inclut le medium-risk
    assert validate_low_risk_claim(3800, recommendations) == 3800
    assert validate_low_risk_claim(3000, recommendations) == 3000


def test_forbidden_words_case_insensitive():
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording("GUARANTEED cloud cost reduction", savings_type="potential")


def test_potential_savings_not_labeled_as_guaranteed():
    metric = {
        "type": "potential_savings",
        "label": "€93,600/year in guaranteed savings",
    }
    with pytest.raises(FinOpsInvariantError):
        validate_claim_wording(metric["label"], savings_type="potential_savings")
    # Formulation défendable pour le même montant
    assert (
        validate_claim_wording(
            "€93,600/year in potential savings identified", savings_type="potential_savings"
        )
        is not None
    )
