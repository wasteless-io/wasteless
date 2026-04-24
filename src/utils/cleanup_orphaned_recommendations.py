#!/usr/bin/env python3
"""
Cleanup Orphaned Recommendations

This script synchronizes the database with the actual state of AWS resources.
It marks recommendations as 'obsolete' when the associated EC2 instance
no longer exists in AWS (e.g., manually terminated).

Use cases:
- Clean up after manual instance termination in AWS Console
- Synchronize database after external infrastructure changes
- Maintenance cleanup before reporting

Usage:
    python src/utils/cleanup_orphaned_recommendations.py
    python src/utils/cleanup_orphaned_recommendations.py --dry-run

Author: Wasteless
"""

import os
import sys
import boto3
import argparse
from datetime import datetime
from typing import Set, Dict, List
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection

load_dotenv()

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RecommendationCleaner:
    """Clean up orphaned recommendations for deleted AWS resources."""

    def __init__(self, dry_run: bool = False):
        """
        Initialize the cleaner.

        Args:
            dry_run: If True, only show what would be cleaned without making changes
        """
        self.dry_run = dry_run
        self.region = os.getenv('AWS_REGION', 'eu-west-1')

        # Initialize AWS client
        self.ec2_client = boto3.client('ec2', region_name=self.region)

        # Database connection
        self.conn = get_db_connection()

        logger.info(f"🔧 Recommendation Cleaner initialized (dry_run={dry_run})")

    def get_all_ec2_instance_ids(self) -> Set[str]:
        """
        Fetch all EC2 instance IDs that currently exist in AWS.

        Returns:
            Set of instance IDs (including all states: running, stopped, terminated, etc.)
        """
        logger.info("Fetching all EC2 instances from AWS...")

        instance_ids = set()

        try:
            # Pagination support for large accounts
            paginator = self.ec2_client.get_paginator('describe_instances')

            for page in paginator.paginate():
                for reservation in page['Reservations']:
                    for instance in reservation['Instances']:
                        instance_id = instance['InstanceId']
                        state = instance['State']['Name']

                        # Include all instances EXCEPT terminated ones
                        # (terminated instances are truly gone after ~1 hour)
                        if state != 'terminated':
                            instance_ids.add(instance_id)
                        else:
                            logger.debug(f"Skipping terminated instance: {instance_id}")

            logger.info(f"✅ Found {len(instance_ids)} EC2 instances in AWS")
            return instance_ids

        except Exception as e:
            logger.error(f"❌ Failed to fetch EC2 instances: {e}")
            raise

    def get_active_recommendations(self) -> List[Dict]:
        """
        Get all active recommendations from database.

        Returns:
            List of dicts with recommendation details
        """
        logger.info("Fetching active recommendations from database...")

        cursor = self.conn.cursor()

        query = """
        SELECT
            r.id as recommendation_id,
            r.waste_id,
            r.status,
            r.recommendation_type,
            w.resource_id,
            w.resource_type,
            w.detection_date
        FROM recommendations r
        JOIN waste_detected w ON r.waste_id = w.id
        WHERE w.resource_type = 'ec2_instance'
          AND r.status NOT IN ('obsolete', 'cancelled')
        ORDER BY w.detection_date DESC;
        """

        cursor.execute(query)
        results = cursor.fetchall()

        recommendations = []
        for row in results:
            recommendations.append({
                'recommendation_id': row[0],
                'waste_id': row[1],
                'status': row[2],
                'recommendation_type': row[3],
                'resource_id': row[4],
                'resource_type': row[5],
                'detection_date': row[6]
            })

        cursor.close()
        logger.info(f"✅ Found {len(recommendations)} active recommendations")

        return recommendations

    def mark_as_obsolete(self, recommendation_ids: List[int]) -> int:
        """
        Mark recommendations as obsolete in database.

        Args:
            recommendation_ids: List of recommendation IDs to mark

        Returns:
            Number of recommendations updated
        """
        if not recommendation_ids:
            return 0

        if self.dry_run:
            logger.info(f"[DRY-RUN] Would mark {len(recommendation_ids)} recommendations as obsolete")
            return len(recommendation_ids)

        cursor = self.conn.cursor()

        try:
            # Update recommendations status to 'obsolete'
            query = """
            UPDATE recommendations
            SET status = 'obsolete'
            WHERE id = ANY(%s);
            """

            cursor.execute(query, (recommendation_ids,))
            updated_count = cursor.rowcount

            self.conn.commit()
            logger.info(f"✅ Marked {updated_count} recommendations as obsolete")

            return updated_count

        except Exception as e:
            self.conn.rollback()
            logger.error(f"❌ Failed to update recommendations: {e}")
            raise
        finally:
            cursor.close()

    def run(self):
        """
        Main orchestration method.
        Compares AWS state with database and cleans up orphaned recommendations.
        """
        print("\n" + "=" * 70)
        print("🧹 CLEANUP ORPHANED RECOMMENDATIONS")
        print("=" * 70)
        print(f"Mode: {'DRY-RUN (no changes will be made)' if self.dry_run else 'LIVE (will update database)'}")
        print(f"Region: {self.region}")
        print("=" * 70 + "\n")

        # Step 1: Get all EC2 instances from AWS
        aws_instance_ids = self.get_all_ec2_instance_ids()

        # Step 2: Get all active recommendations from database
        recommendations = self.get_active_recommendations()

        # Step 3: Find orphaned recommendations (instance doesn't exist in AWS)
        orphaned_recommendations = []

        print("\n" + "=" * 70)
        print("🔍 CHECKING RECOMMENDATIONS")
        print("=" * 70)

        for rec in recommendations:
            instance_id = rec['resource_id']

            if instance_id not in aws_instance_ids:
                orphaned_recommendations.append(rec)
                logger.warning(
                    f"⚠️  Orphaned: {instance_id} (recommendation #{rec['recommendation_id']}, "
                    f"status: {rec['status']}, type: {rec['recommendation_type']})"
                )
            else:
                logger.debug(f"✅ Valid: {instance_id} exists in AWS")

        # Step 4: Summary
        print("\n" + "=" * 70)
        print("📊 CLEANUP SUMMARY")
        print("=" * 70)
        print(f"Total active recommendations: {len(recommendations)}")
        print(f"Valid recommendations: {len(recommendations) - len(orphaned_recommendations)}")
        print(f"Orphaned recommendations: {len(orphaned_recommendations)}")
        print("=" * 70)

        if orphaned_recommendations:
            print("\n🗑️  ORPHANED RECOMMENDATIONS DETAILS:")
            print("-" * 70)
            for rec in orphaned_recommendations:
                print(f"  • Instance: {rec['resource_id']}")
                print(f"    Recommendation ID: {rec['recommendation_id']}")
                print(f"    Status: {rec['status']}")
                print(f"    Type: {rec['recommendation_type']}")
                print(f"    Detection Date: {rec['detection_date']}")
                print()

        # Step 5: Mark as obsolete
        if orphaned_recommendations:
            orphaned_ids = [rec['recommendation_id'] for rec in orphaned_recommendations]
            updated_count = self.mark_as_obsolete(orphaned_ids)

            print("=" * 70)
            if self.dry_run:
                print(f"[DRY-RUN] Would mark {len(orphaned_ids)} recommendations as obsolete")
                print("Run without --dry-run to apply changes")
            else:
                print(f"✅ Successfully marked {updated_count} recommendations as obsolete")
            print("=" * 70)
        else:
            print("\n✅ No orphaned recommendations found!")
            print("   All recommendations are in sync with AWS state.")

        print("\n✅ Cleanup completed!\n")

    def __del__(self):
        """Close database connection on cleanup."""
        if hasattr(self, 'conn'):
            self.conn.close()


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description='Clean up orphaned recommendations for deleted AWS resources'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be cleaned without making changes'
    )

    args = parser.parse_args()

    try:
        cleaner = RecommendationCleaner(dry_run=args.dry_run)
        cleaner.run()
    except KeyboardInterrupt:
        print("\n\n⚠️  Cleanup interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
