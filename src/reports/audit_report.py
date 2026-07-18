#!/usr/bin/env python3
"""
Deterministic Markdown assembly for the AWS FinOps audit report.

This is NOT an LLM call. Golden-snapshot testing needs byte-stable output,
which an LLM cannot give run to run. All arithmetic and consistency checks
come from core.finops_invariants (audit_dataset, generate_cto_safe_summary);
this module only formats already-validated numbers into the 12-section
structure documented in docs/FINOPS_AUDIT_TEMPLATE.md. An LLM may later
narrate around this output (see reports/prompts/audit_report_system_prompt.md)
but never replaces it — the numbers and the consistency checks must stay
reproducible without a model in the loop.
"""

from typing import Any, Dict, List


from core.finops_invariants import (  # noqa: E402
    Violation,
    audit_dataset,
    generate_cto_safe_summary,
)

# audit_dataset() violation rules mapped to the human-readable check names
# used in section 11. Any rule not in this dict still surfaces (see
# _section_11) but without a friendly label.
CHECK_LABELS = {
    "waste_within_spend": "Waste percentage calculation",
    "potential_within_detected": "Potential vs. detected savings",
    "yearly_is_monthly_x12": "Monthly to annual savings consistency",
    "reduction_recomputable": "Reduction percentage recomputable",
    "forecast_not_below_spend": "Forecast sanity",
    "budget_used_recomputable": "Budget usage calculation",
    "service_sum_matches_total": "Service cost sum",
    "duplicate_resource": "Duplicate resources",
    "saving_within_resource_cost": "Saving does not exceed resource cost",
    "risk_floor": "Production destructive action safety",
}

# Checks that audit_dataset() can run only when both operands are present in
# the dataset. Declaring them here lets section 11 report "Not enough data"
# instead of silently omitting the row.
CHECK_REQUIREMENTS = {
    "waste_within_spend": ("cloud_spend_monthly", "detected_waste_monthly"),
    "potential_within_detected": ("detected_waste_monthly", "potential_savings_monthly"),
    "yearly_is_monthly_x12": ("potential_savings_monthly", "potential_savings_yearly"),
    "reduction_recomputable": (
        "reduction_percentage",
        "cloud_spend_monthly",
        "potential_savings_monthly",
    ),
    "forecast_not_below_spend": ("forecast_end_of_month", "cloud_spend_monthly"),
    "budget_used_recomputable": ("budget_monthly", "cloud_spend_monthly", "budget_used_percentage"),
    "service_sum_matches_total": ("services", "cloud_spend_monthly"),
    "duplicate_resource": ("recommendations",),
    "saving_within_resource_cost": ("recommendations",),
    "risk_floor": ("recommendations",),
}

RISK_LEVELS = ("critical", "high", "medium", "low")


