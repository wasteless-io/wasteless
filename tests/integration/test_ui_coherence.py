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
    cur.execute("""
        SELECT COALESCE(SUM(monthly_waste_eur), 0), COUNT(*)
        FROM active_waste WHERE resource_type = %s
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
    """, (resource_type,))
    return cur.fetchone()[0]


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
