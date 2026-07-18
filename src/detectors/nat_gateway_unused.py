#!/usr/bin/env python3
"""
Unused NAT Gateway Detector for Wasteless (Steampipe collection)

Flags NAT gateways that are not 'available' or had zero outbound traffic
over the last 30 days. NAT gateways bill hourly even when idle.

Pricing: $0.048/hour (eu-west-1) * 730h = 35.04 USD/month
(data processing charges excluded — an idle gateway has none).
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

NAT_GATEWAY_MONTHLY_COST_USD = 35.04  # $0.048/h * 730h


class NATGatewayUnusedDetector(SteampipeWasteDetector):
    query_name = "nat_gateway_unused"
    resource_type = "nat_gateway"
    waste_type = "unused_nat_gateway"
    recommendation_type = "delete_nat_gateway"
    banner = "UNUSED NAT GATEWAY DETECTION"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            state = row.get("state") or ""
            reason = (
                f"in '{state}' state" if state != "available" else "no outbound traffic in 30 days"
            )
            items.append(
                {
                    "resource_id": row["nat_gateway_id"],
                    "monthly_cost": NAT_GATEWAY_MONTHLY_COST_USD,
                    "confidence": 0.90,
                    "action": (
                        f"DELETE unused NAT gateway {row['nat_gateway_id']} "
                        f"in {row.get('region', '')} — {reason}"
                    ),
                    "metadata": {
                        "vpc_id": row.get("vpc_id") or "",
                        "state": state,
                        "region": row.get("region") or "",
                        "bytes_out_30d": row.get("bytes_out_30d") or 0,
                        "monthly_cost_eur": NAT_GATEWAY_MONTHLY_COST_USD,
                    },
                }
            )
        return items


def main():
    detector = None
    try:
        detector = NATGatewayUnusedDetector()
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
