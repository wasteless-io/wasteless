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
from datetime import date
from typing import List, Dict, Any, Tuple

from dotenv import load_dotenv
import psycopg2
from psycopg2 import DatabaseError, OperationalError

from core.database import get_db_connection, release_connection
from core.database import DatabaseError as CoreDatabaseError

from core.pricing import stamp_pricing

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# EC2 instance pricing (USD/month, eu-west-1)
# Source: AWS Pricing Calculator + https://instances.vantage.sh/
# Updated: 2026-01-11
# Calculation: hourly_rate_USD * 730 hours. No currency conversion anywhere
# in the pipeline since 2026-07-18: figures stay in AWS's billing currency
# (values below are the former EUR table divided back by the 0.92 rate).
EC2_PRICING: Dict[str, float] = {
    # T2 instances
    "t2.nano": 5.17,
    "t2.micro": 10.33,
    "t2.small": 20.65,
    "t2.medium": 41.30,
    # T3 instances (most common)
    "t3.nano": 3.87,
    "t3.micro": 7.73,
    "t3.small": 15.46,
    "t3.medium": 30.91,
    "t3.large": 61.83,
    "t3.xlarge": 123.65,
    "t3.2xlarge": 247.30,
    # M5 instances
    "m5.large": 104.35,
    "m5.xlarge": 208.70,
    "m5.2xlarge": 417.39,
    "m5.4xlarge": 834.78,
    # C5 instances
    "c5.large": 92.39,
    "c5.xlarge": 184.78,
    "c5.2xlarge": 369.57,
    # R5 instances
    "r5.large": 136.96,
    "r5.xlarge": 273.91,
    "r5.2xlarge": 547.83,
}

# Default cost for unknown instance types
DEFAULT_INSTANCE_COST_USD = 54.35


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
        raise ValidationError(f"cpu_threshold must be a number, got {type(cpu_threshold).__name__}")
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
        raise ValidationError(f"days must be an integer, got {type(days).__name__}")
    if days <= 0:
        raise ValidationError(f"days must be a positive integer, got {days}")
    if days > 365:
        raise ValidationError(f"days cannot exceed 365, got {days}")


