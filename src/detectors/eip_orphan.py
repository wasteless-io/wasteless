#!/usr/bin/env python3
"""
Elastic IP Orphan Detector for Wasteless

Detects Elastic IPs not associated with any instance or network interface.
These IPs are billed even when idle (~3.36 EUR/month each).

Detection criteria:
- No InstanceId
- No NetworkInterfaceId

Pricing: $0.005/hour = $3.65/month * 0.92 EUR/USD = ~3.36 EUR/month
"""

import os
import sys
import json
import logging
from datetime import date
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
import psycopg2

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

EIP_MONTHLY_COST_EUR = 3.36  # $0.005/hr * 730h * 0.92 EUR/USD
REGIONS = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']


def _fetch_unassociated_eips(region: str) -> List[Dict[str, Any]]:
    try:
        import boto3
        ec2 = boto3.client('ec2', region_name=region)
        result = []
        for addr in ec2.describe_addresses().get('Addresses', []):
            # Skip if associated with an instance or network interface
            if addr.get('InstanceId') or addr.get('NetworkInterfaceId'):
                continue
            result.append({
                'allocation_id': addr.get('AllocationId', addr.get('PublicIp')),
                'public_ip':     addr.get('PublicIp', ''),
                'domain':        addr.get('Domain', 'vpc'),
                'region':        region,
                'monthly_cost':  EIP_MONTHLY_COST_EUR,
            })
        logger.info(f"  {region}: {len(result)} unassociated EIP(s)")
        return result
    except Exception as e:
        logger.warning(f"  {region}: error — {e}")
        return []


class EIPOrphanDetector:

    def __init__(self):
        db_vars = ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing = [v for v in db_vars if not os.getenv(v)]
        if missing:
            raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

        self.conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT')),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            connect_timeout=10
        )

    def detect(self) -> List[Dict[str, Any]]:
        logger.info("Scanning for unassociated Elastic IPs across regions...")
        with ThreadPoolExecutor(max_workers=len(REGIONS)) as executor:
            results = list(executor.map(_fetch_unassociated_eips, REGIONS))
        eips = [e for region_list in results for e in region_list]
        logger.info(f"Total unassociated EIPs found: {len(eips)}")
        return eips

    def save(self, eips: List[Dict[str, Any]]) -> List[int]:
        if not eips:
            return []

        cursor = self.conn.cursor()
        account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
        today = date.today()
        waste_ids = []

        try:
            for eip in eips:
                cursor.execute("""
                    INSERT INTO waste_detected (
                        detection_date, provider, account_id, resource_id,
                        resource_type, waste_type, monthly_waste_eur,
                        confidence_score, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (resource_id, resource_type) DO UPDATE SET
                        detection_date    = EXCLUDED.detection_date,
                        monthly_waste_eur = EXCLUDED.monthly_waste_eur,
                        confidence_score  = EXCLUDED.confidence_score,
                        metadata          = EXCLUDED.metadata,
                        updated_at        = NOW()
                    RETURNING id;
                """, (
                    today,
                    'aws',
                    account_id,
                    eip['allocation_id'],
                    'elastic_ip',
                    'unassociated_ip',
                    eip['monthly_cost'],
                    0.99,  # unambiguous — no association = billing waste
                    json.dumps({
                        'public_ip':        eip['public_ip'],
                        'allocation_id':    eip['allocation_id'],
                        'domain':           eip['domain'],
                        'region':           eip['region'],
                        'monthly_cost_eur': eip['monthly_cost'],
                    })
                ))
                waste_ids.append(cursor.fetchone()[0])

            self.conn.commit()
            logger.info(f"Saved {len(waste_ids)} waste records")
            return waste_ids

        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to save waste records: {e}") from e
        finally:
            cursor.close()

    def recommend(self, waste_ids: List[int]) -> int:
        if not waste_ids:
            return 0

        cursor = self.conn.cursor()
        count = 0

        try:
            cursor.execute("""
                SELECT id, resource_id, monthly_waste_eur, metadata
                FROM waste_detected WHERE id = ANY(%s)
            """, (waste_ids,))

            for row in cursor.fetchall():
                waste_id, allocation_id, monthly_cost, meta = row
                if isinstance(meta, str):
                    meta = json.loads(meta)
                public_ip = meta.get('public_ip', allocation_id)
                region = meta.get('region', '')

                cursor.execute("""
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO NOTHING;
                """, (
                    waste_id,
                    'release_ip',
                    f"RELEASE unassociated Elastic IP {public_ip} ({allocation_id}) in {region} — "
                    f"not attached to any instance or interface",
                    monthly_cost,
                    'pending'
                ))
                count += 1

            self.conn.commit()
            logger.info(f"Created {count} recommendations")
            return count

        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to create recommendations: {e}") from e
        finally:
            cursor.close()

    def run(self) -> None:
        print("\n" + "=" * 70)
        print("ELASTIC IP ORPHAN DETECTION")
        print("=" * 70 + "\n")

        eips = self.detect()

        if not eips:
            print("No unassociated Elastic IPs found.\n")
            return

        total_waste = sum(e['monthly_cost'] for e in eips)
        print(f"Unassociated EIPs found: {len(eips)}")
        print(f"Total monthly waste:     {total_waste:.2f} EUR/mo")
        print(f"Annual waste:            {total_waste * 12:.2f} EUR/year\n")

        for e in eips:
            print(f"  - {e['public_ip']} ({e['allocation_id']}) in {e['region']} "
                  f"→ {e['monthly_cost']:.2f} EUR/mo")

        waste_ids = self.save(eips)
        rec_count = self.recommend(waste_ids)

        print(f"\nRecommendations created: {rec_count}")
        print("View at http://localhost:8888/recommendations\n")

    def close(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()

    def __del__(self):
        self.close()


def main():
    detector = None
    try:
        detector = EIPOrphanDetector()
        detector.run()
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
