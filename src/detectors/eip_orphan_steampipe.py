#!/usr/bin/env python3
"""
Elastic IP Orphan Detector (Steampipe collection) for Wasteless

Same detection as eip_orphan.py, but the collection layer is a single SQL
query (sql/steampipe/eip_orphan.sql). save() and recommend() are inherited
unchanged from EIPOrphanDetector, so both detectors feed the same rows and
dedupe via ON CONFLICT.

Prerequisites: see src/collectors/steampipe.py
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running as a script: python3 src/detectors/eip_orphan_steampipe.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError, run_query_file
from detectors.eip_orphan import EIPOrphanDetector, EIP_MONTHLY_COST_EUR

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def rows_to_eips(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Map Steampipe rows to the EIP dicts EIPOrphanDetector.save() expects."""
    return [{
        'allocation_id': row['allocation_id'],
        'public_ip':     row.get('public_ip') or '',
        'domain':        row.get('domain') or 'vpc',
        'region':        row.get('region') or '',
        'monthly_cost':  EIP_MONTHLY_COST_EUR,
    } for row in rows]


class SteampipeEIPOrphanDetector(EIPOrphanDetector):
    """EIP orphan detector whose collection layer is a Steampipe SQL query."""

    def detect(self) -> List[Dict[str, Any]]:
        logger.info("Scanning for unassociated Elastic IPs via Steampipe...")
        eips = rows_to_eips(run_query_file('eip_orphan'))
        logger.info(f"Total unassociated EIPs found: {len(eips)}")
        return eips


def main():
    detector = None
    try:
        detector = SteampipeEIPOrphanDetector()
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