class EC2IdleDetector:
    """Detect idle EC2 instances based on CPU utilization."""

    def __init__(self) -> None:
        """Initialize detector with database connection."""
        logger.info("Initializing EC2 Idle Detector")

        # Verify database credentials
        db_vars = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
        missing = [var for var in db_vars if not os.getenv(var)]
        if missing:
            logger.error(f"Missing database variables: {', '.join(missing)}")
            raise DetectorError(f"Missing required environment variables: {', '.join(missing)}")

        # Central pool from core.database: config, timeouts and validation
        # live in ONE place instead of a copy per detector. Release with
        # release_connection(), not close() -- the connection is pooled.
        try:
            self.conn = get_db_connection()
            logger.info("Database connection established")
        except CoreDatabaseError as e:
            logger.error(f"Failed to connect to database: {e}")
            raise DetectorError(f"Database connection failed: {e}") from e

    def get_instance_monthly_cost(self, instance_type: str) -> float:
        """
        Get monthly cost for EC2 instance type.

        Args:
            instance_type: EC2 instance type (e.g., 't3.medium')

        Returns:
            Monthly cost in USD
        """
        if not instance_type:
            logger.warning("Empty instance_type, using default cost")
            return DEFAULT_INSTANCE_COST_USD

        cost = EC2_PRICING.get(instance_type)
        if cost is None:
            logger.warning(
                f"Pricing not found for {instance_type}, "
                f"using default {DEFAULT_INSTANCE_COST_USD} USD/month"
            )
            return DEFAULT_INSTANCE_COST_USD
        return cost

    def detect_idle_instances(
        self, cpu_threshold: float = 5.0, days: int = 7
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

        logger.info(f"Detecting idle instances (CPU < {cpu_threshold}%, last {days} days)...")

        cursor = self.conn.cursor()

        try:
            # Query to find instances with low CPU
            # region: MAX() over the window — an instance lives in one
            # region, the aggregate just picks the non-NULL stamp (rows
            # collected before multi-region support are NULL).
            query = """
            SELECT
                instance_id,
                instance_type,
                instance_state,
                AVG(cpu_avg) as cpu_avg_7d,
                MAX(cpu_max) as cpu_max_7d,
                MIN(cpu_avg) as cpu_min_7d,
                COUNT(*) as datapoints,
                MAX(region) as region
            FROM ec2_metrics
            WHERE collection_date >= CURRENT_DATE - %s::interval
              AND cpu_avg IS NOT NULL
            GROUP BY instance_id, instance_type, instance_state
            HAVING AVG(cpu_avg) < %s
            ORDER BY AVG(cpu_avg) ASC;
            """

            cursor.execute(query, (f"{days} days", cpu_threshold))
            idle_instances = cursor.fetchall()

            logger.info(f"Found {len(idle_instances)} idle instances")

            # Calculate waste for each instance
            waste_list: List[Dict[str, Any]] = []

            for instance in idle_instances:
                (
                    instance_id,
                    instance_type,
                    instance_state,
                    cpu_avg,
                    cpu_max,
                    cpu_min,
                    datapoints,
                    region,
                ) = instance

                # Get monthly cost
                monthly_cost = self.get_instance_monthly_cost(instance_type)

                # Calculate confidence score (0.0-1.0)
                # Closer to 0% CPU = higher confidence
                confidence = round(1.0 - (float(cpu_avg) / cpu_threshold), 2)
                confidence = max(0.0, min(1.0, confidence))

                # A near-zero average backed by a couple of datapoints
                # proves little: cap confidence until the observation
                # window fills up. The 0.70 cap keeps single-datapoint
                # detections below the 0.80 auto-remediation safeguard.
                if datapoints < 3:
                    confidence = min(confidence, 0.70)
                elif datapoints < days:
                    confidence = min(confidence, 0.85)

                # Calculate waste proportional to idle percentage
                waste_ratio = 1.0 - (float(cpu_avg) / 100.0)
                monthly_waste = round(monthly_cost * waste_ratio, 2)

                waste_record: Dict[str, Any] = {
                    "resource_id": instance_id,
                    "resource_type": "ec2_instance",
                    "waste_type": "idle_compute",
                    "monthly_waste_eur": monthly_waste,
                    "confidence_score": confidence,
                    "metadata": stamp_pricing(
                        {
                            "cpu_avg_7d": float(cpu_avg),
                            "cpu_max_7d": float(cpu_max),
                            "cpu_min_7d": float(cpu_min),
                            "instance_type": instance_type,
                            "instance_state": instance_state,
                            # Region stamped by the collector on each
                            # metrics row; the remediator reads
                            # metadata->>'region' before falling back to
                            # AWS_REGION at execution time. NULL only on
                            # rows collected before multi-region support
                            # — those all came from AWS_REGION.
                            "region": region or os.getenv("AWS_REGION"),
                            "monthly_cost_eur": monthly_cost,
                            "waste_ratio": waste_ratio,
                            "datapoints": datapoints,
                            "observation_days": days,
                            "detection_method": "cloudwatch_cpu_avg",
                            "threshold_used": cpu_threshold,
                        }
                    ),
                }

                waste_list.append(waste_record)

                logger.info(
                    f"  - {instance_id} ({instance_type}): "
                    f"CPU {cpu_avg:.2f}%, waste {monthly_waste} USD/mo, "
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
            account_id = os.getenv("AWS_ACCOUNT_ID", "unknown")
            today = date.today()

            for waste in waste_list:
                cursor.execute(
                    """
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
                """,
                    (
                        today,
                        "aws",
                        account_id,
                        waste["resource_id"],
                        waste["resource_type"],
                        waste["waste_type"],
                        waste["monthly_waste_eur"],
                        waste["confidence_score"],
                        json.dumps(waste["metadata"]),
                    ),
                )

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
            cursor.execute(
                """
                SELECT id, resource_id, confidence_score, monthly_waste_eur, metadata
                FROM waste_detected
                WHERE id = ANY(%s);
            """,
                (waste_ids,),
            )

            waste_records = cursor.fetchall()

            # Index by ID for fast lookup
            waste_by_id: Dict[int, Tuple] = {record[0]: record for record in waste_records}

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

                cpu_avg = metadata.get("cpu_avg_7d", 0)

                # Determine recommendation type based on confidence.
                # Stop first, never terminate as a first move: even a
                # zero-CPU instance may hold state on its root volume, and
                # a stopped instance can be restarted — a terminated one
                # cannot. Termination stays a manual follow-up once the
                # instance has sat stopped without anyone noticing.
                if confidence >= 0.90:
                    recommendation_type = "stop_instance"
                    action = (
                        f"STOP instance {resource_id} "
                        f"(avg CPU: {cpu_avg:.1f}%) — terminate manually "
                        f"once it has stayed stopped without impact"
                    )
                elif confidence >= 0.60:
                    recommendation_type = "stop_instance"
                    action = (
                        f"STOP instance {resource_id} during off-hours "
                        f"(avg CPU: {cpu_avg:.1f}%)"
                    )
                else:
                    recommendation_type = "downsize_instance"
                    action = (
                        f"DOWNSIZE instance {resource_id} to smaller type "
                        f"(avg CPU: {cpu_avg:.1f}%)"
                    )

                # Insert recommendation.
                # On re-detection the waste_id already exists, so we upsert:
                # - a still-'pending' reco just gets its savings/action
                #   refreshed;
                # - an 'obsolete' reco is *revived* to 'pending'. Obsolete
                #   means the sync job saw the instance stopped/terminated/
                #   gone outside wasteless; if it is running and idle again,
                #   the waste is real again and the reco must resurface —
                #   without this branch an obsolete reco stayed buried
                #   forever (the ON CONFLICT WHERE never matched it).
                # Any human-resolved status (applied, dismissed, rejected,
                # scheduled, pr_open, approved_manual) is left untouched.
                cursor.execute(
                    """
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO UPDATE SET
                        estimated_monthly_savings_eur = EXCLUDED.estimated_monthly_savings_eur,
                        recommendation_type = EXCLUDED.recommendation_type,
                        action_required = EXCLUDED.action_required,
                        status = 'pending',
                        applied_at = NULL,
                        -- The AI insight quotes generation-time figures: drop
                        -- it when the resynced savings drifts beyond
                        -- max(10 pct of old, 0.50 USD) so enrich_recommendations()
                        -- rewrites it with fresh numbers on the next run. Also
                        -- dropped when the recommended action itself changed
                        -- (stop vs downsize) or the reco revives from obsolete
                        -- (the instance left and came back — old story).
                        ai_insight = CASE
                            WHEN recommendations.status = 'obsolete'
                              OR recommendations.recommendation_type
                                 IS DISTINCT FROM EXCLUDED.recommendation_type
                              OR abs(recommendations.estimated_monthly_savings_eur
                                     - EXCLUDED.estimated_monthly_savings_eur)
                                 > GREATEST(recommendations.estimated_monthly_savings_eur * 0.10, 0.50)
                            THEN NULL
                            ELSE recommendations.ai_insight
                        END
                    WHERE recommendations.status IN ('pending', 'obsolete');
                """,
                    (waste_id, recommendation_type, action, monthly_waste, "pending"),
                )

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
        waste_list = self.detect_idle_instances(cpu_threshold=cpu_threshold, days=days)

        if not waste_list:
            from core.snapshots import snapshot_active_waste

            snapshot_active_waste(self.conn)
            print("No idle instances detected!")
            print("   All instances are being utilized efficiently.")
            return

        # Calculate totals
        total_waste = sum(w["monthly_waste_eur"] for w in waste_list)
        avg_confidence = sum(w["confidence_score"] for w in waste_list) / len(waste_list)

        print("\nIDLE INSTANCES DETECTED")
        print("=" * 70)
        print(f"Instances found: {len(waste_list)}")
        print(f"Total monthly waste: {total_waste:,.2f} USD")
        print(f"Average confidence: {avg_confidence:.2f}")
        print("=" * 70)

        # Save to database
        waste_ids = self.save_waste_detected(waste_list)

        # Generate recommendations
        recommendations_count = self.generate_recommendations(waste_ids)

        from core.snapshots import snapshot_active_waste

        snapshot_active_waste(self.conn)

        # AI insights (no-op unless WASTELESS_LLM_MODEL is configured)
        from core.llm import enrich_recommendations

        enrich_recommendations(self.conn)

        print("\n" + "=" * 70)
        print("DETECTION SUMMARY")
        print("=" * 70)
        print(f"Waste records saved: {len(waste_ids)}")
        print(f"Recommendations created: {recommendations_count}")
        print(f"Potential monthly savings: {total_waste:,.2f} USD")
        print(f"Annual savings potential: {total_waste * 12:,.2f} USD")
        print("=" * 70)

        print("\nDetection completed successfully!")
        print("   View results in Metabase dashboards.\n")

    def close(self) -> None:
        """Close database connection."""
        if hasattr(self, "conn") and self.conn:
            release_connection(self.conn)
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


if __name__ == "__main__":
    main()
