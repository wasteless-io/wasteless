"""
Tests de confidence score déterministe (couche 2 de l'audit FinOps).

High confidence exige : currency, period, pricing_source, resource_id,
observation window valide, owner, environment, métriques d'usage
pertinentes. L'absence de chacun dégrade la confiance selon la table du
document (missing period/currency -> low ; le reste -> medium au maximum).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import assess_confidence

BASE_METADATA = {
    'currency': 'EUR',
    'period': '30d',
    'pricing_source': 'aws_on_demand_static',
    'owner': 'team-x',
    'environment': 'dev',
}


def test_missing_pricing_source_lowers_confidence():
    metadata = {**BASE_METADATA, 'pricing_source': None}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'medium'


def test_missing_period_lowers_confidence():
    metadata = {**BASE_METADATA, 'period': None}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'low'


def test_missing_owner_lowers_confidence():
    metadata = {**BASE_METADATA, 'owner': None}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'medium'


def test_missing_environment_lowers_confidence():
    metadata = {**BASE_METADATA, 'environment': None}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'medium'


def test_missing_currency_lowers_confidence():
    metadata = {**BASE_METADATA, 'currency': None}
    assert assess_confidence(metadata, signal_confidence=0.95) == 'low'


def test_short_observation_window_lowers_confidence():
    assert assess_confidence(
        BASE_METADATA, signal_confidence=0.95,
        observation_days=2, min_observation_days=14) == 'medium'
    assert assess_confidence(
        BASE_METADATA, signal_confidence=0.95,
        observation_days=14, min_observation_days=14) == 'high'


def test_incomplete_metrics_prevent_high_confidence():
    assert assess_confidence(
        BASE_METADATA, signal_confidence=0.95, metrics_complete=False) == 'medium'
    assert assess_confidence(
        BASE_METADATA, signal_confidence=0.95, metrics_complete=True) == 'high'


def test_all_fields_present_and_strong_signal_yields_high():
    assert assess_confidence(BASE_METADATA, signal_confidence=0.9) == 'high'


def test_exercise_19_savings_estimate_cannot_be_high():
    # Exercice 19 : pricing source, période et devise non précisées
    metadata = {'currency': None, 'period': None, 'pricing_source': None}
    assert assess_confidence(metadata, signal_confidence=1.0) == 'low'
