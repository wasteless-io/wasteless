#!/usr/bin/env python3
"""
Savings Tracker for Wasteless

Measures actual savings after remediation by comparing:
- Cost before action (historical average)
- Cost after action (current average)

Verifies if estimated savings match reality.

Author: Wasteless
"""

import os
import sys
import boto3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database import get_db_connection

load_dotenv()

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SavingsTracker:
    """Track and verify actual savings after remediation."""
    
    def __init__(self):
        """Initialize savings tracker."""
        logger.info("Initializing Savings Tracker")
        
        self.region = os.getenv('AWS_REGION')
        self.account_id = os.getenv('AWS_ACCOUNT_ID')
        
        # Initialize AWS Cost Explorer client
        self.ce_client = boto3.client(
            'ce',  # Cost Explorer
            region_name='us-east-1',  # Cost Explorer is only in us-east-1
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
        )
        
        # Database connection
        self.conn = get_db_connection()
        
        logger.info("✅ Savings Tracker initialized")
    
    def get_instance_cost_for_period(
        self,
        instance_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> float:
        """
        Get total cost for a specific instance during a period.
        
        Args:
            instance_id: EC2 instance ID
            start_date: Period start
            end_date: Period end
            
        Returns:
            Total cost in USD (convert to EUR later)
        """
        try:
            logger.debug(
                f"Getting cost for {instance_id} "
                f"from {start_date.date()} to {end_date.date()}"
            )
            
            response = self.ce_client.get_cost_and_usage(
                TimePeriod={
                    'Start': start_date.strftime('%Y-%m-%d'),
                    'End': end_date.strftime('%Y-%m-%d')
                },
                Granularity='DAILY',
                Metrics=['UnblendedCost'],
                Filter={
                    'Dimensions': {
                        'Key': 'RESOURCE_ID',
                        'Values': [instance_id]
                    }
                }
            )
            
            total_cost = 0.0
            
            for result in response['ResultsByTime']:
                cost = float(result['Total']['UnblendedCost']['Amount'])
                total_cost += cost
            
            logger.debug(f"Total cost: ${total_cost:.2f}")
            
            return total_cost
            
        except Exception as e:
            logger.error(f"Failed to get cost for {instance_id}: {e}")
            return 0.0
    
    def calculate_average_daily_cost(
        self,
        instance_id: str,
        days_back: int = 30
    ) -> float:
        """
        Calculate average daily cost for an instance.
        
        Args:
            instance_id: EC2 instance ID
            days_back: Number of days to average
            
        Returns:
            Average daily cost
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        total_cost = self.get_instance_cost_for_period(
            instance_id,
            start_date,
            end_date
        )
        
        avg_daily_cost = total_cost / days_back
        
        logger.info(
            f"Instance {instance_id}: "
            f"${avg_daily_cost:.2f}/day average ({days_back} days)"
        )
        
        return avg_daily_cost
    
    def verify_savings_for_action(self, action_log_id: int) -> Optional[Dict]:
        """
        Verify actual savings for a completed action.
        
        Args:
            action_log_id: ID from actions_log table
            
        Returns:
            Dict with savings verification or None
        """
        logger.info(f"🔍 Verifying savings for action {action_log_id}")
        
        cursor = self.conn.cursor()
        
        # Get action details
        cursor.execute("""
            SELECT 
                a.resource_id,
                a.action_date,
                a.action_type,
                r.id as recommendation_id,
                r.estimated_monthly_savings_eur,
                w.metadata->>'instance_type' as instance_type
            FROM actions_log a
            JOIN recommendations r ON a.recommendation_id = r.id
            JOIN waste_detected w ON r.waste_id = w.id
            WHERE a.id = %s
              AND a.action_status = 'success'
              AND a.action_type = 'stop';
        """, (action_log_id,))
        
        row = cursor.fetchone()
        
        if not row:
            logger.warning(f"No successful stop action found for {action_log_id}")
            cursor.close()
            return None
        
        (instance_id, action_date, action_type, 
         recommendation_id, estimated_savings, instance_type) = row
        
        # Check if enough time has passed (minimum 7 days for meaningful data)
        days_since_action = (datetime.now() - action_date).days
        
        if days_since_action < 7:
            logger.info(
                f"⏳ Only {days_since_action} days since action, "
                f"need 7+ for verification"
            )
            cursor.close()
            return None
        
        logger.info(
            f"Analyzing savings for {instance_id} "
            f"({days_since_action} days after stop)"
        )
        
        # Calculate cost BEFORE action (30 days before action date)
        before_end = action_date
        before_start = before_end - timedelta(days=30)
        
        cost_before_period = self.get_instance_cost_for_period(
            instance_id,
            before_start,
            before_end
        )
        
        avg_daily_cost_before = cost_before_period / 30
        monthly_cost_before = avg_daily_cost_before * 30
        
        # Calculate cost AFTER action (last 7-30 days depending on time elapsed)
        measurement_days = min(days_since_action, 30)
        after_start = datetime.now() - timedelta(days=measurement_days)
        after_end = datetime.now()
        
        cost_after_period = self.get_instance_cost_for_period(
            instance_id,
            after_start,
            after_end
        )
        
        avg_daily_cost_after = cost_after_period / measurement_days
        monthly_cost_after = avg_daily_cost_after * 30
        
        # Calculate actual savings
        actual_monthly_savings = monthly_cost_before - monthly_cost_after
        
        # Convert USD to EUR (rough approximation, use real exchange rate in prod)
        usd_to_eur = 0.92  # TODO: Get real exchange rate from API
        
        cost_before_eur = monthly_cost_before * usd_to_eur
        cost_after_eur = monthly_cost_after * usd_to_eur
        actual_savings_eur = actual_monthly_savings * usd_to_eur
        
        # Calculate accuracy vs estimate
        savings_accuracy = 0.0
        if estimated_savings > 0:
            savings_accuracy = (actual_savings_eur / float(estimated_savings)) * 100
        
        logger.info(f"\n📊 SAVINGS ANALYSIS:")
        logger.info(f"   Cost before: €{cost_before_eur:.2f}/month")
        logger.info(f"   Cost after:  €{cost_after_eur:.2f}/month")
        logger.info(f"   Actual savings: €{actual_savings_eur:.2f}/month")
        logger.info(f"   Estimated savings: €{estimated_savings:.2f}/month")
        logger.info(f"   Accuracy: {savings_accuracy:.1f}%")
        
        # Save to database
        cursor.execute("""
            INSERT INTO savings_realized (
                recommendation_id,
                action_log_id,
                resource_id,
                resource_type,
                measurement_start_date,
                measurement_end_date,
                cost_before_eur,
                cost_after_eur,
                actual_savings_eur,
                estimated_savings_eur,
                savings_accuracy_percent,
                verification_method,
                verified_at,
                verified_by,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (
            recommendation_id,
            action_log_id,
            instance_id,
            'ec2_instance',
            before_start.date(),
            after_end.date(),
            cost_before_eur,
            cost_after_eur,
            actual_savings_eur,
            estimated_savings,
            savings_accuracy,
            'aws_cost_explorer',
            datetime.now(),
            'system',
            json.dumps({
                'instance_type': instance_type,
                'days_since_action': days_since_action,
                'measurement_days_after': measurement_days,
                'usd_to_eur_rate': usd_to_eur
            })
        ))
        
        savings_id = cursor.fetchone()[0]
        self.conn.commit()
        
        logger.info(f"✅ Savings record created: {savings_id}")
        
        cursor.close()
        
        return {
            'savings_id': savings_id,
            'instance_id': instance_id,
            'cost_before_eur': cost_before_eur,
            'cost_after_eur': cost_after_eur,
            'actual_savings_eur': actual_savings_eur,
            'estimated_savings_eur': float(estimated_savings),
            'accuracy_percent': savings_accuracy,
            'days_measured': measurement_days
        }
    
    def verify_all_unverified_actions(self, min_days_elapsed: int = 7) -> List[Dict]:
        """
        Verify savings for all actions that haven't been verified yet.
        
        Args:
            min_days_elapsed: Minimum days since action to verify
            
        Returns:
            List of verification results
        """
        logger.info(
            f"🔍 Finding unverified actions "
            f"(older than {min_days_elapsed} days)"
        )
        
        cursor = self.conn.cursor()
        
        # Find actions not yet verified
        # Join via recommendation_id since both tables have it
        cursor.execute("""
            SELECT a.id
            FROM actions_log a
            LEFT JOIN savings_realized s ON s.recommendation_id = a.recommendation_id
            WHERE a.action_status = 'success'
              AND a.action_type IN ('stop', 'terminate', 'downsize')
              AND s.id IS NULL
              AND a.action_date < NOW() - INTERVAL '%s days'
            ORDER BY a.action_date ASC;
        """, (min_days_elapsed,))
        
        action_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()
        
        logger.info(f"Found {len(action_ids)} actions to verify")
        
        results = []
        
        for action_id in action_ids:
            result = self.verify_savings_for_action(action_id)
            if result:
                results.append(result)
        
        return results
    
    def get_total_verified_savings(self) -> Dict:
        """
        Get total verified savings across all actions.
        
        Returns:
            Dict with totals
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT
                COUNT(*) as total_actions,
                SUM(actual_monthly_savings_eur) as total_savings_eur
            FROM savings_realized;
        """)
        
        row = cursor.fetchone()
        cursor.close()
        
        if not row or row[0] == 0:
            return {
                'total_actions': 0,
                'total_savings_eur': 0.0,
                'avg_accuracy_percent': 0.0,
                'total_cost_before': 0.0,
                'total_cost_after': 0.0
            }
        
        (count, savings, accuracy, cost_before, cost_after) = row
        
        return {
            'total_actions': count,
            'total_savings_eur': float(savings or 0),
            'avg_accuracy_percent': float(accuracy or 0),
            'total_cost_before': float(cost_before or 0),
            'total_cost_after': float(cost_after or 0)
        }
    
    def __del__(self):
        """Close database connection."""
        if hasattr(self, 'conn'):
            self.conn.close()


def main():
    """Main execution for testing."""
    print("\n" + "="*70)
    print("💰 SAVINGS TRACKER - Verification")
    print("="*70)
    
    tracker = SavingsTracker()
    
    # Verify all unverified actions
    print("\nVerifying unverified actions (7+ days old)...")
    results = tracker.verify_all_unverified_actions(min_days_elapsed=7)
    
    print(f"\n✅ Verified {len(results)} actions")
    
    # Get totals
    totals = tracker.get_total_verified_savings()
    
    print("\n" + "="*70)
    print("📊 TOTAL VERIFIED SAVINGS")
    print("="*70)
    print(f"Actions verified: {totals['total_actions']}")
    print(f"Total cost before: €{totals['total_cost_before']:,.2f}/month")
    print(f"Total cost after: €{totals['total_cost_after']:,.2f}/month")
    print(f"Total savings: €{totals['total_savings_eur']:,.2f}/month")
    print(f"Average accuracy: {totals['avg_accuracy_percent']:.1f}%")
    
    if totals['total_actions'] > 0:
        annual_savings = totals['total_savings_eur'] * 12
        print(f"\nProjected annual savings: €{annual_savings:,.2f}/year")
    
    print("="*70 + "\n")


if __name__ == '__main__':
    main()