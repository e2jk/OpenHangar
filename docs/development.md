# Development Guide

## Running the application

OpenHangar runs via Docker Compose. The dev setup uses a local build and mounts
the `app/` directory into the container for live code reload — no rebuild needed
when you edit Python files or templates.

### First-time setup (dev)

**1. Install vendor frontend assets** (Bootstrap, Leaflet, etc. — not committed to git):

```bash
python3 scripts/install_vendor_assets.py
```

This runs `npm ci --ignore-scripts` in `requirements/` (integrity-verified against
`requirements/package-lock.json`) and copies the needed files into
`app/static/vendor/`. Re-run after pulling when `requirements/package-lock.json`
has changed. Pass `--copy-only` to skip npm and copy from an existing
`requirements/node_modules/`.

**2. Configure your `.env`** and point your dev `docker-compose.yml` at the project:

The dev compose file should:
- Build from the project root (`build: context: ../OpenHangar`)
- Mount the app directory for live reload: `volumes: - ../OpenHangar/app:/app`
- Set `FLASK_ENV=development` (or `OPENHANGAR_ENV=development`) to enable Werkzeug
  auto-reload

Example dev service snippet:

```yaml
openhangar-web:
  build:
    context: ../OpenHangar
    dockerfile: docker/Dockerfile
  volumes:
    - ../OpenHangar/app:/app
    - ./openhangar/data/uploads:/data/uploads
    - ./openhangar/data/backups:/data/backups
  environment:
    - OPENHANGAR_ENV=development
    - OPENHANGAR_DATABASE_URL=postgresql://...
    - OPENHANGAR_SECRET_KEY=dev-secret
```

**3. Build and start:**

```bash
docker compose up --build openhangar-db openhangar-web
```

Because `app/` is volume-mounted, Flask's Werkzeug dev server watches the files
on the host and reloads automatically. The volume mount shadows `/app` inside the
container, so the vendor assets fetched in step 1 must exist in your local
`app/static/vendor/` — they are **not** baked into the image when running with
a volume mount.

The Flask app is served at the host configured in your `.env` file (via Traefik).

### Testing PWA / service-worker caching in development

The service worker (stale-while-revalidate caching, HTMX prefetch) is
**disabled by default** in development mode to prevent stale HTML masking
template changes. To enable it temporarily:

```yaml
# In your dev docker-compose service environment block:
- OPENHANGAR_SW_ENABLED=true
```

or, when running Flask directly:

```bash
OPENHANGAR_SW_ENABLED=true flask run
```

While the service worker is active, enable **Update on reload** in
Chrome DevTools → Application → Service Workers so that every page reload
fetches fresh HTML (instead of the stale-while-revalidate cached copy),
making it easy to iterate on templates without manually unregistering the SW.

