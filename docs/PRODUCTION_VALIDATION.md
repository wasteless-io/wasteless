# Production validation — real auto-remediation on a sandbox

> Reproducible procedure that proves the core value of wasteless — **acting on
> AWS** — end to end against a real (throwaway) account, with real detection,
> a real `stop_instances` call, real audit rows, and a real rollback.
>
> This closes item #1 of [MATURITY_TODO.md](MATURITY_TODO.md): moving from
> "dry-run by default, never exercised live" to "demonstrated on a sandbox with
> captured evidence".

**Run this only against a dedicated sandbox AWS account you own.** It stops a
real instance and incurs (tiny) real cost. Never point it at an account with
production workloads. Always finish with `terraform destroy`.

---

## 0. Prerequisites

- A sandbox AWS account + credentials on `PATH` (see [AWS_SETUP.md](AWS_SETUP.md)
  and [../onboarding/](../onboarding/) for the read/write role split).
- Postgres up: `docker-compose up -d postgres`.
- Root venv active, schema applied (`sql/init.sql` + migrations).

## 1. Create the wasteful fixtures

```bash
cd terraform/test-fixtures
terraform init
terraform apply        # ~0.09 USD/h total — see fixtures.tf cost notes
terraform output remediation_target_instance_id   # note this instance id
```

The `wasteless-fixture-idle-target` instance (t3.nano, no workload) is the
remediation target: with nothing running its CPU stays near 0%.

## 2. Let idle metrics accumulate

The `ec2_idle` detector needs ~7 days of CloudWatch data averaging < 5% CPU,
and the safeguards require `min_idle_days: 14` and `min_instance_age_days: 30`.
Two options:

- **Faithful:** leave the fixture running and re-run collection daily for the
  full window (most honest, but slow and costs ~2 USD over two weeks).
- **Fast (documented shortcut):** in the sandbox `config/remediation.yaml`,
  temporarily lower `protection.min_instance_age_days` and
  `protection.min_idle_days` to `0`, and seed a few days of low-CPU rows into
  `ec2_metrics` for the target instance. Note in the evidence that thresholds
  were relaxed — this validates the *mechanism*, not the 14/30-day policy.

Run collection + detection:

```bash
source venv/bin/activate
wasteless collect        # or: python3 src/collectors/aws_cloudwatch.py && python3 src/detectors/ec2_idle.py
```

Confirm the target shows up:

```sql
SELECT resource_id, confidence_score, estimated_monthly_savings
FROM waste_detected WHERE resource_id = '<target-instance-id>';
```

## 3. Enable auto-remediation — scoped to the sandbox only

In the **sandbox** `config/remediation.yaml` (never the template, never a real
account's config):

```yaml
auto_remediation:
  enabled: true
  actions:
    stop_instance: true
dry_run: false
schedule:
  enabled: false          # or set an allowed window; false = always allowed
```

Make sure the fixture is **not** whitelisted: it must not carry
`Environment=Production` or `Critical=true` tags (the fixtures don't), and its
id must not be in `whitelist.instance_ids`.

## 4. Run the remediation and watch the write happen

```bash
python3 -c "from remediators.ec2_remediator import EC2Remediator; \
            EC2Remediator(dry_run=False).process_pending_recommendations()"
```

Then confirm on AWS side the instance actually stopped:

```bash
aws ec2 describe-instances --instance-ids <target-instance-id> \
  --query 'Reservations[].Instances[].State.Name'   # expect: ["stopping"|"stopped"]
```

## 5. Capture the evidence

The whole point of the exercise — these rows are the proof:

```sql
-- the action itself
SELECT id, resource_id, action_type, status, created_at
FROM actions_log WHERE resource_id = '<target-instance-id>' ORDER BY created_at DESC;

-- the rollback snapshot (state_before captured, can_rollback = true)
SELECT id, resource_id, can_rollback, rollback_expiry
FROM rollback_snapshots WHERE resource_id = '<target-instance-id>';

-- realized savings, once verified via Cost Explorer
SELECT * FROM savings_realized WHERE resource_id = '<target-instance-id>';
```

Save this output (screenshots or a `.txt`) alongside the run date and the
instance id. That is the artifact this procedure exists to produce.

## 6. Prove rollback works

```bash
python3 -c "from remediators.ec2_remediator import EC2Remediator; \
            print(EC2Remediator(dry_run=False).start_instance('<target-instance-id>', reason='validation rollback'))"
```

Expect `success=True` and the instance back to `running` on AWS.

## 7. Tear down — mandatory

```bash
cd terraform/test-fixtures && terraform destroy
```

Then **revert** the sandbox `config/remediation.yaml` (`enabled: false`,
`dry_run: true`) and restore any relaxed protection thresholds.

---

## Wiring it into CI later

Once this has been run by hand and the evidence captured, the same flow can be
promoted to the gated `live-aws` job in
[`.github/workflows/nightly-integration.yml`](../.github/workflows/nightly-integration.yml):
tag the live tests `-m live_aws`, set the `AWS_SANDBOX_*` repo secrets/vars,
and flip `AWS_SANDBOX_ENABLED` to `true`. Until then that job stays skipped and
no pipeline ever touches a real account.
