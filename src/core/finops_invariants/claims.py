"""
CTO-safe wording for any number or claim shown to a human: forbidden words, percentage/annualization sanity, and the dashboard headline / CTO summary generators. See feedback-cto-safe-formulation conventions.
"""

from typing import Any, Dict, List, Optional
from ._shared import FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS, FinOpsInvariantError
from .arithmetic import annualize, waste_percentage


def validate_claim_percentage(
    claimed_pct: float,
    detected_waste_monthly: float,
    spend_monthly: float,
    tolerance_pts: float = 1.0,
) -> float:
    """Un pourcentage annoncé doit être recalculable depuis les données.

    Tout claim au-dessus du waste réellement détecté (à la tolérance près,
    en points) est indéfendable devant un CTO.
    """
    actual_pct = waste_percentage(detected_waste_monthly, spend_monthly)
    if claimed_pct > actual_pct + tolerance_pts:
        raise FinOpsInvariantError(
            f"Claimed {claimed_pct}% but data supports {actual_pct:.1f}%: "
            f"claim {claimed_pct / actual_pct:.1f}x above evidence"
        )
    return claimed_pct


def validate_claim_wording(text: str, savings_type: str, completed_actions: int = 0) -> str:
    """« guaranteed », « instantly », « no risk » sont interdits sur du
    potential savings : ils promettent une certitude qu'un chiffre non
    remédié ne peut pas soutenir. « realized »/« guaranteed » n'importe où
    exige des actions effectivement exécutées."""
    lowered = text.lower()

    if savings_type != "realized":
        hits = [w for w in FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS if w in lowered]
        if hits:
            raise FinOpsInvariantError(
                f"Forbidden wording {hits} in a '{savings_type}' savings " f"claim: {text!r}"
            )

    if ("realized" in lowered or "guaranteed" in lowered) and completed_actions == 0:
        raise FinOpsInvariantError(
            f"Claim implies certainty ('realized'/'guaranteed') with zero "
            f"completed remediation actions: {text!r}"
        )

    return text


def validate_annualized_claim(annual_value: float, monthly_basis: Optional[float]) -> float:
    """Un claim annualisé doit exhiber sa base mensuelle et en être le
    produit exact par 12 — jamais un chiffre annuel stocké seul."""
    if monthly_basis is None:
        raise FinOpsInvariantError("Annualized claim has no explicit monthly basis")
    expected = annualize(monthly_basis)
    if abs(annual_value - expected) > 0.01:
        raise FinOpsInvariantError(
            f"Annualized claim ({annual_value}) != monthly basis "
            f"({monthly_basis}) x 12 ({expected})"
        )
    return annual_value


def validate_up_to_claim(claimed_value: float, dataset_max_value: float) -> float:
    """Un claim « up to X » ne peut pas dépasser le maximum que le dataset
    supporte réellement."""
    if claimed_value > dataset_max_value:
        raise FinOpsInvariantError(
            f"'Up to {claimed_value}' claim exceeds dataset-supported "
            f"maximum ({dataset_max_value})"
        )
    return claimed_value


def validate_low_risk_claim(claimed_savings: float, recommendations: List[Dict[str, Any]]) -> float:
    """Un claim « low-risk savings » ne doit sommer que les recommandations
    effectivement classées low risk — pas l'ensemble du potentiel."""
    low_risk_total = sum(
        r.get("potential_saving", r.get("saving", 0.0))
        for r in recommendations
        if r.get("risk") == "low"
    )
    if claimed_savings > low_risk_total + 0.01:
        raise FinOpsInvariantError(
            f"Low-risk claim ({claimed_savings}) exceeds sum of low-risk "
            f"recommendations ({low_risk_total})"
        )
    return claimed_savings


def generate_cto_safe_summary(dataset: Dict[str, Any]) -> str:
    """Résumé textuel distinguant detected / potential / annualized /
    realized / confirmed — le format validé pour toute communication
    chiffrée (voir memory: feedback-cto-safe-formulation)."""
    spend = dataset.get("cloud_spend_monthly", 0.0)
    waste = dataset.get("detected_waste_monthly", 0.0)
    potential = dataset.get("potential_savings_monthly", waste)
    realized = dataset.get("realized_savings_monthly", 0.0)
    confirmed = dataset.get("confirmed_savings_monthly", 0.0)
    pct = waste_percentage(waste, spend) if spend else 0.0
    return (
        f"Detected waste: {waste:.0f}/mo ({pct:.1f}% of {spend:.0f} spend). "
        f"Potential savings: up to {potential:.0f}/mo. "
        f"Annualized potential: up to {annualize(potential):.0f}/yr. "
        f"Realized savings: {realized:.0f} (requires completed actions). "
        f"Confirmed savings: {confirmed:.0f} (verified via Cost Explorer)."
    )


# Termes qui affirment une certitude ou une absence de risque qu'un montant
# non remédié ne peut jamais soutenir — étend FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS
# avec les formulations relevées dans l'audit de wording dashboard.
FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS = FORBIDDEN_WORDS_FOR_POTENTIAL_CLAIMS + (
    "risk-free",
    "confirmed savings",
    "permanent savings",
    "save automatically",
)


def validate_dashboard_headline(
    headline: str, realized_savings_monthly: float, executed_actions_count: int
) -> str:
    """Un headline mentionnant 'realized' sans action exécutée surpromet :
    même règle que validate_claim_wording, appliquée au headline dashboard."""
    lowered = headline.lower()
    if "realized" in lowered and executed_actions_count == 0:
        raise FinOpsInvariantError(
            f"Headline claims 'realized' with zero executed actions: " f"{headline!r}"
        )
    return headline


def validate_annualized_claim_assumptions(claim_type: str, assumptions: List[str]) -> bool:
    """Un claim annualisé doit expliciter ses hypothèses (waste constant,
    remédiation totale et immédiate) — sinon il se lit comme une garantie."""
    if claim_type == "annualized_potential" and not assumptions:
        raise FinOpsInvariantError("Annualized potential claim must state its assumptions")
    return True