See [`OPENHANGAR_SW_ENABLED`](configuration.md#openhangar_sw_enabled) in the
configuration reference for full details.

---

## Updating vendor frontend assets

Frontend library versions are pinned in `requirements/package.json` and managed
by Renovate, which opens automated PRs on a weekly schedule. `npm ci` in the
Docker build verifies every package against the SHA-512 hashes in
`requirements/package-lock.json`.

### Upgrading a library manually

Edit the version in `requirements/package.json`, then regenerate the lock file
and refresh your local vendor folder:

```bash
cd requirements && npm install && cd ..
python3 scripts/install_vendor_assets.py --no-install
git add requirements/package.json requirements/package-lock.json
git commit -m "chore(deps): upgrade bootstrap 5.3.3 → 5.3.8"
```

Other developers run `python3 scripts/install_vendor_assets.py` after pulling to
refresh their local vendor folder. The Docker image build does this automatically.

### Adding a new library

1. Add it to `requirements/package.json` and run `cd requirements && npm install`
2. Add the file mapping to `_FILES` in `scripts/install_vendor_assets.py`
3. Run `python3 scripts/install_vendor_assets.py --no-install` to copy the files
4. Reference it from templates via `url_for('static', filename='vendor/<lib>/...')`
5. Commit `requirements/package.json`, `requirements/package-lock.json`, and
   `scripts/install_vendor_assets.py`

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

## End-to-end (Playwright) tests

Playwright tests live in `tests/e2e/` and cover JavaScript interactions that the
Flask test client cannot observe: AJAX flows, dynamic form visibility, and
client-side navigation.

They are **not** part of the standard coverage suite — they are slow and require a
live server process. They are gated behind a `--e2e` flag and skipped by default.

### First-time setup

Playwright itself is already in `requirements/dev.txt`. Install the Chromium
browser binary once:

```bash
source .venv/bin/activate
playwright install chromium
```

### Running E2E tests locally (in-process)

```bash
# E2E tests only (starts a live Flask server automatically)
bash scripts/run-tests-with-coverage.sh --e2e

# Unit tests + E2E in one go
bash scripts/run-tests-with-coverage.sh --all

# Run a single E2E class directly
pytest --e2e tests/e2e/test_ui_interactions.py::TestLogbookToggle --override-ini='addopts='
```

`--override-ini='addopts='` strips the `-n auto` parallel flag from `pytest.ini`
because the live-server fixture is session-scoped and must not be forked.

`tests/e2e/routes.json` must exist before running.  Refresh it whenever routes
change (see below).

### Running E2E tests against the dev Docker server

```bash
# 1. Refresh the route inventory from the actual running database
bash scripts/refresh-e2e-sitemap.sh

# 2. Run against the dev server — destructive tests are skipped automatically
E2E_BASE_URL=https://your-dev-server/ \
  bash scripts/run-tests-with-coverage.sh --e2e
```

`refresh-e2e-sitemap.sh` pipes `generate_routes.py` into the web container
via `docker exec` so it queries the live database directly — no PostgreSQL port
exposure needed.  It writes both `tests/e2e/routes.json` (route inventory) and
`tests/e2e/seed.json` (live seed IDs).

Run it whenever routes change or the database content has shifted significantly.
For local in-process tests, `routes.json` only needs the route list (URLs are
re-resolved dynamically), so a refresh is only needed when Flask routes are
added or removed.

If the auto-detected container is wrong, pass the container name explicitly:

```bash
bash scripts/refresh-e2e-sitemap.sh my-container-name
```

Tests marked `@pytest.mark.destructive` (flight deletion, role change) are
**automatically skipped** whenever `E2E_BASE_URL` is set, so running against
the dev server is safe.  To override (e.g. on a disposable test instance) add
`E2E_ALLOW_DESTRUCTIVE=1` to the command.

### Test layout

```
tests/e2e/
  conftest.py              # live Flask server + Playwright fixtures; seed data
  test_crawl.py            # parametrised GET crawl + auth-guard POST sweep
  test_ui_interactions.py  # JavaScript interaction tests (clicks, AJAX, TOTP…)
  test_access_control.py   # role-based access: pilot/viewer vs admin-only pages
  routes.json              # route inventory (gitignored; refresh with refresh-e2e-sitemap.sh)
  seed.json                # seed IDs for Docker/dev-server mode (gitignored)
  fixtures/
    test_flight.gpx        # GPX fixture used by GPS-parse tests
```

### E2E tests in CI

Three jobs in `.github/workflows/ci.yml` run the Playwright suite, all in
parallel with `docker-validate` after the amd64 Docker image is built. Each
gets its own disposable Postgres container, so none of them share state:

**`browser-tests-seeded-crawl`** runs only `test_crawl.py` — the generic
route crawler, ~80% of the seeded suite's test count (one parametrised dot
per route). Split into its own job purely to shorten the CI critical path;
see `browser-tests-seeded-rest` below for why this split is safe.

**`browser-tests-seeded-rest`** runs everything else in the seeded suite
(`test_access_control.py`, `test_htmx_boost.py`, `test_ui_interactions.py`
— anything under `tests/e2e/` except `test_crawl.py` and
`test_setup_flow.py`). Both seeded jobs:

1. Start a PostgreSQL container and the freshly-built app image in
   `FLASK_ENV=development` (dev seed auto-applied by `docker-init-db.py`).
