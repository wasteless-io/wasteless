# src/collectors/

Pull raw data from AWS into PostgreSQL. Collectors are idempotent
(`ON CONFLICT DO NOTHING`) — safe to re-run or schedule at any interval.

| File | Purpose |
|---|---|
| `aws_cloudwatch.py` | `AWSCloudWatchCollector` — lists running EC2 instances, fetches daily CPU/network `GetMetricStatistics`, writes to `ec2_metrics`. The default collection path (`wasteless.sh collect` step 1). |
| `ec2_metrics_steampipe.py` | `SteampipeEC2MetricsCollector` — same `ec2_metrics` table, but sourced from `sql/steampipe/ec2_cpu_daily.sql` instead of boto3 `GetMetricStatistics`. Alternative path, not the one `wasteless.sh` runs by default. |
| `steampipe.py` | Thin wrapper around the `steampipe` CLI binary (`run_query`, `run_query_file`). Raises `SteampipeNotInstalledError` if the binary isn't on `PATH`. Everything under `sql/steampipe/*.sql` goes through this. |
| `init.py` | Empty compatibility shim (not `__init__.py` — package init is the separate `__init__.py` file). |

## Steampipe prerequisite

Anything routed through `steampipe.py` requires:
```bash
brew install turbot/tap/steampipe
steampipe plugin install aws
```
Multi-region is configured once in `~/.steampipe/config/aws.spc`, so
Steampipe-backed collectors/detectors never loop over regions manually —
the boto3-based ones (`aws_cloudwatch.py`) do.
