-- Unused NAT gateways: 'available' (the only billed state — deleted ones
-- stay API-visible for a while and must not be flagged) with zero outbound
-- traffic over the last 30 days. NAT gateways bill hourly (~32 EUR/month).
-- Adapted from steampipe-mod-aws-thrifty (Apache-2.0), vpc_nat_gateway_unused.
select
  nat.nat_gateway_id,
  nat.vpc_id,
  nat.state,
  nat.region,
  nat.create_time,
  coalesce(sum(m.average), 0)::bigint as bytes_out_30d
from
  aws_vpc_nat_gateway nat
  left join aws_vpc_nat_gateway_metric_bytes_out_to_destination m
    on m.nat_gateway_id = nat.nat_gateway_id
   and m.timestamp >= now() - interval '30 days'
where
  nat.state = 'available'
group by
  nat.nat_gateway_id, nat.vpc_id, nat.state, nat.region, nat.create_time
having
  coalesce(sum(m.average), 0) = 0;
