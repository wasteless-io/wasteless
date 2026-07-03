#!/usr/bin/env python3
"""
EBS gp2 → gp3 Migration Detector for Wasteless (Steampipe collection)

Flags attached gp2 volumes: gp3 is ~20% cheaper for equal or better
baseline performance, and the migration is online (no downtime).
Unattached gp2 volumes are left to the ebs_orphan detector (deletion
saves more than migration).

Savings: size_gb * (0.0920 - 0.0736) EUR/GiB/month = size_gb * 0.0184
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running as a script: python3 src/detectors/ebs_gp2_migration.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError
from detectors.ebs_orphan import EBS_PRICING_EUR_PER_GIB
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

GP2_TO_GP3_SAVINGS_EUR_PER_GIB = round(
    EBS_PRICING_EUR_PER_GIB['gp2'] - EBS_PRICING_EUR_PER_GIB['gp3'], 4
)


class EBSGp2MigrationDetector(SteampipeWasteDetector):
    query_name = 'ebs_gp2'
    resource_type = 'ebs_volume'
    waste_type = 'gp2_volume'
    recommendation_type = 'migrate_gp2_to_gp3'
    banner = 'EBS GP2 -> GP3 MIGRATION DETECTION'

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            size_gb = row.get('size_gb') or 0
            savings = round(size_gb * GP2_TO_GP3_SAVINGS_EUR_PER_GIB, 2)
            name = row.get('name') or ''
            label = f"{name} ({row['volume_id']})" if name else row['volume_id']
            items.append({
                'resource_id':  row['volume_id'],
                'monthly_cost': savings,
                'confidence':   0.95,  # online migration, no downtime, pure savings
                'action': (
                    f"MIGRATE volume {label} from gp2 to gp3 — "
                    f"{size_gb} GiB in {row.get('region', '')}, "
                    f"~20% cheaper, no downtime"
                ),
                'metadata': {
                    'name':                name,
                    'size_gb':             size_gb,
                    'az':                  row.get('az') or '',
                    'region':              row.get('region') or '',
                    'savings_eur_per_gib': GP2_TO_GP3_SAVINGS_EUR_PER_GIB,
                    'monthly_cost_eur':    savings,
                },
            })
        return items


def main():
    detector = None
    try:
        detector = EBSGp2MigrationDetector()
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
