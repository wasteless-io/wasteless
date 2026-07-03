-- Daily CPU utilization per EC2 instance over the last 7 days, with
-- instance attributes. Replaces the CloudWatch GetMetricStatistics calls
-- of src/collectors/aws_cloudwatch.py for CPU (network metrics stay with
-- the boto3 collector; the ec2_idle detector does not use them).
select
  m.instance_id,
  i.instance_type,
  coalesce(i.tags ->> 'Name', '')   as instance_name,
  i.instance_state,
  m.timestamp::date                 as collection_date,
  round(m.average::numeric, 2)      as cpu_avg,
  round(m.maximum::numeric, 2)      as cpu_max
from
  aws_ec2_instance_metric_cpu_utilization_daily m
  join aws_ec2_instance i on i.instance_id = m.instance_id
where
  m.timestamp >= now() - interval '7 days'
order by
  m.instance_id, collection_date;
