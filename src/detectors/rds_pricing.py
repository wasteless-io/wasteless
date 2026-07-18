"""
Shared RDS price approximations (eu-west-1, on-demand, USD list prices).
Deliberately a small, documented map rather
than a live pricing API: RDS idle/stopped detection only needs an
order-of-magnitude monthly figure for a manual-review recommendation.

Not exhaustive — unknown storage types / instance classes fall back to a
conservative default. Refresh the numbers when they drift materially.
"""

HOURS_PER_MONTH = 730

# Storage USD / GiB-month by storage_type (eu-west-1).
RDS_STORAGE_USD_PER_GB = {
    "gp2": 0.116,
    "gp3": 0.128,
    "io1": 0.125,
    "io2": 0.125,
    "standard": 0.10,
    "magnetic": 0.10,
}
RDS_STORAGE_DEFAULT_USD = 0.116

# Manual snapshot / backup storage beyond the free tier (USD / GiB-month).
RDS_SNAPSHOT_USD_PER_GB = 0.095

# Instance on-demand USD / hour, single-AZ (eu-west-1). Multi-AZ ≈ ×2.
RDS_INSTANCE_USD_PER_HOUR = {
    "db.t3.micro": 0.018,
    "db.t3.small": 0.036,
    "db.t3.medium": 0.072,
    "db.t3.large": 0.145,
    "db.t4g.micro": 0.016,
    "db.t4g.small": 0.032,
    "db.t4g.medium": 0.065,
    "db.t4g.large": 0.130,
    "db.m5.large": 0.178,
    "db.m5.xlarge": 0.356,
    "db.m5.2xlarge": 0.712,
    "db.m6g.large": 0.159,
    "db.m6g.xlarge": 0.318,
    "db.m6g.2xlarge": 0.636,
    "db.r5.large": 0.240,
    "db.r5.xlarge": 0.480,
    "db.r6g.large": 0.214,
    "db.r6g.xlarge": 0.428,
}
RDS_INSTANCE_DEFAULT_USD_PER_HOUR = 0.145  # ~db.t3.large, conservative middle


def storage_usd(gb: float, storage_type: str) -> float:
    """Monthly USD cost of `gb` GiB of RDS storage of `storage_type`."""
    usd = RDS_STORAGE_USD_PER_GB.get(storage_type, RDS_STORAGE_DEFAULT_USD)
    return round((gb or 0) * usd, 2)


def snapshot_usd(gb: float) -> float:
    """Monthly USD cost of a `gb` GiB manual RDS snapshot."""
    return round((gb or 0) * RDS_SNAPSHOT_USD_PER_GB, 2)


def instance_usd(
    instance_class: str, multi_az: bool, storage_gb: float, storage_type: str
) -> float:
    """Monthly USD cost of a running RDS instance (compute + storage)."""
    hourly = RDS_INSTANCE_USD_PER_HOUR.get(instance_class, RDS_INSTANCE_DEFAULT_USD_PER_HOUR)
    compute_usd = hourly * HOURS_PER_MONTH * (2 if multi_az else 1)
    return round(compute_usd + storage_usd(storage_gb, storage_type), 2)
