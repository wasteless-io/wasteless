#!/usr/bin/env python3
"""
Unused Load Balancer Detector for Wasteless (Steampipe collection)

Flags ALB/NLB/GWLB with no registered targets (no target group, or target
groups with zero registered targets) and Classic LBs with no instances.

Pricing (hourly base * 730h * 0.92 EUR/USD, eu-west-1, LCU/data excluded):
  ALB/NLB $0.0252/h ≈ 16.92 EUR/mo, GWLB $0.0125/h ≈ 8.40 EUR/mo,
  Classic $0.028/h ≈ 18.81 EUR/mo
"""

import logging
import sys
from typing import Any, Dict, List


from collectors.steampipe import SteampipeError
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# EUR/month by load balancer type (approximate, hourly base only)
ELB_MONTHLY_COST_EUR: Dict[str, float] = {
    "application": 16.92,
    "network": 16.92,
    "gateway": 8.40,
    "classic": 18.81,
}
DEFAULT_ELB_COST_EUR = 16.92


class ELBUnusedDetector(SteampipeWasteDetector):
    query_name = "elb_unused"
    resource_type = "load_balancer"
    waste_type = "unused_load_balancer"
    recommendation_type = "delete_load_balancer"
    banner = "UNUSED LOAD BALANCER DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            lb_type = row.get("lb_type") or "application"
            cost = ELB_MONTHLY_COST_EUR.get(lb_type, DEFAULT_ELB_COST_EUR)
            reason = "no instances attached" if lb_type == "classic" else "no registered targets"
            items.append(
                {
                    "resource_id": row.get("arn") or row["name"],
                    "monthly_cost": cost,
                    "confidence": 0.90,
                    "action": (
                        f"DELETE unused {lb_type} load balancer {row['name']} "
                        f"in {row.get('region', '')} — {reason}"
                    ),
                    "metadata": {
                        "name": row["name"],
                        "lb_type": lb_type,
                        "arn": row.get("arn") or "",
                        "region": row.get("region") or "",
                        "monthly_cost_eur": cost,
                    },
                }
            )
        return items


def main():
    detector = None
    try:
        detector = ELBUnusedDetector()
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
