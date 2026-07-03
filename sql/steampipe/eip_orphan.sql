-- Unassociated Elastic IPs (billed ~3.36 EUR/month each while idle).
-- Same criteria as src/detectors/eip_orphan.py: no instance, no ENI.
-- public_ip is inet-typed; host() converts it to text.
select
  coalesce(allocation_id, host(public_ip)) as allocation_id,
  host(public_ip)                          as public_ip,
  coalesce(domain, 'vpc')             as domain,
  region
from
  aws_vpc_eip
where
  instance_id is null
  and network_interface_id is null;
