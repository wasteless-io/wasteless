"""
Unit tests for the Cost Explorer collector (src/aws_collector.py).

The ce_client is mocked — no AWS access required. Focus is the parsing
of get_cost_and_usage responses into the DataFrame that feeds
cloud_costs_raw (and from there the home-page waste rate and the daily
briefing), especially the noise filter and error fallbacks.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from aws_collector import AWSCostCollector


def _collector_with_response(response):
    """Build a collector whose ce_client returns the given API response."""
    with patch("aws_collector.get_client"), patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}):
        collector = AWSCostCollector()
    collector.ce_client = MagicMock()
    collector.ce_client.get_cost_and_usage.return_value = response
    return collector


def _api_response(days):
    """Build a Cost Explorer response: days is a list of (date, groups)
    where groups is a list of (service, cost, usage)."""
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": date},
                "Groups": [
                    {
                        "Keys": [service],
                        "Metrics": {
                            "UnblendedCost": {"Amount": str(cost)},
                            "UsageQuantity": {"Amount": str(usage)},
                        },
                    }
                    for service, cost, usage in groups
                ],
            }
            for date, groups in days
        ]
    }


class TestGetCostsLastNDays:
    """Parsing of the Cost Explorer response."""

    def test_parses_daily_costs_per_service(self):
        collector = _collector_with_response(
            _api_response(
                [
                    ("2026-07-01", [("Amazon EC2", 12.50, 24.0), ("Amazon S3", 0.80, 100.0)]),
                    ("2026-07-02", [("Amazon EC2", 11.00, 24.0)]),
                ]
            )
        )
        df = collector.get_costs_last_n_days(days=2)

        assert len(df) == 3
        assert list(df.columns) == ["date", "service", "cost_usd", "usage"]
        ec2_day1 = df[(df["date"] == "2026-07-01") & (df["service"] == "Amazon EC2")]
        assert ec2_day1["cost_usd"].iloc[0] == 12.50
        assert ec2_day1["usage"].iloc[0] == 24.0

    def test_negligible_costs_are_filtered_out(self):
        """Costs at or below one cent are noise and must not reach
        cloud_costs_raw. The boundary (exactly 0.01) is excluded."""
        collector = _collector_with_response(
            _api_response(
                [
                    (
                        "2026-07-01",
                        [
                            ("Amazon EC2", 5.00, 1.0),
                            ("AWS CloudTrail", 0.01, 1.0),
                            ("Amazon SNS", 0.001, 1.0),
                            ("AWS Lambda", 0.0, 1.0),
                        ],
                    )
                ]
            )
        )
        df = collector.get_costs_last_n_days(days=1)

        assert list(df["service"]) == ["Amazon EC2"]

    def test_empty_response_returns_empty_dataframe(self):
        collector = _collector_with_response({"ResultsByTime": []})
        df = collector.get_costs_last_n_days(days=1)

        assert len(df) == 0

    def test_api_error_returns_empty_dataframe(self):
        """A Cost Explorer failure must degrade to 'no data', not crash
        the run (this collector is invoked manually, often unattended)."""
        collector = _collector_with_response({})
        collector.ce_client.get_cost_and_usage.side_effect = Exception("throttled")

        df = collector.get_costs_last_n_days(days=1)

        assert len(df) == 0


class TestInit:
    """Environment validation at construction."""

    def test_missing_aws_region_exits(self):
        env = {k: v for k, v in os.environ.items() if k != "AWS_REGION"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("aws_collector.load_dotenv"),
            pytest.raises(SystemExit),
        ):
            AWSCostCollector()

    def test_client_factory_failure_exits(self):
        with (
            patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}),
            patch("aws_collector.get_client", side_effect=Exception("no credentials")),
            pytest.raises(SystemExit),
        ):
            AWSCostCollector()
