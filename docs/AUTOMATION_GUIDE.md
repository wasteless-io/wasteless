# Automation Guide

> How Wasteless runs collection, detection, and cleanup automatically — one
> command, no hand-written crontab.

---

## TL;DR

```bash
./wasteless.sh schedule      # install OS-level auto-collection (every 5 min)
./wasteless.sh status        # UI state + schedule state
./wasteless.sh unschedule    # remove it
./wasteless.sh collect       # run the pipeline once, manually
```

`wasteless schedule` is the canonical automation path. It survives reboots,
picks the right backend for your OS, and runs the **full** pipeline — not
just the EC2 idle detector.

---

## What runs, and when

### The collection pipeline (`wasteless collect`, every 5 minutes)

One run executes 10 steps against the root `venv`:

1. CloudWatch metrics collection (`src/collectors/aws_cloudwatch.py`)
2. Idle EC2 detection
3. Long-stopped EC2 detection
4. Orphaned EBS volume detection
5. Unassociated Elastic IP detection
6. Old EBS snapshot detection
7. Unused load balancer detection (Steampipe)
8. Unused NAT gateway detection (Steampipe)
9. Unused VPC detection (Steampipe)
10. gp2→gp3 migration candidates (Steampipe)

Steps 7–10 need the `steampipe` CLI (`brew install turbot/tap/steampipe &&
steampipe plugin install aws`). Without it they are skipped with a single
warning, and the run is recorded as **partial** in the `collection_runs`
table so the UI can flag under-reporting instead of hiding it.

Concurrent runs are prevented by an atomic lock
(`~/.wasteless-collect.lock.d`) — overlapping schedules are harmless.

### UI background jobs (APScheduler, inside the FastAPI process)

While the UI is running, three jobs fire every 5 minutes on their own —
nothing to install:

| Job | Purpose |
|---|---|
| `sync_aws_job` | Syncs EC2 states and marks recommendations `obsolete` when the underlying resource no longer exists (all resource types — see [CLEANUP_GUIDE.md](CLEANUP_GUIDE.md)) |
| `grace_executor_job` | Executes scheduled remediations whose grace period expired |
| `terraform_pr_sync_job` | Tracks the state of open Terraform remediation PRs |

---

## Scheduler backends

`wasteless schedule` auto-detects the platform:

| Platform | Backend | Notes |
|---|---|---|
| macOS | launchd LaunchAgent (`~/Library/LaunchAgents/io.wasteless.collect.plist`) | `RunAtLoad` + every 5 min |
| Linux with systemd | systemd **user** timer (`wasteless-collect.timer`) | `loginctl enable-linger` is set so it runs without an open SSH session — required on a headless VPS |
| Linux/WSL without systemd | crontab entry (marked `# wasteless-collect`) | On WSL, cron only runs while the distro is up; the command prints how to enable systemd or use Windows Task Scheduler instead |

If no scheduler is installed, `wasteless start` falls back to an in-process
loop (every 5 min) tied to that session — it does **not** survive
`wasteless stop` or a reboot. Install the real schedule for anything beyond
a local test.

---

## Logs & monitoring

All scheduled output is appended to `~/.wasteless.log`:

```bash
./wasteless.sh logs          # tail -f
tail -100 ~/.wasteless.log   # recent runs
grep -i error ~/.wasteless.log | tail -20
```

Check run completeness in the database:

```sql
SELECT ran_at, full_run, skipped_steps
FROM collection_runs ORDER BY ran_at DESC LIMIT 10;
```

Backend health:

```bash
# macOS
launchctl list | grep io.wasteless

# systemd
systemctl --user status wasteless-collect.timer
journalctl --user -u wasteless-collect --since today

# cron
crontab -l | grep wasteless-collect
```

---

## Cost considerations

The 5-minute cadence keeps dashboards near-real-time. The AWS API cost is
dominated by CloudWatch `GetMetricStatistics` calls and scales with instance
count — on the order of **a few euros per month** for a typical account
(CloudWatch API requests are ~$0.01 per 1,000). Detection steps read from
PostgreSQL and Steampipe's cached inventory, not from AWS directly. The
savings surfaced by detection exceed this by orders of magnitude; if it ever
matters, the interval is `COLLECT_INTERVAL_SEC` at the top of
`wasteless.sh` (re-run `wasteless schedule` after changing it).

---

## FAQ

**Multiple AWS accounts?** One clone + `.env` + `wasteless schedule` per
account. Native multi-account support is on the roadmap.

**Pause temporarily?** `./wasteless.sh unschedule`, then
`./wasteless.sh schedule` to resume.

**Change the interval?** Edit `COLLECT_INTERVAL_SEC` in `wasteless.sh`
(seconds), then re-run `./wasteless.sh schedule` to re-install the backend
with the new value.

**Docker deployment?** The scheduler runs on the host (it needs the root
`venv` and the AWS credentials); only PostgreSQL/Metabase are containerized.

**What replaced `scripts/install_automation.sh`?** That legacy cron
installer only ran the CloudWatch collector and the EC2 idle detector — 2 of
the 10 pipeline steps — and drifted out of sync as detectors were added. It
was removed; `wasteless schedule` is the single canonical path.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| No new data in the UI | `tail ~/.wasteless.log`; run `./wasteless.sh collect` manually and read the step output |
| Schedule inactive after reboot (Linux) | `loginctl show-user $USER` must show `Linger=yes`; fix with `sudo loginctl enable-linger $USER` |
| Steps 7–10 always skipped | `command -v steampipe`; install it and the AWS plugin |
| `venv not found` in the log | Recreate the root venv: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.lock` |
| AWS credential errors | The pipeline loads `.env` itself (`load_dotenv`); check `AWS_ROLE_ARN` / source credentials, then `aws sts get-caller-identity` |
| DB connection errors | `docker ps` shows the postgres container? If not: `docker compose up -d postgres` |

---

## Related documentation

- [Deployment](DEPLOYMENT.md) — VPS setup, reverse proxy, backups
- [Cleanup Guide](CLEANUP_GUIDE.md) — obsolete-recommendation handling
- [AWS Setup](AWS_SETUP.md) — IAM roles and onboarding
