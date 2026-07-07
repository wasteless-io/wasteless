"""
Basic arithmetic invariants: annualization, percentages, forecast/budget checks, service-breakdown consistency. The foundational layer other modules build on.
"""

from typing import Dict
from ._shared import MONTHS_PER_YEAR, FinOpsInvariantError


def annualize(monthly: float) -> float:
    """Seule conversion mensuel→annuel autorisée : jamais stocker le chiffre
    annuel indépendamment du mensuel."""
    return monthly * MONTHS_PER_YEAR


def waste_percentage(waste_monthly: float, spend_monthly: float) -> float:
    """% de waste sur le spend, même période et même devise exigées."""
    if spend_monthly <= 0:
        raise FinOpsInvariantError(f"Cloud spend must be positive, got {spend_monthly}")
    if waste_monthly < 0:
        raise FinOpsInvariantError(f"Detected waste cannot be negative, got {waste_monthly}")
    if waste_monthly > spend_monthly:
        raise FinOpsInvariantError(
            f"Detected waste ({waste_monthly}) exceeds cloud spend "
            f"({spend_monthly}): on ne peut pas gaspiller plus qu'on ne dépense "
            f"— périodes ou devises probablement mélangées"
        )
    return waste_monthly / spend_monthly * 100


def budget_used_percentage(spent: float, budget: float) -> float:
    if budget <= 0:
        raise FinOpsInvariantError(f"Budget must be positive, got {budget}")
    if spent < 0:
        raise FinOpsInvariantError(f"Spend cannot be negative, got {spent}")
    return spent / budget * 100


def validate_forecast(forecast_end_of_month: float, current_spend_mtd: float) -> float:
    """Le forecast fin de mois ne peut pas être sous le déjà-dépensé."""
    if forecast_end_of_month < current_spend_mtd:
        raise FinOpsInvariantError(
            f"Forecast ({forecast_end_of_month}) is below month-to-date spend "
            f"({current_spend_mtd}): impossible, le réalisé ne diminue pas"
        )
    return forecast_end_of_month


def validate_service_breakdown(
    service_costs: Dict[str, float], total_spend: float, tolerance: float = 0.005
) -> float:
    """Vérifie que la ventilation par service boucle sur le total.

    Retourne le montant non ventilé (à afficher en ligne « Other »), lève si
    la somme des services dépasse le total ou si l'écart silencieux excède
    la tolérance (fraction du total).
    """
    breakdown_sum = sum(service_costs.values())
    gap = total_spend - breakdown_sum
    if gap < -abs(total_spend) * tolerance:
        raise FinOpsInvariantError(
            f"Service costs sum ({breakdown_sum}) exceeds total spend "
            f"({total_spend}): double comptage probable"
        )
    if gap > abs(total_spend) * tolerance:
        raise FinOpsInvariantError(
            f"Service breakdown leaves {gap:.2f} unaccounted "
            f"({gap / total_spend * 100:.1f}% of total): ajouter une ligne "
            f"'Other' explicite plutôt qu'un écart silencieux"
        )
    return max(gap, 0.0)
