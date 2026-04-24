#!/usr/bin/env python3
"""
EC2 Remediator for Wasteless

Executes remediation actions on EC2 instances:
- Stop idle instances
- Start instances (rollback)
- Terminate instances (future)

WITH COMPREHENSIVE SAFEGUARDS

Author: Wasteless
"""

import os
import sys
import boto3
import json
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime objects."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.safeguards import Safeguards, SafeguardException
from core.database import get_db_connection

load_dotenv()

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EC2Remediator:
    """Execute and track EC2 remediation actions."""
    
    def __init__(self, dry_run: bool = True):
        """
        Initialize EC2 remediator.
        
        Args:
            dry_run: If True, no actual AWS actions taken
        """
        logger.info("Initializing EC2 Remediator")
        
        self.dry_run = dry_run
        self.region = os.getenv('AWS_REGION')
        self.account_id = os.getenv('AWS_ACCOUNT_ID')

        # Initialize AWS client
        # Use boto3 default credential provider chain (IAM role / env vars)
        self.ec2_client = boto3.client(
            'ec2',
            region_name=self.region
        )

        logger.debug("Using boto3 default credential provider (IAM role / env vars)")
        
        # Initialize safeguards
        self.safeguards = Safeguards()
        
        # Database connection
        self.conn = get_db_connection()

        # Advisory lock ID for remediator (unique per account)
        # Using hash of account_id to get consistent integer
        self.lock_id = hash(self.account_id) % (2**31)

        logger.info(f"✅ EC2 Remediator initialized (dry_run={dry_run})")

    def acquire_lock(self, timeout_seconds: int = 5) -> bool:
        """
        Acquire distributed lock using PostgreSQL advisory lock.
        Prevents concurrent remediation runs.

        Args:
            timeout_seconds: How long to wait for lock

        Returns:
            True if lock acquired, False if another process holds it
        """
        try:
            cursor = self.conn.cursor()

            # Try to acquire lock with timeout
            cursor.execute("""
                SELECT pg_try_advisory_lock(%s);
            """, (self.lock_id,))

            acquired = cursor.fetchone()[0]
            cursor.close()

            if acquired:
                logger.info(f"🔒 Distributed lock acquired (ID: {self.lock_id})")
            else:
                logger.warning(
                    f"⚠️  Could not acquire lock - another remediator is running"
                )

            return acquired

        except Exception as e:
            logger.error(f"Failed to acquire lock: {e}")
            return False

    def release_lock(self):
        """Release distributed lock."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT pg_advisory_unlock(%s);
            """, (self.lock_id,))

            released = cursor.fetchone()[0]
            cursor.close()

            if released:
                logger.info(f"🔓 Distributed lock released (ID: {self.lock_id})")
            else:
                logger.warning(f"⚠️  Lock was not held (ID: {self.lock_id})")

        except Exception as e:
            logger.error(f"Failed to release lock: {e}")

    def get_instance_details(self, instance_id: str) -> Optional[Dict]:
        """
        Get full instance details from AWS.
        
        Args:
            instance_id: EC2 instance ID
            
        Returns:
            Dict with instance details or None
        """
        try:
            response = self.ec2_client.describe_instances(
                InstanceIds=[instance_id]
            )
            
            if not response['Reservations']:
                logger.error(f"Instance {instance_id} not found")
                return None
            
            instance = response['Reservations'][0]['Instances'][0]
            
            # Extract tags
            tags = {}
            if 'Tags' in instance:
                for tag in instance['Tags']:
                    tags[tag['Key']] = tag['Value']
            
            details = {
                'instance_id': instance['InstanceId'],
                'instance_type': instance['InstanceType'],
                'state': instance['State']['Name'],
                'launch_time': instance['LaunchTime'],
                'availability_zone': instance['Placement']['AvailabilityZone'],
                'tags': tags,
                'raw': instance  # Full response for snapshot
            }
            
            return details
            
        except Exception as e:
            logger.error(f"Failed to get instance details: {e}")
            return None
    
    def create_rollback_snapshot(
        self,
        action_log_id: int,
        instance_id: str,
        instance_details: Dict
    ) -> int:
        """
        Create snapshot of instance state for rollback.
        
        Args:
            action_log_id: ID from actions_log table
            instance_id: EC2 instance ID
            instance_details: Full instance details
            
        Returns:
            Snapshot ID
        """
        cursor = self.conn.cursor()
        
        # Rollback expires after 7 days
        expiry = datetime.now() + timedelta(days=7)
        
        cursor.execute("""
            INSERT INTO rollback_snapshots (
                action_log_id, resource_id, resource_type,
                state_before, can_rollback, rollback_expiry
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            action_log_id,
            instance_id,
            'ec2_instance',
            json.dumps(instance_details, cls=DateTimeEncoder),
            True,
            expiry
        ))
        
        snapshot_id = cursor.fetchone()[0]
        self.conn.commit()
        
        logger.info(f"📸 Rollback snapshot created: {snapshot_id}")
        
        cursor.close()
        return snapshot_id
    
    def log_action(
        self,
        recommendation_id: int,
        resource_id: str,
        action_type: str,
        status: str,
        metadata: Dict = None,
        error_message: str = None
    ) -> int:
        """
        Log remediation action to database.
        
        Args:
            recommendation_id: ID from recommendations table
            resource_id: EC2 instance ID
            action_type: 'stop', 'start', 'terminate'
            status: 'pending', 'success', 'failed'
            metadata: Additional info (JSON)
            error_message: Error if failed
            
        Returns:
            Action log ID
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            INSERT INTO actions_log (
                recommendation_id, resource_id, resource_type,
                action_type, action_status, dry_run,
                metadata, error_message, executed_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            recommendation_id,
            resource_id,
            'ec2_instance',
            action_type,
            status,
            self.dry_run,
            json.dumps(metadata, cls=DateTimeEncoder) if metadata else None,
            error_message,
            'system'
        ))
        
        action_id = cursor.fetchone()[0]
        self.conn.commit()
        
        cursor.close()
        return action_id
    
    def stop_instance(
        self,
        instance_id: str,
        recommendation_id: int,
        reason: str = "Idle instance detected"
    ) -> Dict:
        """
        Stop an EC2 instance (main remediation action).
        
        Args:
            instance_id: EC2 instance ID
            recommendation_id: ID from recommendations table
            reason: Why we're stopping
            
        Returns:
            Dict with execution results
        """
        logger.info(f"🎯 Attempting to stop instance: {instance_id}")
        
        result = {
            'success': False,
            'instance_id': instance_id,
            'action': 'stop',
            'dry_run': self.dry_run,
            'action_log_id': None,
            'error': None
        }
        
        try:
            # Step 1: Get instance details
            instance_details = self.get_instance_details(instance_id)
            if not instance_details:
                raise Exception("Instance not found")
            
            # Step 2: Run safeguard checks
            # (Get additional data from database)
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT
                    w.confidence_score,
                    -- Calculate idle days: days since waste was first detected
                    -- This represents how long the instance has been idle (not metric age)
                    EXTRACT(DAY FROM (CURRENT_DATE - w.detection_date))::int as idle_days
                FROM waste_detected w
                JOIN recommendations r ON r.waste_id = w.id
                WHERE r.id = %s;
            """, (recommendation_id,))
            
            row = cursor.fetchone()
            if not row:
                raise Exception("Recommendation not found in database")
            
            confidence, idle_days = row
            cursor.close()
            
            # Run safeguards
            safeguard_result = self.safeguards.validate_all(
                instance_id=instance_id,
                instance_tags=instance_details['tags'],
                launch_time=instance_details['launch_time'],
                confidence=float(confidence),
                idle_days=idle_days or 0,
                current_count=0  # TODO: Track per run
            )
            
            if not safeguard_result['safe_to_proceed']:
                raise SafeguardException(
                    f"Safeguard check failed: {safeguard_result['reason']}"
                )
            
            # Step 3: Log action (pending)
            action_metadata = {
                'reason': reason,
                'instance_type': instance_details['instance_type'],
                'instance_state_before': instance_details['state'],
                'safeguard_checks': safeguard_result['checks_passed'],
                'confidence': float(confidence),
                'idle_days': idle_days
            }
            
            action_log_id = self.log_action(
                recommendation_id=recommendation_id,
                resource_id=instance_id,
                action_type='stop',
                status='pending',
                metadata=action_metadata
            )
            
            result['action_log_id'] = action_log_id
            
            # Step 4: Create rollback snapshot
            snapshot_id = self.create_rollback_snapshot(
                action_log_id=action_log_id,
                instance_id=instance_id,
                instance_details=instance_details
            )
            
            # Step 5: Execute AWS action
            if self.dry_run:
                logger.info(
                    f"🧪 [DRY-RUN] Would stop instance {instance_id} "
                    f"(type: {instance_details['instance_type']})"
                )
            else:
                logger.info(f"⚡ STOPPING instance {instance_id}")
                
                response = self.ec2_client.stop_instances(
                    InstanceIds=[instance_id],
                    DryRun=False
                )
                
                logger.info(f"✅ Instance {instance_id} stopped successfully")
                logger.info(f"   Previous state: {instance_details['state']}")
                logger.info(f"   New state: stopping")
            
            # Step 6 & 7: Update action log and recommendation in single transaction
            cursor = self.conn.cursor()
            try:
                # Update action log status
                cursor.execute("""
                    UPDATE actions_log
                    SET action_status = 'success',
                        updated_at = NOW()
                    WHERE id = %s;
                """, (action_log_id,))

                # Update recommendation status
                cursor.execute("""
                    UPDATE recommendations
                    SET status = 'applied',
                        applied_at = NOW()
                    WHERE id = %s;
                """, (recommendation_id,))

                # Commit both updates in single transaction
                self.conn.commit()
            finally:
                cursor.close()
            
            result['success'] = True
            
        except SafeguardException as e:
            logger.warning(f"🛡️  Safeguard prevented action: {e}")
            result['error'] = str(e)
            
            if result['action_log_id']:
                self._update_action_status(
                    result['action_log_id'],
                    'blocked',
                    error_message=str(e)
                )
        
        except Exception as e:
            logger.error(f"❌ Failed to stop instance: {e}")
            result['error'] = str(e)
            
            if result['action_log_id']:
                self._update_action_status(
                    result['action_log_id'],
                    'failed',
                    error_message=str(e)
                )
        
        return result
    
    def start_instance(
        self,
        instance_id: str,
        reason: str = "Manual rollback"
    ) -> Dict:
        """
        Start an EC2 instance (rollback action).
        
        Args:
            instance_id: EC2 instance ID
            reason: Why we're starting
            
        Returns:
            Dict with execution results
        """
        logger.info(f"🔄 Rolling back (starting) instance: {instance_id}")
        
        result = {
            'success': False,
            'instance_id': instance_id,
            'action': 'start',
            'error': None
        }
        
        try:
            # Get instance details
            instance_details = self.get_instance_details(instance_id)
            if not instance_details:
                raise Exception("Instance not found")
            
            # Check current state
            if instance_details['state'] != 'stopped':
                raise Exception(
                    f"Instance is {instance_details['state']}, "
                    f"cannot start (must be stopped)"
                )
            
            # Execute AWS action
            if self.dry_run:
                logger.info(f"🧪 [DRY-RUN] Would start instance {instance_id}")
            else:
                logger.info(f"⚡ STARTING instance {instance_id}")
                
                response = self.ec2_client.start_instances(
                    InstanceIds=[instance_id],
                    DryRun=False
                )
                
                logger.info(f"✅ Instance {instance_id} started successfully")
            
            # Log rollback action
            action_log_id = self.log_action(
                recommendation_id=None,  # Rollback, no recommendation
                resource_id=instance_id,
                action_type='start',
                status='success',
                metadata={'reason': reason, 'rollback': True}
            )
            
            result['success'] = True
            result['action_log_id'] = action_log_id
            
        except Exception as e:
            logger.error(f"❌ Failed to start instance: {e}")
            result['error'] = str(e)
        
        return result
    
    def _update_action_status(
        self,
        action_log_id: int,
        status: str,
        error_message: str = None
    ):
        """Update action log status."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE actions_log
            SET action_status = %s,
                error_message = %s,
                updated_at = NOW()
            WHERE id = %s;
        """, (status, error_message, action_log_id))
        self.conn.commit()
        cursor.close()
    
    def process_pending_recommendations(self, limit: int = 10) -> List[Dict]:
        """
        Process all pending stop recommendations.
        Uses distributed locking to prevent concurrent execution.

        Args:
            limit: Max recommendations to process

        Returns:
            List of execution results
        """
        # Acquire distributed lock
        if not self.acquire_lock():
            logger.error(
                "❌ Cannot process recommendations - another remediator is running"
            )
            return []

        try:
            logger.info("🔍 Fetching pending recommendations...")

            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT
                    r.id as recommendation_id,
                    r.action_required,
                    r.estimated_monthly_savings_eur,
                    w.resource_id,
                    w.confidence_score
                FROM recommendations r
                JOIN waste_detected w ON r.waste_id = w.id
                WHERE r.status = 'pending'
                  AND r.recommendation_type = 'stop_instance'
                ORDER BY r.estimated_monthly_savings_eur DESC
                LIMIT %s;
            """, (limit,))

            pending = cursor.fetchall()
            cursor.close()

            logger.info(f"Found {len(pending)} pending recommendations")

            results = []

            for row in pending:
                rec_id, action, savings, instance_id, confidence = row

                logger.info(
                    f"\n{'='*60}\n"
                    f"Processing recommendation {rec_id}\n"
                    f"Instance: {instance_id}\n"
                    f"Potential savings: €{savings:.2f}/month\n"
                    f"Confidence: {confidence:.2f}\n"
                    f"{'='*60}"
                )

                result = self.stop_instance(
                    instance_id=instance_id,
                    recommendation_id=rec_id,
                    reason=action
                )

                results.append(result)

                # Respect max_instances_per_run limit
                if len([r for r in results if r['success']]) >= 3:
                    logger.info("⚠️  Max instances per run reached (3)")
                    break

            return results

        finally:
            # Always release lock
            self.release_lock()
    
    def __del__(self):
        """Close database connection and release lock."""
        if hasattr(self, 'conn'):
            # Try to release lock if held
            try:
                self.release_lock()
            except:
                pass  # Ignore errors during cleanup

            self.conn.close()


def main():
    """Main execution for testing."""
    print("\n" + "="*70)
    print("🚀 EC2 REMEDIATOR - DRY RUN TEST")
    print("="*70)
    
    # Initialize in DRY-RUN mode
    remediator = EC2Remediator(dry_run=True)
    
    # Process pending recommendations
    results = remediator.process_pending_recommendations(limit=5)
    
    # Summary
    print("\n" + "="*70)
    print("📊 EXECUTION SUMMARY")
    print("="*70)
    print(f"Total recommendations processed: {len(results)}")
    print(f"Successful: {len([r for r in results if r['success']])}")
    print(f"Failed: {len([r for r in results if not r['success']])}")
    print(f"Dry-run mode: {results[0]['dry_run'] if results else 'N/A'}")
    
    if results:
        print("\nDetails:")
        for i, r in enumerate(results, 1):
            status = "✅ SUCCESS" if r['success'] else "❌ FAILED"
            print(f"{i}. {r['instance_id']}: {status}")
            if r['error']:
                print(f"   Error: {r['error']}")
    
    print("="*70)
    print("\n✅ Dry-run test completed!\n")


if __name__ == '__main__':
    main()