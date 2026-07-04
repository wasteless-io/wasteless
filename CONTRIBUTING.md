# Contributing to Wasteless

Thank you for considering contributing to Wasteless.

---

## Ways to contribute

- **Bug reports** — Open an [issue](https://github.com/wastelessio/wasteless/issues) with steps to reproduce, expected vs actual behavior, and your environment (OS, Python, Docker versions)
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
git remote add upstream https://github.com/wastelessio/wasteless.git

# Backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
docker-compose up -d
cp .env.template .env

# UI
cd ui && ./install.sh
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

- Format with **Black**: `black src/ ui/`
- Lint with **Ruff**: `ruff check src/ ui/`
- No hardcoded credentials or secrets
- Error handling on all AWS/DB calls

---

## Testing

```bash
# Backend
pytest tests/

# UI
cd ui && python run_tests.py
```

---

## Pull request process

1. Branch from `dev`
2. Make changes, add tests if applicable
3. Format and lint
4. Open PR against `dev` with a clear description
5. Address review feedback
6. Merged by maintainers once approved

---

## Adding a new detector

1. Create `src/detectors/your_detector.py`
2. Add SQL migration in `sql/migrations/` if needed
3. Add a test in `tests/`
4. Update the Features table in `README.md`

Structure to follow:

```python
# src/detectors/rds_idle.py
from src.core.database import get_db_connection

class RDSIdleDetector:
    def __init__(self):
        self.conn = get_db_connection()

    def detect(self):
        """Detect idle RDS instances. Returns list of waste dicts."""
        pass
```

---

## Repository structure

```
wasteless/
├── src/
│   ├── collectors/     # CloudWatch metrics collection
│   ├── detectors/      # Waste detection rules
│   ├── remediators/    # Stop / terminate execution
│   ├── trackers/       # Savings verification
│   └── core/           # Database, safeguards
├── ui/                 # FastAPI web dashboard
├── sql/                # Schema + migrations
├── config/             # remediation.yaml
├── tests/              # Integration tests
└── docs/               # Documentation
```

---

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
