# ADR-0004 — Two separate Python environments (backend vs UI)

Status: accepted

## Context

The backend pipeline (`src/`) and the FastAPI UI (`ui/`) have different
dependency sets — for example the UI pins `httpx` to a v2 line intentionally,
which the backend doesn't want. A single environment forces one to compromise.

## Decision

Two environments with separate `requirements.txt`:

- Root `venv/` for the backend pipeline (`src/`).
- `ui/venv/` for the FastAPI UI.

`src/` is packaged as an editable install (`pyproject.toml`,
`package-dir = {"" = "src"}`) and `pip install -e .`'d into `ui/venv/`, so the UI
does clean imports like `from remediators.ec2_remediator import EC2Remediator`
with no `sys.path` hacks. Only the subpackages with `__init__.py` (`core`,
`detectors`, `remediators`, `reports`, `collectors`) are packaged; `trackers`
and `utils` are out of scope (`utils` would collide with `ui/utils/`).

Local venvs live in `.nosync` dirs with a `venv` symlink, to keep iCloud from
syncing them.

## Consequences

- After changing `src/`'s package structure, re-run `pip install -e .` in
  `ui/venv/` (or `make doctor` on `ModuleNotFoundError`).
- Two dependency files to maintain; CI installs both where needed.
