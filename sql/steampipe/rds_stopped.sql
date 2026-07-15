-- Stopped RDS instances. A stopped DB pays no compute but keeps billing for
-- its provisioned storage (and backups) — and AWS automatically restarts any
-- instance stopped for more than 7 days, so a "stopped to save money" DB
-- silently resumes full billing. Either delete it or accept it will come back.
select
  db_instance_identifier,
  class,
  engine,
  engine_version,
  allocated_storage,
  storage_type,
  iops,
  multi_az,
  region,
  arn,
  availability_zone,
  extract(day from now() - create_time)::int as age_days
from
  aws_rds_db_instance
where
  status = 'stopped';
