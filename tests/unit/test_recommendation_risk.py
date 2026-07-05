"""
Tests de criticité environnement et de recommandations dangereuses
(couche 2 de l'audit FinOps : cohérence risque opérationnel).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import (
    FinOpsInvariantError,
    minimum_observation_days,
    minimum_risk_for,
    validate_batch_workload_classification,
    validate_cloudwatch_retention_saving,
    validate_ebs_delete,
    validate_eks_resize,
    validate_nat_gateway_delete,
    validate_observation_window,
    validate_risk_level,
    validate_underutilization_action,
)


# --- Criticité environnement -------------------------------------------

def test_production_resource_requires_higher_risk_than_dev():
    assert (minimum_risk_for('resize', 'production', owner='team-x')
            != minimum_risk_for('resize', 'dev', owner='team-x'))
    prod_floor = minimum_risk_for('stop', 'production', owner='team-x')
    dev_floor = minimum_risk_for('stop', 'dev', owner='team-x')
    levels = ('low', 'medium', 'high', 'critical')
    assert levels.index(prod_floor) > levels.index(dev_floor)


def test_unknown_environment_cannot_be_low_risk_for_destructive_action():
    with pytest.raises(FinOpsInvariantError):
        validate_risk_level('delete', 'unknown', 'low', owner='team-x')
    with pytest.raises(FinOpsInvariantError):
        validate_risk_level('delete', None, 'low', owner='team-x')
    assert minimum_risk_for('delete', 'unknown', owner='team-x') == 'critical'


def test_missing_owner_increases_risk_level():
    # Exercice 10 : instance dev sans tag owner -> plancher relevé
    with pytest.raises(FinOpsInvariantError):
        validate_risk_level('stop', 'dev', 'low', owner=None)
    assert minimum_risk_for('stop', 'dev', owner=None) == 'medium'
    # Avec owner connu, low reste défendable pour un stop en dev
    assert validate_risk_level('stop', 'dev', 'low', owner='team-x') == 'low'


def test_missing_environment_increases_risk_level():
    floor_with_env = minimum_risk_for('resize', 'dev', owner='team-x')
    floor_missing_env = minimum_risk_for('resize', None, owner='team-x')
    levels = ('low', 'medium', 'high', 'critical')
    assert levels.index(floor_missing_env) > levels.index(floor_with_env)


# --- Fenêtre d'observation ------------------------------------------------

def test_production_idle_detection_requires_minimum_observation_window():
    # Exercice 16 : 24h (1 jour) est très insuffisant pour un stop en prod
    with pytest.raises(FinOpsInvariantError):
        validate_observation_window('production', observation_days=1, action='stop')
    assert minimum_observation_days('production') == 14
    assert validate_observation_window(
        'production', observation_days=14, action='stop') == 14


def test_dev_idle_detection_accepts_shorter_observation_window():
    assert minimum_observation_days('dev') < minimum_observation_days('production')
    # 7 jours suffisent en dev (exercice 10 : 14 jours, largement au-dessus)
    assert validate_observation_window(
        'dev', observation_days=7, action='stop') == 7
    with pytest.raises(FinOpsInvariantError):
        validate_observation_window('dev', observation_days=2, action='stop')


def test_weekend_only_idle_does_not_trigger_production_stop():
    # Une fenêtre de 2 jours (week-end) ne peut jamais justifier un stop prod
    with pytest.raises(FinOpsInvariantError):
        validate_observation_window('production', observation_days=2, action='stop')


def test_batch_workload_not_marked_idle_without_schedule_context():
    with pytest.raises(FinOpsInvariantError):
        validate_batch_workload_classification(
            is_batch_workload=True, has_schedule_context=False, action='stop')
    assert validate_batch_workload_classification(
        is_batch_workload=True, has_schedule_context=True, action='stop') is True
    assert validate_batch_workload_classification(
        is_batch_workload=False, has_schedule_context=False, action='stop') is True


# --- Recommandations dangereuses -----------------------------------------

def test_rds_delete_never_recommended_by_underutilization_detector():
    with pytest.raises(FinOpsInvariantError):
        validate_underutilization_action('rds_underutilized', 'rds', 'delete')
    with pytest.raises(FinOpsInvariantError):
        validate_underutilization_action('rds_idle', 'rds', 'delete')
    # downsize reste acceptable pour ce type de détecteur
    assert validate_underutilization_action(
        'rds_underutilized', 'rds', 'downsize') == 'downsize'


def test_ebs_delete_requires_snapshot_or_retention_policy():
    # Exercice 11 : volume sans snapshot, environment unknown
    with pytest.raises(FinOpsInvariantError):
        validate_ebs_delete(has_snapshot=False, has_retention_policy=False)
    assert validate_ebs_delete(has_snapshot=True, has_retention_policy=False) is True
    assert validate_ebs_delete(has_snapshot=False, has_retention_policy=True) is True


def test_nat_gateway_delete_requires_route_table_validation():
    with pytest.raises(FinOpsInvariantError):
        validate_nat_gateway_delete(route_tables_validated=False)
    assert validate_nat_gateway_delete(route_tables_validated=True) is True


def test_cloudwatch_retention_change_is_not_counted_as_full_log_cost_saving():
    # Rétention réduite : ne peut pas économiser plus que la part stockage
    with pytest.raises(FinOpsInvariantError):
        validate_cloudwatch_retention_saving(
            claimed_saving_monthly=2000,
            storage_cost_monthly=800,
            total_log_cost_monthly=2000)
    # Ni égaler/dépasser le coût total des logs
    with pytest.raises(FinOpsInvariantError):
        validate_cloudwatch_retention_saving(
            claimed_saving_monthly=2000,
            storage_cost_monthly=2000,
            total_log_cost_monthly=2000)
    assert validate_cloudwatch_retention_saving(
        claimed_saving_monthly=500,
        storage_cost_monthly=800,
        total_log_cost_monthly=2000) == 500


def test_eks_resize_requires_nodegroup_and_workload_context():
    with pytest.raises(FinOpsInvariantError):
        validate_eks_resize(nodegroup=None, workload_metrics={'cpu': 0.3, 'memory': 0.4})
    with pytest.raises(FinOpsInvariantError):
        validate_eks_resize(nodegroup='ng-1', workload_metrics={'cpu': 0.3})
    with pytest.raises(FinOpsInvariantError):
        validate_eks_resize(nodegroup='ng-1', workload_metrics=None)
    assert validate_eks_resize(
        nodegroup='ng-1', workload_metrics={'cpu': 0.3, 'memory': 0.4}) is True