2. Wait for the `/health` endpoint.
3. Run `generate_routes.py --db-url $DATABASE_URL --seed-out tests/e2e/seed.json`
   to capture live seed IDs from that job's own PostgreSQL database.
4. Run their respective test subset with `E2E_BASE_URL=http://localhost:5000`.

Splitting `test_crawl.py` out is safe because there are no cross-file
imports between any of these files, and the `SEED` dict each job builds
comes from that job's own independent database — so even though
`test_ui_interactions.py` deletes the `fe_del1`/`fe_del2` seed rows
(destructive tests, `E2E_ALLOW_DESTRUCTIVE=1`), no other file mutates or
depends on that mutation, and running in separate jobs against separate
databases means it couldn't matter even if they did.

**`browser-tests-fresh-db`** runs only `test_setup_flow.py` (the empty-DB /
first-run setup wizard tests), which need a genuinely unseeded database: a
disposable PostgreSQL container plus the same app image running in
`OPENHANGAR_ENV=production` — production mode never auto-seeds (see
`docker-init-db.py`), so the container boots with zero users. `fresh_server`
in `tests/e2e/conftest.py` truncates all tables directly against
`E2E_SETUP_FLOW_DB_URL` before each test function, since the job shares one
container across all of them. Locally (no `E2E_SETUP_FLOW_BASE_URL` set),
`fresh_server` falls back to an isolated in-process Flask+SQLite server per
test, so `pytest --e2e` still works without Docker.

The `publish` job requires `browser-tests-seeded-crawl`,
`browser-tests-seeded-rest`, `browser-tests-fresh-db` (and `lint-and-test`,
`docker-validate`, `docker-build-arm64`) to all pass before tagging and
publishing a release.

### Writing new E2E tests

- Mark every test class with `pytestmark = pytest.mark.e2e`.
- If a test needs specific data: prefer adding it to `_seed_helpers.py` (shared
  with dev and demo environments) so all environments benefit. Only add
  test-only extras directly in `conftest.py` if the data is destructive (deleted
  by the test) or truly synthetic.
- Add any new seed IDs to the `SEED` dict and expose them via the `seed` fixture.
- In Docker/dev-server mode `live_app` is `None`; use HTTP assertions
  (`page.request.get(...)`) rather than direct DB queries for post-action checks.
- Use `pw_expect(locator).to_be_visible()` / `to_be_hidden()` for visibility
  assertions; avoid class-string matching.

---

## Fuzzing

