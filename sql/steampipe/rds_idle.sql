-- Idle RDS instances: available (running, full billing) with zero database
-- connections over the last 14 days. Uses the daily connection metric; we
-- require the metric to actually exist (db_instance_identifier is not null in
-- the join) so a brand-new instance without 14 days of history isn't flagged.
-- max_conn_14d = 0 means not a single connection in two weeks.
with conn as (
  select
    db_instance_identifier,
    max(maximum) as max_conn,
    avg(average) as avg_conn
  from
    aws_rds_db_instance_metric_connections_daily
  where
    timestamp >= now() - interval '14 days'
  group by
    db_instance_identifier
)
select
  i.db_instance_identifier,
  i.class,
  i.engine,
  i.engine_version,
  i.allocated_storage,
  i.storage_type,
  i.multi_az,
  i.region,
  i.arn,
  coalesce(c.max_conn, 0) as max_conn_14d,
  round(coalesce(c.avg_conn, 0)::numeric, 2) as avg_conn_14d
from
  aws_rds_db_instance i
  join conn c on c.db_instance_identifier = i.db_instance_identifier
where
  i.status = 'available'
  and coalesce(c.max_conn, 0) = 0;
