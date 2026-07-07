#!/usr/bin/env python3
"""
EC2 CPU Metrics Collector (Steampipe) for Wasteless

Fills the ec2_metrics table from Steampipe's CloudWatch metric tables
(sql/steampipe/ec2_cpu_daily.sql) instead of boto3 GetMetricStatistics
calls. Feeds the same table as src/collectors/aws_cloudwatch.py, so the
ec2_idle detector works unchanged.

Scope: CPU avg/max only. Network metrics (unused by detectors) stay with
the boto3 collector; the upsert here never touches network columns, so
both collectors can coexist.

Prerequisites: see src/collectors/steampipe.py
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values

# Allow running as a script: python3 src/collectors/ec2_metrics_steampipe.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.steampipe import SteampipeError, run_query_file

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def rows_to_records(rows: List[Dict[str, Any]]) -> List[Tuple]:
    """Map Steampipe rows to ec2_metrics insert tuples."""
    return [
        (
            row["instance_id"],
            row.get("instance_type") or "",
            row.get("instance_name") or "",
            row.get("instance_state") or "",
            row["collection_date"],
            row.get("cpu_avg"),
            row.get("cpu_max"),
        )
        for row in rows
    ]


class SteampipeEC2MetricsCollector:

    def __init__(self):
        db_vars = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
        missing = [v for v in db_vars if not os.getenv(v)]
        if missing:
            raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

        self.conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT")),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=10,
        )

    def collect(self) -> List[Tuple]:
        logger.info("Collecting EC2 CPU metrics via Steampipe (last 7 days)...")
        records = rows_to_records(run_query_file("ec2_cpu_daily"))
        logger.info(f"{len(records)} metric datapoint(s) collected")
        return records

    def save(self, records: List[Tuple]) -> int:
        if not records:
            return 0

        cursor = self.conn.cursor()
        try:
            # Upsert CPU fields only: never touch network columns so the
            # boto3 collector's data survives
            execute_values(
                cursor,
                """
                INSERT INTO ec2_metrics (
                    instance_id, instance_type, instance_name,
                    instance_state, collection_date, cpu_avg, cpu_max
                ) VALUES %s
                ON CONFLICT (instance_id, collection_date) DO UPDATE SET
                    instance_type  = EXCLUDED.instance_type,
                    instance_name  = EXCLUDED.instance_name,
                    instance_state = EXCLUDED.instance_state,
                    cpu_avg        = EXCLUDED.cpu_avg,
                    cpu_max        = EXCLUDED.cpu_max;
            """,
                records,
            )
            self.conn.commit()
            logger.info(f"Upserted {len(records)} rows into ec2_metrics")
            return len(records)
        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to save metrics: {e}") from e
        finally:
            cursor.close()

    def run(self) -> None:
        print("\n" + "=" * 70)
        print("EC2 CPU METRICS COLLECTION (STEAMPIPE)")
        print("=" * 70 + "\n")

        records = self.collect()

        if not records:
            print(
                "No datapoints found (no running instances, or no "
                "CloudWatch data in the last 7 days).\n"
            )
            return

        saved = self.save(records)
        instances = len({r[0] for r in records})
        print(f"Instances covered:  {instances}")
        print(f"Datapoints saved:   {saved}")
        print("\nRun the idle detector next: python3 src/detectors/ec2_idle.py\n")

    def close(self):
        if hasattr(self, "conn") and self.conn:
            self.conn.close()

    def __del__(self):
        self.close()


def main():
    collector = None
    try:
        collector = SteampipeEC2MetricsCollector()
        collector.run()
    except SteampipeError as e:
        logger.error(f"Steampipe error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Collection failed: {e}")
        sys.exit(1)
    finally:
        if collector:
            collector.close()


if __name__ == "__main__":
    main()
