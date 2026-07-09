# Cleanup Guide — Obsolete Recommendations

> What happens to recommendations when the underlying AWS resource is
> deleted outside Wasteless (e.g. terminated in the AWS Console).

---

## The problem

Wasteless recommends actions on resources it detected. If you delete one of
those resources manually, the recommendation would otherwise linger as
`pending` and pollute dashboards and reports.

## Automatic cleanup (default — nothing to install)

The UI runs `sync_aws_job` every 5 minutes (APScheduler, inside the FastAPI
process). It re-checks every non-terminal recommendation against live AWS
state and marks it `obsolete` when the resource is gone. This covers **all
resource types** the detectors know about (EC2 instances, EBS volumes,
Elastic IPs, snapshots, load balancers, NAT gateways, VPCs) — see
`ui/utils/aws_sync.py` and `ui/jobs.py`.

As long as the UI is running (`wasteless start`), cleanup is automatic. No
cron job needed.

## Manual cleanup (UI not running)

A standalone utility exists for EC2 recommendations, useful for maintenance
on a database while the UI is down:

```bash
source venv/bin/activate

# Preview — no changes
python src/utils/cleanup_orphaned_recommendations.py --dry-run

# Apply
python src/utils/cleanup_orphaned_recommendations.py
```

It lists every active EC2 recommendation whose instance no longer exists in
AWS, then (without `--dry-run`) sets their status to `obsolete`. It never
touches AWS resources — database-only.

Note: it covers **EC2 only**; the UI's `sync_aws_job` is the complete
implementation.

---

## What `obsolete` means

- The recommendation disappears from active dashboards and reports.
- The row is kept for auditing — nothing is deleted.
- Historical savings data is unaffected.

To restore one manually:

```sql
UPDATE recommendations SET status = 'pending' WHERE id = 123;
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Stale recommendations persist | Is the UI running? `./wasteless.sh status`. The sync job only runs inside the UI process |
| Sync job errors | `/logs` page in the UI, or `~/.wasteless.log` |
| Manual utility: AWS credential error | Same credentials as the pipeline (`.env`, `AWS_ROLE_ARN`); test with `aws sts get-caller-identity` |
| Manual utility: DB connection error | `docker ps` shows postgres? Check `DB_*` in `.env` |

---

## Related documentation

- [Automation Guide](AUTOMATION_GUIDE.md) — the full scheduling picture
- [Deployment](DEPLOYMENT.md) — production setup
