#!/usr/bin/env bash
# Creates 2 EC2 instances (t4g.micro, cheapest type) and 2 unattached
# 1GB gp3 EBS volumes per continent, in the cheapest enabled region of
# each continent. Everything is tagged wasteless-test=true so
# teardown_test_resources.sh can find and delete it all.
# Africa (af-south-1) and Middle East (me-south-1) are opt-in regions
# not enabled on this account, so they are skipped.
set -uo pipefail

# continent: region (cheapest t4g.micro on-demand of the continent)
REGIONS=(
  "us-east-1"        # North America, N. Virginia
  "sa-east-1"        # South America, Sao Paulo (only region)
  "eu-north-1"       # Europe, Stockholm
  "ap-south-1"       # Asia, Mumbai
  "ap-southeast-2"   # Oceania, Sydney (only region)
)

for R in "${REGIONS[@]}"; do
  echo "== $R =="
  AMI=$(aws ssm get-parameter --region "$R" \
    --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
    --query 'Parameter.Value' --output text) || { echo "  no AMI, skipping"; continue; }
  read -r SUBNET AZ <<< "$(aws ec2 describe-subnets --region "$R" \
    --filters Name=default-for-az,Values=true \
    --query 'Subnets[0].[SubnetId,AvailabilityZone]' --output text)"
  if [ "$SUBNET" = "None" ] || [ -z "$SUBNET" ]; then
    echo "  no default subnet, skipping"; continue
  fi

  aws ec2 run-instances --region "$R" --count 2 \
    --instance-type t4g.micro --image-id "$AMI" --subnet-id "$SUBNET" \
    --tag-specifications \
      "ResourceType=instance,Tags=[{Key=wasteless-test,Value=true},{Key=Name,Value=wasteless-test-ec2-$R}]" \
      "ResourceType=volume,Tags=[{Key=wasteless-test,Value=true},{Key=Name,Value=wasteless-test-ec2-root-$R}]" \
    --query 'Instances[].InstanceId' --output text

  for i in 1 2; do
    aws ec2 create-volume --region "$R" --availability-zone "$AZ" \
      --size 1 --volume-type gp3 \
      --tag-specifications \
        "ResourceType=volume,Tags=[{Key=wasteless-test,Value=true},{Key=Name,Value=wasteless-test-ebs-$R-$i}]" \
      --query 'VolumeId' --output text
  done
done

echo "Done. All resources tagged wasteless-test=true."
