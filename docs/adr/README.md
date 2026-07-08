# Architecture Decision Records (ADR)

Short records of the **non-obvious** decisions in wasteless — the ones a new
maintainer would otherwise have to reverse-engineer from code comments or, worse,
re-litigate by breaking something. Each ADR is one decision: context, the call,
and what it costs us.

These reduce the bus factor (MATURITY_TODO.md #4): the *why* lives here, not only
in inline comments.

| ADR | Decision |
|---|---|
| [0001](0001-sync-routes-and-connection-pool.md) | Route handlers are sync `def` + a threaded connection pool |
| [0002](0002-one-canonical-detector-per-resource.md) | Exactly one detector implementation per resource type |
| [0003](0003-auto-remediation-off-by-default.md) | Auto-remediation is disabled by default; dry-run first, 7 safeguards |
| [0004](0004-two-python-environments.md) | Two separate Python environments (backend vs UI) |
| [0005](0005-scoped-mypy-and-coverage-gates.md) | mypy and coverage gates are scoped, not whole-repo |

## Writing a new one

Copy the shape of an existing ADR. Number sequentially, keep it to a screen,
and state the decision in the title. Mark superseded ADRs rather than deleting
them — the history is the point.
