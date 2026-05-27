# Development Guide

## Running the application

The app runs via Docker Compose. From the directory containing your `docker-compose.yml`:

```bash
docker compose up openhangar-db openhangar-web
```

The Flask app is served at the host configured in your `.env` file (via Traefik).

Set `OPENHANGAR_ENV=development` in your environment to enable Flask's dev server
with auto-reload; any other value runs gunicorn in production mode.

---

## Running the tests

Tests are run locally using a Python virtual environment. The test suite uses
Flask's built-in test client and does not require the database or Docker to be running.

### First-time setup

```bash
python3.11 -m venv .venv      # Python 3.12+ is faster for coverage runs
source .venv/bin/activate
pip install -r requirements/runtime.txt
pip install -r requirements/dev.txt
```

### Running tests

There are three modes depending on what you need:

**Quick run** — use this during active development to check nothing is broken (~63s):

```bash
pytest
```

**Coverage check** — use this before pushing to confirm 100% coverage is maintained.
Also used by CI. Generates `htmlcov/` and `coverage.xml` (~105s):

```bash
bash scripts/run-tests-with-coverage.sh
```

The pre-push hook (see below) checks `coverage.xml` if it was generated in the last 10 minutes,
so running this shortly before `git push` is enough to satisfy it.

### Test layout

```
pytest.ini              # pytest configuration (testpaths, pythonpath, -n auto)
requirements/dev.txt    # test-only dependencies (pytest, coverage, etc.)
tests/
  conftest.py           # shared fixtures: app, client, captured_templates
  test_*.py             # one file per feature area
```

### Notes

- `.venv/` is listed in `.gitignore` and should never be committed.
- Tests run in parallel (`-n auto`, 4 workers) against an in-memory SQLite database.
  No running Docker instance is required.
- Python 3.12+ uses `sys.monitoring` for coverage tracing, which is significantly
  faster than `sys.settrace` on 3.11. If coverage run time matters, consider
  upgrading the venv Python.

---

## Database migrations (Alembic)

