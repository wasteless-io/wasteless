-- Old EBS snapshots (> 90 days, mirrors SNAPSHOT_AGE_DAYS) owned by this
-- account, excluding snapshots that back a registered AMI — the exclusion
-- is a SQL anti-join here instead of the extra describe_images pass the
-- boto3 detector needs. Keep the CTE in sync with ami_backed_snapshots.sql.
with ami_snapshots as (
  select distinct
    mapping -> 'Ebs' ->> 'SnapshotId' as snapshot_id
  from
    aws_ec2_ami,
    jsonb_array_elements(block_device_mappings) as mapping
  where
    mapping -> 'Ebs' ->> 'SnapshotId' is not null
)
select
  s.snapshot_id,
  coalesce(s.description, '')                       as description,
  coalesce(s.volume_id, '')                         as volume_id,
  s.volume_size                                     as size_gb,
  s.state,
  s.start_time,
  extract(day from now() - s.start_time)::int       as age_days,
  s.encrypted,
  s.region
from
  aws_ebs_snapshot s
where
  s.owner_id = s.account_id
  and s.start_time < now() - interval '90 days'
  and s.snapshot_id not in (select snapshot_id from ami_snapshots)
order by
  s.start_time asc;
