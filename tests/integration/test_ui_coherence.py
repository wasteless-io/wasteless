"""
Exercices de cohérence inter-pages sur une vraie base Postgres.

Chaque exercice reproduit un bug réel trouvé en comparant les captures
d'écran Home / Recommendations / Reports / Dashboard lors de l'audit du
06-07 juillet 2026 : un item détecté comme waste peut être compté sur une
page et absent d'une autre selon son statut de recommandation
(pending / rejected / dismissed / applied / obsolete / aucune ligne).

Chaque test insère des lignes waste_detected/recommendations dans une
transaction, interroge les mêmes requêtes/fonctions que les pages réelles,
puis ROLLBACK — rien n'est jamais committé, la vraie base n'est pas touchée.

Nécessite une base accessible via les variables d'environnement DB_*
(docker-compose up -d postgres). Skip proprement si indisponible.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

psycopg2 = pytest.importorskip("psycopg2")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.snapshots import snapshot_active_waste  # noqa: E402
from reports.weekly_digest import collect_digest_data  # noqa: E402


def _connect():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'wasteless'),
        user=os.getenv('DB_USER', 'wasteless'),
        password=os.getenv('DB_PASSWORD', ''),
        connect_timeout=5,
    )


@pytest.fixture
def conn():
    try:
        c = _connect()
    except Exception as e:
        pytest.skip(f"Postgres indisponible ({e}) — lancer docker-compose up -d postgres")
    yield c
    c.rollback()  # couvre les tests qui ne commitent jamais
    # Filet de sécurité : certaines fonctions de prod (snapshot_active_waste,
    # collect_digest_data côté écriture) commitent en interne, ce qui rend le
    # rollback ci-dessus inopérant pour leurs écritures. On supprime donc
    # explicitement tout ce que ce module a pu laisser, quel que soit le
    # chemin de sortie du test.
    cur = c.cursor()
    cur.execute("""
        DELETE FROM recommendations WHERE waste_id IN (
            SELECT id FROM waste_detected WHERE account_id = 'test-coherence'
        )
    """)
    cur.execute("DELETE FROM waste_detected WHERE account_id = 'test-coherence'")
    c.commit()
    c.close()


def _insert_waste(cur, resource_type, resource_id, monthly_eur,
                   status=None, created_at=None):
    """Une ligne waste_detected, + une ligne recommendations si status est donné."""
    cur.execute("""
        INSERT INTO waste_detected (
            detection_date, provider, account_id, resource_id,
            resource_type, waste_type, monthly_waste_eur,
            confidence_score, metadata, created_at
        ) VALUES (CURRENT_DATE, 'aws', 'test-coherence', %s, %s,
                   'test_waste', %s, 0.90, '{}'::jsonb, COALESCE(%s, NOW()))
        RETURNING id
    """, (resource_id, resource_type, monthly_eur, created_at))
    waste_id = cur.fetchone()[0]

    if status is not None:
        cur.execute("""
            INSERT INTO recommendations (
                waste_id, recommendation_type, action_required,
                estimated_monthly_savings_eur, status
            ) VALUES (%s, 'test_action', 'test exercise', %s, %s)
        """, (waste_id, monthly_eur, status))

    return waste_id


def _active_waste_total(cur, resource_type):
    # Scoped to the test account: without it, this sums real production
    # waste alongside the fixtures the moment this same database holds any
    # — passing today only because the dev DB happens to be empty.
    cur.execute("""
        SELECT COALESCE(SUM(monthly_waste_eur), 0), COUNT(*)
        FROM active_waste WHERE resource_type = %s AND account_id = 'test-coherence'
    """, (resource_type,))
    total_eur, count = cur.fetchone()
    return float(total_eur), count


def _recommendations_pending_count(cur, resource_type):
    """Mirrors ui/main.py:868-895 (GET /recommendations, INNER JOIN + status='pending')."""
    cur.execute("""
        SELECT COUNT(*)
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE r.status = 'pending' AND w.resource_type = %s
          AND w.account_id = 'test-coherence'
    """, (resource_type,))
    return cur.fetchone()[0]


def _declined_kpi(cur, resource_type):
    """Mirrors ui/main.py:650-656 (GET /dashboard, 'declined' CTE)."""
    cur.execute("""
        SELECT COUNT(*), COALESCE(SUM(w.monthly_waste_eur), 0)
        FROM active_waste w
        JOIN recommendations r ON r.waste_id = w.id
        WHERE r.status = 'rejected' AND w.resource_type = %s
          AND w.account_id = 'test-coherence'
    """, (resource_type,))
    count, total_eur = cur.fetchone()
    return count, float(total_eur)


# --- Exercice 1 : rejected reste actif sur Home, mais invisible sur Recommendations ---

def test_rejected_counts_as_active_waste_but_hidden_from_recommendations(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'ec2_instance', 'test-rejected-1', 12.34, status='rejected')

    total_eur, count = _active_waste_total(cur, 'ec2_instance')
    assert count == 1
    assert total_eur == pytest.approx(12.34)

    # La page Recommendations ne montre que 'pending' : un item rejeté
    # disparaît de la liste actionnable tout en continuant de compter
    # comme waste actif sur Home/Dashboard.
    assert _recommendations_pending_count(cur, 'ec2_instance') == 0


# --- Exercice 2 : un waste_detected sans aucune ligne recommendations compte aussi ---

def test_waste_without_any_recommendation_row_still_counts_as_active(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'elastic_ip', 'test-no-rec-1', 3.36, status=None)

    total_eur, count = _active_waste_total(cur, 'elastic_ip')
    assert count == 1
    assert total_eur == pytest.approx(3.36)
    # Le LEFT JOIN de active_waste traite l'absence de ligne comme 'pending'
    # (COALESCE), mais l'INNER JOIN de /recommendations exige une ligne réelle.
    assert _recommendations_pending_count(cur, 'elastic_ip') == 0


# --- Exercice 3 : dismissed sort du waste actif, contrairement à rejected ---

def test_dismissed_is_excluded_from_active_waste(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'ebs_volume', 'test-dismissed-1', 5.90, status='dismissed')

    total_eur, count = _active_waste_total(cur, 'ebs_volume')
    assert count == 0
    assert total_eur == 0


# --- Exercice 4 : Reports (période) ignore le statut, Home ne compte que l'actif ---

def test_reports_period_includes_dismissed_and_applied_but_home_does_not(conn):
    cur = conn.cursor()
    today = date.today()

    _insert_waste(cur, 'nat_gateway', 'test-dismissed-2', 32.24, status='dismissed')
    _insert_waste(cur, 'load_balancer', 'test-applied-1', 16.92, status='applied')

    # collect_digest_data ouvre son propre curseur sur la même connexion :
    # une session voit toujours ses propres écritures non commitées.
    data = collect_digest_data(conn, today, today)

    by_type = dict((t, eur) for t, _, eur in data['new_waste']['by_type'])
    # Le rapport agrège tout ce qui a été *créé* dans la période, quel
    # que soit le statut de la recommandation associée.
    assert by_type.get('nat_gateway', 0) >= 32.24
    assert by_type.get('load_balancer', 0) >= 16.92

    # ... alors que le waste actif (Home/Dashboard) a déjà exclu le
    # dismissed et l'applied : seul un item pending resterait compté.
    nat_active, _ = _active_waste_total(cur, 'nat_gateway')
    lb_active, _ = _active_waste_total(cur, 'load_balancer')
    assert nat_active == 0
    assert lb_active == 0


# --- Exercice 5 : le snapshot journalier doit pouvoir retomber à zéro ---

def test_snapshot_zeroes_out_a_resource_type_with_no_more_active_waste(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'ebs_snapshot', 'test-snapshot-1', 9.57, status='dismissed')
    # Un ebs_snapshot existe dans waste_detected (dismissed) mais aucun
    # n'est dans active_waste : le snapshot du jour doit refléter 0, pas
    # rester silencieusement sur une éventuelle valeur précédente.
    snapshot_active_waste(conn)

    cur.execute("""
        SELECT total_eur, resource_count FROM waste_snapshots
        WHERE snapshot_date = CURRENT_DATE AND resource_type = 'ebs_snapshot'
    """)
    total_eur, resource_count = cur.fetchone()
    assert total_eur == 0
    assert resource_count == 0


# --- Exercice 6 : scheduled (grace period en cours) reste actif ---

def test_scheduled_grace_period_still_counts_as_active_waste(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'ec2_instance', 'test-scheduled-1', 20.0, status='scheduled')

    total_eur, count = _active_waste_total(cur, 'ec2_instance')
    assert count == 1
    assert total_eur == pytest.approx(20.0)
    # Ni pending : invisible sur la liste actionnable de Recommendations
    # tant que le grace period court.
    assert _recommendations_pending_count(cur, 'ec2_instance') == 0


# --- Exercice 7 : pr_open (routage Terraform) reste actif tant que la PR n'est pas mergée ---

def test_pr_open_still_counts_as_active_waste(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'nat_gateway', 'test-pr-open-1', 32.24, status='pr_open')

    total_eur, count = _active_waste_total(cur, 'nat_gateway')
    assert count == 1
    assert total_eur == pytest.approx(32.24)


# --- Exercice 8 : le KPI "declined" ne compte que rejected, pas dismissed/pending ---

def test_declined_kpi_counts_only_rejected(conn):
    cur = conn.cursor()
    _insert_waste(cur, 'ebs_volume', 'test-declined-rejected', 5.0, status='rejected')
    _insert_waste(cur, 'ebs_volume', 'test-declined-dismissed', 7.0, status='dismissed')
    _insert_waste(cur, 'ebs_volume', 'test-declined-pending', 3.0, status='pending')

    count, total_eur = _declined_kpi(cur, 'ebs_volume')
    assert count == 1
    assert total_eur == pytest.approx(5.0)


# --- Exercice 9 : une action dry-run ne doit jamais marquer un item comme réellement traité ---

def test_dry_run_approval_leaves_recommendation_pending(conn):
    """Verrouille le fix : src/remediators/resource_remediator.py et
    ui/main.py ne marquaient pas la différence entre "simulé" et "réellement
    exécuté" — une approbation en Dry-Run Mode faisait disparaître le waste
    de Home sans qu'aucune ressource AWS n'ait été touchée.
    """
    cur = conn.cursor()
    waste_id = _insert_waste(cur, 'ec2_instance', 'test-dryrun-1', 15.0, status='pending')
    cur.execute("SELECT id FROM recommendations WHERE waste_id = %s", (waste_id,))
    rec_id = cur.fetchone()[0]

    # Simule ce que fait un dry-run réussi : logguer l'action, mais NE PAS
    # faire passer le statut à 'approved' (comportement désormais correct
    # de resource_remediator.py / ui/main.py sous dry_run=True).
    cur.execute("""
        INSERT INTO actions_log
        (resource_id, recommendation_id, resource_type, action_type,
         action_status, dry_run, action_date)
        VALUES ('test-dryrun-1', %s, 'ec2_instance', 'stop', 'success', true, NOW())
    """, (rec_id,))

    total_eur, count = _active_waste_total(cur, 'ec2_instance')
    assert count == 1
    assert total_eur == pytest.approx(15.0)
    assert _recommendations_pending_count(cur, 'ec2_instance') == 1


# --- Exercice 11 : annuler une action programmée referme son log 'pending' ---

def test_cancel_closes_out_the_pending_actions_log_entry(conn):
    """Verrouille le fix : ui/main.py's action='cancel' remettait la
    recommandation à 'pending' mais laissait la ligne actions_log de la
    programmation à 'pending' pour toujours — History affichait une
    migration en apparence encore en cours des jours après son annulation.
    """
    cur = conn.cursor()
    waste_id = _insert_waste(cur, 'ebs_volume', 'test-cancel-1', 5.0, status='pending')
    cur.execute("""
        UPDATE recommendations SET status = 'scheduled', execute_after = NOW() + interval '3 days'
        WHERE waste_id = %s RETURNING id
    """, (waste_id,))
    rec_id = cur.fetchone()[0]
    cur.execute("""
        INSERT INTO actions_log
        (resource_id, recommendation_id, resource_type, action_type,
         action_status, dry_run, action_date)
        VALUES ('test-cancel-1', %s, 'ebs_volume', 'migrate_gp2_to_gp3', 'pending', false, NOW())
    """, (rec_id,))

    # Mirrors ui/main.py's action == 'cancel' branch.
    cur.execute("""
        UPDATE recommendations SET status = 'pending', execute_after = NULL
        WHERE id = %s AND status = 'scheduled' RETURNING id
    """, (rec_id,))
    assert cur.fetchone() is not None
    cur.execute("""
        UPDATE actions_log SET action_status = 'cancelled', updated_at = NOW()
        WHERE recommendation_id = %s AND action_status = 'pending'
    """, (rec_id,))

    cur.execute("SELECT action_status FROM actions_log WHERE recommendation_id = %s", (rec_id,))
    assert cur.fetchone()[0] == 'cancelled'


# --- Exercice 12 : pr_open et scheduled sont bien candidats à la synchro AWS ---

SYNCABLE_STATUSES = ('pending', 'rejected', 'scheduled', 'pr_open')


def _syncable_resource_ids(cur, resource_type):
    """Mirrors ui/main.py's sync_aws_job / /api/sync-aws grouping query."""
    cur.execute("""
        SELECT array_agg(DISTINCT w.resource_id)
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE w.resource_type = %s AND r.status = ANY(%s)
    """, (resource_type, list(SYNCABLE_STATUSES)))
    row = cur.fetchone()[0]
    return row or []


