"""
Tests des invariants FinOps (src/core/finops_invariants.py).

Les cas de test reprennent les exercices de l'audit de cohérence des
chiffres : chaque chiffre volontairement faux de l'audit doit être rejeté,
chaque chiffre correct doit passer.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), '..', 'fixtures')


def _load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name)) as f:
        return json.load(f)


from core.finops_invariants import (
    FinOpsInvariantError,
    annualize,
    assess_confidence,
    audit_dataset,
    generate_cto_safe_summary,
    budget_used_percentage,
    deduplicated_total_savings,
    minimum_risk_for,
    validate_claim_percentage,
    validate_forecast,
    validate_potential_vs_detected,
    validate_realized_savings,
    validate_recommendation_saving,
    validate_risk_level,
    validate_service_breakdown,
    waste_percentage,
)


def test_monthly_to_yearly_savings():
    # Exercice 1 : 7 500 €/mois -> 90 000 €/an
    assert annualize(7500) == 90000
    # Exercice 2 : 3 200 €/mois -> 38 400 €/an, jamais 32 000
    assert annualize(3200) == 38400
    assert annualize(3200) != 32000


def test_waste_percentage_calculation():
    # Exercice 1 : 7 500 / 50 000 = 15 %
    assert waste_percentage(7500, 50000) == pytest.approx(15.0)
    # Exercice 18 : 6 000 / 80 000 = 7,5 %, pas 40 %
    assert waste_percentage(6000, 80000) == pytest.approx(7.5)
    # Exercice 3 : waste > spend est impossible (150 % affiché)
    with pytest.raises(FinOpsInvariantError):
        waste_percentage(18000, 12000)
    with pytest.raises(FinOpsInvariantError):
        waste_percentage(1000, 0)


def test_budget_usage_percentage():
    # Exercice 6 : 42 000 / 60 000 = 70 %, pas 80 %
    assert budget_used_percentage(42000, 60000) == pytest.approx(70.0)
    # Exercice 20 : 50 000 / 60 000 = 83,3 %, pas 90 %
    assert budget_used_percentage(50000, 60000) == pytest.approx(83.33, abs=0.01)
    with pytest.raises(FinOpsInvariantError):
        budget_used_percentage(42000, 0)


def test_forecast_not_below_current_spend():
    # Un forecast au-dessus du réalisé passe
    assert validate_forecast(45000, 15000) == 45000
    assert validate_forecast(50000, 50000) == 50000
    # Exercice 20 : forecast 42 000 < spend 50 000 est impossible
    with pytest.raises(FinOpsInvariantError):
        validate_forecast(42000, 50000)


def test_service_cost_sum_matches_total():
    # Ventilation exacte : aucun écart
    assert validate_service_breakdown(
        {'EC2': 14000, 'RDS': 9000, 'S3': 4000}, 27000) == 0.0
    # Exercice 4 : 40 000 ventilés sur 42 000 -> écart silencieux de 2 000 rejeté
    with pytest.raises(FinOpsInvariantError):
        validate_service_breakdown(
            {'EC2': 14000, 'RDS': 9000, 'S3': 4000, 'EKS': 8000,
             'NAT Gateway': 3000, 'CloudWatch': 2000}, 42000)
    # Somme des services au-dessus du total : double comptage
    with pytest.raises(FinOpsInvariantError):
        validate_service_breakdown({'EC2': 30000, 'RDS': 20000}, 42000)


def test_recommendation_savings_not_above_resource_cost():
    assert validate_recommendation_saving(200, 240) == 200
    # Saving = 100 % du coût toléré (borne incluse), au-dessus rejeté
    assert validate_recommendation_saving(240, 240) == 240
    with pytest.raises(FinOpsInvariantError):
        validate_recommendation_saving(300, 240)
    with pytest.raises(FinOpsInvariantError):
        validate_recommendation_saving(-10, 240)


def test_no_duplicate_resource_savings():
    # Exercice 13 : stop et delete sur i-123 sont exclusifs -> 350, pas 550
    recommendations = [
        {'resource_id': 'i-123', 'potential_saving': 200, 'action': 'stop'},
        {'resource_id': 'i-123', 'potential_saving': 200, 'action': 'delete'},
        {'resource_id': 'i-456', 'potential_saving': 150, 'action': 'stop'},
    ]
    assert deduplicated_total_savings(recommendations) == 350


def test_detected_waste_greater_or_equal_potential_savings():
    assert validate_potential_vs_detected(7500, 7500) == 7500
    assert validate_potential_vs_detected(5000, 7500) == 5000
    # Exercice 20 : potentiel 15 000 > waste détecté 12 000 est impossible
    with pytest.raises(FinOpsInvariantError):
        validate_potential_vs_detected(15000, 12000)


def test_realized_savings_requires_completed_action():
    # Exercice 8 : 8 000 € « réalisés » sans aucune remédiation exécutée
    with pytest.raises(FinOpsInvariantError):
        validate_realized_savings(8000, completed_actions=0)
    # Avec actions exécutées, ou à zéro, le chiffre passe
    assert validate_realized_savings(8000, completed_actions=3) == 8000
    assert validate_realized_savings(0, completed_actions=0) == 0


def test_production_delete_is_never_low_risk():
    # Exercice 9 : delete RDS production affiché low -> rejeté
    with pytest.raises(FinOpsInvariantError):
        validate_risk_level('delete', 'production', 'low')
    assert minimum_risk_for('delete', 'production') == 'critical'
    # Environnement inconnu traité comme production (exercice 11)
    assert minimum_risk_for('delete', None) == 'critical'
    assert minimum_risk_for('delete', 'unknown') == 'critical'
    # Exercice 16 : stop en production ne peut pas être low
    with pytest.raises(FinOpsInvariantError):
        validate_risk_level('stop', 'production', 'low')
    # Exercices 10 et 12 : stop dev et release EIP hors prod restent défendables
    # avec un owner connu (sans owner, le plancher remonte à medium)
    assert validate_risk_level('stop', 'dev', 'low', owner='team-x') == 'low'
    assert minimum_risk_for('release', 'dev', owner='team-x') == 'medium'


def test_missing_currency_flags_low_confidence():
    # Exercice 19 : sans devise, jamais high, quel que soit le signal
    metadata = {'period': '30d', 'pricing_source': 'aws_on_demand_static',
                'owner': 'team-x', 'environment': 'dev'}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'low'
    metadata['currency'] = 'EUR'
    assert assess_confidence(metadata, signal_confidence=0.95) == 'high'


def test_missing_period_flags_low_confidence():
    metadata = {'currency': 'EUR', 'pricing_source': 'aws_on_demand_static',
                'owner': 'team-x', 'environment': 'dev'}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'low'
    metadata['period'] = '30d'
    assert assess_confidence(metadata, signal_confidence=0.95) == 'high'
    # Signal moyen : plafonné medium même avec métadonnées complètes
    assert assess_confidence(metadata, signal_confidence=0.6) == 'medium'


def test_claim_percentage_matches_dataset():
    # Exercice 18 : « Reduce your AWS bill by 40% » avec 7,5 % détecté
    with pytest.raises(FinOpsInvariantError):
        validate_claim_percentage(40, detected_waste_monthly=6000,
                                  spend_monthly=80000)
    # Claim aligné sur les données : passe
    assert validate_claim_percentage(
        7.5, detected_waste_monthly=6000, spend_monthly=80000) == 7.5
    # Exercice 1 : 15 % annoncé, 15 % recalculé
    assert validate_claim_percentage(
        15, detected_waste_monthly=7500, spend_monthly=50000) == 15


class TestAuditDataset:
    """Exercice 20 : le dataset volontairement incohérent doit être démonté."""

    DATASET = {
        'cloud_spend_monthly': 50000,
        'currency': 'EUR',
        'forecast_end_of_month': 42000,
        'budget_monthly': 60000,
        'budget_used_percentage': 90,
        'detected_waste_monthly': 12000,
        'potential_savings_monthly': 15000,
        'potential_savings_yearly': 120000,
        'reduction_percentage': 40,
        'services': [
            {'name': 'EC2', 'monthly_cost': 18000},
            {'name': 'RDS', 'monthly_cost': 12000},
            {'name': 'EKS', 'monthly_cost': 9000},
            {'name': 'S3', 'monthly_cost': 4000},
            {'name': 'CloudWatch', 'monthly_cost': 3000},
        ],
        'recommendations': [
            {'resource_id': 'i-123', 'service': 'EC2', 'environment': 'dev',
             'monthly_cost': 500, 'potential_saving': 500,
             'action': 'stop', 'risk': 'low'},
            {'resource_id': 'i-123', 'service': 'EC2', 'environment': 'dev',
             'monthly_cost': 500, 'potential_saving': 500,
             'action': 'delete', 'risk': 'low'},
            {'resource_id': 'rds-prod-01', 'service': 'RDS',
             'environment': 'production', 'monthly_cost': 2000,
             'potential_saving': 2000, 'action': 'delete', 'risk': 'low'},
        ],
    }

    def test_all_planted_errors_are_caught(self):
        rules = {v.rule for v in audit_dataset(self.DATASET)}
        assert rules >= {
            'potential_within_detected',   # 15 000 > 12 000
            'yearly_is_monthly_x12',       # 120 000 != 15 000 x 12
            'reduction_recomputable',      # 40 % != 30 %
            'forecast_not_below_spend',    # 42 000 < 50 000
            'budget_used_recomputable',    # 90 % != 83,3 %
            'service_sum_matches_total',   # 46 000 != 50 000
            'duplicate_resource',          # i-123 en double
            'risk_floor',                  # delete prod / delete dev en low
        }

    def test_violations_sorted_by_severity(self):
        violations = audit_dataset(self.DATASET)
        order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        ranks = [order[v.severity] for v in violations]
        assert ranks == sorted(ranks)
        assert violations[0].severity == 'critical'

    def test_coherent_dataset_passes(self):
        dataset = {
            'cloud_spend_monthly': 50000,
            'currency': 'EUR',
            'forecast_end_of_month': 51000,
            'budget_monthly': 60000,
            'budget_used_percentage': 83.3,
            'detected_waste_monthly': 7500,
            'potential_savings_monthly': 7500,
            'potential_savings_yearly': 90000,
            'reduction_percentage': 15,
            'services': [
                {'name': 'EC2', 'monthly_cost': 30000},
                {'name': 'RDS', 'monthly_cost': 15000},
                {'name': 'Other', 'monthly_cost': 5000},
            ],
            'recommendations': [
                {'resource_id': 'i-456', 'environment': 'dev', 'owner': 'team-x',
                 'monthly_cost': 240, 'potential_saving': 220,
                 'action': 'stop', 'risk': 'low'},
            ],
        }
        assert audit_dataset(dataset) == []


class TestRealisticDatasetFixtures:
    """Fixtures dédiées : un dataset valide passe, un invalide remonte des
    erreurs classées par gravité (section 9 du plan de test avancé)."""

    def test_valid_finops_dataset_passes_all_invariants(self):
        dataset = _load_fixture('valid_finops_dataset.json')
        assert audit_dataset(dataset) == []

    def test_valid_dataset_has_no_critical_findings(self):
        dataset = _load_fixture('valid_finops_dataset.json')
        violations = audit_dataset(dataset)
        assert all(v.severity != 'critical' for v in violations)

    def test_valid_dataset_generates_cto_safe_summary(self):
        dataset = _load_fixture('valid_finops_dataset.json')
        summary = generate_cto_safe_summary(dataset)
        for keyword in ('Detected waste', 'Potential savings',
                        'Annualized potential', 'Realized savings',
                        'Confirmed savings'):
            assert keyword in summary

    def test_invalid_dataset_reports_graded_errors(self):
        dataset = _load_fixture('invalid_finops_dataset.json')
        violations = audit_dataset(dataset)
        assert violations, "invalid dataset must raise at least one violation"
        assert any(v.severity == 'critical' for v in violations)
        # Chaque violation est explicite et traçable jusqu'à sa règle
        for v in violations:
            assert v.rule
            assert v.message