def _fmt_eur(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "Not provided"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "Not provided"


def _get(d: Dict[str, Any], *path, default="Not provided"):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return default if cur is None else cur


def _section_1(dataset: Dict[str, Any]) -> str:
    meta = dataset.get("meta", {})
    fin = dataset
    recs = dataset.get("recommendations", [])
    high_confidence = sum(1 for r in recs if (r.get("confidence") or 0) >= 0.80)
    critical = sum(1 for r in recs if r.get("risk") == "critical")

    lines = [
        "## 1. Executive Summary",
        "",
        f"- Audit date: {_get(meta, 'audit_date')}",
        f"- AWS account: {_get(meta, 'account_id')}",
        f"- Period analyzed: {_get(meta, 'period_start')} → {_get(meta, 'period_end')}",
        f"- Region scope: {_get(meta, 'region_scope')}",
        f"- Currency: {_get(dataset, 'currency')}",
        f"- Monthly cloud spend: {_fmt_eur(fin.get('cloud_spend_monthly'))}",
        f"- Forecast end of month: {_fmt_eur(fin.get('forecast_end_of_month'))}",
        f"- Monthly budget: {_fmt_eur(fin.get('budget_monthly'))}",
        f"- Budget usage: {_fmt_pct(fin.get('budget_used_percentage'))}",
        f"- Detected waste: {_fmt_eur(fin.get('detected_waste_monthly'))}",
        f"- Potential monthly savings: {_fmt_eur(fin.get('potential_savings_monthly'))}",
        f"- Annualized potential savings: {_fmt_eur(fin.get('potential_savings_yearly'))}",
        f"- Realized savings: {_fmt_eur(fin.get('realized_savings_monthly', 0))}",
        f"- Confirmed savings (verified via Cost Explorer): {_fmt_eur(fin.get('confirmed_savings_monthly', 0))}",
        f"- Number of recommendations: {len(recs)}",
        f"- Number of high-confidence recommendations (≥ 0.80): {high_confidence}",
        f"- Number of critical-risk recommendations: {critical}",
        "",
        "> Potential and annualized figures are theoretical maxima assuming full remediation. "
        "Only Confirmed savings are Cost Explorer-verified; Realized and Confirmed savings "
        "require completed remediation actions.",
    ]
    return "\n".join(lines)


def _section_2(dataset: Dict[str, Any]) -> str:
    scope = dataset.get("scope", {})
    analyzed = scope.get("analyzed_services", [])
    exclusions = scope.get("exclusions", [])
    lines = ["## 2. Audit Scope", "", "### Analyzed services", ""]
    lines += [f"- {s}" for s in analyzed] or ["- Not provided"]
    lines += ["", "### Exclusions", ""]
    lines += [f"- {s}" for s in exclusions] or ["- Not provided"]
    return "\n".join(lines)


def _section_3(dataset: Dict[str, Any]) -> str:
    fin = dataset
    rows = [
        ("Monthly spend", _fmt_eur(fin.get("cloud_spend_monthly"))),
        ("Forecast", _fmt_eur(fin.get("forecast_end_of_month"))),
        ("Budget", _fmt_eur(fin.get("budget_monthly"))),
        ("Budget usage", _fmt_pct(fin.get("budget_used_percentage"))),
        ("Detected waste", _fmt_eur(fin.get("detected_waste_monthly"))),
        ("Potential savings", _fmt_eur(fin.get("potential_savings_monthly"))),
        ("Annualized potential savings", _fmt_eur(fin.get("potential_savings_yearly"))),
        ("Realized savings", _fmt_eur(fin.get("realized_savings_monthly", 0))),
        (
            "Confirmed savings (verified via Cost Explorer)",
            _fmt_eur(fin.get("confirmed_savings_monthly", 0)),
        ),
    ]
    lines = ["## 3. Financial Overview", "", "| Metric | Value |", "|---|---:|"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines)


def _section_4(dataset: Dict[str, Any]) -> str:
    services = dataset.get("services", [])
    spend = dataset.get("cloud_spend_monthly") or 0
    lines = ["## 4. Cost Breakdown by Service", "", "| Service | Cost | Share |", "|---|---:|---:|"]
    for s in services:
        share = (s["monthly_cost"] / spend * 100) if spend else 0
        lines.append(f"| {s['name']} | {_fmt_eur(s['monthly_cost'])} | {share:.1f}% |")
    accounted = sum(s["monthly_cost"] for s in services)
    if spend and abs(spend - accounted) > max(spend * 0.005, 0.01):
        lines.append(
            f"| Other (unaccounted) | {_fmt_eur(spend - accounted)} | {(spend - accounted) / spend * 100:.1f}% |"
        )
    return "\n".join(lines)


def _section_5(dataset: Dict[str, Any]) -> str:
    recs = sorted(
        dataset.get("recommendations", []), key=lambda r: r.get("potential_saving", 0), reverse=True
    )
    lines = [
        "## 5. Top Recommendations",
        "",
        "| Priority | Resource | Service | Env | Owner | Saving | Risk | Confidence | Action |",
        "|---:|---|---|---|---|---:|---|---|---|",
    ]
    for i, r in enumerate(recs, start=1):
        confidence = r.get("confidence")
        confidence_str = (
            f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "Not provided"
        )
        lines.append(
            f"| {i} | {r.get('resource_id', 'Not provided')} | {r.get('service', 'Not provided')} | "
            f"{r.get('environment', 'Not provided')} | {r.get('owner') or 'Not provided'} | "
            f"{_fmt_eur(r.get('potential_saving'))} | {r.get('risk', 'Not provided')} | "
            f"{confidence_str} | {r.get('action', 'Not provided')} |"
        )
    return "\n".join(lines)


def _section_6(dataset: Dict[str, Any]) -> str:
    recs = dataset.get("recommendations", [])
    blocks = ["## 6. Detailed Findings"]
    for r in recs:
        evidence = r.get("evidence") or {}
        evidence_lines = (
            "\n".join(f"  - {k}: {v}" for k, v in evidence.items()) or "  - Not provided"
        )
        blocks.append(f"""
### Recommendation: {r.get('id', 'Not provided')}

- Resource: {r.get('resource_id', 'Not provided')}
- Resource type: {r.get('resource_type', 'Not provided')}
- Service: {r.get('service', 'Not provided')}
- Environment: {r.get('environment', 'Not provided')}
- Owner: {r.get('owner') or 'Not provided'}
- Status: {r.get('status', 'pending')}
- Finding: {r.get('finding', 'Not provided')}
- Evidence:
{evidence_lines}
- Estimated monthly cost: {_fmt_eur(r.get('monthly_cost'))}
- Potential monthly saving: {_fmt_eur(r.get('potential_saving'))}
- Recommended action: {r.get('action', 'Not provided')}
- Risk: {r.get('risk', 'Not provided')}
- Confidence: {r.get('confidence', 'Not provided')}
- Approval required: {r.get('approval_required', 'Not provided')}
- Rollback plan: {r.get('rollback_plan', 'Not provided')}""".strip("\n"))
    return "\n\n".join(blocks)


def _section_7(dataset: Dict[str, Any]) -> str:
    recs = dataset.get("recommendations", [])
    counts = {level: 0 for level in RISK_LEVELS}
    for r in recs:
        risk = r.get("risk")
        if risk in counts:
            counts[risk] += 1
    lines = [
        "## 7. Risk Summary",
        "",
        "| Risk level | Count | Comment |",
        "|---|---:|---|",
        f"| Low | {counts['low']} | |",
        f"| Medium | {counts['medium']} | |",
        f"| High | {counts['high']} | |",
        f"| Critical | {counts['critical']} | |",
        "",
        "No production destructive action should be executed without explicit approval.",
    ]
    red_flags = [r for r in recs if r.get("risk") in ("high", "critical")]
    if red_flags:
        lines += ["", "### Operational Red Flags", ""]
        for r in red_flags:
            lines.append(
                f"- {r.get('resource_id', 'Not provided')} ({r.get('action', 'Not provided')}, "
                f"{r.get('environment', 'Not provided')}): risk={r.get('risk')}, "
                f"approval required={r.get('approval_required', 'Not provided')}"
            )
    return "\n".join(lines)


def _section_8(dataset: Dict[str, Any]) -> str:
    tagging = dataset.get("tagging", {})
    rows = [
        ("Resources analyzed", tagging.get("resources_analyzed", "Not provided")),
        ("Owner tag coverage", _fmt_pct(tagging.get("owner_tag_coverage_pct"))),
        ("Environment tag coverage", _fmt_pct(tagging.get("environment_tag_coverage_pct"))),
        ("Untagged spend", _fmt_eur(tagging.get("untagged_spend_eur"))),
        ("Resources without owner", tagging.get("resources_without_owner", "Not provided")),
    ]
    lines = ["## 8. Tagging & Ownership", "", "| Metric | Value |", "|---|---:|"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    lines += ["", "Missing ownership reduces recommendation confidence and limits accountability."]
    return "\n".join(lines)


def _section_9(dataset: Dict[str, Any]) -> str:
    return """## 9. Methodology

### Waste percentage

```
detected_waste / cloud_spend × 100
```

### Annualized potential savings

```
potential_monthly_savings × 12
```

### Budget usage

```
current_month_to_date_spend / monthly_budget × 100
```

### Risk scoring

Risk is based on environment, action type, owner availability, rollback availability, \
production criticality, resource type, and confidence score.

### Confidence scoring

Confidence reflects the completeness of the detection metadata (currency, period, pricing \
source, owner, environment, observation window) and the strength of the underlying usage \
signal. A missing currency or period caps confidence at low regardless of signal strength."""


def _section_10(dataset: Dict[str, Any]) -> str:
    a = dataset.get("assumptions", {})
    fields = [
        ("Pricing source", a.get("pricing_source")),
        ("Currency", dataset.get("currency")),
        ("Period", a.get("period")),
        ("Forecast method", a.get("forecast_method")),
        ("Services not analyzed", ", ".join(a.get("services_not_analyzed", [])) or None),
        ("Discounts not included", ", ".join(a.get("discounts_not_included", [])) or None),
        ("Data freshness", a.get("data_freshness")),
    ]
    lines = ["## 10. Assumptions & Limitations", ""]
    lines += [f"- {label}: {value if value else 'Not provided'}" for label, value in fields]
    return "\n".join(lines)


def _check_status(rule: str, violations: List[Violation], dataset: Dict[str, Any]) -> str:
    requirements = CHECK_REQUIREMENTS.get(rule, ())
    if not all(dataset.get(field) not in (None, [], {}) for field in requirements):
        return "Not enough data"
    matches = [v for v in violations if v.rule == rule]
    if not matches:
        return "OK"
    return "Error" if any(v.severity in ("critical", "high") for v in matches) else "Warning"


def _section_11(dataset: Dict[str, Any]) -> str:
    violations = audit_dataset(dataset)
    lines = [
        "## 11. Data Quality & Consistency Checks",
        "",
        "| Check | Status | Comment |",
        "|---|---|---|",
    ]
    for rule, label in CHECK_LABELS.items():
        status = _check_status(rule, violations, dataset)
        comment = next((v.message for v in violations if v.rule == rule), "")
        lines.append(f"| {label} | {status} | {comment} |")
    if dataset.get("currency"):
        lines.append("| Currency present | OK | |")
    else:
        lines.append("| Currency present | Error | Dataset has no currency |")
    return "\n".join(lines)


def _section_12(dataset: Dict[str, Any]) -> str:
    summary = generate_cto_safe_summary(dataset)
    return f"""## 12. CTO-safe Summary

{summary}

These savings are estimates based on detected waste and current usage patterns. They require \
technical validation, approval, and execution before being classified as realized savings.

No production destructive action is recommended without explicit approval."""


def generate_audit_report(dataset: Dict[str, Any]) -> str:
    """Assemble the 12-section AWS FinOps audit report as Markdown.

    Pure function of `dataset`: same input always produces the same output,
    which is what makes it safe to golden-snapshot. No AWS/DB/LLM calls.
    """
    sections = [
        "# AWS FinOps Audit Report — Wasteless",
        _section_1(dataset),
        _section_2(dataset),
        _section_3(dataset),
        _section_4(dataset),
        _section_5(dataset),
        _section_6(dataset),
        _section_7(dataset),
        _section_8(dataset),
        _section_9(dataset),
        _section_10(dataset),
        _section_11(dataset),
        _section_12(dataset),
    ]
    return "\n\n".join(sections) + "\n"
