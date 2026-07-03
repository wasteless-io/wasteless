-- Snapshot IDs backing a registered AMI owned by this account.
-- These must never be flagged for deletion (deleting them breaks the AMI).
select distinct
  mapping -> 'Ebs' ->> 'SnapshotId' as snapshot_id
from
  aws_ec2_ami,
  jsonb_array_elements(block_device_mappings) as mapping
where
  mapping -> 'Ebs' ->> 'SnapshotId' is not null;
