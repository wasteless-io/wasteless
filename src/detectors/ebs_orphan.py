#!/usr/bin/env python3
"""
EBS Orphan Volume Detector for Wasteless

Detects EBS volumes in 'available' state (not attached to any instance).
These volumes continue to be billed but provide no value.

Detection criteria:
- Volume state == 'available' (not attached)

Pricing: gp3 = 0.08 USD/GiB/month, gp2 = 0.10, io1/io2 = 0.125
"""

import os
import sys
import json
import logging
from datetime import date, datetime, timezone
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
import psycopg2
from psycopg2 import DatabaseError, OperationalError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# EBS pricing USD/GiB/month (eu-west-1) × 0.92 EUR/USD
EBS_PRICING_EUR_PER_GIB: Dict[str, float] = {
    'gp3':  0.0736,
    'gp2':  0.0920,
    'io1':  0.1150,
    'io2':  0.1150,
    'st1':  0.0460,
    'sc1':  0.0230,
    'standard': 0.0552,
}
DEFAULT_EBS_PRICE = 0.0920  # gp2 fallback

REGIONS = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']


def _volume_monthly_cost(size_gb: int, vol_type: str) -> float:
    price = EBS_PRICING_EUR_PER_GIB.get(vol_type, DEFAULT_EBS_PRICE)
    return round(size_gb * price, 2)


def _fetch_orphaned_volumes(region: str) -> List[Dict[str, Any]]:
    try:
        import boto3
        ec2 = boto3.client('ec2', region_name=region)
        response = ec2.describe_volumes(
            Filters=[{'Name': 'status', 'Values': ['available']}]
        )
        result = []
        for vol in response.get('Volumes', []):
            name = next(
                (t['Value'] for t in vol.get('Tags', []) if t['Key'] == 'Name'),
                ''
            )
            vol_type = vol.get('VolumeType', 'gp2')
            size_gb = vol.get('Size', 0)
            create_time = vol.get('CreateTime')
            if create_time:
                if create_time.tzinfo is None:
                    create_time = create_time.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - create_time).days
            else:
                age_days = None
            result.append({
                'volume_id':   vol['VolumeId'],
                'name':        name,
                'size_gb':     size_gb,
                'vol_type':    vol_type,
                'az':          vol.get('AvailabilityZone', region),
                'region':      region,
                'encrypted':   vol.get('Encrypted', False),
                'monthly_cost': _volume_monthly_cost(size_gb, vol_type),
                'age_days':    age_days,
            })
        logger.info(f"  {region}: {len(result)} orphaned volume(s)")
        return result
    except Exception as e:
        logger.warning(f"  {region}: error — {e}")
        return []


class EBSOrphanDetector:

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
        logger.info("Scanning for orphaned EBS volumes across regions...")
        with ThreadPoolExecutor(max_workers=len(REGIONS)) as executor:
            results = list(executor.map(_fetch_orphaned_volumes, REGIONS))
        volumes = [v for region_list in results for v in region_list]
        logger.info(f"Total orphaned volumes found: {len(volumes)}")
        return volumes

    def save(self, volumes: List[Dict[str, Any]]) -> List[int]:
        if not volumes:
            return []

        cursor = self.conn.cursor()
        account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
        today = date.today()
        waste_ids = []

        try:
            for vol in volumes:
                label = vol['name'] or vol['volume_id']
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
                    vol['volume_id'],
                    'ebs_volume',
                    'orphaned_volume',
                    vol['monthly_cost'],
                    0.95,  # high confidence — state=available is unambiguous
                    json.dumps({
                        'name':             vol['name'],
                        'size_gb':          vol['size_gb'],
                        'vol_type':         vol['vol_type'],
                        'az':               vol['az'],
                        'region':           vol['region'],
                        'encrypted':        vol['encrypted'],
                        'monthly_cost_eur': vol['monthly_cost'],
                        'age_days':         vol.get('age_days'),
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
                waste_id, volume_id, monthly_cost, meta = row
                if isinstance(meta, str):
                    meta = json.loads(meta)
                size_gb = meta.get('size_gb', '?')
                vol_type = meta.get('vol_type', '')
                region = meta.get('region', '')
                name = meta.get('name', '')
                label = f"{name} ({volume_id})" if name else volume_id

                cursor.execute("""
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO NOTHING;
                """, (
                    waste_id,
                    'delete_volume',
                    f"DELETE orphaned EBS volume {label} — "
                    f"{size_gb} GiB {vol_type} in {region}, "
                    f"not attached to any instance",
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
        print("EBS ORPHAN VOLUME DETECTION")
        print("=" * 70 + "\n")

        volumes = self.detect()

        if not volumes:
            print("No orphaned volumes found — nothing to clean up.\n")
            return

        total_waste = sum(v['monthly_cost'] for v in volumes)
        print(f"\nOrphaned volumes found: {len(volumes)}")
        print(f"Total monthly waste:    {total_waste:.2f} EUR/mo")
        print(f"Annual waste:           {total_waste * 12:.2f} EUR/year\n")

        for v in volumes:
            label = v['name'] or v['volume_id']
            print(f"  - {label}: {v['size_gb']} GiB {v['vol_type']} "
                  f"({v['region']}) → {v['monthly_cost']:.2f} EUR/mo")

        waste_ids = self.save(volumes)
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
        detector = EBSOrphanDetector()
        detector.run()
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
