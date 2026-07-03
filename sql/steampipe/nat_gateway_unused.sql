-- Unused NAT gateways: not in 'available' state, or zero outbound traffic
-- over the last 30 days. NAT gateways bill hourly (~32 EUR/month) even idle.
-- Adapted from steampipe-mod-aws-thrifty (Apache-2.0), vpc_nat_gateway_unused.
select
  nat.nat_gateway_id,
  nat.vpc_id,
  nat.state,
  nat.region,
  coalesce(sum(m.average), 0)::bigint as bytes_out_30d
from
  aws_vpc_nat_gateway nat
  left join aws_vpc_nat_gateway_metric_bytes_out_to_destination m
    on m.nat_gateway_id = nat.nat_gateway_id
   and m.timestamp >= now() - interval '30 days'
group by
  nat.nat_gateway_id, nat.vpc_id, nat.state, nat.region
having
  nat.state <> 'available'
  or coalesce(sum(m.average), 0) = 0;
