-- Load balancers that bill hourly for nothing. Two signals:
--   no_targets  — no registered targets at all (any type), or no instances
--                 (classic). The clearest waste.
--   no_traffic  — ALB/NLB that DO have targets but saw zero requests / flows
--                 over the last 30 days (and are themselves older than 30 days,
--                 so a freshly-created LB isn't flagged before it has traffic).
-- Adapted from steampipe-mod-aws-thrifty (Apache-2.0), ec2_*_lb_unused.
with lb_targets as (
  select
    lb_arn,
    sum(coalesce(jsonb_array_length(tg.target_health_descriptions), 0)) as registered_targets
  from
    aws_ec2_target_group tg,
    jsonb_array_elements_text(tg.load_balancer_arns) as lb_arn
  group by
    lb_arn
),
alb_req as (
  select name, coalesce(sum(sum), 0) as req_30d
  from aws_ec2_application_load_balancer_metric_request_count_daily
  where timestamp >= now() - interval '30 days'
  group by name
),
nlb_flow as (
  select name, coalesce(sum(sum), 0) as flow_30d
  from aws_ec2_network_load_balancer_metric_net_flow_count_daily
  where timestamp >= now() - interval '30 days'
  group by name
)
select
  'application' as lb_type, a.name, a.arn, a.region,
  coalesce(t.registered_targets, 0) as registered_targets,
  case when coalesce(t.registered_targets, 0) = 0 then 'no_targets' else 'no_traffic' end as reason
from aws_ec2_application_load_balancer a
  left join lb_targets t on t.lb_arn = a.arn
  left join alb_req r on r.name = a.name
where coalesce(t.registered_targets, 0) = 0
   or (coalesce(r.req_30d, 0) = 0 and a.created_time < now() - interval '30 days')

union all

select
  'network' as lb_type, n.name, n.arn, n.region,
  coalesce(t.registered_targets, 0) as registered_targets,
  case when coalesce(t.registered_targets, 0) = 0 then 'no_targets' else 'no_traffic' end as reason
from aws_ec2_network_load_balancer n
  left join lb_targets t on t.lb_arn = n.arn
  left join nlb_flow f on f.name = n.name
where coalesce(t.registered_targets, 0) = 0
   or (coalesce(f.flow_30d, 0) = 0 and n.created_time < now() - interval '30 days')

union all

select
  'gateway' as lb_type, g.name, g.arn, g.region,
  coalesce(t.registered_targets, 0) as registered_targets,
  'no_targets' as reason
from aws_ec2_gateway_load_balancer g
  left join lb_targets t on t.lb_arn = g.arn
where coalesce(t.registered_targets, 0) = 0

union all

-- Classic LBs register instances directly, no target groups
select
  'classic' as lb_type, c.name, c.arn, c.region,
  0 as registered_targets,
  'no_instances' as reason
from aws_ec2_classic_load_balancer c
where coalesce(jsonb_array_length(c.instances), 0) = 0;
