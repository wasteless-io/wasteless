"""
Caps and deduplication for savings figures: a recommendation can't claim more than the resource costs, potential can't be claimed twice, realized savings need an executed action behind them.
"""

from typing import Any, Dict, List
from ._shared import FinOpsInvariantError


def validate_recommendation_saving(saving_monthly: float, resource_monthly_cost: float) -> float:
    """Une recommandation ne peut pas économiser plus que ne coûte la ressource."""
    if saving_monthly < 0:
        raise FinOpsInvariantError(f"Saving cannot be negative, got {saving_monthly}")
    if saving_monthly > resource_monthly_cost:
        raise FinOpsInvariantError(
            f"Saving ({saving_monthly}) exceeds resource cost " f"({resource_monthly_cost})"
        )
    return saving_monthly


def validate_potential_vs_detected(
    potential_savings_monthly: float, detected_waste_monthly: float
) -> float:
    """Le potentiel est plafonné par le waste détecté (contrainte en cascade
    potential ≤ detected ≤ spend)."""
    if potential_savings_monthly > detected_waste_monthly:
        raise FinOpsInvariantError(
            f"Potential savings ({potential_savings_monthly}) exceed detected "
            f"waste ({detected_waste_monthly}): le potentiel ne peut pas "
            f"dépasser ce qui a été détecté"
        )
    return potential_savings_monthly


def deduplicated_total_savings(recommendations: List[Dict[str, Any]]) -> float:
    """Total des savings avec une ressource comptée une seule fois.

    Deux actions sur la même ressource (ex. stop et delete de i-123) sont
    mutuellement exclusives : seule la meilleure compte. Agrégation par
    resource_id au max, jamais par recommandation.
    """
    best_per_resource: Dict[str, float] = {}
    for rec in recommendations:
        rid = rec["resource_id"]
        saving = rec.get("potential_saving", rec.get("saving", 0.0))
        best_per_resource[rid] = max(best_per_resource.get(rid, 0.0), saving)
    return sum(best_per_resource.values())


def validate_realized_savings(realized_monthly: float, completed_actions: int) -> float:
    """Aucune économie « réalisée » sans action de remédiation exécutée.

    Afficher du potentiel comme du réalisé est du misreporting : realized
    reste à 0 tant que rien n'a été appliqué et vérifié (savings_realized
    via Cost Explorer).
    """
    if realized_monthly > 0 and completed_actions == 0:
        raise FinOpsInvariantError(
            f"Realized savings ({realized_monthly}) reported with zero "
            f"completed remediation: seuls les montants post-action vérifiés "
            f"sont des économies réalisées"
        )
    return realized_monthly
