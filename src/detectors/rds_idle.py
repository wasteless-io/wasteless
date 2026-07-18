#!/usr/bin/env python3
"""
Idle RDS Detector for Wasteless (Steampipe collection)

Flags running RDS instances with zero database connections over the last
14 days. Unlike a stopped DB, an idle one pays full compute + storage while
serving nothing — the single biggest RDS quick win.

Cost = instance compute (class, multi-AZ) + storage, from the approximate
map in rds_pricing. Manual review: downsizing or deleting a database is a
judgement call (a rarely-used DB may still be needed).
"""

import logging
import sys
from typing import Any, Dict, List

from collectors.steampipe import SteampipeError
from detectors.rds_pricing import instance_usd
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class RDSIdleDetector(SteampipeWasteDetector):
    query_name = "rds_idle"
    resource_type = "rds_instance"
    waste_type = "idle_rds"
    recommendation_type = "downsize_rds_instance"
    banner = "IDLE RDS DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            storage_gb = float(row.get("allocated_storage") or 0)
            storage_type = row.get("storage_type") or "gp2"
            db_class = row.get("class") or ""
            multi_az = bool(row.get("multi_az"))
            cost = instance_usd(db_class, multi_az, storage_gb, storage_type)
            db_id = row["db_instance_identifier"]
            items.append(
                {
                    "resource_id": db_id,
                    "monthly_cost": cost,
                    "confidence": 0.80,
                    "action": (
                        f"REVIEW idle RDS {db_id} ({db_class}, "
                        f"{row.get('engine', '')}) in {row.get('region', '')} "
                        f"— 0 connections in 14 days; downsize or delete."
                    ),
                    "metadata": {
                        "class": db_class,
                        "engine": row.get("engine") or "",
                        "engine_version": row.get("engine_version") or "",
                        "allocated_storage": storage_gb,
                        "storage_type": storage_type,
                        "multi_az": multi_az,
                        "max_conn_14d": row.get("max_conn_14d"),
                        "avg_conn_14d": row.get("avg_conn_14d"),
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
        detector = RDSIdleDetector()
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
