#!/usr/bin/env python3
"""
EBS Orphan Volume Detector (Steampipe collection) for Wasteless

Same detection as ebs_orphan.py, but the collection layer is a single SQL
query (sql/steampipe/ebs_orphan.sql) run through Steampipe instead of
per-region boto3 describe_volumes calls. save() and recommend() are
inherited unchanged from EBSOrphanDetector, so both detectors feed the
same waste_detected / recommendations rows and dedupe via ON CONFLICT.

Prerequisites: see src/collectors/steampipe.py
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running as a script: python3 src/detectors/ebs_orphan_steampipe.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError, run_query_file
from detectors.ebs_orphan import EBSOrphanDetector, _volume_monthly_cost

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def rows_to_volumes(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map Steampipe rows to the volume dicts EBSOrphanDetector.save() expects."""
    volumes = []
    for row in rows:
        size_gb = row.get('size_gb') or 0
        vol_type = row.get('vol_type') or 'gp2'
        volumes.append({
            'volume_id':    row['volume_id'],
            'name':         row.get('name') or '',
            'size_gb':      size_gb,
            'vol_type':     vol_type,
            'az':           row.get('az') or '',
            'region':       row.get('region') or '',
            'encrypted':    bool(row.get('encrypted')),
            'monthly_cost': _volume_monthly_cost(size_gb, vol_type),
            'age_days':     row.get('age_days'),
        })
    return volumes


class SteampipeEBSOrphanDetector(EBSOrphanDetector):
    """EBS orphan detector whose collection layer is a Steampipe SQL query."""

    def detect(self) -> List[Dict[str, Any]]:
        logger.info("Scanning for orphaned EBS volumes via Steampipe...")
        rows = run_query_file('ebs_orphan')
        volumes = rows_to_volumes(rows)
        logger.info(f"Total orphaned volumes found: {len(volumes)}")
        return volumes


def main():
    detector = None
    try:
        detector = SteampipeEBSOrphanDetector()
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
