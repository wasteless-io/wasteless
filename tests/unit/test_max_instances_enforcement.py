"""
Non-régression du garde-fou max_instances_per_run.

Historique du bug : stop_instance appelait validate_all avec current_count=0
en dur (le compteur n'était jamais suivi), et la boucle
process_pending_recommendations s'arrêtait sur un `>= 3` en dur — donc la
valeur configurable max_instances_per_run était ignorée dans les deux cas.

Ces tests verrouillent le câblage : la boucle incrémente le compteur, le
passe à stop_instance, et s'arrête à la valeur lue depuis la config (pas 3).
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from remediators.ec2_remediator import EC2Remediator


def _remediator_with_pending(n_rows: int, max_per_run: int) -> EC2Remediator:
    """Build an EC2Remediator without touching __init__ (no DB/AWS), wired with
    n_rows pending recommendations and a config cap of max_per_run."""
    rem = object.__new__(EC2Remediator)

    rem.safeguards = MagicMock()
    rem.safeguards.config = {"protection": {"max_instances_per_run": max_per_run}}

    rem.acquire_lock = lambda *a, **k: True  # type: ignore[method-assign]
    rem.release_lock = lambda *a, **k: None  # type: ignore[method-assign]

    rows = [(rec_id, "stop_instance", 10.0, f"i-{rec_id:04d}", 0.9) for rec_id in range(n_rows)]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    rem.conn = MagicMock()
    rem.conn.cursor.return_value = cursor

    return rem


def test_loop_stops_at_configured_cap_not_hardcoded_three():
    # Cap of 5 with 10 pending: the old hardcoded `>= 3` would stop at 3.
    rem = _remediator_with_pending(n_rows=10, max_per_run=5)
    rem.stop_instance = MagicMock(return_value={"success": True})  # type: ignore[method-assign]

    results = rem.process_pending_recommendations(limit=10)

    assert len(results) == 5, "loop must honor the configured cap (5), not the old hardcoded 3"
    assert rem.stop_instance.call_count == 5


def test_current_count_is_tracked_and_passed_incrementing():
    rem = _remediator_with_pending(n_rows=10, max_per_run=3)
    rem.stop_instance = MagicMock(return_value={"success": True})  # type: ignore[method-assign]

    rem.process_pending_recommendations(limit=10)

    # current_count fed to each call must be the number already stopped: 0,1,2
    counts = [c.kwargs["current_count"] for c in rem.stop_instance.call_args_list]
    assert counts == [0, 1, 2]


def test_failed_stops_do_not_count_toward_cap():
    # First two attempts fail, then successes: failures must not consume the cap.
    rem = _remediator_with_pending(n_rows=6, max_per_run=2)
    rem.stop_instance = MagicMock(  # type: ignore[method-assign]
        side_effect=[
            {"success": False},
            {"success": False},
            {"success": True},
            {"success": True},
            {"success": True},
            {"success": True},
        ]
    )

    results = rem.process_pending_recommendations(limit=10)

    # 2 failures + 2 successes = 4 attempts before the cap of 2 successes is hit
    assert len(results) == 4
    assert sum(1 for r in results if r["success"]) == 2
