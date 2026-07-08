# ADR-0002 — Exactly one detector implementation per resource type

Status: accepted

## Context

`ebs_orphan`, `eip_orphan`, and `snapshot_orphan` each once had **two**
implementations: a boto3 one and a Steampipe one. Nothing kept the two in sync,
so they drifted — different thresholds, different edge-case handling — and it was
unclear which one actually ran.

## Decision

Each resource type has **exactly one canonical detector**. Never both a boto3 and
a Steampipe implementation of the same rule. The Steampipe duplicates of the
three detectors above were removed.

New detectors follow the Steampipe pattern (SQL in `sql/steampipe/<name>.sql` +
a `SteampipeWasteDetector` subclass) unless there's a concrete reason boto3 is
required.

## Consequences

- No ambiguity about which code path produced a recommendation.
- A detector must be wired into `wasteless.sh`'s `_collect()` to actually run —
  writing the class alone does nothing.
- Steampipe steps are skipped (with one warning) when the `steampipe` CLI is
  absent, rather than failing the whole collection run.