`fuzz/` holds [Atheris](https://github.com/google/atheris) harnesses that fuzz
real app functions directly (not reimplementations) on untrusted-input
surfaces — security guards (`_safe_next`, `_safe_join`,
`_safe_path_component`) and file-upload parsers (pilot logbook CSV/XLSX
import, GPS GPX/KML/Garmin-CSV import). `.github/workflows/fuzzing.yml` runs
each harness for ~90–120s per merge to `main` and ~20min on a weekly
schedule, deliberately independent of `ci.yml` — a fuzzing crash never blocks
a PR or release (see `docs/backlog.md`'s "CI: continuous fuzzing harness"
entry for the full reasoning). Findings surface via the job summary, the
Security tab (SARIF), and a downloadable crash-repro artifact.

### Running a harness locally

```bash
pip install -r requirements/fuzz.txt   # Linux only — not part of dev.txt
python fuzz/fuzz_safe_next.py fuzz/corpus/fuzz_safe_next -max_total_time=30
```

Corpus directories (`fuzz/corpus/<harness>/`) are gitignored — CI persists
them between runs via `actions/cache`; locally they're just scratch space.

### Adding a new harness

Import the real target function (see any existing `fuzz/fuzz_*.py` for the
pattern) and wrap the import in `with atheris.instrument_imports():` rather
than only `@atheris.instrument_func` on `TestOneInput` — the latter only
instruments the harness wrapper itself, leaving Atheris blind to every branch
inside the code actually being fuzzed (verified during Phase 2: coverage
went from a flat 2 basic blocks to 50+ once the target import was
instrumented too). Then:
1. Add the harness name to the `matrix.harness` list in
   `.github/workflows/fuzzing.yml`.
2. Add its target module(s) to `_TARGET_MODULES` in
   `scripts/fuzz_coverage_report.py` (see below) if they aren't already
   covered by an existing harness on the same file.

### Fuzz coverage report

Separate from the 100%-enforced pytest coverage above, `scripts/fuzz_coverage_report.py`
replays every file already sitting in each harness's persisted corpus once
through that harness's `TestOneInput`, measuring real line coverage (via the
same `coverage.py` package pytest uses) restricted to just the specific
modules the harnesses target — not the whole `app/` tree, since a lot of
those files (e.g. `reservations/routes.py`) are large route modules where
only one helper function is actually being fuzzed; a whole-file percentage
would mostly measure code the harness was never meant to reach. This runs at
release time as part of `ci.yml`'s Pages-site assembly (restoring the latest
corpus `fuzzing.yml` has grown on `main`, since that's a separate workflow
with its own cache), producing `htmlcov-fuzz/` and `coverage-fuzz.xml`
alongside the pytest `htmlcov/`/`coverage.xml`. Published at
[e2jk.github.io/OpenHangar/fuzz-coverage/](https://e2jk.github.io/OpenHangar/fuzz-coverage/)
— like the pytest coverage report, it only refreshes on a tagged release,
not on every push.

A low or 0% number for a given file is expected and not a problem by itself —
it just means the accumulated corpus hasn't reached much of that file yet.
A file can also be **entirely absent** from the report rather than showing
0%: `fuzz_coverage_report.py` skips a harness outright when its corpus
directory doesn't exist yet, which happens right after a brand-new harness
is added — the very first release built after that harness lands can catch
`ci.yml`'s corpus-restore step before `fuzzing.yml` has ever run once for
it (that workflow only triggers on push-to-main/weekly schedule, so there's
an unavoidable gap between "harness merged" and "harness has a corpus to
report on"). It resolves itself once `fuzzing.yml` runs at least once
before the next release — no action needed.

The **README badge** shows the harness count (e.g. "fuzz harnesses: 6"),
not a percentage — a coverage-style badge would misleadingly read as a
failing metric right next to the 100% pytest Coverage badge, when it
measures something different in kind (see above) and is only ever going to
be a small fraction of the target files' lines. The count comes from
`fuzz/fuzz_*.py` directly (a `curl` to shields.io's static-badge endpoint in
`ci.yml`, not `genbadge`), so it updates automatically whenever a harness
file is added or removed — no manual badge edit needed.

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

A pre-push hook lives in `.githooks/pre-push`. It mirrors CI so you find out
locally before the build fails: Ruff (lint + format), mypy, bandit, zizmor,
actionlint, a quick check against the last coverage run, pip-audit, the
Alembic migration chain, and translation completeness. Each tool is skipped
with a message (not a failure) if it isn't installed locally.

**Enable it once per clone:**

```bash
git config core.hooksPath .githooks
```

Most of these ship in `requirements/dev.txt`, installed as part of the normal
venv setup. actionlint is the one exception — it's a standalone Go binary, not
a pip package; the hook prints an install command if it's missing.

**Keeping local tool versions in sync:** these tools get their own version-bump
PRs over time (Renovate for the pip-installed ones, a manual bump for
actionlint), so a venv set up months ago can silently drift behind what CI
actually runs. The hook checks for this on every push (comparing installed
versions against what `requirements/dev.txt` / `ci.yml` pin — no network
calls) and prints a one-line notice if anything's behind. To sync everything
in one go:

```bash
bash .githooks/pre-push --update
```

This reinstalls the pinned versions of every pip tool and re-downloads
actionlint, then runs the normal checks.

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
| **Role** | Per-tenant: Admin, Owner, Pilot/Renter, Maintenance, Viewer (plus Student and Instructor for flight-school flows). Enforced server-side on every query. |
| **Aircraft** | Modular assembly: airframe + 1..n engines + 1..n propellers + avionics entries. |
| **Component** | Any trackable unit (engine, prop, ELT, lifed part); optional TBO hours and calendar life limit. |
| **Trigger** | Maintenance rule linked to an aircraft: calendar date or engine-hours threshold. |
| **FlightEntry** | A logged flight: flight/engine time counter start & end, departure/arrival ICAO, optional photo. |
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
  └─ FlightEntry(id, aircraft_id, pilot_user_id?, date, flight/engine time counter start+end, ...)
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
`overdue` — the hours argument is the aircraft's current **engine** hours
(`Aircraft.total_engine_hours`). The `utils.compute_aircraft_statuses()` helper aggregates statuses
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

## Landing changes on main

`main` is protected by a ruleset requiring 8 status checks to pass, with no
bypass — including for direct pushes by the repo owner, since a commit
pushed straight to `main` has never had CI run against it. Two ways to land
a change:

- **Manual PR**: push a branch, open a PR, wait for checks, merge.
- **The `ship` branch (recommended)**: push the remote branch named exactly
  `ship`. The push itself runs no CI — the
  [`auto-pr-merge.yml`](../.github/workflows/auto-pr-merge.yml) workflow just
  opens (or reuses) a PR to `main` and enables GitHub's native auto-merge
  (rebase — no merge commits cluttering `main`'s history). Opening that PR is
  what triggers `ci.yml`'s real work: it computes the actual release version,
  builds both platform images with it baked in, runs the full test suite
  against that exact image, and — if everything passes — publishes it
  immediately (GHCR manifest, sign, attest, GitHub Release) before the PR
  ever merges. Auto-merge then lands the PR once the required checks are
  recorded, which is a no-op as far as `main` is concerned: the content it
  receives has already been fully tested and already published.

  Since a rebase merge gives `main` new commit SHAs for the same content, a
  local branch that still holds the pre-rebase commits is stale the moment
  the merge lands — pushing it again as-is would re-submit already-merged
  changes alongside the new ones. [`scripts/ship.sh`](../scripts/ship.sh)

  This workflow opens the PR using a fine-grained Personal Access Token
  (repo secret `PAT_AUTO_PR_MERGE`), not the default `GITHUB_TOKEN`. GitHub
  now requires manual workflow-run approval on every run of any PR authored
  by `github-actions[bot]` (i.e. opened with the default token) — a
  platform-side policy, not a per-repo setting, and it applies even to
  same-repo, non-fork PRs like this one. A PR opened with a PAT from a real
  trusted account is authored by that account instead, so it isn't subject
  to the bot-authored approval gate. If this token expires or is revoked,
  `auto-pr-merge.yml` runs will fail at the `gh pr create`/`gh pr merge`
  step; regenerate it as a fine-grained PAT scoped to just this repo with
  `Pull requests: read/write` and `Contents: read/write` permissions, then
  update the `PAT_AUTO_PR_MERGE` secret under repo Settings → Secrets and
  variables → Actions.
  handles this for you:

  ```bash
  bash scripts/ship.sh
  ```

  It rebases your current branch onto the latest `origin/main` — any
  commits from a previous round that already landed are dropped
  automatically (git detects their patch is already applied upstream and
  skips them, the same mechanism `git pull --rebase` relies on) — then
  pushes to `ship`. No separate manual sync step is needed before your next
  round of commits: just run the script again — you don't have to wait for
  the current round to finish landing first. Watch a push land with
  `gh pr status`. `bash scripts/ship.sh --no-verify` skips the pre-push
  hook, same flag and meaning as `git push --no-verify`.

  `ship` is deleted from the remote automatically after each merge (repo-wide
  `delete_branch_on_merge`) and simply recreated on the next push.

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

Both trigger independent, full CI runs — the `ship`/PR flow computes and
publishes a normal auto-computed `MINOR+1` version (see "Landing changes on
main" above); the tag push publishes the tagged version. Both will succeed,
resulting in two Docker images published (the auto-computed one and the
explicitly tagged one). If you only want the tagged version published, delete
the extra tag from GHCR afterward, or land the tag first and let the
auto-computed version bump from there instead.

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
