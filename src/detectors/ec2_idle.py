#!/usr/bin/env python3
"""
EC2 Idle Instance Detector for Wasteless

Detects EC2 instances with low CPU utilization over a period of time.
Generates waste estimates and actionable recommendations.

Detection criteria:
- Average CPU < 5% over 7 days
- Confidence score based on how close to 0% the average is

Author: Wasteless
"""

import os
import sys
import json
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Tuple

from dotenv import load_dotenv
import psycopg2
from psycopg2 import DatabaseError, OperationalError
from psycopg2.extras import execute_values

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# EC2 instance pricing (EUR/month, eu-west-1)
# Source: AWS Pricing Calculator + https://instances.vantage.sh/
# Updated: 2026-01-11
# Calculation: hourly_rate_USD * 730 hours * 0.92 EUR/USD
EC2_PRICING: Dict[str, float] = {
    # T2 instances
    't2.nano': 4.76,
    't2.micro': 9.50,
    't2.small': 19.00,
    't2.medium': 38.00,

    # T3 instances (most common)
    't3.nano': 3.56,
    't3.micro': 7.11,
    't3.small': 14.22,
    't3.medium': 28.44,
    't3.large': 56.88,
    't3.xlarge': 113.76,
    't3.2xlarge': 227.52,

    # M5 instances
    'm5.large': 96.00,
    'm5.xlarge': 192.00,
    'm5.2xlarge': 384.00,
    'm5.4xlarge': 768.00,

    # C5 instances
    'c5.large': 85.00,
    'c5.xlarge': 170.00,
    'c5.2xlarge': 340.00,

    # R5 instances
    'r5.large': 126.00,
    'r5.xlarge': 252.00,
    'r5.2xlarge': 504.00,
}

# Default cost for unknown instance types
DEFAULT_INSTANCE_COST_EUR = 50.0


class DetectorError(Exception):
    """Custom exception for detector operations."""
    pass


class ValidationError(Exception):
    """Exception raised for parameter validation failures."""
    pass


def validate_cpu_threshold(cpu_threshold: float) -> None:
    """
    Validate CPU threshold parameter.

    Args:
        cpu_threshold: CPU percentage threshold

    Raises:
        ValidationError: If threshold is invalid
    """
    if not isinstance(cpu_threshold, (int, float)):
        raise ValidationError(
            f"cpu_threshold must be a number, got {type(cpu_threshold).__name__}"
        )
    if cpu_threshold <= 0 or cpu_threshold > 100:
        raise ValidationError(
            f"cpu_threshold must be between 0 and 100 (exclusive), got {cpu_threshold}"
        )


def validate_days(days: int) -> None:
    """
    Validate days parameter.

    Args:
        days: Number of days to analyze

    Raises:
        ValidationError: If days is invalid
    """
    if not isinstance(days, int):
        raise ValidationError(
            f"days must be an integer, got {type(days).__name__}"
        )
    if days <= 0:
        raise ValidationError(
            f"days must be a positive integer, got {days}"
        )
    if days > 365:
        raise ValidationError(
            f"days cannot exceed 365, got {days}"
        )


