"""
Top-level orchestration: audit_dataset() runs the arithmetic/savings/risk checks over a full dataset and collects Violations; the read-only-audit helpers additionally cross-check claims against a real AWS account.
"""

from typing import Any, Dict, List, Optional
from ._shared import FinOpsInvariantError, Violation
from .arithmetic import (
    annualize,
    budget_used_percentage,
    validate_forecast,
    validate_service_breakdown,
    waste_percentage,
)
from .savings import validate_potential_vs_detected, validate_recommendation_saving
from .risk import validate_risk_level


def audit_dataset(dataset: Dict[str, Any]) -> List[Violation]:
    """Passe un dataset dashboard complet au crible de tous les invariants.

    Ne lève pas : retourne la liste des violations classées par sévérité,
    pour affichage ou blocage en amont de la publication. Les clés attendues
    suivent le format de l'exercice 20 de l'audit (cloud_spend_monthly,
    detected_waste_monthly, potential_savings_monthly/yearly,
    reduction_percentage, budget_monthly, budget_used_percentage,
    forecast_end_of_month, services, recommendations).
    """
    violations: List[Violation] = []

    def _check(rule: str, severity: str, fn) -> None:
        try:
            fn()
        except FinOpsInvariantError as e:
            violations.append(Violation(rule, severity, str(e)))

    spend = dataset.get("cloud_spend_monthly")
    waste = dataset.get("detected_waste_monthly")
    potential = dataset.get("potential_savings_monthly")
    yearly = dataset.get("potential_savings_yearly")
    reduction = dataset.get("reduction_percentage")
    budget = dataset.get("budget_monthly")
    budget_used = dataset.get("budget_used_percentage")
    forecast = dataset.get("forecast_end_of_month")
    services = dataset.get("services") or []
    recommendations = dataset.get("recommendations") or []

    if not dataset.get("currency"):
        violations.append(
            Violation(
                "missing_currency",
                "high",
                "Dataset has no currency: tous les montants sont invérifiables",
            )
        )

    if spend is not None and waste is not None:
        _check("waste_within_spend", "critical", lambda: waste_percentage(waste, spend))

    if waste is not None and potential is not None:
        _check(
            "potential_within_detected",
            "critical",
            lambda: validate_potential_vs_detected(potential, waste),
        )

    if potential is not None and yearly is not None:
        expected = annualize(potential)
        if abs(yearly - expected) > 0.01:
            violations.append(
                Violation(
                    "yearly_is_monthly_x12",
                    "high",
                    f"potential_savings_yearly ({yearly}) != monthly x 12 "
                    f"({expected}): le chiffre annuel doit être calculé, "
                    f"jamais stocké indépendamment",
                )
            )

    if reduction is not None and spend and potential is not None:
        actual = potential / spend * 100
        if abs(reduction - actual) > 1.0:
            violations.append(
                Violation(
                    "reduction_recomputable",
                    "high",
                    f"reduction_percentage ({reduction}%) not recomputable: "
                    f"potential/spend = {actual:.1f}%",
                )
            )

    if forecast is not None and spend is not None:
        _check("forecast_not_below_spend", "critical", lambda: validate_forecast(forecast, spend))

    if budget is not None and spend is not None and budget_used is not None:
        actual = budget_used_percentage(spend, budget)
        if abs(budget_used - actual) > 0.5:
            violations.append(
                Violation(
                    "budget_used_recomputable",
                    "medium",
                    f"budget_used_percentage ({budget_used}%) != spend/budget " f"({actual:.1f}%)",
                )
            )

    if services and spend is not None:
        costs = {s["name"]: s["monthly_cost"] for s in services}
        _check(
            "service_sum_matches_total", "medium", lambda: validate_service_breakdown(costs, spend)
        )

    seen_resources = set()
    for rec in recommendations:
        rid = rec.get("resource_id", "?")
        if rid in seen_resources:
            violations.append(
                Violation(
                    "duplicate_resource",
                    "high",
                    f"Resource {rid} has multiple recommendations: actions "
                    f"mutuellement exclusives, dédupliquer les savings au max "
                    f"par ressource",
                    context={"resource_id": rid},
                )
            )
        seen_resources.add(rid)

        _check(
            "saving_within_resource_cost",
            "high",
            lambda r=rec: validate_recommendation_saving(
                r.get("potential_saving", 0.0), r.get("monthly_cost", 0.0)
            ),
        )
        _check(
            "risk_floor",
            "critical",
            lambda r=rec: validate_risk_level(
                r.get("action", ""), r.get("environment"), r.get("risk", "low"), r.get("owner")
            ),
        )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    violations.sort(key=lambda v: severity_order[v.severity])
    return violations


def validate_within_tolerance_pct(
    actual: float, reference: float, tolerance_pct: float, label: str = "value"
) -> float:
    """Écart en % entre un chiffre Wasteless et une source externe
    (Cost Explorer, Compute Optimizer...). Lève si l'écart dépasse la
    tolérance documentée pour ce type de comparaison."""
    if reference == 0:
        raise FinOpsInvariantError(f"{label} reference is zero: cannot compute a tolerance")
    delta_pct = abs(actual - reference) / abs(reference) * 100
    if delta_pct > tolerance_pct:
        raise FinOpsInvariantError(
            f"{label} delta {delta_pct:.2f}% exceeds tolerance "
            f"{tolerance_pct}% (actual={actual}, reference={reference})"
        )
    return delta_pct


def validate_resources_exist_in_aws(
    recommended_resource_ids: List[str], aws_resource_ids: set
) -> bool:
    missing = [r for r in recommended_resource_ids if r not in aws_resource_ids]
    if missing:
        raise FinOpsInvariantError(
            f"Recommendations reference resources not found in AWS: {missing}"
        )
    return True


def validate_read_only_audit(api_calls: List[str], forbidden_write_actions: set) -> bool:
    violations = set(api_calls) & forbidden_write_actions
    if violations:
        raise FinOpsInvariantError(f"Write API(s) called during a read-only audit: {violations}")
    return True


def validate_audit_trace(
    trace_id: Optional[str], started_at: Optional[str], completed_at: Optional[str]
) -> bool:
    missing = [
        name
        for name, value in (
            ("trace_id", trace_id),
            ("started_at", started_at),
            ("completed_at", completed_at),
        )
        if not value
    ]
    if missing:
        raise FinOpsInvariantError(f"Audit run missing required trace fields: {missing}")
    return True
