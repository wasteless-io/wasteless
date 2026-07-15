#!/usr/bin/env python3
"""
Stopped RDS Detector for Wasteless (Steampipe collection)

Flags stopped RDS instances. A stopped DB pays no compute but keeps billing
for its provisioned storage and backups — and AWS automatically restarts any
instance stopped for more than 7 days, so a "stopped to save money" DB
silently resumes full billing. Either delete it or accept it will come back.

Cost = allocated storage GiB * RDS storage price for its storage_type.
Manual review: deleting a database is irreversible.
"""

import logging
import sys
from typing import Any, Dict, List

from collectors.steampipe import SteampipeError
from detectors.rds_pricing import storage_eur
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RDSStoppedDetector(SteampipeWasteDetector):
    query_name = "rds_stopped"
    resource_type = "rds_instance"
    waste_type = "stopped_rds"
    recommendation_type = "delete_rds_instance"
    banner = "STOPPED RDS DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            storage_gb = float(row.get("allocated_storage") or 0)
            storage_type = row.get("storage_type") or "gp2"
            cost = storage_eur(storage_gb, storage_type)
            age_days = row.get("age_days")
            db_id = row["db_instance_identifier"]
            items.append(
                {
                    "resource_id": db_id,
                    "monthly_cost": cost,
                    "confidence": 0.75,
                    "action": (
                        f"REVIEW stopped RDS {db_id} ({row.get('class', '')}, "
                        f"{row.get('engine', '')}) in {row.get('region', '')} "
                        f"— still billing {storage_gb:g} GiB storage; AWS "
                        f"auto-restarts after 7 days, so delete it or it resumes "
                        f"full billing."
                    ),
                    "metadata": {
                        "class": row.get("class") or "",
                        "engine": row.get("engine") or "",
                        "engine_version": row.get("engine_version") or "",
                        "allocated_storage": storage_gb,
                        "storage_type": storage_type,
                        "multi_az": bool(row.get("multi_az")),
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
        detector = RDSStoppedDetector()
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
