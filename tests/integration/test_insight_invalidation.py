"""
Semantics of the ai_insight invalidation clause, against real Postgres.

The detectors' recommendation upserts (see the guard test in
tests/unit/test_insight_invalidation.py, which pins all 6 copies to the
canonical clause) NULL ai_insight when the resynced savings drifts beyond
max(10% of the old value, 0.50 EUR), so a stale "Why act?" text is
regenerated with fresh figures instead of contradicting the row.

Everything runs inside one never-committed transaction and is rolled back —
the real database is not touched. Skips cleanly when Postgres is down.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

psycopg2 = pytest.importorskip("psycopg2")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

# Literal copy of the simple upsert shared by ec2_stopped / ebs_orphan /
# eip_orphan / snapshot_orphan / steampipe_base. The unit guard test keeps
# the in-tree copies identical to the canonical clause exercised here.
SIMPLE_UPSERT = """
    INSERT INTO recommendations (
        waste_id, recommendation_type, action_required,
        estimated_monthly_savings_eur, status
    ) VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (waste_id) DO UPDATE SET
        estimated_monthly_savings_eur = EXCLUDED.estimated_monthly_savings_eur,
        ai_insight = CASE
            WHEN abs(recommendations.estimated_monthly_savings_eur
                     - EXCLUDED.estimated_monthly_savings_eur)
                 > GREATEST(recommendations.estimated_monthly_savings_eur * 0.10, 0.50)
            THEN NULL
            ELSE recommendations.ai_insight
        END
    WHERE recommendations.status = 'pending';
"""

# Literal copy of ec2_idle's richer upsert (action can flip stop/downsize,
# obsolete recos revive to pending).
EC2_IDLE_UPSERT = """
    INSERT INTO recommendations (
        waste_id, recommendation_type, action_required,
        estimated_monthly_savings_eur, status
    ) VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (waste_id) DO UPDATE SET
        estimated_monthly_savings_eur = EXCLUDED.estimated_monthly_savings_eur,
        recommendation_type = EXCLUDED.recommendation_type,
        action_required = EXCLUDED.action_required,
        status = 'pending',
        applied_at = NULL,
        ai_insight = CASE
            WHEN recommendations.status = 'obsolete'
              OR recommendations.recommendation_type
                 IS DISTINCT FROM EXCLUDED.recommendation_type
              OR abs(recommendations.estimated_monthly_savings_eur
                     - EXCLUDED.estimated_monthly_savings_eur)
                 > GREATEST(recommendations.estimated_monthly_savings_eur * 0.10, 0.50)
            THEN NULL
            ELSE recommendations.ai_insight
        END
    WHERE recommendations.status IN ('pending', 'obsolete');
"""

INSIGHT = "This instance costs 3.56 EUR per month to run while idle."


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "wasteless"),
        user=os.getenv("DB_USER", "wasteless"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=5,
    )


@pytest.fixture
def cur():
    try:
        conn = _connect()
    except Exception as e:
        pytest.skip(f"Postgres indisponible ({e}) — lancer docker-compose up -d postgres")
    cursor = conn.cursor()
    yield cursor
    conn.rollback()
    conn.close()


def _seed_reco(cur, savings, status="pending", rec_type="terminate_instance", insight=INSIGHT):
    """One waste row + its recommendation carrying an AI insight."""
    cur.execute(
        """
        INSERT INTO waste_detected (detection_date, resource_id, resource_type,
                                    monthly_waste_eur, confidence_score)
        VALUES (%s, %s, %s, %s, 0.9) RETURNING id;
    """,
        (date.today(), f"i-insight-test-{os.urandom(4).hex()}", "ec2_instance", savings),
    )
    waste_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO recommendations (waste_id, recommendation_type, action_required,
                                     estimated_monthly_savings_eur, status, ai_insight)
        VALUES (%s, %s, 'act', %s, %s, %s) RETURNING id;
    """,
        (waste_id, rec_type, savings, status, insight),
    )
    return waste_id, cur.fetchone()[0]