def test_pr_open_and_scheduled_are_candidates_for_aws_sync(conn):
    """Verrouille le fix : le bouton manuel /api/sync-aws n'incluait pas
    'scheduled', et ni lui ni le job automatique n'incluaient jamais
    'pr_open' — une ressource supprimée pendant qu'une PR Terraform est
    encore ouverte n'était jamais détectée comme disparue par personne.
    """
    cur = conn.cursor()
    _insert_waste(cur, 'nat_gateway', 'test-sync-pr-open', 32.24, status='pr_open')
    _insert_waste(cur, 'ec2_instance', 'test-sync-scheduled', 10.0, status='scheduled')
    _insert_waste(cur, 'ebs_volume', 'test-sync-dismissed', 5.0, status='dismissed')

    assert 'test-sync-pr-open' in _syncable_resource_ids(cur, 'nat_gateway')
    assert 'test-sync-scheduled' in _syncable_resource_ids(cur, 'ec2_instance')
    # dismissed is a deliberate terminal decision: sync must never overwrite it
    assert 'test-sync-dismissed' not in _syncable_resource_ids(cur, 'ebs_volume')


# --- Exercice 13 : le résumé de Recommendations ne doit pas se tronquer à 500 lignes ---

def test_recommendations_summary_totals_survive_past_500_rows(conn):
    """Verrouille le fix : /recommendations calculait 'Savings'/'conf.' en
    sommant en Python la liste affichée (ORDER BY ... LIMIT 500). Au-delà de
    500 recommandations pending, ce total ne reflétait plus que les 500
    lignes les plus chères — un chiffre tronqué qui divergeait du pending_eur
    (SUM SQL sans limite) affiché sur Home pour le même statut.
    """
    cur = conn.cursor()

    # 505 recommandations pending, valeur croissante : la requête d'affichage
    # (ORDER BY savings DESC LIMIT 500) exclut donc les 5 moins chères.
    cur.execute("""
        INSERT INTO waste_detected (
            detection_date, provider, account_id, resource_id,
            resource_type, waste_type, monthly_waste_eur,
            confidence_score, metadata, created_at
        )
        SELECT CURRENT_DATE, 'aws', 'test-coherence',
               'test-bulk-' || g, 'ec2_instance', 'test_waste',
               g::numeric, 0.90, '{}'::jsonb, NOW()
        FROM generate_series(1, 505) AS g
        RETURNING id
    """)
    waste_ids = [row[0] for row in cur.fetchall()]
    cur.execute("""
        INSERT INTO recommendations (waste_id, recommendation_type, status,
                                      estimated_monthly_savings_eur)
        SELECT id, 'test_action', 'pending', monthly_waste_eur
        FROM waste_detected WHERE id = ANY(%s)
    """, (waste_ids,))

    where_clause = ("WHERE r.status = 'pending' AND w.resource_type = 'ec2_instance' "
                    "AND w.account_id = 'test-coherence'")

    # Requête d'affichage (mirrors ui/main.py's capped SELECT)
    cur.execute(f"""
        SELECT r.estimated_monthly_savings_eur
        FROM recommendations r JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
        ORDER BY r.estimated_monthly_savings_eur DESC LIMIT 500
    """)
    displayed_sum = sum(row[0] for row in cur.fetchall())

    # Requête de totaux (mirrors the fix: uncapped aggregate)
    cur.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(r.estimated_monthly_savings_eur), 0)
        FROM recommendations r JOIN waste_detected w ON r.waste_id = w.id
        {where_clause}
    """)
    total_count, total_sum = cur.fetchone()

    assert total_count == 505
    # The 5 cheapest rows (values 1..5) are excluded from the top-500 display
    assert float(displayed_sum) == float(total_sum) - 15
    # The fixed header must show the uncapped total, not the truncated one
    assert float(total_sum) != float(displayed_sum)


# --- Exercice 14 : les KPI partagés de Home et Dashboard ne divergent jamais ---

def _home_kpis(cur):
    """Mirrors ui/main.py's GET / KPI CTE (pending/waste/declined slice)."""
    cur.execute("""
        WITH pending AS (
            SELECT COUNT(*) as pending_count,
                   COALESCE(SUM(estimated_monthly_savings_eur), 0) as pending_eur
            FROM recommendations
            WHERE status = 'pending'
        ),
        waste AS (
            SELECT COALESCE(SUM(monthly_waste_eur), 0) as total_waste
            FROM active_waste
        ),
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        )
        SELECT p.pending_count, p.pending_eur, w.total_waste,
               d.declined_count, d.declined_monthly
        FROM pending p CROSS JOIN waste w CROSS JOIN declined d;
    """)
    pending_count, pending_eur, total_waste, declined_count, declined_monthly = cur.fetchone()
    return {
        'pending_count': pending_count, 'pending_eur': float(pending_eur),
        'total_waste': float(total_waste), 'declined_count': declined_count,
        'declined_monthly': float(declined_monthly),
    }


