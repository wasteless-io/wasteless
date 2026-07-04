-- Unused VPCs: no network interface at all in the VPC. Every running
-- resource (EC2, load balancer, NAT gateway, RDS, VPC endpoint...) creates
-- an ENI, so zero ENI means nothing runs there. A VPC itself costs 0 EUR —
-- this is hygiene, not savings. Default VPCs are included but flagged
-- (AWS only recreates a deleted default VPC on request).
select
  v.vpc_id,
  v.region,
  v.cidr_block::text as cidr_block,
  v.is_default,
  coalesce(v.tags ->> 'Name', '') as name
from
  aws_vpc v
  left join aws_ec2_network_interface eni on eni.vpc_id = v.vpc_id
where
  eni.network_interface_id is null;
