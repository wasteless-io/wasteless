#!/usr/bin/env bash
# Deletes every AWS resource created for production-like testing.
# Everything is tagged wasteless-test=true; this script finds resources
# by that tag so it stays correct even if IDs change.
# Regions: eu-west-1 holds the original full set; the per-continent set
# (create_test_resources_continents.sh) added EC2+EBS in 5 more regions.
# Order matters: instances first (frees volumes/ENIs), then volumes,
# EIPs, snapshots, RDS, and finally the VPCs.
set -uo pipefail
TAG_FILTER="Name=tag:wasteless-test,Values=true"

for REGION in us-east-1 sa-east-1 eu-north-1 ap-south-1 ap-southeast-2; do
  export AWS_DEFAULT_REGION=$REGION
  echo "==== $REGION ===="
  IDS=$(aws ec2 describe-instances --filters "$TAG_FILTER" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
  if [ -n "$IDS" ]; then
    aws ec2 terminate-instances --instance-ids $IDS --query 'TerminatingInstances[].InstanceId' --output text
    aws ec2 wait instance-terminated --instance-ids $IDS
  fi
  for v in $(aws ec2 describe-volumes --filters "$TAG_FILTER" --query 'Volumes[].VolumeId' --output text); do
    aws ec2 delete-volume --volume-id "$v" && echo "deleted $v"
  done
  for vpc in $(aws ec2 describe-vpcs --filters "$TAG_FILTER" --query 'Vpcs[].VpcId' --output text); do
    for sn in $(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" --query 'Subnets[].SubnetId' --output text); do
      aws ec2 delete-subnet --subnet-id "$sn" && echo "deleted $sn"
    done
    aws ec2 delete-vpc --vpc-id "$vpc" && echo "deleted $vpc"
  done
done

export AWS_DEFAULT_REGION=eu-west-1
echo "==== eu-west-1 (original full set) ===="

echo "== EC2 instances =="
IDS=$(aws ec2 describe-instances --filters "$TAG_FILTER" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$IDS" ]; then
  aws ec2 terminate-instances --instance-ids $IDS --query 'TerminatingInstances[].InstanceId' --output text
  aws ec2 wait instance-terminated --instance-ids $IDS
fi

echo "== EBS volumes =="
for v in $(aws ec2 describe-volumes --filters "$TAG_FILTER" --query 'Volumes[].VolumeId' --output text); do
  aws ec2 delete-volume --volume-id "$v" && echo "deleted $v"
done

echo "== Elastic IPs =="
for a in $(aws ec2 describe-addresses --filters "$TAG_FILTER" --query 'Addresses[].AllocationId' --output text); do
  aws ec2 release-address --allocation-id "$a" && echo "released $a"
done

echo "== AMIs (deregister before their backing snapshots) =="
for ami in $(aws ec2 describe-images --owners self --filters "$TAG_FILTER" --query 'Images[].ImageId' --output text); do
  aws ec2 deregister-image --image-id "$ami" && echo "deregistered $ami"
done

echo "== Load balancers =="
for lb in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[?starts_with(LoadBalancerName, `wasteless-test`)].LoadBalancerArn' --output text); do
  aws elbv2 delete-load-balancer --load-balancer-arn "$lb" && echo "deleted $lb"
done

echo "== Snapshots =="
for s in $(aws ec2 describe-snapshots --owner-ids self --filters "$TAG_FILTER" --query 'Snapshots[].SnapshotId' --output text); do
  aws ec2 delete-snapshot --snapshot-id "$s" && echo "deleted $s"
done

echo "== RDS =="
for db in $(aws rds describe-db-instances --query 'DBInstances[?starts_with(DBInstanceIdentifier, `wasteless-test-rds`)].DBInstanceIdentifier' --output text); do
  aws rds delete-db-instance --db-instance-identifier "$db" \
    --skip-final-snapshot --delete-automated-backups \
    --query 'DBInstance.DBInstanceStatus' --output text
done
for db in $(aws rds describe-db-instances --query 'DBInstances[?starts_with(DBInstanceIdentifier, `wasteless-test-rds`)].DBInstanceIdentifier' --output text); do
  aws rds wait db-instance-deleted --db-instance-identifier "$db"
done
aws rds delete-db-subnet-group --db-subnet-group-name wasteless-test-subnets 2>/dev/null && echo "deleted subnet group"

echo "== NAT gateways (block VPC deletion while present) =="
NAT_IDS=$(aws ec2 describe-nat-gateways --filter "$TAG_FILTER" \
  "Name=state,Values=pending,available" \
  --query 'NatGateways[].NatGatewayId' --output text)
for n in $NAT_IDS; do
  aws ec2 delete-nat-gateway --nat-gateway-id "$n" && echo "deleting $n"
done
if [ -n "$NAT_IDS" ]; then
  aws ec2 wait nat-gateway-deleted --nat-gateway-ids $NAT_IDS 2>/dev/null || true
fi

echo "== VPCs (subnets first) =="
for vpc in $(aws ec2 describe-vpcs --filters "$TAG_FILTER" --query 'Vpcs[].VpcId' --output text); do
  for sn in $(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" --query 'Subnets[].SubnetId' --output text); do
    aws ec2 delete-subnet --subnet-id "$sn" && echo "deleted $sn"
  done
  aws ec2 delete-vpc --vpc-id "$vpc" && echo "deleted $vpc"
done

echo "Teardown done. Check the AWS console for leftovers if any command errored."