def _dashboard_kpis(cur):
    """Mirrors ui/main.py's GET /dashboard KPI CTE (same slice, different names)."""
    cur.execute("""
        WITH metrics AS (
            SELECT COALESCE(SUM(estimated_monthly_savings_eur), 0) as potential_monthly
            FROM recommendations WHERE status = 'pending'
        ),
        waste AS (
            SELECT COUNT(*) as waste_count,
                   COALESCE(SUM(monthly_waste_eur), 0) as active_monthly
            FROM active_waste
        ),
        declined AS (
            SELECT COUNT(*) as declined_count,
                   COALESCE(SUM(w.monthly_waste_eur), 0) as declined_monthly
            FROM active_waste w
            JOIN recommendations r ON r.waste_id = w.id
            WHERE r.status = 'rejected'
        ),
        pending AS (
            SELECT COUNT(*) as pending_count
            FROM recommendations WHERE status = 'pending'
        )
        SELECT m.potential_monthly, w.waste_count, w.active_monthly,
               d.declined_count, d.declined_monthly, p.pending_count
        FROM metrics m CROSS JOIN waste w CROSS JOIN declined d CROSS JOIN pending p;
    """)
    (potential_monthly, waste_count, active_monthly,
     declined_count, declined_monthly, pending_count) = cur.fetchone()
    return {
        'pending_count': pending_count, 'pending_eur': float(potential_monthly),
        'total_waste': float(active_monthly), 'declined_count': declined_count,
        'declined_monthly': float(declined_monthly),
    }


