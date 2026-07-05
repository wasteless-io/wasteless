# AWS FinOps Audit Report — Wasteless

## 1. Executive Summary

- Report ID / audit version:
- Audit date:
- AWS account(s):
- Period analyzed:
- Currency:
- Monthly cloud spend:
- Forecast end of month:
- Detected waste:
- Potential monthly savings:
- Annualized potential savings:
- Confirmed savings (Cost Explorer-verified):
- Number of recommendations:
- Recommendations above auto-remediation confidence threshold (≥ 0.80):
- Recommendations blocked by safeguards:
- Critical risks:

> **Note on savings figures:** this report distinguishes five categories — *Detected waste* (identified by the detectors, a lower bound dependent on detector coverage), *Potential savings* (monthly savings if 100% of detected waste is remediated), *Annualized potential* (Potential × 12, a theoretical ceiling assuming constant waste and immediate full remediation), *Realized savings* (savings obtained after remediation, from `savings_realized`), and *Confirmed savings* (realized savings verified against Cost Explorer/invoice — the only figures citable without reservation). Only Confirmed savings should be treated as a firm result.

## 2. Audit Scope

### Analyzed services

- EC2
- EBS
- NAT Gateway
- Elastic IP
- CloudWatch
- Tags
- Cost Explorer

### Excluded from remediation eligibility (safeguards)

- Whitelisted resources: N
- Resources under 30 days old: N
- Resources below 0.80 confidence score: N
- Resources below 14-day idle duration: N
- Outside allowed remediation schedule: N

### Exclusions from scope

- Reserved Instances
- Savings Plans
- Support costs
- Marketplace costs
- Taxes
- Enterprise discounts
- RDS (no detector implemented yet — not analyzed, not to be listed as in-scope)
- EKS (no detector implemented yet — not analyzed, not to be listed as in-scope)

## 3. Financial Overview

| Metric | Value |
|---|---:|
| Monthly spend | € |
| Forecast | € |
| Budget | € |
| Budget usage | % |
| Detected waste | € |
| Potential savings | € |
| Annualized potential savings | € |
| Realized savings | € |
| Confirmed savings (Cost Explorer-verified) | € |

## 4. Cost Breakdown by Service

| Service | Cost | Share |
|---|---:|---:|
| EC2 | € | % |
| EBS | € | % |
| RDS | € | % |
| EKS | € | % |
| NAT Gateway | € | % |
| Elastic IP | € | % |
| CloudWatch | € | % |

*Services listed here must match the "Analyzed services" list in section 2 — do not include services outside audit scope (e.g. S3) unless the scope is updated accordingly.*

## 5. Top Recommendations

| Priority | Resource | Service | Env | Owner | Saving | Confidence | Risk | Status | Action |
|---:|---|---|---|---|---:|---:|---|---|---|

## 6. Detailed Findings

### Recommendation: rec-001

- Resource:
- Resource type:
- Waste type:
- Service:
- Environment:
- Owner:
- Status (pending / approved / applied / rejected):
- Finding:
- Evidence:
- Estimated cost:
- Potential saving:
- Recommended action:
- Risk:
- Confidence score:
- Approval required:
- Rollback plan:

## 7. Risk Summary

| Risk level | Count | Comment |
|---|---:|---|
| Low | | |
| Medium | | |
| High | | |
| Critical | | |

### Safeguard gating

| Safeguard check | Recommendations blocked |
|---|---:|
| Auto-remediation disabled | |
| Instance whitelisted | |
| Instance age < 30 days | |
| Confidence score < 0.80 | |
| Idle duration < 14 days | |
| Outside allowed schedule | |
| Run rate limit reached | |

## 8. Tagging & Ownership

| Metric | Value |
|---|---:|
| Resources analyzed | |
| Owner tag coverage | % |
| Environment tag coverage | % |
| Untagged spend | € |
| Resources without owner | |

## 9. Methodology

### Detection logic

- EC2 idle detection: average CPU < 5% over a rolling 7-day window of `ec2_metrics`.
- Confidence score: 0–1, computed per detector; auto-remediation requires ≥ 0.80 (see safeguards).

### Waste percentage

`detected_waste / cloud_spend × 100`

### Annualized potential savings

`potential_monthly_savings × 12` — theoretical ceiling; assumes constant waste and full, immediate remediation. Not a forecast.

### Risk scoring

Risk is based on:

- environment
- action type
- owner
- rollback availability
- production criticality
- confidence score

Risk scoring feeds the safeguards system (`src/core/safeguards.py`), which runs 7 sequential checks before any AWS action is taken. Any action failing a check is aborted and logged, not silently skipped — see section 7 (Safeguard gating) for counts.

## 10. Assumptions & Limitations

- Pricing source:
- Currency:
- Period:
- Forecast method:
- Services not analyzed:
- Discounts not included:
- Data freshness (last collector run):
- Detector coverage (only services listed in section 2 contribute to "Detected waste" — waste in unanalyzed services is not reflected):

## 11. CTO-safe Summary

Wasteless identified €X/month in **detected waste**, corresponding to €Y/month in **potential savings** (€Z/year annualized) across the analyzed AWS account(s).

Of these, €W has been **confirmed** via Cost Explorer verification following remediation; the remaining figures are estimates pending validation.

These savings are estimates based on detected waste and require validation before execution. No production destructive action is recommended without explicit approval.
