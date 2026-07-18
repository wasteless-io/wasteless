#!/usr/bin/env python3
"""
Unused Load Balancer Detector for Wasteless (Steampipe collection)

Flags ALB/NLB/GWLB with no registered targets (no target group, or target
groups with zero registered targets) and Classic LBs with no instances.

Pricing (hourly base * 730h, USD, eu-west-1, LCU/data excluded):
  ALB/NLB $0.0252/h = 18.40 USD/mo, GWLB $0.0125/h = 9.13 USD/mo,
  Classic $0.028/h = 20.44 USD/mo
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

# USD/month by load balancer type (approximate, hourly base only)
ELB_MONTHLY_COST_USD: Dict[str, float] = {
    "application": 18.40,
    "network": 18.40,
    "gateway": 9.13,
    "classic": 20.44,
}
DEFAULT_ELB_COST_USD = 18.40


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
            cost = ELB_MONTHLY_COST_USD.get(lb_type, DEFAULT_ELB_COST_USD)
            reason = row.get("reason") or ("no_instances" if lb_type == "classic" else "no_targets")
            registered = int(row.get("registered_targets") or 0)
            # "no_traffic" is a slightly weaker signal than an empty LB (a
            # low-traffic internal LB could in theory see exactly zero over the
            # window), so it gets lower confidence — still above the 0.80
            # auto-remediation floor.
            if reason == "no_traffic":
                why = f"no traffic in 30 days ({registered} target(s) registered)"
                confidence = 0.85
            elif reason == "no_instances":
                why = "no instances attached"
                confidence = 0.90
            else:  # no_targets
                why = "no registered targets"
                confidence = 0.90
            items.append(
                {
                    "resource_id": row.get("arn") or row["name"],
                    "monthly_cost": cost,
                    "confidence": confidence,
                    "action": (
                        f"DELETE unused {lb_type} load balancer {row['name']} "
                        f"in {row.get('region', '')} — {why}"
                    ),
                    "metadata": {
                        "name": row["name"],
                        "lb_type": lb_type,
                        "arn": row.get("arn") or "",
                        "region": row.get("region") or "",
                        "reason": reason,
                        "registered_targets": registered,
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
