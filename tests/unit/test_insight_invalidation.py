"""
Guard test: every detector's recommendation upsert must carry the ai_insight
invalidation clause.

The AI insight (the "Why act?" text in the UI) quotes figures from the moment
it was generated. Since 776e11e the savings of pending recommendations are
resynced at every re-detection, so without invalidation the stored prose
drifts away from the numbers shown on the row. The fix is a CASE clause in
each detector's ON CONFLICT (waste_id) DO UPDATE that NULLs ai_insight when
the resynced savings moves beyond max(10% of old, 0.50 EUR) —
enrich_recommendations() then rewrites it with fresh figures on the next run.

The clause is duplicated in the 6 upserts (same convention as the resync
line itself; bandit S608 rules out assembling SQL from fragments), so this
test is what keeps the copies from drifting or a new detector from shipping
without it. Semantics are covered against real Postgres in
tests/integration/test_insight_invalidation.py.
"""

import os

import pytest

DETECTORS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src", "detectors")

# Every file with an ON CONFLICT (waste_id) upsert. steampipe_base covers the
# four Steampipe detectors (elb_unused, nat_gateway_unused, vpc_unused,
# ebs_gp2_migration), which share its upsert.
UPSERT_FILES = [
    "ec2_idle.py",
    "ec2_stopped.py",
    "ebs_orphan.py",
    "eip_orphan.py",
    "snapshot_orphan.py",
    "steampipe_base.py",
]

# The canonical drift condition, whitespace-normalized.
DRIFT_CONDITION = (
    "abs(recommendations.estimated_monthly_savings_eur "
    "- EXCLUDED.estimated_monthly_savings_eur) "
    "> GREATEST(recommendations.estimated_monthly_savings_eur * 0.10, 0.50)"
)


def _normalized_source(filename: str) -> str:
    path = os.path.join(DETECTORS_DIR, filename)
    with open(path) as f:
        return " ".join(f.read().split())


@pytest.mark.parametrize("filename", UPSERT_FILES)
def test_upsert_invalidates_stale_insight(filename):
    src = _normalized_source(filename)
    assert "ai_insight = CASE" in src, (
        f"{filename}: the recommendation upsert resyncs savings but never "
        "invalidates ai_insight — the 'Why act?' text will drift away from "
        "the figures shown on the row"
    )
    assert DRIFT_CONDITION in src, (
        f"{filename}: ai_insight invalidation exists but its drift condition "
        "differs from the canonical one (max(10% of old, 0.50 EUR)) — keep "
        "the 6 copies identical, and update tests/integration/"
        "test_insight_invalidation.py if the threshold is changing on purpose"
    )


def test_ec2_idle_also_invalidates_on_action_change_and_revival():
    """ec2_idle's upsert additionally rewrites recommendation_type and revives
    obsolete recos — both make the old insight wrong regardless of savings."""
    src = _normalized_source("ec2_idle.py")
    assert (
        "recommendations.recommendation_type IS DISTINCT FROM EXCLUDED.recommendation_type" in src
    )
    assert "WHEN recommendations.status = 'obsolete'" in src
