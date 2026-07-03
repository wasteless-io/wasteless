-- Orphaned EBS volumes (state = 'available', attached to nothing).
-- Runs against Steampipe's aws_ebs_volume table; regions come from
-- ~/.steampipe/config/aws.spc, so no per-region loop is needed.
select
  volume_id,
  coalesce(tags ->> 'Name', '')                     as name,
  size                                              as size_gb,
  volume_type                                       as vol_type,
  availability_zone                                 as az,
  region,
  encrypted,
  extract(day from now() - create_time)::int        as age_days
from
  aws_ebs_volume
where
  state = 'available'
order by
  size desc;
