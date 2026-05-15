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
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r docker/docker-requirements.txt
pip install -r requirements-dev.txt
```

### Running tests

```bash
source .venv/bin/activate
pytest
```

For more verbose output:

```bash
pytest -v
```

### Test layout

```
pytest.ini              # pytest configuration (testpaths, pythonpath)
requirements-dev.txt    # test-only dependencies (pytest)
tests/
  conftest.py           # shared fixtures: app, client, captured_templates
  test_routes.py        # HTTP-level tests (status codes, response content)
  test_templates.py     # template rendering tests (correct template, CSS links)
```

### Notes

- `.venv/` is listed in `.gitignore` and should never be committed.
- When DB tests are added in a future iteration, a dedicated Docker-based test
  service will be introduced. Until then, the local venv approach is sufficient.

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

Ruff is included in `requirements-dev.txt` and is installed as part of the
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
requirements-dev.txt    Test-only dependencies
```

---

## Roadmap

The phased delivery plan with completion status is tracked in [docs/implementation_plan.md](implementation_plan.md).
