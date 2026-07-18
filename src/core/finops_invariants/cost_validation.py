"""
AWS pricing sanity checks: per-resource-type cost realism (EC2/EBS/EIP/NAT/RDS), required pricing metadata, and saving-type-specific validation (resize/schedule/delete).
"""

from typing import Any, Dict
from ._shared import HOURS_PER_MONTH, FinOpsInvariantError


def validate_cost_within_tolerance(
    actual: float, expected: float, tolerance_pct: float = 0.05, label: str = "cost"
) -> float:
    if expected <= 0:
        raise FinOpsInvariantError(f"Expected {label} must be positive")
    deviation = abs(actual - expected) / expected
    if deviation > tolerance_pct:
        raise FinOpsInvariantError(
            f"{label} {actual} deviates {deviation * 100:.1f}% from expected "
            f"{expected:.2f} (tolerance {tolerance_pct * 100:.0f}%)"
        )
    return actual


def validate_ec2_cost(
    instance_type: str,
    hours_running: float,
    monthly_cost: float,
    pricing_table: Dict[str, float],
    hours_per_month: float = HOURS_PER_MONTH,
    tolerance_pct: float = 0.05,
) -> float:
    """Le coût affiché doit correspondre au tarif on-demand du type
    d'instance, proraté aux heures réellement tournées sur le mois."""
    if instance_type not in pricing_table:
        raise FinOpsInvariantError(f"Unknown instance type '{instance_type}': cannot validate cost")
    full_month_price = pricing_table[instance_type]
    expected = full_month_price * (hours_running / hours_per_month)
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f"EC2 {instance_type}"
    )


def validate_ebs_cost(
    size_gb: float,
    volume_type: str,
    monthly_cost: float,
    pricing_eur_per_gib: Dict[str, float],
    tolerance_pct: float = 0.05,
) -> float:
    if volume_type not in pricing_eur_per_gib:
        raise FinOpsInvariantError(f"Unknown EBS volume type '{volume_type}': cannot validate cost")
    expected = size_gb * pricing_eur_per_gib[volume_type]
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f"EBS {volume_type}"
    )


def validate_elastic_ip_cost(
    monthly_cost: float, known_eip_cost: float, tolerance_pct: float = 0.10
) -> float:
    """Une Elastic IP inutilisée coûte quelques euros/mois — un montant à
    deux chiffres (ex. 40 $/mois) est suspect et doit être rejeté."""
    return validate_cost_within_tolerance(
        monthly_cost, known_eip_cost, tolerance_pct, label="Elastic IP"
    )


def validate_nat_gateway_cost(
    monthly_cost: float,
    base_hourly_cost: float,
    data_processed_gb: float,
    data_processing_rate_eur_per_gb: float,
    tolerance_pct: float = 0.05,
) -> float:
    """Le coût d'une NAT Gateway doit inclure la composante horaire ET le
    data processing — un chiffre qui ignore le trafic sous-estime le coût
    réel dès que la ressource traite des données."""
    expected = base_hourly_cost + data_processed_gb * data_processing_rate_eur_per_gb
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label="NAT Gateway"
    )


def validate_rds_cost(
    instance_class: str,
    storage_gb: float,
    monthly_cost: float,
    instance_pricing_table: Dict[str, float],
    storage_rate_eur_per_gb: float,
    tolerance_pct: float = 0.05,
) -> float:
    """Le coût RDS doit couvrir l'instance ET le stockage — les deux
    composantes de la facture, pas seulement le compute."""
    if instance_class not in instance_pricing_table:
        raise FinOpsInvariantError(
            f"Unknown RDS instance class '{instance_class}': cannot " f"validate cost"
        )
    expected = instance_pricing_table[instance_class] + storage_gb * storage_rate_eur_per_gb
    return validate_cost_within_tolerance(
        monthly_cost, expected, tolerance_pct, label=f"RDS {instance_class}"
    )


# Champs sans lesquels un coût par ressource n'est pas traçable jusqu'à sa
# source de pricing (AWS Price List API ou équivalent), quelle que soit la
# qualité du signal d'usage.
REQUIRED_PRICING_METADATA_FIELDS = ("pricing_source", "currency", "period", "region")

# Services facturés à l'heure : le coût mensuel affiché doit être dérivable
# d'un tarif horaire et d'un nombre d'heures, pas d'un forfait opaque.
HOURLY_BILLED_SERVICES = frozenset({"EC2", "RDS", "NAT Gateway", "NAT_GATEWAY"})


def validate_pricing_metadata_complete(
    item: Dict[str, Any], required_fields: tuple = REQUIRED_PRICING_METADATA_FIELDS
) -> bool:
    missing = [f for f in required_fields if not item.get(f)]
    if missing:
        raise FinOpsInvariantError(f"Missing pricing metadata: {missing}")
    return True


def validate_estimated_cost_matches_unit_price(
    unit_price_hourly: float, hours: float, estimated_cost: float, tolerance_pct: float = 0.02
) -> float:
    expected = unit_price_hourly * hours
    return validate_cost_within_tolerance(
        estimated_cost, expected, tolerance_pct, label="estimated_monthly_cost"
    )


def validate_resize_saving(
    current_monthly_cost: float, recommended_monthly_cost: float, potential_saving: float
) -> float:
    """Un saving de resize/downsize doit être exactement le delta entre le
    coût courant et le coût recommandé — jamais un montant approximatif."""
    expected = current_monthly_cost - recommended_monthly_cost
    if abs(potential_saving - expected) > 0.01:
        raise FinOpsInvariantError(
            f"Resize saving ({potential_saving}) != current cost - "
            f"recommended cost ({expected})"
        )
    return potential_saving


def validate_schedule_saving(
    full_monthly_cost: float,
    reduced_runtime_ratio: float,
    claimed_saving: float,
    tolerance_pct: float = 0.05,
) -> float:
    """Un saving de schedule (arrêt hors horaires) doit refléter la
    proportion d'heures effectivement coupées, pas le coût plein."""
    if not 0 <= reduced_runtime_ratio <= 1:
        raise FinOpsInvariantError("reduced_runtime_ratio must be between 0 and 1")
    expected = full_monthly_cost * (1 - reduced_runtime_ratio)
    return validate_cost_within_tolerance(
        claimed_saving, expected, tolerance_pct, label="schedule_saving"
    )


def validate_delete_saving(is_delete_safe: bool, saving: float, full_monthly_cost: float) -> float:
    """Un saving delete ne vaut le coût plein que si la suppression est
    confirmée sûre (snapshot/retention/route tables validées) — sinon ce
    n'est pas un saving exploitable, c'est un risque non couvert."""
    if not is_delete_safe:
        raise FinOpsInvariantError(
            "Delete saving claimed but delete is not confirmed safe "
            "(missing snapshot/retention policy/route table validation)"
        )
    if abs(saving - full_monthly_cost) > 0.01:
        raise FinOpsInvariantError(
            f"Delete saving ({saving}) should equal the full resource cost "
            f"({full_monthly_cost}) once delete is confirmed safe"
        )
    return saving
