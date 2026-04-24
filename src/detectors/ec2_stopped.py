#!/usr/bin/env python3
"""
EC2 Stopped Instance Detector for Wasteless

Detects EC2 instances that have been in 'stopped' state for more than
STOPPED_DAYS days. Stopped instances still incur EBS storage costs.

Detection criteria:
- instance_state = 'stopped' for all datapoints in the last STOPPED_DAYS
- Fetches actual attached EBS volumes via boto3 to calculate real cost

Recommendation: terminate_instance (already stopped, no compute savings needed)
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

STOPPED_DAYS = 7  # minimum days stopped before flagging

# EBS pricing EUR/GiB/month (eu-west-1)
EBS_PRICING_EUR_PER_GIB: Dict[str, float] = {
    'gp3': 0.0736, 'gp2': 0.0920,
    'io1': 0.1150, 'io2': 0.1150,
    'st1': 0.0460, 'sc1': 0.0230,
    'standard': 0.0552,
}
DEFAULT_EBS_PRICE = 0.0920

REGIONS = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']


def _ebs_cost(size_gb: int, vol_type: str) -> float:
    price = EBS_PRICING_EUR_PER_GIB.get(vol_type, DEFAULT_EBS_PRICE)
    return round(size_gb * price, 2)


def _fetch_ebs_cost_for_instance(instance_id: str, region: str) -> Dict[str, Any]:
    """Return total EBS cost and volume details for a stopped instance."""
    try:
        import boto3
        ec2 = boto3.client('ec2', region_name=region)

        # Get block device mappings
        resp = ec2.describe_instances(
            Filters=[{'Name': 'instance-id', 'Values': [instance_id]}]
        )
        reservations = resp.get('Reservations', [])
        if not reservations:
            return {'found': False}

        instance = reservations[0]['Instances'][0]
        launch_time = instance.get('LaunchTime')
        if launch_time:
            if launch_time.tzinfo is None:
                launch_time = launch_time.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - launch_time).days
        else:
            age_days = None

        volume_ids = [
            bdm['Ebs']['VolumeId']
            for bdm in instance.get('BlockDeviceMappings', [])
            if 'Ebs' in bdm
        ]

        if not volume_ids:
            return {'found': True, 'region': region, 'ebs_cost': 0, 'volumes': [], 'age_days': age_days}

        # Get volume details
        vol_resp = ec2.describe_volumes(VolumeIds=volume_ids)
        volumes = []
        total_cost = 0.0
        for vol in vol_resp.get('Volumes', []):
            size = vol['Size']
            vol_type = vol['VolumeType']
            cost = _ebs_cost(size, vol_type)
            total_cost += cost
            volumes.append({
                'volume_id': vol['VolumeId'],
                'size_gb': size,
                'vol_type': vol_type,
                'cost': cost,
            })

        return {
            'found': True,
            'region': region,
            'ebs_cost': round(total_cost, 2),
            'volumes': volumes,
            'age_days': age_days,
        }

    except Exception as e:
        logger.warning(f"  Could not fetch EBS for {instance_id} in {region}: {e}")
        return {'found': False}


class EC2StoppedDetector:

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
        """Find instances stopped for >= STOPPED_DAYS days and calculate EBS cost."""
        logger.info(f"Scanning for instances stopped >= {STOPPED_DAYS} days...")

        cursor = self.conn.cursor()
        try:
            # Instances where every datapoint in the window shows 'stopped'
            cursor.execute("""
                SELECT instance_id, instance_type, COUNT(*) as datapoints
                FROM ec2_metrics
                WHERE collection_date >= CURRENT_DATE - %s::interval
                GROUP BY instance_id, instance_type
                HAVING COUNT(*) > 0
                   AND COUNT(*) = COUNT(CASE WHEN instance_state = 'stopped' THEN 1 END)
                ORDER BY instance_id
            """, (f'{STOPPED_DAYS} days',))
            stopped = cursor.fetchall()
        finally:
            cursor.close()

        if not stopped:
            logger.info("No stopped instances found")
            return []

        logger.info(f"Found {len(stopped)} stopped instance(s), fetching EBS info...")

        # Resolve actual EBS cost from AWS (parallel per instance × region)
        def _resolve(row):
            instance_id, instance_type, datapoints = row['instance_id'], row['instance_type'], row['datapoints']
            for region in REGIONS:
                info = _fetch_ebs_cost_for_instance(instance_id, region)
                if info.get('found'):
                    return {
                        'instance_id':   instance_id,
                        'instance_type': instance_type,
                        'datapoints':    datapoints,
                        'region':        info['region'],
                        'ebs_cost':      info['ebs_cost'],
                        'volumes':       info['volumes'],
                        'age_days':      info.get('age_days'),
                    }
            # Instance not found in any region (may have been terminated already)
            logger.info(f"  {instance_id}: not found in any region, skipping")
            return None

        # Use dict-style access for psycopg2 RealDictRow
        results = []
        with ThreadPoolExecutor(max_workers=min(len(stopped), 8)) as executor:
            futures = [executor.submit(_resolve, row) for row in stopped]
            for f in futures:
                result = f.result()
                if result and result['ebs_cost'] > 0:
                    results.append(result)

        logger.info(f"Stopped instances with EBS cost: {len(results)}")
        return results

    def save(self, instances: List[Dict[str, Any]]) -> List[int]:
        if not instances:
            return []

        cursor = self.conn.cursor()
        account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
        today = date.today()
        waste_ids = []

        try:
            for inst in instances:
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
                    inst['instance_id'],
                    'ec2_instance',
                    'stopped_instance',
                    inst['ebs_cost'],
                    0.95,  # high confidence — state=stopped is explicit
                    json.dumps({
                        'instance_type':    inst['instance_type'],
                        'instance_state':   'stopped',
                        'region':           inst['region'],
                        'ebs_cost':         inst['ebs_cost'],
                        'monthly_cost_eur': inst['ebs_cost'],
                        'volumes':          inst['volumes'],
                        'days_stopped':     inst['datapoints'],
                        'age_days':         inst.get('age_days'),
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
                waste_id, instance_id, ebs_cost, meta = row
                if isinstance(meta, str):
                    meta = json.loads(meta)
                itype = meta.get('instance_type', '')
                region = meta.get('region', '')
                days = meta.get('days_stopped', STOPPED_DAYS)
                vol_count = len(meta.get('volumes', []))

                cursor.execute("""
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO NOTHING;
                """, (
                    waste_id,
                    'terminate_instance',
                    f"TERMINATE stopped instance {instance_id} ({itype}) in {region} — "
                    f"stopped for >= {days} days, still billing {vol_count} EBS volume(s) "
                    f"at {ebs_cost:.2f} EUR/mo",
                    ebs_cost,
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
        print(f"EC2 STOPPED INSTANCE DETECTION (>= {STOPPED_DAYS} DAYS)")
        print("=" * 70 + "\n")

        instances = self.detect()

        if not instances:
            print(f"No instances stopped for >= {STOPPED_DAYS} days.\n")
            return

        total_waste = sum(i['ebs_cost'] for i in instances)
        print(f"Stopped instances found: {len(instances)}")
        print(f"Total EBS waste:         {total_waste:.2f} EUR/mo")
        print(f"Annual waste:            {total_waste * 12:.2f} EUR/year\n")

        for inst in instances:
            vol_info = f"{len(inst['volumes'])} volume(s)"
            print(f"  - {inst['instance_id']} ({inst['instance_type']}) "
                  f"in {inst['region']} — {vol_info} → {inst['ebs_cost']:.2f} EUR/mo")

        waste_ids = self.save(instances)
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
        detector = EC2StoppedDetector()
        detector.run()
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
