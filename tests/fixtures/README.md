# tests/fixtures/

Static JSON datasets shared across `tests/unit/` and `tests/integration/` —
kept as data files rather than inlined in test code so the same dataset can
be reused by multiple tests and diffed independently in review.

| File | Shape | Used by |
|---|---|---|
| `golden_aws_audit_dataset.json` | Object, ~18 keys | Input dataset for the audit report golden-snapshot tests (`tests/integration/test_llm_report_generation.py`); pairs with `tests/snapshots/golden_aws_audit_report.md` as the expected output. |
| `valid_finops_dataset.json` | Object, ~11 keys | Well-formed FinOps numbers — should pass every check in `src/core/finops_invariants.py`. |
| `invalid_finops_dataset.json` | Object, ~11 keys | Deliberately broken FinOps numbers (bad arithmetic, inconsistent claims) — should trip specific `finops_invariants.py` validators; used to prove the guard-rails actually catch bad data, not just pass good data. |
| `pricing_edge_cases.json` | List, 3 entries | Edge-case pricing scenarios for `tests/unit/test_pricing_sanity.py`. |
| `dangerous_recommendations.json` | List, 3 entries | Examples of recommendations that should be flagged as high-risk by `tests/unit/test_recommendation_risk.py` (e.g. targeting production-tagged or recently-created resources). |

When adding a new invariant to `finops_invariants.py`, prefer extending
`valid_finops_dataset.json` / `invalid_finops_dataset.json` with a case that
exercises it over hand-rolling a one-off dict in the test file — it keeps
the "what does good/bad data look like" reference in one place.
