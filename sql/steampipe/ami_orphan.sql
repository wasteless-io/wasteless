-- Self-owned AMIs older than 90 days that no longer back any EC2 instance.
-- The AMI itself is free, but each one pins its backing EBS snapshots, which
-- DO cost storage — deregistering the AMI is the prerequisite to deleting
-- those snapshots. block_device_mappings holds the backing snapshots
-- (Ebs.SnapshotId / Ebs.VolumeSize). backing_gb uses the source volume size,
-- which overestimates the (compressed, incremental) snapshot storage — a
-- deliberately conservative cost for a manual-review recommendation.
with ami_backing as (
  select
    a.image_id,
    a.name,
    a.creation_date,
    a.region,
    a.platform_details,
    coalesce(sum((bdm -> 'Ebs' ->> 'VolumeSize')::numeric), 0) as backing_gb,
    count(bdm -> 'Ebs' ->> 'SnapshotId') as snapshot_count
  from
    aws_ec2_ami a
    left join jsonb_array_elements(a.block_device_mappings) as bdm
      on (bdm -> 'Ebs') is not null
  where
    a.owner_id = a.account_id          -- only images we own
    and a.state = 'available'
    and a.creation_date < now() - interval '90 days'
  group by
    a.image_id, a.name, a.creation_date, a.region, a.platform_details
)
select
  b.image_id,
  b.name,
  b.region,
  b.platform_details,
  b.backing_gb,
  b.snapshot_count,
  extract(day from now() - b.creation_date)::int as age_days
from
  ami_backing b
where
  b.image_id not in (
    select image_id from aws_ec2_instance where image_id is not null
  );
