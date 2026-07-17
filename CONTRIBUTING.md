# Contributing to Wasteless

Thank you for considering contributing to Wasteless.

---

## Ways to contribute

- **Bug reports** — Open an [issue](https://github.com/wasteless-io/wasteless/issues) with steps to reproduce, expected vs actual behavior, and your environment (OS, Python, Docker versions)
- **Feature requests** — Open an issue describing the problem, proposed solution, and use case
- **Documentation** — Fix typos, clarify sections, add examples
- **Code** — Fix bugs, add detectors, improve the UI

---

## Development setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/wasteless.git
cd wasteless

# Add upstream
git remote add upstream https://github.com/wasteless-io/wasteless.git

# Install both Python environments, PostgreSQL and development tools
./install.sh --no-schedule
```

---

## Branch strategy

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready, tagged releases |
| `dev` | Integration branch — base for all PRs |

Create branches from `dev`:

```bash
git checkout dev
git pull upstream dev
git checkout -b feature/your-feature
```

Naming conventions:
- `feature/add-rds-detector`
- `bugfix/fix-postgres-timeout`
- `docs/update-contributing`
- `refactor/simplify-cost-calc`

---

## Commit messages

Format: `type: description`

| Type | When to use |
|------|-------------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `refactor:` | Code restructuring, no behavior change |
| `test:` | Adding or fixing tests |
| `chore:` | Maintenance (deps, config) |

---

## Code standards

- Format with **Black**: `./venv/bin/black src/ ui/ tests/`
- Run the complete local quality gate with `make lint` (Black, Ruff, mypy and
  shellcheck; CI also runs `pip-audit` on both runtime lock files)
- No hardcoded credentials or secrets
- Error handling on all AWS/DB calls

---

## Testing

```bash
# Backend
make test

# UI
make test-ui

# Formatting, lint, types and shell scripts
make lint
```

---

## Pull request process

1. Branch from `dev`
2. Make changes, add tests if applicable
3. Format and lint
4. Open PR against `dev` with a clear description
5. Work through the review checklist in the PR template — it covers this
   repo's specific risks (safeguards order, action registry, pricing
   provenance, dry-run defaults), not just style
6. Address review feedback
7. Merged by maintainers once approved

Maintainer changes follow the same discipline: anything touching
`src/core/safeguards.py`, `src/remediators/`, `ui/jobs.py` or a detector's
`save()`/`recommend()` goes through a PR with the same checklist, even
without an external reviewer — CI (lint + 4 test jobs) is the minimum
second gate, the checklist is the second pair of eyes.

---

## Adding a new detector

1. Choose the existing boto3 or Steampipe pattern; do not create a second
   implementation of an existing rule.
2. Declare the recommendation type and its execution mode in
   `ui/utils/action_registry.py`.
3. Add migrations and unit tests where required.
4. Wire the detector into `wasteless.sh collect`.
5. Update `src/detectors/README.md`, `docs/ARCHITECTURE.md` and the product
   capability summary when its scope changes.

The complete workflow and examples are in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#adding-a-new-detector).

---

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
