# src/reports/

Turns database rows into the Markdown/text reports surfaced in the UI and
CLI. Numbers are always computed deterministically in SQL/Python first; the
LLM (via `src/core/llm.py`) only adds narrative commentary on top — it never
invents a figure.

| File | Purpose |
|---|---|
| `audit_report.py` | `generate_audit_report()` — deterministic Markdown assembly for the full AWS FinOps audit report (the "golden" report format checked against `tests/snapshots/golden_aws_audit_report.md`). Guided by the system prompt in `prompts/audit_report_system_prompt.md`. |
| `weekly_digest.py` | `build_digest()` — a period's activity in one message: `collect_digest_data()` pulls the numbers, `format_digest()` renders them, `generate_narrative()` asks the LLM for a one-paragraph summary (used by `/api/reports/narrative`). |
| `daily_briefing.py` | `get_or_create_briefing()` — one AI-written CTO status message per day, cached in the `daily_briefings` table (see `sql/migrations/daily_briefings.sql`) so the LLM is called at most once/day. `collect_briefing_data()` + `build_briefing_prompt()` feed `generate_briefing()`. Backs `/api/briefing/today`. |

## prompts/

`audit_report_system_prompt.md` — the system prompt for the audit report's
AI narrative layer. Keep it in sync with `src/core/finops_invariants.py`'s
CTO-safe wording rules (`validate_claim_wording`, `generate_cto_safe_summary`)
— the file's own header comment tracks corrections made after past reviews;
update that log when you change the forbidden-words list or category names.
