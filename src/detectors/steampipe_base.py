#!/usr/bin/env python3
"""
Generic base class for Steampipe-native waste detectors.

A concrete detector provides class attributes (query_name, resource_type,
waste_type, recommendation_type, banner) and one method, map_rows(), which
turns Steampipe rows into waste items:

    {
        'resource_id':  str,
        'monthly_cost': float,   # USD/month
        'confidence':   float,   # 0.0-1.0
        'action':       str,     # recommendation text
        'metadata':     dict,    # stored as JSONB
    }

save() and recommend() then feed waste_detected / recommendations exactly
like the historical detectors (same ON CONFLICT dedupe).
"""

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from core.database import get_db_connection, release_connection

from collectors.steampipe import run_query_file
from core.llm import enrich_recommendations
from core.pricing import stamp_pricing
from core.snapshots import snapshot_active_waste

load_dotenv()

logger = logging.getLogger(__name__)


def age_days_from(value: Any) -> Optional[int]:
    """Days elapsed since an ISO timestamp as Steampipe returns them, or
    None when absent/unparseable. Detectors store it as metadata age_days,
    which feeds the trend-history backfill and the Saved-so-far lifetime
    cap — both treat a missing age as 'lived only since detection'."""
    if not value:
        return None
    try:
        created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - created).days, 0)


class SteampipeWasteDetector:
    """Template for detectors whose collection layer is a Steampipe query."""

    query_name: str = ""  # sql/steampipe/<query_name>.sql
    resource_type: str = ""  # waste_detected.resource_type
    waste_type: str = ""  # waste_detected.waste_type
    recommendation_type: str = ""  # recommendations.recommendation_type
    banner: str = "WASTE DETECTION"  # printed by run()

    def __init__(self):
        db_vars = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
        missing = [v for v in db_vars if not os.getenv(v)]
        if missing:
            raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

        # Central pool from core.database: config, timeouts and validation
        # live in ONE place instead of a copy per detector. Release with
        # release_connection(), not close() -- the connection is pooled.
        self.conn = get_db_connection()

    def map_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Turn Steampipe rows into waste items. Must be overridden."""
        raise NotImplementedError

    def detect(self) -> List[Dict[str, Any]]:
        logger.info(f"Running Steampipe query '{self.query_name}'...")
        items = self.map_rows(run_query_file(self.query_name))
        logger.info(f"{len(items)} waste item(s) detected")
        return items

    def save(self, items: List[Dict[str, Any]]) -> List[int]:
        if not items:
            return []

        cursor = self.conn.cursor()
        account_id = os.getenv("AWS_ACCOUNT_ID", "unknown")
        today = date.today()
        waste_ids = []

        try:
            for item in items:
                metadata = stamp_pricing(item["metadata"])
                # waste_type is refreshed too: a resource can move between
                # detections over time (e.g. an orphaned volume gets attached
                # and is later flagged for gp2 migration)
                cursor.execute(
                    """
                    INSERT INTO waste_detected (
                        detection_date, provider, account_id, resource_id,
                        resource_type, waste_type, monthly_waste_eur,
                        confidence_score, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (resource_id, resource_type) DO UPDATE SET
                        detection_date    = EXCLUDED.detection_date,
                        waste_type        = EXCLUDED.waste_type,
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
                        item["resource_id"],
                        self.resource_type,
                        self.waste_type,
                        item["monthly_cost"],
                        item["confidence"],
                        json.dumps(metadata),
                    ),
                )
                waste_ids.append(cursor.fetchone()[0])

            self.conn.commit()
            logger.info(f"Saved {len(waste_ids)} waste records")
            return waste_ids

        except Exception as e:
            self.conn.rollback()
            raise RuntimeError(f"Failed to save waste records: {e}") from e
        finally:
            cursor.close()

    def recommend(self, waste_ids: List[int], items: List[Dict[str, Any]]) -> int:
        if not waste_ids:
            return 0

        cursor = self.conn.cursor()
        count = 0

        try:
            for waste_id, item in zip(waste_ids, items):
                cursor.execute(
                    """
                    INSERT INTO recommendations (
                        waste_id, recommendation_type, action_required,
                        estimated_monthly_savings_eur, status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (waste_id) DO UPDATE SET
                        estimated_monthly_savings_eur = EXCLUDED.estimated_monthly_savings_eur,
                        -- The AI insight quotes generation-time figures: drop
                        -- it when the resynced savings drifts beyond
                        -- max(10 pct of old, 0.50 USD) so enrich_recommendations()
                        -- rewrites it with fresh numbers on the next run.
                        ai_insight = CASE
                            WHEN abs(recommendations.estimated_monthly_savings_eur
                                     - EXCLUDED.estimated_monthly_savings_eur)
                                 > GREATEST(recommendations.estimated_monthly_savings_eur * 0.10, 0.50)
                            THEN NULL
                            ELSE recommendations.ai_insight
                        END
                    WHERE recommendations.status = 'pending';
                """,
                    (
                        waste_id,
                        self.recommendation_type,
                        item["action"],
                        item["monthly_cost"],
                        "pending",
                    ),
                )
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
        print(self.banner)
        print("=" * 70 + "\n")

        items = self.detect()

        if not items:
            snapshot_active_waste(self.conn)
            print("Nothing detected — no waste of this type.\n")
            return

        total_waste = sum(i["monthly_cost"] for i in items)
        print(f"Items found:         {len(items)}")
        print(f"Total monthly waste: {total_waste:.2f} USD/mo")
        print(f"Annual waste:        {total_waste * 12:.2f} USD/year\n")

        for i in items:
            print(f"  - {i['action']} → {i['monthly_cost']:.2f} USD/mo")

        waste_ids = self.save(items)
        rec_count = self.recommend(waste_ids, items)
        snapshot_active_waste(self.conn)
        insights = enrich_recommendations(self.conn)
        if insights:
            print(f"AI insights generated:   {insights}")

        print(f"\nRecommendations created: {rec_count}")
        print("View at http://localhost:8888/recommendations\n")

    def close(self):
        if hasattr(self, "conn") and self.conn:
            release_connection(self.conn)

    def __del__(self):
        self.close()
