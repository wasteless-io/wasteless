# ADR-0005 — mypy and coverage gates are scoped, not whole-repo

Status: accepted

## Context

Type-checking and covering the entire codebase in one step would either block on
a large backlog of pre-existing issues or force blanket `# type: ignore`s that
hide real problems. But the code that gates or executes real AWS actions is
exactly where a type error or an untested branch is most expensive.

## Decision

- **mypy** blocks CI over all of `src/core/` (config, safeguards, the
  finops_invariants that gate claim wording, the aws client factory, pricing)
  plus the three UI files that execute real AWS actions
  (`ui/utils/remediator.py`, `ui/jobs.py`, `ui/utils/aws_clients.py`).
  `src/detectors/` and `ui/routes/` are not type-checked yet.
- **Coverage** on `src/` is a blocking gate at `--cov-fail-under=60`
  (current ~65%). The floor is meant to be ratcheted **up**, never lowered.

Both gates are meant to widen over time — the scope is a starting line, not the
target. Ratchet plan, in order (each step = green locally, then added to the CI
command and `make lint`):

1. ~~`src/remediators/` — the code that writes to AWS~~ (done)
2. `src/detectors/steampipe_base.py` + `src/collectors/` — shared plumbing
3. `src/detectors/` — bulk of untyped AWS dicts, lowest yield, last
4. `ui/routes/` — after the detectors, same reasoning

## Consequences

- Adding modules to the mypy command may surface a batch of errors to fix first;
  do it deliberately, one area at a time.
- Lowering the coverage floor to make a red build green is a smell — add tests
  instead.
- `E402` is ignored repo-wide because several scripts do
  `sys.path.insert()`/`load_dotenv()` before local imports.
