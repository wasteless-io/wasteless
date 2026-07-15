#!/usr/bin/env python3
"""
Orphaned AMI Detector for Wasteless (Steampipe collection)

Flags self-owned AMIs older than 90 days that no longer back any EC2
instance. The AMI is free, but it pins its backing EBS snapshots (which do
cost storage) — deregistering it is the prerequisite to deleting them.

Cost = backing snapshot GiB * EBS snapshot price. The source volume size
overestimates the compressed, incremental snapshot storage, so the figure
is deliberately conservative. Manual review: an AMI can still be referenced
by a launch template / launch configuration, which this check does not
inspect.
"""

import logging
import sys
from typing import Any, Dict, List

from constants import USD_TO_EUR

from collectors.steampipe import SteampipeError
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# EBS snapshot standard-tier storage, eu-west-1 (USD 0.05 / GiB-month).
SNAPSHOT_EUR_PER_GIB = round(0.05 * USD_TO_EUR, 4)


class AMIOrphanDetector(SteampipeWasteDetector):
    query_name = "ami_orphan"
    resource_type = "ami"
    waste_type = "orphaned_ami"
    recommendation_type = "deregister_ami"
    banner = "ORPHANED AMI DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            backing_gb = float(row.get("backing_gb") or 0)
            snapshot_count = int(row.get("snapshot_count") or 0)
            cost = round(backing_gb * SNAPSHOT_EUR_PER_GIB, 2)
            age_days = row.get("age_days")
            name = row.get("name") or ""
            label = f"{name} ({row['image_id']})" if name else row["image_id"]
            items.append(
                {
                    "resource_id": row["image_id"],
                    "monthly_cost": cost,
                    "confidence": 0.80,
                    "action": (
                        f"DEREGISTER unused AMI {label} in {row.get('region', '')} "
                        f"— {age_days}d old, backs no running instance; frees "
                        f"{snapshot_count} snapshot(s) (~{backing_gb:g} GiB). "
                        f"Verify no launch template references it first."
                    ),
                    "metadata": {
                        "name": name,
                        "backing_gb": backing_gb,
                        "snapshot_count": snapshot_count,
                        "age_days": age_days,
                        "region": row.get("region") or "",
                        "platform_details": row.get("platform_details") or "",
                        "snapshot_eur_per_gib": SNAPSHOT_EUR_PER_GIB,
                        "monthly_cost_eur": cost,
                    },
                }
            )
        return items


def main():
    detector = None
    try:
        detector = AMIOrphanDetector()
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


if __name__ == "__main__":
    main()
