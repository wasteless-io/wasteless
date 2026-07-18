#!/usr/bin/env python3
"""
Old Manual RDS Snapshot Detector for Wasteless (Steampipe collection)

Flags MANUAL RDS snapshots older than 90 days. Automated snapshots expire on
their own via the instance retention window; manual snapshots live forever
until someone deletes them and bill for backup storage the whole time.

Cost = allocated storage GiB * RDS backup storage price. Manual review:
a snapshot can be the only remaining copy of a deleted database.
"""

import logging
import sys
from typing import Any, Dict, List

from collectors.steampipe import SteampipeError
from detectors.rds_pricing import snapshot_usd
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RDSSnapshotOrphanDetector(SteampipeWasteDetector):
    query_name = "rds_snapshot_orphan"
    resource_type = "rds_snapshot"
    waste_type = "old_rds_snapshot"
    recommendation_type = "delete_rds_snapshot"
    banner = "OLD RDS SNAPSHOT DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            storage_gb = float(row.get("allocated_storage") or 0)
            cost = snapshot_usd(storage_gb)
            age_days = row.get("age_days")
            snap_id = row["db_snapshot_identifier"]
            src = row.get("db_instance_identifier") or "?"
            items.append(
                {
                    "resource_id": snap_id,
                    "monthly_cost": cost,
                    "confidence": 0.80,
                    "action": (
                        f"DELETE old manual RDS snapshot {snap_id} of {src} "
                        f"in {row.get('region', '')} — {age_days}d old, "
                        f"{storage_gb:g} GiB backup storage."
                    ),
                    "metadata": {
                        "db_instance_identifier": src,
                        "engine": row.get("engine") or "",
                        "engine_version": row.get("engine_version") or "",
                        "allocated_storage": storage_gb,
                        "storage_type": row.get("storage_type") or "",
                        "age_days": age_days,
                        "region": row.get("region") or "",
                        "arn": row.get("arn") or "",
                        "monthly_cost_eur": cost,
                    },
                }
            )
        return items


def main():
    detector = None
    try:
        detector = RDSSnapshotOrphanDetector()
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
