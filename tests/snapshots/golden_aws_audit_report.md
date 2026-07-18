# AWS FinOps Audit Report — Wasteless

## 1. Executive Summary

- Audit date: 2026-07-01
- AWS account: 123456789012
- Period analyzed: 2026-06-01 → 2026-06-30
- Region scope: eu-west-1
- Currency: USD
- Monthly cloud spend: $12000.00
- Forecast end of month: $12400.00
- Monthly budget: $15000.00
- Budget usage: 80.0%
- Detected waste: $900.00
- Potential monthly savings: $750.00
- Annualized potential savings: $9000.00
- Realized savings: $0.00
- Confirmed savings (verified via Cost Explorer): $0.00
- Number of recommendations: 3
- Number of high-confidence recommendations (≥ 0.80): 2
- Number of critical-risk recommendations: 1

> Potential and annualized figures are theoretical maxima assuming full remediation. Only Confirmed savings are Cost Explorer-verified; Realized and Confirmed savings require completed remediation actions.

## 2. Audit Scope

### Analyzed services

- EC2
- EBS
- NAT Gateway
- Elastic IP
- CloudWatch
- Tags
- Cost Explorer

### Exclusions

- Reserved Instances
- Savings Plans
- Support costs
- Marketplace costs
- Taxes
- Enterprise Discount Program
- RDS (no detector implemented yet)
- EKS (no detector implemented yet)

## 3. Financial Overview

| Metric | Value |
|---|---:|
| Monthly spend | $12000.00 |
| Forecast | $12400.00 |
| Budget | $15000.00 |
| Budget usage | 80.0% |
| Detected waste | $900.00 |
| Potential savings | $750.00 |
| Annualized potential savings | $9000.00 |
| Realized savings | $0.00 |
| Confirmed savings (verified via Cost Explorer) | $0.00 |

## 4. Cost Breakdown by Service

| Service | Cost | Share |
|---|---:|---:|
| EC2 | $7000.00 | 58.3% |
| NAT Gateway | $2000.00 | 16.7% |
| EBS | $1500.00 | 12.5% |
| CloudWatch | $1000.00 | 8.3% |
| Elastic IP | $500.00 | 4.2% |

## 5. Top Recommendations

| Priority | Resource | Service | Env | Owner | Saving | Risk | Confidence | Action |
|---:|---|---|---|---|---:|---|---|---|
| 1 | i-0a1b2c3d4e5f6a7b8 | EC2 | production | platform-team | $320.00 | high | 0.85 | stop_instance |
| 2 | nat-0123456789abcdef0 | NAT Gateway | production | Not provided | $300.00 | critical | 0.75 | delete_nat_gateway |
| 3 | vol-0aabbccddeeff0011 | EBS | dev | data-team | $25.00 | medium | 0.90 | delete_volume |

## 6. Detailed Findings

### Recommendation: rec-001

- Resource: i-0a1b2c3d4e5f6a7b8
- Resource type: ec2_instance
- Service: EC2
- Environment: production
- Owner: platform-team
- Status: pending
- Finding: Average CPU 2.3% over a 7-day observation window, below the 5% idle threshold.
- Evidence:
  - cpu_avg_7d: 2.3
  - cpu_max_7d: 6.1
  - observation_days: 7
- Estimated monthly cost: $400.00
- Potential monthly saving: $320.00
- Recommended action: stop_instance
- Risk: high
- Confidence: 0.85
- Approval required: True
- Rollback plan: Restart via EC2 console or API; stop is reversible, instance state and EBS volumes are preserved.

### Recommendation: rec-002

- Resource: nat-0123456789abcdef0
- Resource type: nat_gateway
- Service: NAT Gateway
- Environment: production
- Owner: Not provided
- Status: pending
- Finding: Zero bytes processed over the last 30 days; no active route depends on this gateway.
- Evidence:
  - data_processed_gb_30d: 0
  - route_tables_referencing: 0
- Estimated monthly cost: $300.00
- Potential monthly saving: $300.00
- Recommended action: delete_nat_gateway
- Risk: critical
- Confidence: 0.75
- Approval required: True
- Rollback plan: Irreversible once deleted. Requires route table validation before execution; no owner tag found — assign an owner before approval.

### Recommendation: rec-003

- Resource: vol-0aabbccddeeff0011
- Resource type: ebs_volume
- Service: EBS
- Environment: dev
- Owner: data-team
- Status: pending
- Finding: Unattached gp3 volume, no attachment for 21 days, snapshot present.
- Evidence:
  - unattached_days: 21
  - has_snapshot: True
- Estimated monthly cost: $25.00
- Potential monthly saving: $25.00
- Recommended action: delete_volume
- Risk: medium
- Confidence: 0.9
- Approval required: True
- Rollback plan: Restore from snapshot (retained per config/remediation.yaml rollback policy, 7-day retention).

## 7. Risk Summary

| Risk level | Count | Comment |
|---|---:|---|
| Low | 0 | |
| Medium | 1 | |
| High | 1 | |
| Critical | 1 | |

No production destructive action should be executed without explicit approval.

### Operational Red Flags

- i-0a1b2c3d4e5f6a7b8 (stop_instance, production): risk=high, approval required=True
- nat-0123456789abcdef0 (delete_nat_gateway, production): risk=critical, approval required=True

## 8. Tagging & Ownership

| Metric | Value |
|---|---:|
| Resources analyzed | 42 |
| Owner tag coverage | 88.1% |
| Environment tag coverage | 95.2% |
| Untagged spend | $240.00 |
| Resources without owner | 5 |

Missing ownership reduces recommendation confidence and limits accountability.

## 9. Methodology

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

Risk is based on environment, action type, owner availability, rollback availability, production criticality, resource type, and confidence score.

### Confidence scoring

Confidence reflects the completeness of the detection metadata (currency, period, pricing source, owner, environment, observation window) and the strength of the underlying usage signal. A missing currency or period caps confidence at low regardless of signal strength.

## 10. Assumptions & Limitations

- Pricing source: AWS Price List API
- Currency: USD
- Period: 2026-06-01 to 2026-06-30
- Forecast method: Linear burn rate (month-to-date spend / days elapsed x days in month)
- Services not analyzed: RDS, EKS
- Discounts not included: Enterprise Discount Program, Reserved Instances, Savings Plans
- Data freshness: Collectors run daily; last run 2026-07-01

## 11. Data Quality & Consistency Checks

| Check | Status | Comment |
|---|---|---|
| Waste percentage calculation | OK |  |
| Potential vs. detected savings | OK |  |
| Monthly to annual savings consistency | OK |  |
| Reduction percentage recomputable | OK |  |
| Forecast sanity | OK |  |
| Budget usage calculation | OK |  |
| Service cost sum | OK |  |
| Duplicate resources | OK |  |
| Saving does not exceed resource cost | OK |  |
| Production destructive action safety | OK |  |
| Currency present | OK | |

## 12. CTO-safe Summary

Detected waste: 900/mo (7.5% of 12000 spend). Potential savings: up to 750/mo. Annualized potential: up to 9000/yr. Realized savings: 0 (requires completed actions). Confirmed savings: 0 (verified via Cost Explorer).

These savings are estimates based on detected waste and current usage patterns. They require technical validation, approval, and execution before being classified as realized savings.

No production destructive action is recommended without explicit approval.
