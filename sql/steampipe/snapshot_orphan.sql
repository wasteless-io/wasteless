-- Old EBS snapshots (> 90 days, mirrors SNAPSHOT_AGE_DAYS) owned by this
-- account that are safe to flag for deletion. Exclusions (adapted from
-- steampipe-mod-aws-thrifty, Apache-2.0):
--   1. snapshots backing a registered AMI (deleting them breaks the AMI);
--      keep the CTE in sync with ami_backed_snapshots.sql
--   2. snapshots managed by AWS Backup or Data Lifecycle Manager (their
--      policies own the retention; deleting breaks backup chains)
--   3. snapshots whose source volume still exists (legitimate backup of a
--      live volume, not an orphan)
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
  left join aws_ebs_volume v on v.volume_id = s.volume_id
where
  s.owner_id = s.account_id
  and s.start_time < now() - interval '90 days'
  and s.snapshot_id not in (select snapshot_id from ami_snapshots)
  -- coalesce: tags is often null, and `not (null ? key)` is null (row lost)
  and not coalesce(s.tags ? 'aws:backup:source-resource', false)
  and not coalesce(s.tags ? 'aws:dlm:lifecycle-policy-id', false)
  and v.volume_id is null
order by
  s.start_time asc;
