# config/

Remediation policy — the safeguard thresholds, whitelist, schedule and
feature toggles read by `src/core/config.py`'s `RemediationConfig.from_yaml()`
at runtime, and enforced by `src/core/safeguards.py`.

| File | Purpose |
|---|---|
| `remediation.yaml.template` | Versioned in git. The starting point `install.sh` copies to `remediation.yaml` on first run (auto-remediation disabled). |
| `remediation.yaml` | **Not versioned** (gitignored, like `.env`) — the live, local policy. Editable from the UI (Settings page), via `POST /api/config`, or by importing a YAML export (`POST /api/policies/import`). |

## Sections (see `remediation.yaml.template` for the exact defaults)

- `auto_remediation` — master on/off switch (`enabled: false` by default) and the per-action-type toggles (`stop_instance`, `delete_volume`, …).
- `approval.grace_period_days` — delay between approval and execution; `0` = immediate but still cancellable until it runs.
- `protection` — the four numeric safeguard thresholds: `min_instance_age_days`, `min_idle_days`, `min_confidence_score`, `max_instances_per_run`.
- `whitelist` — `instance_ids` and `tags` excluded from any remediation regardless of confidence.
- `schedule` — restricts *when* auto-remediation may execute (`allowed_days`, `allowed_hours`, `timezone`); disabled (`enabled: false`) means no time restriction.
- `terraform_pr` — GitOps mode: repo, base branch, and the EUR threshold above which a change must go through a PR instead of direct execution.
- `aws` — region/role used when the policy itself needs to resolve a role (most AWS auth actually comes from `.env`, see `src/core/aws_clients.py`).
- `dry_run` — global dry-run flag; `true` by default, independent of `auto_remediation.enabled`.

Round-trip the whole policy as YAML from Settings → Policy as Code
(`GET /api/policies/export` / `POST /api/policies/import`) to version a
specific configuration in git without touching this template.
