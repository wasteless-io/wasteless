#!/usr/bin/env python3
"""
Régression : les jobs de fond (sync_aws_job, terraform_pr_sync_job,
grace_executor_job) ouvrent chacun une connexion psycopg2 non poolée. Avant le
correctif, une exception survenue entre le connect et le conn.close() final ne
fermait jamais la connexion (le `except` et le `finally` ne la fermaient pas) :
sur un tick de scheduler toutes les 5 min, une erreur récurrente fuyait une
connexion à chaque passage jusqu'à épuiser Postgres.

Ces tests forcent une exception en cours de traitement et vérifient que la
connexion est bien fermée malgré tout.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobs


class TestJobsCloseConnectionOnError(unittest.TestCase):
    def _mock_conn(self) -> MagicMock:
        conn = MagicMock(name="connection")
        return conn

    def test_sync_aws_job_closes_connection_on_error(self):
        conn = self._mock_conn()
        # Fail on the first query, deep inside the try block.
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch.object(jobs, "check_aws_reachable", return_value=False),
        ):
            jobs.sync_aws_job()  # must swallow the error, not raise
        conn.close.assert_called_once()

    def test_terraform_pr_sync_job_closes_connection_on_error(self):
        conn = self._mock_conn()
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch("utils.terraform_pr.sync_open_prs", side_effect=RuntimeError("boom")),
        ):
            jobs.terraform_pr_sync_job()
        conn.close.assert_called_once()

    def test_grace_executor_job_closes_connection_on_error(self):
        conn = self._mock_conn()
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with patch.object(jobs.psycopg2, "connect", return_value=conn):
            jobs.grace_executor_job()
        conn.close.assert_called_once()

    def test_cost_collector_job_closes_connection_on_error(self):
        conn = self._mock_conn()
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with patch.object(jobs.psycopg2, "connect", return_value=conn):
            jobs.cost_collector_job()  # must swallow the error, not raise
        conn.close.assert_called_once()


class TestCostCollectorBillingGuard(unittest.TestCase):
    """L'API Cost Explorer est facturée 0,01 $ par requête : tant que les
    données d'hier sont en base, le job ne doit PAS appeler AWS."""

    def test_skips_ce_call_when_data_is_fresh(self):
        from datetime import date

        conn = MagicMock(name="connection")
        conn.cursor.return_value.fetchone.return_value = {"latest": date.today()}
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch("utils.aws_clients.get_client") as get_client,
        ):
            jobs.cost_collector_job()
        get_client.assert_not_called()
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
