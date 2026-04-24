#!/usr/bin/env python3
"""
EBS Snapshot Orphan Detector for Wasteless

Detects old EBS snapshots (> 90 days) owned by this account.
These snapshots accumulate over time and are often forgotten.

Detection criteria:
- Owner = self
- Age > SNAPSHOT_AGE_DAYS (default: 90)

Pricing: $0.05/GiB/month (eu-west-1) * 0.92 EUR/USD = 0.046 EUR/GiB/month
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SNAPSHOT_PRICE_EUR_PER_GIB = 0.046  # $0.05 * 0.92 EUR/USD
SNAPSHOT_AGE_DAYS = 90
REGIONS = ['eu-west-1', 'eu-west-2', 'eu-west-3', 'us-east-1']


def _snapshot_monthly_cost(size_gb: int) -> float:
    return round(size_gb * SNAPSHOT_PRICE_EUR_PER_GIB, 2)


def _fetch_ami_snapshot_ids(ec2_client) -> set:
    """Return snapshot IDs that back a registered AMI — must not be deleted."""
    ami_snap_ids = set()
    for image in ec2_client.describe_images(Owners=['self']).get('Images', []):
        for mapping in image.get('BlockDeviceMappings', []):
            snap_id = mapping.get('Ebs', {}).get('SnapshotId')
            if snap_id:
                ami_snap_ids.add(snap_id)
    return ami_snap_ids


def _fetch_old_snapshots(region: str) -> List[Dict[str, Any]]:
    try:
        import boto3
        ec2 = boto3.client('ec2', region_name=region)
        now = datetime.now(timezone.utc)

        # Exclude snapshots still backing a registered AMI
        ami_snap_ids = _fetch_ami_snapshot_ids(ec2)

        result = []
        skipped = 0

        for snap in ec2.describe_snapshots(OwnerIds=['self']).get('Snapshots', []):
            snap_id = snap['SnapshotId']

            if snap_id in ami_snap_ids:
                skipped += 1
                continue  # Still backing a registered AMI — skip

            start_time = snap.get('StartTime')
            if not start_time:
                continue

            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)

            age_days = (now - start_time).days
            if age_days < SNAPSHOT_AGE_DAYS:
                continue

            size_gb = snap.get('VolumeSize', 0)
            result.append({
                'snapshot_id':  snap_id,
                'description':  snap.get('Description') or '',
                'volume_id':    snap.get('VolumeId') or '',
                'size_gb':      size_gb,
                'state':        snap.get('State', ''),
                'start_time':   start_time.isoformat(),
                'age_days':     age_days,
                'encrypted':    snap.get('Encrypted', False),
                'region':       region,
                'monthly_cost': _snapshot_monthly_cost(size_gb),
            })

        logger.info(f"  {region}: {len(result)} old snapshot(s) (>{SNAPSHOT_AGE_DAYS}d), "
                    f"{skipped} skipped (AMI-backed)")
        return result, ami_snap_ids
    except Exception as e:
        logger.warning(f"  {region}: error — {e}")
        return [], set()


class SnapshotOrphanDetector:

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

    def detect(self) -> tuple:
        logger.info(f"Scanning for old EBS snapshots (>{SNAPSHOT_AGE_DAYS} days) across regions...")
        with ThreadPoolExecutor(max_workers=len(REGIONS)) as executor:
            results = list(executor.map(_fetch_old_snapshots, REGIONS))
        snapshots = [s for snaps, _ in results for s in snaps]
        all_ami_snap_ids = set().union(*[ids for _, ids in results])
        logger.info(f"Total old snapshots found: {len(snapshots)} "
                    f"({len(all_ami_snap_ids)} AMI-backed excluded)")
        return snapshots, all_ami_snap_ids

    def save(self, snapshots: List[Dict[str, Any]]) -> List[int]:
        if not snapshots:
            return []

        cursor = self.conn.cursor()
        account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
        today = date.today()
        waste_ids = []

        try:
            for snap in snapshots:
                # Confidence increases with age: 90d=0.60, 180d=0.75, 365d=0.90
                age = snap['age_days']
                confidence = min(0.90, 0.60 + (age - SNAPSHOT_AGE_DAYS) / 1000)
                confidence = round(confidence, 2)

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
                    snap['snapshot_id'],
                    'ebs_snapshot',
                    'old_snapshot',
                    snap['monthly_cost'],
                    confidence,
                    json.dumps({
                        'description':      snap['description'],
                        'volume_id':        snap['volume_id'],
                        'size_gb':          snap['size_gb'],
                        'state':            snap['state'],
                        'start_time':       snap['start_time'],
                        'age_days':         snap['age_days'],
                        'encrypted':        snap['encrypted'],
                        'region':           snap['region'],
                        'monthly_cost_eur': snap['monthly_cost'],
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
                waste_id, snapshot_id, monthly_cost, meta = row
                if isinstance(meta, str):
                    meta = json.loads(meta)
                size_gb = meta.get('size_gb', '?')
                age_days = meta.get('age_days', '?')
                region = meta.get('region', '')
                desc = meta.get('description', '')
                label = f"{snapshot_id}" + (f" ({desc[:40]})" if desc else "")

                cursor.execute("""
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO NOTHING;
                """, (
                    waste_id,
                    'delete_snapshot',
                    f"DELETE old snapshot {label} — "
                    f"{size_gb} GiB, {age_days} days old, in {region}",
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

    def mark_ami_backed_obsolete(self, ami_snap_ids: set) -> int:
        """Mark pending recommendations obsolete for snapshots backing a registered AMI."""
        if not ami_snap_ids:
            return 0
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                UPDATE recommendations r SET status = 'obsolete'
                FROM waste_detected w
                WHERE r.waste_id = w.id
                  AND w.resource_type = 'ebs_snapshot'
                  AND w.resource_id = ANY(%s)
                  AND r.status = 'pending'
            """, (list(ami_snap_ids),))
            count = cursor.rowcount
            self.conn.commit()
            if count:
                logger.info(f"Marked {count} AMI-backed snapshot recommendation(s) as obsolete")
            return count
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"Failed to cleanup AMI-backed snapshots: {e}")
            return 0
        finally:
            cursor.close()

    def run(self) -> None:
        print("\n" + "=" * 70)
        print(f"EBS SNAPSHOT DETECTION (>{SNAPSHOT_AGE_DAYS} DAYS OLD)")
        print("=" * 70 + "\n")

        snapshots, ami_snap_ids = self.detect()

        # Clean up any previously-saved recs for AMI-backed snapshots
        cleaned = self.mark_ami_backed_obsolete(ami_snap_ids)
        if cleaned:
            print(f"Cleaned up {cleaned} obsolete recommendation(s) (AMI-backed snapshots)\n")

        if not snapshots:
            print(f"No snapshots older than {SNAPSHOT_AGE_DAYS} days found.\n")
            return

        total_waste = sum(s['monthly_cost'] for s in snapshots)
        print(f"Old snapshots found:  {len(snapshots)}")
        print(f"Total monthly waste:  {total_waste:.2f} EUR/mo")
        print(f"Annual waste:         {total_waste * 12:.2f} EUR/year\n")

        for s in snapshots:
            label = s['snapshot_id']
            if s['description']:
                label += f" ({s['description'][:30]})"
            print(f"  - {label}: {s['size_gb']} GiB, {s['age_days']}d old "
                  f"({s['region']}) → {s['monthly_cost']:.2f} EUR/mo")

        waste_ids = self.save(snapshots)
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
        detector = SnapshotOrphanDetector()
        detector.run()
    except Exception as e:
        logger.error(f"Detection failed: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
