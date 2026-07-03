#!/usr/bin/env python3
"""
EBS Snapshot Orphan Detector (Steampipe collection) for Wasteless

Same detection as snapshot_orphan.py, but the collection layer is SQL:
sql/steampipe/snapshot_orphan.sql excludes AMI-backed snapshots with an
anti-join, and sql/steampipe/ami_backed_snapshots.sql feeds the
mark_ami_backed_obsolete() cleanup inherited from SnapshotOrphanDetector.
save() and recommend() are inherited unchanged, so both detectors feed the
same rows and dedupe via ON CONFLICT.

Prerequisites: see src/collectors/steampipe.py
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Allow running as a script: python3 src/detectors/snapshot_orphan_steampipe.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError, run_query_file
from detectors.snapshot_orphan import (
    SnapshotOrphanDetector,
    SNAPSHOT_AGE_DAYS,
    _snapshot_monthly_cost,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def rows_to_snapshots(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map Steampipe rows to the dicts SnapshotOrphanDetector.save() expects."""
    snapshots = []
    for row in rows:
        size_gb = row.get('size_gb') or 0
        snapshots.append({
            'snapshot_id':  row['snapshot_id'],
            'description':  row.get('description') or '',
            'volume_id':    row.get('volume_id') or '',
            'size_gb':      size_gb,
            'state':        row.get('state') or '',
            'start_time':   row.get('start_time') or '',
            'age_days':     row.get('age_days') or 0,
            'encrypted':    bool(row.get('encrypted')),
            'region':       row.get('region') or '',
            'monthly_cost': _snapshot_monthly_cost(size_gb),
        })
    return snapshots


class SteampipeSnapshotOrphanDetector(SnapshotOrphanDetector):
    """Snapshot orphan detector whose collection layer is Steampipe SQL."""

    def detect(self) -> Tuple[List[Dict[str, Any]], Set[str]]:
        logger.info(
            f"Scanning for old EBS snapshots (>{SNAPSHOT_AGE_DAYS} days) via Steampipe..."
        )
        ami_snap_ids = {
            row['snapshot_id'] for row in run_query_file('ami_backed_snapshots')
        }
        snapshots = rows_to_snapshots(run_query_file('snapshot_orphan'))
        logger.info(f"Total old snapshots found: {len(snapshots)} "
                    f"({len(ami_snap_ids)} AMI-backed excluded)")
        return snapshots, ami_snap_ids


def main():
    detector = None
    try:
        detector = SteampipeSnapshotOrphanDetector()
        detector.run()
    except SteampipeError as e:
        logger.error(f"Steampipe error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
