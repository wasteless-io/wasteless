"""Remediator teardown must return its DB connection to the shared pool.

Both remediators check a connection out of the core.database pool in
__init__. Their __del__ used to call conn.close(), which kills the socket
but leaves the pool slot marked as checked out — after maxconn remediator
instances (10 UI actions) the pool was exhausted and every further action
failed with "connection pool exhausted". The teardown must go through
release_connection() instead, and never call close() itself.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from remediators.ec2_remediator import EC2Remediator  # noqa: E402
from remediators.resource_remediator import ResourceRemediator  # noqa: E402


def _bare_instance(cls):
    """Instance with a mock connection, without running __init__ (no DB)."""
    obj = object.__new__(cls)
    obj.conn = MagicMock()
    return obj


class TestEC2RemediatorPoolRelease:
    def test_del_releases_to_pool_instead_of_closing(self):
        obj = _bare_instance(EC2Remediator)
        obj.release_lock = MagicMock()
        with patch("remediators.ec2_remediator.release_connection") as release:
            obj.__del__()
        release.assert_called_once_with(obj.conn)
        obj.conn.close.assert_not_called()

    def test_del_releases_even_if_lock_release_fails(self):
        obj = _bare_instance(EC2Remediator)
        obj.release_lock = MagicMock(side_effect=RuntimeError("boom"))
        with patch("remediators.ec2_remediator.release_connection") as release:
            obj.__del__()
        release.assert_called_once_with(obj.conn)


class TestResourceRemediatorPoolRelease:
    def test_del_releases_to_pool_instead_of_closing(self):
        obj = _bare_instance(ResourceRemediator)
        with patch("remediators.resource_remediator.release_connection") as release:
            obj.__del__()
        release.assert_called_once_with(obj.conn)
        obj.conn.close.assert_not_called()
