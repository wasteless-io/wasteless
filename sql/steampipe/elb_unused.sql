-- Load balancers with no registered targets: they bill hourly for nothing.
-- Goes further than the Thrifty original: also flags LBs whose attached
-- target groups exist but have zero registered targets.
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
)
select
  'application' as lb_type, a.name, a.arn, a.region
from aws_ec2_application_load_balancer a
  left join lb_targets t on t.lb_arn = a.arn
where coalesce(t.registered_targets, 0) = 0

union all

select
  'network' as lb_type, n.name, n.arn, n.region
from aws_ec2_network_load_balancer n
  left join lb_targets t on t.lb_arn = n.arn
where coalesce(t.registered_targets, 0) = 0

union all

select
  'gateway' as lb_type, g.name, g.arn, g.region
from aws_ec2_gateway_load_balancer g
  left join lb_targets t on t.lb_arn = g.arn
where coalesce(t.registered_targets, 0) = 0

union all

-- Classic LBs register instances directly, no target groups
select
  'classic' as lb_type, c.name, c.arn, c.region
from aws_ec2_classic_load_balancer c
where coalesce(jsonb_array_length(c.instances), 0) = 0;
