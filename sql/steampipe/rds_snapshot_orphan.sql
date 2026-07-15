-- Old MANUAL RDS snapshots (> 90 days). Automated snapshots are governed by
-- the instance retention window and expire on their own, so they're excluded;
-- manual snapshots live forever until someone deletes them and bill for backup
-- storage the whole time. type = 'manual' is the AWS-provided classification.
select
  db_snapshot_identifier,
  db_instance_identifier,
  engine,
  engine_version,
  allocated_storage,
  storage_type,
  region,
  arn,
  create_time,
  extract(day from now() - create_time)::int as age_days
from
  aws_rds_db_snapshot
where
  type = 'manual'
  and create_time < now() - interval '90 days';