def _redetect(cur, waste_id, savings, upsert=SIMPLE_UPSERT, rec_type="terminate_instance"):
    cur.execute(upsert, (waste_id, rec_type, "act", savings, "pending"))


def _reco(cur, rec_id):
    cur.execute(
        "SELECT estimated_monthly_savings_eur, ai_insight, status, recommendation_type"
        " FROM recommendations WHERE id = %s;",
        (rec_id,),
    )
    return cur.fetchone()


class TestSavingsDrift:
    def test_small_drift_keeps_insight(self, cur):
        """3.56 -> 3.54: the row still tells the same story, no LLM re-spend."""
        waste_id, rec_id = _seed_reco(cur, 3.56)
        _redetect(cur, waste_id, 3.54)
        savings, insight, _, _ = _reco(cur, rec_id)
        assert float(savings) == 3.54  # resync still happens
        assert insight == INSIGHT

    def test_material_drift_drops_insight(self, cur):
        """3.56 -> 7.00: the stored prose now contradicts the row."""
        waste_id, rec_id = _seed_reco(cur, 3.56)
        _redetect(cur, waste_id, 7.00)
        savings, insight, _, _ = _reco(cur, rec_id)
        assert float(savings) == 7.00
        assert insight is None

    def test_relative_threshold_dominates_on_large_amounts(self, cur):
        """100 -> 105 is 5 EUR but only 5%: not material for a big line."""
        waste_id, rec_id = _seed_reco(cur, 100)
        _redetect(cur, waste_id, 105)
        _, insight, _, _ = _reco(cur, rec_id)
        assert insight == INSIGHT
        _redetect(cur, waste_id, 117)  # > 10% of the resynced 105
        _, insight, _, _ = _reco(cur, rec_id)
        assert insight is None

    def test_absolute_floor_dominates_on_small_amounts(self, cur):
        """0.20 -> 0.55 is +175% but 0.35 EUR: below the 0.50 floor."""
        waste_id, rec_id = _seed_reco(cur, 0.20)
        _redetect(cur, waste_id, 0.55)
        _, insight, _, _ = _reco(cur, rec_id)
        assert insight == INSIGHT

    def test_resolved_reco_left_untouched(self, cur):
        """Human-resolved rows keep both their savings and their insight."""
        waste_id, rec_id = _seed_reco(cur, 3.56, status="applied")
        _redetect(cur, waste_id, 7.00)
        savings, insight, status, _ = _reco(cur, rec_id)
        assert float(savings) == 3.56
        assert insight == INSIGHT
        assert status == "applied"


class TestEc2IdleExtraConditions:
    def test_action_change_drops_insight_even_with_stable_savings(self, cur):
        waste_id, rec_id = _seed_reco(cur, 3.56, rec_type="stop_instance")
        _redetect(cur, waste_id, 3.56, upsert=EC2_IDLE_UPSERT, rec_type="downsize_instance")
        _, insight, _, rec_type = _reco(cur, rec_id)
        assert rec_type == "downsize_instance"
        assert insight is None

    def test_revival_from_obsolete_drops_insight(self, cur):
        waste_id, rec_id = _seed_reco(cur, 3.56, status="obsolete", rec_type="stop_instance")
        _redetect(cur, waste_id, 3.56, upsert=EC2_IDLE_UPSERT, rec_type="stop_instance")
        _, insight, status, _ = _reco(cur, rec_id)
        assert status == "pending"
        assert insight is None

    def test_stable_pending_keeps_insight(self, cur):
        waste_id, rec_id = _seed_reco(cur, 3.56, rec_type="stop_instance")
        _redetect(cur, waste_id, 3.60, upsert=EC2_IDLE_UPSERT, rec_type="stop_instance")
        _, insight, _, _ = _reco(cur, rec_id)
        assert insight == INSIGHT
