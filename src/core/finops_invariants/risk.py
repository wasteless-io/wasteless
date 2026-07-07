"""
Risk-level floors for destructive/service-interrupting actions, and confidence scoring based on which pricing/ownership metadata is present.
"""

from typing import Any, Dict, Optional
from ._shared import DESTRUCTIVE_ACTIONS, SERVICE_INTERRUPTING_ACTIONS, RISK_LEVELS, LOW_CAP_FIELDS, MEDIUM_CAP_FIELDS, FinOpsInvariantError

def minimum_risk_for(action: str, environment: Optional[str],
                     owner: Optional[str] = None) -> str:
    """Plancher de risque imposé par l'action, l'environnement et le owner.

    Une action destructive en production est toujours critical — jamais
    éligible à l'auto-remédiation. Un environnement manquant ou inconnu est
    traité comme production (on ne dégrade pas le risque faute
    d'information) : il relève le plancher des actions non destructives à
    medium plutôt que low. L'absence de owner relève aussi le plancher à au
    moins medium — personne à prévenir avant d'agir est un facteur de risque
    en soi, quelle que soit l'action.
    """
    env = (environment or 'unknown').lower()
    action_key = (action or '').lower()
    is_prod_like = env in ('production', 'prod', 'unknown')

    if action_key in DESTRUCTIVE_ACTIONS:
        floor = 'critical' if is_prod_like else 'medium'
    elif action_key in SERVICE_INTERRUPTING_ACTIONS:
        floor = 'high' if is_prod_like else 'low'
    else:
        floor = 'medium' if is_prod_like else 'low'

    if owner is None and RISK_LEVELS.index(floor) < RISK_LEVELS.index('medium'):
        floor = 'medium'

    return floor


def validate_risk_level(action: str, environment: Optional[str],
                        displayed_risk: str,
                        owner: Optional[str] = None) -> str:
    """Lève si le risque affiché est en dessous du plancher requis."""
    floor = minimum_risk_for(action, environment, owner)
    if RISK_LEVELS.index(displayed_risk) < RISK_LEVELS.index(floor):
        raise FinOpsInvariantError(
            f"Risk '{displayed_risk}' for action '{action}' on "
            f"environment '{environment}' (owner={owner!r}) is below "
            f"required floor '{floor}'")
    return displayed_risk


def assess_confidence(metadata: Dict[str, Any],
                      signal_confidence: float = 1.0,
                      observation_days: Optional[int] = None,
                      min_observation_days: Optional[int] = None,
                      metrics_complete: bool = True) -> str:
    """Confiance affichable, plafonnée par la complétude des métadonnées.

    Un montant sans devise ou période est invérifiable : low d'office, quel
    que soit le signal. Une source de pricing, un owner ou un environnement
    manquant, une fenêtre d'observation trop courte, ou des métriques
    d'usage incomplètes dégradent la confiance à medium au maximum — la
    donnée reste vérifiable mais incomplète, pas invérifiable.
    """
    if any(not metadata.get(k) for k in LOW_CAP_FIELDS):
        return 'low'

    medium_capped = any(not metadata.get(k) for k in MEDIUM_CAP_FIELDS)
    medium_capped = medium_capped or not metrics_complete
    if (observation_days is not None and min_observation_days is not None
            and observation_days < min_observation_days):
        medium_capped = True

    if medium_capped:
        return 'medium' if signal_confidence >= 0.5 else 'low'
    if signal_confidence >= 0.8:
        return 'high'
    if signal_confidence >= 0.5:
        return 'medium'
    return 'low'
