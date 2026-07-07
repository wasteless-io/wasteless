#!/usr/bin/env python3
"""
Unused VPC Detector for Wasteless (Steampipe collection)

Flags VPCs containing no network interface: every running resource
(EC2, load balancer, NAT gateway, RDS, VPC endpoint...) creates an ENI,
so an ENI-less VPC hosts nothing.

A VPC itself costs 0 EUR — this is a hygiene check, not a savings one.
Empty VPCs clutter the console, consume the 5-VPCs-per-region quota and
hide real waste. Default VPCs are flagged with lower confidence: AWS only
recreates a deleted default VPC on support request.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Allow running as a script: python3 src/detectors/vpc_unused.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError
from detectors.steampipe_base import SteampipeWasteDetector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class VPCUnusedDetector(SteampipeWasteDetector):
    query_name = "vpc_unused"
    resource_type = "vpc"
    waste_type = "unused_vpc"
    recommendation_type = "delete_vpc"
    banner = "UNUSED VPC DETECTION (hygiene — 0 EUR)"

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items = []
        for row in rows:
            is_default = bool(row.get("is_default"))
            name = row.get("name") or ""
            label = f"{name} ({row['vpc_id']})" if name else row["vpc_id"]
            if is_default:
                action = (
                    f"REVIEW default VPC {label} in {row.get('region', '')} "
                    f"— no network interfaces (nothing runs there); AWS only "
                    f"recreates a deleted default VPC on request"
                )
            else:
                action = (
                    f"DELETE unused VPC {label} in {row.get('region', '')} "
                    f"— no network interfaces (nothing runs there)"
                )
            items.append(
                {
                    "resource_id": row["vpc_id"],
                    "monthly_cost": 0.0,
                    "confidence": 0.60 if is_default else 0.85,
                    "action": action,
                    "metadata": {
                        "name": name,
                        "region": row.get("region") or "",
                        "cidr_block": row.get("cidr_block") or "",
                        "is_default": is_default,
                        "monthly_cost_eur": 0.0,
                        "hygiene": True,
                    },
                }
            )
        return items


def main():
    detector = None
    try:
        detector = VPCUnusedDetector()
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
