"""
Tests de réalisme du pricing AWS (couche 2 de l'audit FinOps).

Un chiffre peut être mathématiquement cohérent mais économiquement faux :
une Elastic IP à 40 €/mois est suspecte quel que soit le calcul qui y mène.
Ces tests confrontent les invariants génériques de finops_invariants.py aux
constantes de pricing réellement utilisées par les détecteurs.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from core.finops_invariants import (
    FinOpsInvariantError,
    validate_ebs_cost,
    validate_ec2_cost,
    validate_elastic_ip_cost,
    validate_nat_gateway_cost,
    validate_rds_cost,
)
from detectors.ebs_orphan import EBS_PRICING_EUR_PER_GIB
from detectors.eip_orphan import EIP_MONTHLY_COST_EUR
from detectors.ec2_idle import EC2_PRICING
from detectors.nat_gateway_unused import NAT_GATEWAY_MONTHLY_COST_EUR

# Tarif public AWS pour le data processing NAT Gateway ($0.045/GB dans la
# plupart des régions), converti au même taux que le reste du pipeline.
NAT_DATA_PROCESSING_USD_PER_GB = 0.045
USD_TO_EUR = 0.92
NAT_DATA_PROCESSING_EUR_PER_GB = round(NAT_DATA_PROCESSING_USD_PER_GB * USD_TO_EUR, 4)


def test_elastic_ip_monthly_cost_reasonable():
    # Le tarif réel du détecteur passe
    assert validate_elastic_ip_cost(EIP_MONTHLY_COST_EUR, EIP_MONTHLY_COST_EUR) == \
        EIP_MONTHLY_COST_EUR
    # Exercice 12 : ~3,60 €/mois est dans la fourchette AWS réelle
    assert validate_elastic_ip_cost(3.60, EIP_MONTHLY_COST_EUR) == 3.60
    # Un montant à deux chiffres (ex. 40 €/mois) est suspect et rejeté
    with pytest.raises(FinOpsInvariantError):
        validate_elastic_ip_cost(40.0, EIP_MONTHLY_COST_EUR)


def test_ebs_cost_matches_volume_size_and_type():
    size_gb = 100
    gp3_cost = size_gb * EBS_PRICING_EUR_PER_GIB['gp3']
    assert validate_ebs_cost(
        size_gb, 'gp3', gp3_cost, EBS_PRICING_EUR_PER_GIB) == pytest.approx(gp3_cost)
    # Coût gonflé sans rapport avec la taille/type déclarés
    with pytest.raises(FinOpsInvariantError):
        validate_ebs_cost(size_gb, 'gp3', gp3_cost * 3, EBS_PRICING_EUR_PER_GIB)
    with pytest.raises(FinOpsInvariantError):
        validate_ebs_cost(size_gb, 'unknown_type', 50.0, EBS_PRICING_EUR_PER_GIB)


def test_ec2_cost_matches_instance_type_region_and_hours():
    instance_type = 't3.medium'
    full_month_price = EC2_PRICING[instance_type]
    # Tournée tout le mois : coût attendu = tarif catalogue
    assert validate_ec2_cost(
        instance_type, hours_running=730, monthly_cost=full_month_price,
        pricing_table=EC2_PRICING) == pytest.approx(full_month_price)
    # Tournée à mi-mois : coût proraté, pas le tarif plein mois
    with pytest.raises(FinOpsInvariantError):
        validate_ec2_cost(
            instance_type, hours_running=365, monthly_cost=full_month_price,
            pricing_table=EC2_PRICING)
    assert validate_ec2_cost(
        instance_type, hours_running=365, monthly_cost=full_month_price / 2,
        pricing_table=EC2_PRICING) == pytest.approx(full_month_price / 2)


def test_rds_cost_matches_instance_class_region_and_storage():
    # Catalogue de test illustratif : Wasteless n'a pas encore de détecteur
    # RDS ; ce test valide le mécanisme générique (instance + stockage), pas
    # une source de pricing RDS réelle.
    rds_pricing_table = {'db.t3.medium': 55.0, 'db.t3.large': 110.0}
    storage_rate = 0.10  # EUR/GB/mois, gp2 storage
    storage_gb = 100
    expected = rds_pricing_table['db.t3.medium'] + storage_gb * storage_rate
    assert validate_rds_cost(
        'db.t3.medium', storage_gb, expected,
        rds_pricing_table, storage_rate) == pytest.approx(expected)
    # Coût qui ignore le stockage (ne couvre que le compute)
    with pytest.raises(FinOpsInvariantError):
        validate_rds_cost(
            'db.t3.medium', storage_gb, rds_pricing_table['db.t3.medium'],
            rds_pricing_table, storage_rate)
    with pytest.raises(FinOpsInvariantError):
        validate_rds_cost(
            'db.unknown', storage_gb, expected, rds_pricing_table, storage_rate)


def test_nat_gateway_cost_includes_hourly_and_data_processing():
    # Gateway idle sans trafic : coût = composante horaire seule
    assert validate_nat_gateway_cost(
        NAT_GATEWAY_MONTHLY_COST_EUR, NAT_GATEWAY_MONTHLY_COST_EUR,
        data_processed_gb=0, data_processing_rate_eur_per_gb=NAT_DATA_PROCESSING_EUR_PER_GB
    ) == NAT_GATEWAY_MONTHLY_COST_EUR

    # Gateway avec trafic : le coût affiché doit inclure le data processing
    data_processed_gb = 500
    expected = (NAT_GATEWAY_MONTHLY_COST_EUR
                + data_processed_gb * NAT_DATA_PROCESSING_EUR_PER_GB)
    with pytest.raises(FinOpsInvariantError):
        validate_nat_gateway_cost(
            NAT_GATEWAY_MONTHLY_COST_EUR, NAT_GATEWAY_MONTHLY_COST_EUR,
            data_processed_gb, NAT_DATA_PROCESSING_EUR_PER_GB)
    assert validate_nat_gateway_cost(
        expected, NAT_GATEWAY_MONTHLY_COST_EUR,
        data_processed_gb, NAT_DATA_PROCESSING_EUR_PER_GB) == pytest.approx(expected)
