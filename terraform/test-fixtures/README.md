# Test fixtures

Real AWS resources to validate the wasteless detectors under business-like
conditions. One billed fixture per detector:

| Fixture | Detector validated | Cost |
|---|---|---|
| Unassociated Elastic IP | `eip_orphan` | ~$0.005/h |
| Private NAT gateway (no traffic) | `nat_gateway_unused` | ~$0.048/h |
| t3.nano + attached 4 GiB gp2 volume | `ebs_gp2_migration` | ~$0.006/h |
| Internal ALB with no target group | `elb_unused` | ~$0.025/h |

Age-based detections (`snapshot_orphan` > 90 days, `ec2_idle` 7 days of
CloudWatch data) cannot be fabricated instantly and are validated against
naturally occurring resources instead.

## Usage

```bash
cd terraform/test-fixtures
terraform init
terraform apply            # ~0.08 USD/hour total, everything tagged wasteless:test-fixture
# run the detectors from the repo root, verify recommendations in the UI
terraform destroy          # ALWAYS — do not leave billed fixtures running
```

Verify nothing is left after destroy:

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=wasteless,Values=test-fixture --region eu-west-3
```