class EC2IdleDetector:
    """Detect idle EC2 instances based on CPU utilization."""

    def __init__(self) -> None:
        """Initialize detector with database connection."""
        logger.info("Initializing EC2 Idle Detector")

        # Verify database credentials
        db_vars = ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing = [var for var in db_vars if not os.getenv(var)]
        if missing:
            logger.error(f"Missing database variables: {', '.join(missing)}")
            raise DetectorError(f"Missing required environment variables: {', '.join(missing)}")

        # Initialize database connection
        try:
            self.conn = psycopg2.connect(
                host=os.getenv('DB_HOST'),
                port=int(os.getenv('DB_PORT')),
                database=os.getenv('DB_NAME'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD'),
                connect_timeout=10
            )
            logger.info("Database connection established")
        except OperationalError as e:
            logger.error(f"Failed to connect to database: {e}")
            raise DetectorError(f"Database connection failed: {e}") from e

    def get_instance_monthly_cost(self, instance_type: str) -> float:
        """
        Get monthly cost for EC2 instance type.

        Args:
            instance_type: EC2 instance type (e.g., 't3.medium')

        Returns:
            Monthly cost in EUR
        """
        if not instance_type:
            logger.warning("Empty instance_type, using default cost")
            return DEFAULT_INSTANCE_COST_EUR

        cost = EC2_PRICING.get(instance_type)
        if cost is None:
            logger.warning(
                f"Pricing not found for {instance_type}, "
                f"using default {DEFAULT_INSTANCE_COST_EUR} EUR/month"
            )
            return DEFAULT_INSTANCE_COST_EUR
        return cost

    def detect_idle_instances(
        self,
        cpu_threshold: float = 5.0,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Detect EC2 instances with low CPU utilization.

        Args:
            cpu_threshold: CPU percentage threshold (0-100, default: 5.0%)
            days: Number of days to analyze (1-365, default: 7)

        Returns:
            List of idle instances with waste details

        Raises:
            ValidationError: If parameters are invalid
            DetectorError: If detection fails
        """
        # Validate parameters
        validate_cpu_threshold(cpu_threshold)
        validate_days(days)

        logger.info(
            f"Detecting idle instances (CPU < {cpu_threshold}%, last {days} days)..."
        )

        cursor = self.conn.cursor()

        try:
            # Query to find instances with low CPU
            query = """
            SELECT
                instance_id,
                instance_type,
                instance_state,
                AVG(cpu_avg) as cpu_avg_7d,
                MAX(cpu_max) as cpu_max_7d,
                MIN(cpu_avg) as cpu_min_7d,
                COUNT(*) as datapoints
            FROM ec2_metrics
            WHERE collection_date >= CURRENT_DATE - %s::interval
              AND cpu_avg IS NOT NULL
            GROUP BY instance_id, instance_type, instance_state
            HAVING AVG(cpu_avg) < %s
            ORDER BY AVG(cpu_avg) ASC;
            """

            cursor.execute(query, (f'{days} days', cpu_threshold))
            idle_instances = cursor.fetchall()

            logger.info(f"Found {len(idle_instances)} idle instances")

            # Calculate waste for each instance
            waste_list: List[Dict[str, Any]] = []

            for instance in idle_instances:
                (instance_id, instance_type, instance_state,
                 cpu_avg, cpu_max, cpu_min, datapoints) = instance

                # Get monthly cost
                monthly_cost = self.get_instance_monthly_cost(instance_type)

                # Calculate confidence score (0.0-1.0)
                # Closer to 0% CPU = higher confidence
                confidence = round(1.0 - (float(cpu_avg) / cpu_threshold), 2)
                confidence = max(0.0, min(1.0, confidence))

                # Calculate waste proportional to idle percentage
                waste_ratio = 1.0 - (float(cpu_avg) / 100.0)
                monthly_waste = round(monthly_cost * waste_ratio, 2)

                waste_record: Dict[str, Any] = {
                    'resource_id': instance_id,
                    'resource_type': 'ec2_instance',
                    'waste_type': 'idle_compute',
                    'monthly_waste_eur': monthly_waste,
                    'confidence_score': confidence,
                    'metadata': {
                        'cpu_avg_7d': float(cpu_avg),
                        'cpu_max_7d': float(cpu_max),
                        'cpu_min_7d': float(cpu_min),
                        'instance_type': instance_type,
                        'instance_state': instance_state,
                        'monthly_cost_eur': monthly_cost,
                        'waste_ratio': waste_ratio,
                        'datapoints': datapoints,
                        'detection_method': 'cloudwatch_cpu_avg',
                        'threshold_used': cpu_threshold
                    }
                }

                waste_list.append(waste_record)

                logger.info(
                    f"  - {instance_id} ({instance_type}): "
                    f"CPU {cpu_avg:.2f}%, waste {monthly_waste} EUR/mo, "
                    f"confidence {confidence:.2f}"
                )

            return waste_list

        except (DatabaseError, OperationalError) as e:
            logger.error(f"Database error during detection: {e}")
            raise DetectorError(f"Failed to detect idle instances: {e}") from e

        finally:
            cursor.close()

    def save_waste_detected(self, waste_list: List[Dict[str, Any]]) -> List[int]:
        """
        Save detected waste to database.

        Args:
            waste_list: List of waste records

        Returns:
            List of inserted waste IDs

        Raises:
            DetectorError: If save fails
        """
        if not waste_list:
            logger.warning("No waste to save")
            return []

        logger.info(f"Saving {len(waste_list)} waste records to database...")

        cursor = self.conn.cursor()
        waste_ids: List[int] = []

        try:
            account_id = os.getenv('AWS_ACCOUNT_ID', 'unknown')
            today = date.today()

            for waste in waste_list:
                cursor.execute("""
                    INSERT INTO waste_detected (
                        detection_date, provider, account_id, resource_id,
                        resource_type, waste_type, monthly_waste_eur,
                        confidence_score, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    waste['resource_id'],
                    waste['resource_type'],
                    waste['waste_type'],
                    waste['monthly_waste_eur'],
                    waste['confidence_score'],
                    json.dumps(waste['metadata'])
                ))

                waste_id = cursor.fetchone()[0]
                waste_ids.append(waste_id)

            self.conn.commit()
            logger.info(f"Saved {len(waste_ids)} waste records")

        except psycopg2.IntegrityError as e:
            self.conn.rollback()
            logger.error(f"Integrity error saving waste records: {e}")
            raise DetectorError(f"Duplicate or constraint violation: {e}") from e

        except (DatabaseError, OperationalError) as e:
            self.conn.rollback()
            logger.error(f"Database error saving waste records: {e}")
            raise DetectorError(f"Failed to save waste records: {e}") from e

        finally:
            cursor.close()

        return waste_ids

    def generate_recommendations(self, waste_ids: List[int]) -> int:
        """
        Generate actionable recommendations for detected waste.
        Uses batch query to avoid N+1 problem.

        Args:
            waste_ids: List of waste record IDs

        Returns:
            Number of recommendations generated

        Raises:
            DetectorError: If generation fails
        """
        if not waste_ids:
            logger.warning("No waste IDs to generate recommendations for")
            return 0

        logger.info(f"Generating recommendations for {len(waste_ids)} waste records...")

        cursor = self.conn.cursor()
        recommendations_created = 0

        try:
            # Batch fetch all waste records (fixes N+1 query problem)
            cursor.execute("""
                SELECT id, resource_id, confidence_score, monthly_waste_eur, metadata
                FROM waste_detected
                WHERE id = ANY(%s);
            """, (waste_ids,))

            waste_records = cursor.fetchall()

            # Index by ID for fast lookup
            waste_by_id: Dict[int, Tuple] = {
                record[0]: record for record in waste_records
            }

            # Generate recommendations
            for waste_id in waste_ids:
                record = waste_by_id.get(waste_id)
                if not record:
                    logger.warning(f"Waste record {waste_id} not found, skipping")
                    continue

                _, resource_id, confidence, monthly_waste, metadata_json = record

                # Parse metadata
                if isinstance(metadata_json, dict):
                    metadata = metadata_json
                else:
                    metadata = json.loads(metadata_json) if metadata_json else {}

                cpu_avg = metadata.get('cpu_avg_7d', 0)

                # Determine recommendation type based on confidence
                if confidence >= 0.90:
                    recommendation_type = 'terminate_instance'
                    action = (
                        f"TERMINATE instance {resource_id} "
                        f"(avg CPU: {cpu_avg:.1f}%)"
                    )
                elif confidence >= 0.60:
                    recommendation_type = 'stop_instance'
                    action = (
                        f"STOP instance {resource_id} during off-hours "
                        f"(avg CPU: {cpu_avg:.1f}%)"
                    )
                else:
                    recommendation_type = 'downsize_instance'
                    action = (
                        f"DOWNSIZE instance {resource_id} to smaller type "
                        f"(avg CPU: {cpu_avg:.1f}%)"
                    )

                # Insert recommendation
                cursor.execute("""
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO NOTHING;
                """, (
                    waste_id,
                    recommendation_type,
                    action,
                    monthly_waste,
                    'pending'
                ))

                recommendations_created += 1

            self.conn.commit()
            logger.info(f"Created {recommendations_created} recommendations")

        except psycopg2.IntegrityError as e:
            self.conn.rollback()
            logger.error(f"Integrity error generating recommendations: {e}")
            raise DetectorError(f"Constraint violation: {e}") from e

        except (DatabaseError, OperationalError) as e:
            self.conn.rollback()
            logger.error(f"Database error generating recommendations: {e}")
            raise DetectorError(f"Failed to generate recommendations: {e}") from e

        finally:
            cursor.close()

        return recommendations_created

    def run(self, cpu_threshold: float = 5.0, days: int = 7) -> None:
        """
        Main orchestration method.

        Args:
            cpu_threshold: CPU percentage threshold (0-100)
            days: Number of days to analyze (1-365)

        Raises:
            ValidationError: If parameters are invalid
            DetectorError: If any step fails
        """
        print("\n" + "=" * 70)
        print("EC2 IDLE INSTANCE DETECTION")
        print("=" * 70)
        print(f"CPU Threshold: < {cpu_threshold}%")
        print(f"Analysis Period: Last {days} days")
        print("=" * 70 + "\n")

        # Detect idle instances
        waste_list = self.detect_idle_instances(
            cpu_threshold=cpu_threshold,
            days=days
        )

        if not waste_list:
            print("No idle instances detected!")
            print("   All instances are being utilized efficiently.")
            return

        # Calculate totals
        total_waste = sum(w['monthly_waste_eur'] for w in waste_list)
        avg_confidence = sum(w['confidence_score'] for w in waste_list) / len(waste_list)

        print(f"\nIDLE INSTANCES DETECTED")
        print("=" * 70)
        print(f"Instances found: {len(waste_list)}")
        print(f"Total monthly waste: {total_waste:,.2f} EUR")
        print(f"Average confidence: {avg_confidence:.2f}")
        print("=" * 70)

        # Save to database
        waste_ids = self.save_waste_detected(waste_list)

        # Generate recommendations
        recommendations_count = self.generate_recommendations(waste_ids)

        print("\n" + "=" * 70)
        print("DETECTION SUMMARY")
        print("=" * 70)
        print(f"Waste records saved: {len(waste_ids)}")
        print(f"Recommendations created: {recommendations_count}")
        print(f"Potential monthly savings: {total_waste:,.2f} EUR")
        print(f"Annual savings potential: {total_waste * 12:,.2f} EUR")
        print("=" * 70)

        print("\nDetection completed successfully!")
        print("   View results in Metabase dashboards.\n")

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def __del__(self) -> None:
        """Close database connection on cleanup."""
        self.close()


def main() -> None:
    """Main execution."""
    detector = None
    try:
        detector = EC2IdleDetector()
        detector.run(cpu_threshold=5.0, days=7)
    except ValidationError as e:
        logger.error(f"Validation error: {e}")
        sys.exit(1)
    except DetectorError as e:
        logger.error(f"Detector error: {e}")
        sys.exit(1)
    finally:
        if detector:
            detector.close()


if __name__ == '__main__':
    main()
