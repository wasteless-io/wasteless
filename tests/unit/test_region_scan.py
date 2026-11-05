"""
"Nothing found" must not be mistaken for "nothing could be scanned": when every
region fails, the boto3 detectors fail too, so the step lands in
collection_runs.failed_steps instead of reporting zero waste.
AWS and the database are mocked.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from core.region_scan import scanned_results
from detectors import ebs_orphan, eip_orphan, snapshot_orphan

REGIONS = ["eu-west-1", "us-east-1"]

DETECTORS = [
    (ebs_orphan, ebs_orphan.EBSOrphanDetector, "_fetch_orphaned_volumes", []),
    (eip_orphan, eip_orphan.EIPOrphanDetector, "_fetch_unassociated_eips", []),
    (snapshot_orphan, snapshot_orphan.SnapshotOrphanDetector, "_fetch_old_snapshots", ([], set())),
]


class TestScannedResults:
    def test_every_region_failed_raises(self):
        with pytest.raises(RuntimeError, match="in any region"):
            scanned_results([None, None], REGIONS, "volumes")

    def test_one_failed_region_is_tolerated_and_logged(self, caplog):
        with caplog.at_level("WARNING"):
            assert scanned_results([None, ["vol"]], REGIONS, "volumes") == [["vol"]]
        assert "eu-west-1" in caplog.text

    def test_empty_results_are_kept_not_treated_as_failures(self):
        assert scanned_results([[], []], REGIONS, "volumes") == [[], []]


@pytest.mark.parametrize("module,detector_class,fetcher,empty", DETECTORS)
class TestDetectors:
    def _detector(self, detector_class):
        # Skips __init__: no database needed to exercise detect() and run()
        return detector_class.__new__(detector_class)

    def test_all_regions_failing_fails_the_detection(self, module, detector_class, fetcher, empty):
        with (
            patch.object(module, "REGIONS", REGIONS),
            patch.object(module, fetcher, return_value=None),
            pytest.raises(RuntimeError),
        ):
            self._detector(detector_class).detect()

    def test_all_regions_empty_is_a_clean_scan(self, module, detector_class, fetcher, empty):
        with (
            patch.object(module, "REGIONS", REGIONS),
            patch.object(module, fetcher, return_value=empty),
        ):
            result = self._detector(detector_class).detect()
        assert result == ([], set()) if isinstance(empty, tuple) else result == []

    def test_main_exits_nonzero_without_recording_zero_waste(
        self, module, detector_class, fetcher, empty
    ):
        with (
            patch.object(module, "REGIONS", REGIONS),
            patch.object(module, fetcher, return_value=None),
            patch.object(detector_class, "__init__", return_value=None),
            patch("core.snapshots.snapshot_active_waste") as snapshot,
            pytest.raises(SystemExit) as exc,
        ):
            module.main()
        assert exc.value.code == 1
        snapshot.assert_not_called()


def test_snapshot_partial_failure_keeps_other_regions():
    good = ([{"snapshot_id": "snap-1"}], {"snap-ami"})
    with (
        patch.object(snapshot_orphan, "REGIONS", REGIONS),
        patch.object(snapshot_orphan, "_fetch_old_snapshots", side_effect=[None, good]),
    ):
        detector = snapshot_orphan.SnapshotOrphanDetector.__new__(
            snapshot_orphan.SnapshotOrphanDetector
        )
        snapshots, ami_ids = detector.detect()
    assert snapshots == [{"snapshot_id": "snap-1"}]
    assert ami_ids == {"snap-ami"}