Schema changes are managed with [Flask-Migrate](https://flask-migrate.readthedocs.io/) (Alembic under the hood). The migration scripts live in `app/migrations/versions/`.

### Container startup behaviour

`docker-init-db.py` runs automatically on every container start and handles three cases:

| Situation | What happens |
|---|---|
| **Fresh database** | `alembic upgrade head` creates all tables |
| **Existing DB without Alembic** (installed before migrations were added) | Stamps the DB at the baseline revision; future upgrades work normally |
| **Existing DB with Alembic** | Applies any pending migrations |

Demo mode is exempt — it always drops and recreates the schema from scratch.

### Generating a new migration

After adding or changing a model column in `models.py`:

```bash
# 1. Run against the dev database (must be up-to-date with current migrations)
FLASK_APP=init:create_app DATABASE_URL=<your-dev-db-url> flask db migrate -m "short description"

# 2. Review the generated file in app/migrations/versions/ — always check
#    that autogenerate produced the right ops (it can miss complex changes).

# 3. Run the upgrade locally to verify
FLASK_APP=init:create_app DATABASE_URL=<your-dev-db-url> flask db upgrade

# 4. Confirm no drift: running migrate again should produce "No changes in schema detected"
FLASK_APP=init:create_app DATABASE_URL=<your-dev-db-url> flask db migrate -m "check"
```

Commit the new `versions/*.py` file alongside the model change.

### Rolling back

```bash
FLASK_APP=init:create_app DATABASE_URL=<your-dev-db-url> flask db downgrade -1
```

### Testing migrations in CI

The `docker-build` CI job applies migrations against a fresh PostgreSQL database using the built image before running the smoke test. A failure here means the migration script has a bug and blocks the build.

---

## Git hooks

A pre-push hook lives in `.githooks/pre-push`. It runs two checks that mirror
CI, so you find out locally before the build fails:

1. **Ruff** — linting and import sorting (same rules as the CI "Lint with Ruff" step).
2. **Translations** — aborts if any locale has untranslated strings.

**Enable it once per clone:**

```bash
git config core.hooksPath .githooks
```

Ruff is included in `requirements/dev.txt` and is installed as part of the
normal venv setup.

Example output when a translation is missing:

```
[pre-push] ERROR: 1 untranslated fr string(s) — translate and commit messages.po before pushing.
```

---

## Coding standards

- **Language**: Python 3.11+, Flask, SQLAlchemy, Bootstrap 5.
- **Style**: PEP 8; no unnecessary abstractions — three similar lines beats a premature helper.
- **Comments**: only when the *why* is non-obvious. No docstrings that restate the function name.
- **i18n**: every user-visible string must be wrapped in `_()` before merging. See [dev-i18n.md](dev-i18n.md).
- **Tests**: every change should be accompanied with its associated tests, we aim for constant 100% test coverage.

---

## Architecture & domain model

### Core concepts

| Concept | Description |
|---|---|
| **Tenant** | An organisation scoping all data (aircraft, users, expenses). Multi-tenant capable. |
| **User** | Authenticates with email + password + optional TOTP. Can belong to multiple tenants. |
| **Role** | Per-tenant: Owner, Admin, User/Renter, Viewer. Enforced server-side on every query. |
| **Aircraft** | Modular assembly: airframe + 1..n engines + 1..n propellers + avionics entries. |
| **Component** | Any trackable unit (engine, prop, ELT, lifed part). |
| **Trigger** | Maintenance rule linked to an aircraft: calendar date, hours/Hobbs, or cycles threshold. |
| **FlightEntry** | A logged flight: hobbs/tach start & end, departure/arrival ICAO, optional photo. |
| **Expense** | A cost record: periodic (insurance) or punctual (fuel, parts); amount + currency + unit. |
| **Document** | An uploaded file attached to an aircraft, component, or logbook entry. |
| **BackupRecord** | Metadata for each backup: filename, size, SHA-256 checksum, timestamp. |

### Data model (key entities)

```
Tenant(id, name, settings)
User(id, email, password_hash, totp_secret, language, is_active)
TenantUser(user_id, tenant_id, role)

Aircraft(id, tenant_id, registration, make, model, ...)
  └─ MaintenanceTrigger(id, aircraft_id, name, trigger_type, due_date, due_engine_hours, ...)
  └─ FlightEntry(id, aircraft_id, pilot_user_id?, date, hobbs/tach start+end, ...)
  └─ Expense(id, aircraft_id, date, amount, currency, unit, type)
  └─ Document(id, aircraft_id?, component_id?, path, title, sensitive_flag, ...)
  └─ Snag(id, aircraft_id, title, is_grounding, reported_at, resolved_at)

PilotProfile(user_id, medical_expiry, sep_expiry, ...)
PilotLogbookEntry(id, pilot_user_id, date, flight_time fields...)
BackupRecord(id, filename, size_bytes, sha256, created_at)
DemoSlot(id, display_id, ...)  -- demo mode only
```

### Multi-tenancy

Every query that returns aircraft, flights, expenses, or documents **must** be
scoped to the current user's tenant. Use `TenantUser.query.filter_by(user_id=…)`
to resolve the tenant, then filter all related models by `tenant_id` or via the
aircraft's `tenant_id`.

### Trigger evaluation

`MaintenanceTrigger.status(current_hobbs)` returns one of `ok`, `due_soon`, or
`overdue`. The `utils.compute_aircraft_statuses()` helper aggregates statuses
across all triggers for a fleet. This is called on every dashboard load —
keep it fast (no external calls).

---

## Repo structure

```
app/                    Flask application package
  aircraft/             Aircraft blueprint (routes, models)
  config/               Configuration page blueprint
  documents/            Document management blueprint
  expenses/             Expense tracking blueprint
  flights/              Flight logging blueprint
  maintenance/          Maintenance triggers blueprint
  pilots/               Pilot logbook blueprint
  share/                Shared-link blueprint
  snags/                Snag tracking blueprint
  templates/            Jinja2 templates (base.html + per-blueprint)
  static/               CSS, JS, images
  translations/         .po files (fr, nl); .mo generated at build time
  init.py               App factory, Babel setup, global routes
  models.py             SQLAlchemy models

docker/                 Dockerfile, entrypoint, requirements
docs/                   Documentation (you are here)
migrations/             Alembic migration scripts
tests/                  pytest test suite
babel.cfg               pybabel extraction config
requirements/dev.txt    Test-only dependencies
```

---

## Publishing a specific version (e.g. v1.0.0)

By default CI auto-computes the next version as `MAJOR.(MINOR+1).0` based on
the latest tag already published to GHCR. To publish a specific version —
including a major bump — push an annotated tag pointing at the commit you want
to release.

Pushing a tag is itself a push event: CI fires a dedicated run for the tag,
checks out the tagged commit, builds the Docker image from that code, and
publishes it. The tag can be pushed independently from a branch push — it does
not need to accompany a `git push origin main`.

### Steps

```bash
# 1. Make sure the commit you want to release is on main and already passing CI
git log --oneline -5

# 2. Create an annotated tag on the current HEAD (or any specific commit SHA)
git tag -a v1.0.0 -m "Release v1.0.0"
# or on a specific commit:
# git tag -a v1.0.0 <sha> -m "Release v1.0.0"

# 3. Push the tag — this triggers its own CI run
git push origin v1.0.0
# Alternatively, push main and the tag together:
# git push origin main --follow-tags
```

CI will see `GITHUB_REF=refs/tags/v1.0.0`, skip the GHCR version query, and
use `1.0.0` as the build version.

> **Tag the right commit.** CI builds and publishes whatever commit the tag
> points to. Tagging an old commit releases old code under the new version
> number. Always tag the commit you have reviewed and intend to ship.

### What if you push the branch and the tag separately

Both trigger independent CI runs. The branch push computes and publishes a
normal `MINOR+1` version; the tag push publishes the tagged version. Both will
succeed, resulting in two Docker images published (the auto-computed one and
the explicitly tagged one). If you only want the tagged version published,
delete the extra tag from GHCR afterward — or use `--follow-tags` to send
them in a single operation and rely on the concurrency group to serialize them.

### Verification

After CI completes, confirm the release:

```bash
# Check the GitHub release was created with the right tag
gh release view v1.0.0

# Check the Docker image tag
docker pull ghcr.io/e2jk/openhangar:1.0.0
```

---

## Roadmap

The phased delivery plan with completion status is tracked in [docs/implementation_plan.md](implementation_plan.md).
