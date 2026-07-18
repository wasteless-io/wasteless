#!/usr/bin/env python3
"""
savings_verifier_job : le chaînon Applied → Verified. Deux garanties :

1. Garde de facturation (même règle que cost_collector_job) : tant
   qu'aucune action éligible n'attend (réelle, non vérifiée, > 7 jours),
   le job ne doit ni instancier le tracker ni toucher AWS — chaque
   vérification coûte ~2 requêtes Cost Explorer facturées.
2. Dès qu'une action est éligible, le tracker est appelé une fois avec le
   seuil de 7 jours.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jobs


class TestSavingsVerifierJob(unittest.TestCase):
    def test_skips_tracker_when_nothing_eligible(self):
        conn = MagicMock(name="connection")
        conn.cursor.return_value.fetchone.return_value = {"n": 0}
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch("trackers.savings_tracker.SavingsTracker") as tracker_cls,
        ):
            jobs.savings_verifier_job()
        tracker_cls.assert_not_called()
        conn.close.assert_called_once()

    def test_runs_tracker_once_when_actions_are_eligible(self):
        conn = MagicMock(name="connection")
        conn.cursor.return_value.fetchone.return_value = {"n": 3}
        tracker = MagicMock(name="tracker")
        tracker.verify_all_unverified_actions.return_value = [{"savings_id": 1}]
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch("trackers.savings_tracker.SavingsTracker", return_value=tracker) as tracker_cls,
        ):
            jobs.savings_verifier_job()
        tracker_cls.assert_called_once()
        tracker.verify_all_unverified_actions.assert_called_once_with(min_days_elapsed=7)
        conn.close.assert_called_once()

    def test_swallows_eligibility_error_and_closes_connection(self):
        conn = MagicMock(name="connection")
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch("trackers.savings_tracker.SavingsTracker") as tracker_cls,
        ):
            jobs.savings_verifier_job()  # must swallow the error, not raise
        tracker_cls.assert_not_called()
        conn.close.assert_called_once()

    def test_swallows_tracker_error(self):
        conn = MagicMock(name="connection")
        conn.cursor.return_value.fetchone.return_value = {"n": 1}
        with (
            patch.object(jobs.psycopg2, "connect", return_value=conn),
            patch(
                "trackers.savings_tracker.SavingsTracker",
                side_effect=RuntimeError("CE down"),
            ),
        ):
            jobs.savings_verifier_job()  # must swallow the error, not raise
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
