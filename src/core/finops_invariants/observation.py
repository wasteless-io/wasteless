"""
Minimum observation windows before a resource can be called idle, and the batch-workload exception (a periodic job can look idle between runs without being idle).
"""

from typing import Optional
from ._shared import (
    DESTRUCTIVE_ACTIONS,
    SERVICE_INTERRUPTING_ACTIONS,
    MIN_OBSERVATION_DAYS,
    DEFAULT_MIN_OBSERVATION_DAYS,
    FinOpsInvariantError,
)


def minimum_observation_days(environment: Optional[str]) -> int:
    env = (environment or "unknown").lower()
    return MIN_OBSERVATION_DAYS.get(env, DEFAULT_MIN_OBSERVATION_DAYS)


def validate_observation_window(
    environment: Optional[str], observation_days: float, action: str
) -> float:
    """Une action destructive ou interruptrice de service exige une fenêtre
    d'observation suffisante : 24h (voire un week-end) ne distingue pas une
    ressource réellement idle d'une charge ponctuellement calme.
    """
    action_key = (action or "").lower()
    if action_key not in DESTRUCTIVE_ACTIONS | SERVICE_INTERRUPTING_ACTIONS:
        return observation_days

    required = minimum_observation_days(environment)
    if observation_days < required:
        raise FinOpsInvariantError(
            f"Observation window ({observation_days}d) is too short for "
            f"action '{action}' on environment '{environment}': minimum "
            f"{required}d required"
        )
    return observation_days


def validate_batch_workload_classification(
    is_batch_workload: bool, has_schedule_context: bool, action: str
) -> bool:
    """Un workload batch (job périodique) ne doit pas être marqué idle sans
    contexte de planification : une absence d'activité pendant la fenêtre
    d'observation peut simplement tomber entre deux exécutions.
    """
    action_key = (action or "").lower()
    is_actionable = action_key in DESTRUCTIVE_ACTIONS | SERVICE_INTERRUPTING_ACTIONS
    if is_batch_workload and is_actionable and not has_schedule_context:
        raise FinOpsInvariantError(
            f"Batch workload flagged for '{action}' without schedule "
            f"context: la fenêtre d'observation peut tomber entre deux "
            f"exécutions planifiées"
        )
    return True
