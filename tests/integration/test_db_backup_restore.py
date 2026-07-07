"""
Vérifie que la procédure de backup/restore documentée dans
docs/DEPLOYMENT.md (pg_dump / psql) produit réellement un dump
restorable — pas juste un fichier qui existe.

Le dump de la vraie base est restauré dans une base PostgreSQL jetable
créée pour l'occasion, à l'intérieur de la même instance : la base
"wasteless" réelle n'est jamais écrasée ni modifiée.

Deux backends selon l'environnement :
- Local (docker-compose up -d postgres) : passe par `docker exec
  wasteless-postgres`, reproduisant mot pour mot les commandes de
  docs/DEPLOYMENT.md.
- CI / Postgres accessible en TCP sans conteneur nommé
  wasteless-postgres (ex: service Postgres GitHub Actions) : utilise les
  binaires `pg_dump`/`psql` de la machine directement, mêmes garanties.
Skip proprement si ni l'un ni l'autre n'est disponible.
"""

import os
import shutil
import subprocess
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

CONTAINER = "wasteless-postgres"
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "wasteless")
DB_NAME = os.getenv("DB_NAME", "wasteless")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Tables that must survive a real backup/restore round-trip: one per
# stage of the pipeline (detection, recommendation, execution).
CHECKED_TABLES = ["waste_detected", "recommendations", "actions_log"]


def _docker_available():
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], capture_output=True, text=True
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _tcp_available():
    if shutil.which("psql") is None or shutil.which("pg_dump") is None:
        return False
    try:
        _connect(DB_NAME).close()
        return True
    except Exception:
        return False


def _pg_env():
    return {**os.environ, "PGPASSWORD": DB_PASSWORD}


def _pg_dump():
    """Dumps DB_NAME, docker-exec locally or host pg_dump in CI."""
    if _docker_available():
        return subprocess.run(
            ["docker", "exec", CONTAINER, "pg_dump", "-U", DB_USER, DB_NAME],
            capture_output=True,
            text=True,
            check=True,
        )
    return subprocess.run(
        ["pg_dump", "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, DB_NAME],
        capture_output=True,
        text=True,
        check=True,
        env=_pg_env(),
    )


def _psql(database, sql=None, sql_input=None, check=True):
    """Runs a single -c statement (sql=) or feeds a whole script (sql_input=)."""
    if _docker_available():
        if sql_input is not None:
            return subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    CONTAINER,
                    "psql",
                    "-U",
                    DB_USER,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-d",
                    database,
                ],
                input=sql_input,
                capture_output=True,
                text=True,
                check=check,
            )
        return subprocess.run(
            [
                "docker",
                "exec",
                CONTAINER,
                "psql",
                "-U",
                DB_USER,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            check=check,
        )

    base = [
        "psql",
        "-h",
        DB_HOST,
        "-p",
        DB_PORT,
        "-U",
        DB_USER,
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if sql_input is not None:
        return subprocess.run(
            base, input=sql_input, capture_output=True, text=True, check=check, env=_pg_env()
        )
    return subprocess.run(
        base + ["-c", sql], capture_output=True, text=True, check=check, env=_pg_env()
    )


def _connect(database):
    return psycopg2.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        database=database,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5,
    )


def _row_counts(database):
    conn = _connect(database)
    try:
        cur = conn.cursor()
        counts = {}
        for table in CHECKED_TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        return counts
    finally:
        conn.close()


@pytest.fixture
def restore_db():
    """A throwaway database inside the same Postgres instance, dropped after the test."""
    if not (_docker_available() or _tcp_available()):
        pytest.skip(
            f"Ni Docker/{CONTAINER} ni psql/pg_dump+Postgres en TCP ne sont "
            "disponibles — lancer docker-compose up -d postgres"
        )

    name = f"wasteless_restore_test_{uuid.uuid4().hex[:8]}"
    _psql("postgres", f"CREATE DATABASE {name}")
    try:
        yield name
    finally:
        # Terminate any lingering connections before dropping (a leaked
        # psycopg2 connection would otherwise make DROP DATABASE hang).
        _psql(
            "postgres",
            (
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            ),
            check=False,
        )
        _psql("postgres", f"DROP DATABASE IF EXISTS {name}")


def test_dump_restores_into_fresh_database_with_matching_row_counts(restore_db):
    """Reproduces docs/DEPLOYMENT.md's backup+restore commands end to end:
    pg_dump the real database, psql-restore the dump elsewhere, and check
    the restored copy actually holds the same data — not just that both
    commands exited 0."""
    dump = _pg_dump()
    assert dump.stdout, "pg_dump produced an empty dump"
    assert (
        "CREATE TABLE" in dump.stdout
    ), "dump has no schema — pg_dump likely targeted the wrong database"

    restore = _psql(restore_db, sql_input=dump.stdout, check=False)
    assert restore.returncode == 0, f"restore failed:\n{restore.stderr}"

    original_counts = _row_counts(DB_NAME)
    restored_counts = _row_counts(restore_db)
    assert restored_counts == original_counts, (
        f"row counts diverged after restore: original={original_counts} "
        f"restored={restored_counts}"
    )


def test_restore_into_nonempty_database_fails_loudly(restore_db):
    """A restore attempted against a database that already has the schema
    must fail (duplicate CREATE TABLE) rather than silently succeed half-way
    — the documented procedure assumes a fresh/empty target."""
    _psql(restore_db, "CREATE TABLE recommendations (id INT)")

    dump = _pg_dump()
    restore = _psql(restore_db, sql_input=dump.stdout, check=False)

    assert restore.returncode != 0
    assert "already exists" in restore.stderr.lower()
