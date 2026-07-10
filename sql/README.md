# sql/

Database schema for the PostgreSQL instance started by `docker compose up -d
postgres`. `install.sh` (and CI) apply these files in order on first run:
`init.sql`, then `ec2_metrics.sql`, then every `migrations/*.sql`.

| Path | Purpose |
|---|---|
| `init.sql` | Base schema applied first: `cloud_costs_raw` and the other foundational tables. |
| `ec2_metrics.sql` | Adds `ec2_metrics` (daily CloudWatch CPU/network rows) — historically "migration 001", still applied as a standalone step by `install.sh` before the `migrations/` folder. |
| `migrations/` | Everything added after the base schema, one file per change, applied in filename order. See [migrations/README.md](migrations/README.md). |
| `steampipe/` | Not schema — read-only inventory queries run against Steampipe's AWS plugin tables (not PostgreSQL). See [steampipe/README.md](steampipe/README.md). |

Apply order at install time: `init.sql` → `ec2_metrics.sql` →
`migrations/*.sql` (see `install.sh`, section "5/7").
