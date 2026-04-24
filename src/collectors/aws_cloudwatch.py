#!/usr/bin/env python3
"""
AWS CloudWatch Collector for Wasteless

Collects CloudWatch metrics for EC2 instances:
- CPU utilization (average, max, min)
- Network I/O (in/out)
- Instance metadata (type, state, tags)

Author: Wasteless
"""

import boto3
import os
import sys
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

# Load environment variables
load_dotenv()

# Configure logging
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AWSCloudWatchCollector:
    """Collect CloudWatch metrics for EC2 instances."""
    
    def __init__(self):
        """Initialize CloudWatch collector with AWS clients."""
        logger.info("Initializing AWS CloudWatch Collector")
        
        # Verify required environment variables
        required_vars = [
            'AWS_REGION',
            'AWS_ACCOUNT_ID'
        ]

        missing = [var for var in required_vars if not os.getenv(var)]
        if missing:
            logger.error(f"Missing environment variables: {', '.join(missing)}")
            sys.exit(1)

        # Initialize AWS clients
        # Use boto3 default credential provider chain:
        # 1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
        # 2. IAM role (when running on EC2/ECS/Lambda)
        # 3. AWS credentials file (~/.aws/credentials)
        # 4. Container credentials (ECS tasks)
        try:
            self.region = os.getenv('AWS_REGION')
            self.account_id = os.getenv('AWS_ACCOUNT_ID')

            # EC2 client (for listing instances)
            # No explicit credentials - uses boto3 credential provider chain
            self.ec2_client = boto3.client(
                'ec2',
                region_name=self.region
            )

            # CloudWatch client (for metrics)
            self.cw_client = boto3.client(
                'cloudwatch',
                region_name=self.region
            )

            logger.info(f"✅ AWS clients initialized for region {self.region}")
            logger.info("   Using boto3 default credential provider (IAM role / env vars)")
            
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {e}")
            sys.exit(1)
    
    def get_ec2_instances(self):
        """
        Get list of all EC2 instances in the account.
        Uses pagination to handle accounts with >1000 instances.

        Returns:
            list[dict]: List of instance details
        """
        logger.info("Fetching EC2 instances with pagination...")

        try:
            instances = []
            paginator = self.ec2_client.get_paginator('describe_instances')

            # Iterate through all pages
            for page in paginator.paginate():
                for reservation in page['Reservations']:
                    for instance in reservation['Instances']:
                        # Extract instance details
                        instance_data = {
                            'instance_id': instance['InstanceId'],
                            'instance_type': instance['InstanceType'],
                            'instance_state': instance['State']['Name'],
                            'launch_time': instance['LaunchTime'],
                            'availability_zone': instance['Placement']['AvailabilityZone'],
                            'tags': {}
                        }

                        # Extract tags
                        if 'Tags' in instance:
                            for tag in instance['Tags']:
                                instance_data['tags'][tag['Key']] = tag['Value']

                        instances.append(instance_data)

            logger.info(f"✅ Found {len(instances)} EC2 instances")

            # Display summary by state
            states = {}
            for inst in instances:
                state = inst['instance_state']
                states[state] = states.get(state, 0) + 1

            for state, count in states.items():
                logger.info(f"  - {state}: {count} instances")

            return instances

        except Exception as e:
            logger.error(f"Failed to fetch EC2 instances: {e}")
            return []
    
    def get_cpu_utilization(self, instance_id, days=7):
        """
        Get CPU utilization metrics for an instance.
        
        Args:
            instance_id (str): EC2 instance ID
            days (int): Number of days to analyze (default: 7)
            
        Returns:
            dict: CPU metrics (avg, max, min) or None if no data
        """
        logger.debug(f"Fetching CPU metrics for {instance_id} (last {days} days)")
        
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=days)
            
            response = self.cw_client.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUUtilization',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour granularity
                Statistics=['Average', 'Maximum', 'Minimum']
            )
            
            datapoints = response.get('Datapoints', [])
            
            if not datapoints:
                logger.warning(f"No CPU metrics for {instance_id}")
                return None
            
            # Calculate overall statistics
            avg_cpu = sum(dp['Average'] for dp in datapoints) / len(datapoints)
            max_cpu = max(dp['Maximum'] for dp in datapoints)
            min_cpu = min(dp['Minimum'] for dp in datapoints)

            metrics = {
                'cpu_avg': round(avg_cpu, 2),
                'cpu_max': round(max_cpu, 2),
                'cpu_min': round(min_cpu, 2),
                'datapoints_count': len(datapoints)
            }
            
            logger.debug(f"  CPU metrics: avg={avg_cpu:.2f}%, max={max_cpu:.2f}%")
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to fetch CPU metrics for {instance_id}: {e}")
            return None
    
    def get_network_metrics(self, instance_id, days=7):
        """
        Get network I/O metrics for an instance.
        
        Args:
            instance_id (str): EC2 instance ID
            days (int): Number of days to analyze
            
        Returns:
            dict: Network metrics (in/out in MB) or None
        """
        logger.debug(f"Fetching network metrics for {instance_id}")
        
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=days)
            
            # Network In
            response_in = self.cw_client.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='NetworkIn',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average']
            )
            
            # Network Out
            response_out = self.cw_client.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='NetworkOut',
                Dimensions=[
                    {'Name': 'InstanceId', 'Value': instance_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average']
            )
            
            datapoints_in = response_in.get('Datapoints', [])
            datapoints_out = response_out.get('Datapoints', [])
            
            if not datapoints_in or not datapoints_out:
                return {'network_in_mb': 0.0, 'network_out_mb': 0.0}
            
            # Calculate averages (convert bytes to MB)
            avg_in_bytes = sum(dp['Average'] for dp in datapoints_in) / len(datapoints_in)
            avg_out_bytes = sum(dp['Average'] for dp in datapoints_out) / len(datapoints_out)
            
            metrics = {
                'network_in_mb': round(avg_in_bytes / 1024 / 1024, 2),
                'network_out_mb': round(avg_out_bytes / 1024 / 1024, 2)
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"Failed to fetch network metrics for {instance_id}: {e}")
            return {'network_in_mb': 0.0, 'network_out_mb': 0.0}

    def save_to_postgres(self, metrics_data):
        """
        Save EC2 metrics to PostgreSQL.

        Args:
            metrics_data (list[dict]): List of metric dictionaries

        Returns:
            int: Number of rows inserted
        """
        if not metrics_data:
            logger.warning("No metrics data to save")
            return 0

        logger.info(f"Saving {len(metrics_data)} metric records to PostgreSQL...")

        # Verify database credentials
        db_vars = ['DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing = [var for var in db_vars if not os.getenv(var)]
        if missing:
            logger.error(f"Missing database variables: {', '.join(missing)}")
            return 0

        try:
            # Connect to PostgreSQL
            conn = psycopg2.connect(
                host=os.getenv('DB_HOST'),
                port=int(os.getenv('DB_PORT')),
                database=os.getenv('DB_NAME'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD')
            )
            cursor = conn.cursor()

            # Prepare data for batch insert
            values = []
            for metric in metrics_data:
                # Get instance name from tags if available
                instance_name = metric.get('tags', {}).get('Name', '')

                values.append((
                    metric['instance_id'],
                    metric['instance_type'],
                    instance_name,
                    metric['instance_state'],
                    metric['collection_date'],
                    metric.get('cpu_avg'),
                    metric.get('cpu_max'),
                    metric.get('network_in_mb'),
                    metric.get('network_out_mb')
                ))

            # Batch insert with ON CONFLICT
            query = """
                INSERT INTO ec2_metrics (
                    instance_id, instance_type, instance_name, instance_state,
                    collection_date, cpu_avg, cpu_max,
                    network_in_mb, network_out_mb
                )
                VALUES %s
                ON CONFLICT (instance_id, collection_date)
                DO UPDATE SET
                    cpu_avg = EXCLUDED.cpu_avg,
                    cpu_max = EXCLUDED.cpu_max,
                    network_in_mb = EXCLUDED.network_in_mb,
                    network_out_mb = EXCLUDED.network_out_mb;
            """

            execute_values(cursor, query, values)
            conn.commit()

            rows_inserted = cursor.rowcount
            logger.info(f"✅ Inserted/updated {rows_inserted} rows in PostgreSQL")

            cursor.close()
            conn.close()

            return rows_inserted

        except Exception as e:
            logger.error(f"Failed to save to PostgreSQL: {e}")
            return 0

    def _collect_instance_metrics(self, instance: Dict, days: int, today: date) -> Optional[Dict]:
        """
        Collect metrics for a single instance (helper for parallel execution).

        Args:
            instance: Instance details dict
            days: Number of days to analyze
            today: Collection date

        Returns:
            Metric record dict or None if skipped
        """
        instance_id = instance['instance_id']
        instance_state = instance['instance_state']

        # Skip if not running (no metrics available)
        if instance_state != 'running':
            logger.debug(f"  ⏭️  Skipping {instance_id} (not running)")
            return None

        # Get CPU and network metrics in parallel
        cpu_metrics = self.get_cpu_utilization(instance_id, days=days)
        network_metrics = self.get_network_metrics(instance_id, days=days)

        # Combine all data
        metric_record = {
            'instance_id': instance['instance_id'],
            'instance_type': instance['instance_type'],
            'instance_state': instance['instance_state'],
            'launch_time': instance['launch_time'],
            'collection_date': today,
            'tags': instance['tags']
        }

        # Add CPU metrics if available
        if cpu_metrics:
            metric_record.update(cpu_metrics)

        # Add network metrics
        if network_metrics:
            metric_record.update(network_metrics)

        return metric_record

    def collect_all_metrics(self, days=7, max_workers=10):
        """
        Collect metrics for all EC2 instances in parallel.

        Args:
            days (int): Number of days to collect metrics for
            max_workers (int): Max parallel API calls (default: 10)

        Returns:
            list[dict]: All collected metrics
        """
        logger.info(f"Collecting metrics for all instances (last {days} days)...")
        logger.info(f"Using parallel execution with {max_workers} workers")

        # Get all instances
        instances = self.get_ec2_instances()

        if not instances:
            logger.warning("No instances to collect metrics for")
            return []

        # Filter running instances only
        running_instances = [
            inst for inst in instances
            if inst['instance_state'] == 'running'
        ]

        logger.info(f"Found {len(running_instances)} running instances to process")

        metrics_data = []
        today = datetime.now().date()

        # Process instances in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_instance = {
                executor.submit(
                    self._collect_instance_metrics,
                    instance,
                    days,
                    today
                ): instance for instance in running_instances
            }

            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_instance):
                instance = future_to_instance[future]
                completed += 1

                try:
                    metric_record = future.result()
                    if metric_record:
                        metrics_data.append(metric_record)
                        logger.info(
                            f"  [{completed}/{len(running_instances)}] "
                            f"✅ {instance['instance_id']}"
                        )
                except Exception as e:
                    logger.error(
                        f"  [{completed}/{len(running_instances)}] "
                        f"❌ {instance['instance_id']}: {e}"
                    )

        logger.info(f"\n✅ Total metrics collected: {len(metrics_data)}")

        return metrics_data

    def run(self, days=7, save_to_db=True):
        """
        Main orchestration method.

        Args:
            days (int): Number of days to collect
            save_to_db (bool): Whether to save to PostgreSQL
        """
        print("\n" + "=" * 70)
        print("🚀 AWS CloudWatch Metrics Collection")
        print("=" * 70)
        print(f"Region: {self.region}")
        print(f"Account: {self.account_id}")
        print(f"Period: Last {days} days")
        print("=" * 70 + "\n")

        # Collect metrics
        metrics_data = self.collect_all_metrics(days=days)

        if not metrics_data:
            print("\n⚠️  No metrics collected")
            return

        # Save to database
        if save_to_db:
            rows_saved = self.save_to_postgres(metrics_data)

            print("\n" + "=" * 70)
            print("📊 COLLECTION SUMMARY")
            print("=" * 70)
            print(f"Instances processed: {len(metrics_data)}")
            print(f"Rows saved to DB: {rows_saved}")
            print("=" * 70)

        print("\n✅ Collection completed successfully!\n")


def main():
    """Main execution."""
    collector = AWSCloudWatchCollector()
    collector.run(days=7, save_to_db=True)


if __name__ == '__main__':
    main()