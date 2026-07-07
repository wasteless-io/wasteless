"""
Linear spend forecasting and budget-overrun flagging, including the forecast-after-remediation projection shown once a recommendation is approved.
"""

from ._shared import FinOpsInvariantError
from .arithmetic import validate_forecast

def linear_forecast(current_spend: float, days_elapsed: float,
                    days_in_month: float) -> float:
    """Forecast fin de mois par burn rate linéaire — recalculable par
    n'importe qui à partir de trois nombres publiés."""
    if days_elapsed <= 0 or days_in_month <= 0:
        raise FinOpsInvariantError(
            "days_elapsed and days_in_month must be positive")
    if days_elapsed > days_in_month:
        raise FinOpsInvariantError(
            "days_elapsed cannot exceed days_in_month")
    return current_spend / days_elapsed * days_in_month


def forecast_after_remediation(forecast: float,
                               validated_low_risk_savings: float,
                               current_spend_mtd: float) -> float:
    """Le forecast après remédiation ne soustrait que les savings validés
    (low-risk, approuvés) — jamais un potentiel non qualifié — et ne peut
    jamais tomber sous le spend déjà réalisé ce mois-ci."""
    result = forecast - validated_low_risk_savings
    return validate_forecast(result, current_spend_mtd)


def flags_budget_overrun(forecast: float, budget: float) -> bool:
    return forecast > budget
