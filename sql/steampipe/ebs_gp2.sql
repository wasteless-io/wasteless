-- Attached gp2 volumes: gp3 is ~20% cheaper at equal or better baseline
-- performance, and the migration is online (no downtime).
-- Unattached gp2 volumes are excluded: ebs_orphan already flags those
-- for deletion, which saves more than migrating them.
-- Adapted from steampipe-mod-aws-thrifty (Apache-2.0), gp2_volumes.
select
  volume_id,
  coalesce(tags ->> 'Name', '')  as name,
  size                           as size_gb,
  availability_zone              as az,
  region
from
  aws_ebs_volume
where
  volume_type = 'gp2'
  and state = 'in-use'
order by
  size desc;