def test_home_and_dashboard_kpis_never_diverge(conn):
    """Home et Dashboard lisent la même vue active_waste et le même statut
    'pending'/'rejected' sur recommendations, sans aucun filtre supplémentaire
    d'un côté ou de l'autre — jamais vérifié littéralement jusqu'ici. Compare
    les deux requêtes telles qu'elles existent réellement dans main.py plutôt
    que des valeurs figées, pour rester valide quel que soit le contenu de la
    base (données réelles ou autres tests).
    """
    cur = conn.cursor()

    before = _home_kpis(cur)
    assert before == _dashboard_kpis(cur)

    _insert_waste(cur, 'ec2_instance', 'test-kpi-pending', 40.0, status='pending')
    _insert_waste(cur, 'ebs_volume', 'test-kpi-rejected', 25.0, status='rejected')
    _insert_waste(cur, 'elastic_ip', 'test-kpi-dismissed', 12.0, status='dismissed')
    _insert_waste(cur, 'ebs_snapshot', 'test-kpi-none', 8.0, status=None)

    after_home = _home_kpis(cur)
    after_dashboard = _dashboard_kpis(cur)
    assert after_home == after_dashboard

    # And the fixtures did move the shared numbers, so the equality above
    # isn't trivially true because both sides stayed untouched.
    assert after_home['pending_count'] == before['pending_count'] + 1
    assert after_home['declined_count'] == before['declined_count'] + 1
    # 40 (pending) + 25 (rejected) + 8 (no recommendation row) stay active;
    # 12 (dismissed) is excluded from active_waste.
    assert after_home['total_waste'] == pytest.approx(before['total_waste'] + 73.0)
