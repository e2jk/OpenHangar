# AGENTS.md — OpenHangar AI Agent Briefing

This file is for AI coding agents (Claude Code, Codex, Cursor, etc.). Read it in full
before making any change. It is the authoritative source on how to work in this repo.

---

## Purpose

**What this repo is:** OpenHangar is a self-hosted, open-source aviation management
platform — Flask/Python backend, PostgreSQL, Jinja2 + Bootstrap 5 + HTMX frontend.
One instance = one organisation (single-tenant). Published at
`ghcr.io/e2jk/openhangar:latest`.

It serves five operating models (chosen at first-run setup): `sole_pilot` (personal
logbook only), `sole_operator` (one owner, one or more aircraft), `shared_ownership`,
`flight_club`, `flight_school`. Features are gated by operating model and by user role
(ADMIN, OWNER, PILOT, MAINTENANCE, RENTER, VIEWER).

**What you are expected to do here:** Implement features, fix bugs, write tests, update
translations, and keep the codebase clean. You are NOT expected to push code, run
destructive git commands, or deploy. Propose commit messages; let the human commit.

---

## Quick start

```bash
# The app runs inside Docker. app/ is volume-mounted for live reload
# (Python/HTML changes take effect immediately, no rebuild needed).

# Run all tests (from the repo root, using the project venv):
.venv/bin/pytest tests/ -q

# Run tests with coverage report (required before pushing):
bash scripts/run-tests-with-coverage.sh

# Run only e2e tests (needs --e2e flag and a live server):
.venv/bin/pytest tests/e2e/ --e2e --override-ini='addopts=' -v

# Lint / type-check / security (same tools CI runs):yes
.venv/bin/ruff check app/ tests/
.venv/bin/ruff format --check app/ tests/
.venv/bin/mypy app/
.venv/bin/bandit -r app/ -c pyproject.toml

# Translations — update + compile after adding new _() strings:
bash scripts/update_i18n.sh          # extract → update → compile (run from project root)
.venv/bin/python scripts/check_translations.py   # validate completeness

# Validate Alembic migration chain:
.venv/bin/python scripts/check_migrations.py

# Install / refresh vendor frontend assets (Bootstrap, Leaflet, etc.):
python3 scripts/install_vendor_assets.py
```

---

## Repo map

```
app/
  init.py                 App factory, SUPPORTED_LOCALES, Babel setup, login helpers
  models.py               ALL SQLAlchemy models live in this one file
  utils.py                Shared decorators: @login_required, @require_role, @require_instance_admin
  extensions.py           Flask extension instances (db, babel, bcrypt, …)
  migrations/versions/    Alembic migration scripts
  translations/
    fr/LC_MESSAGES/messages.po
    nl/LC_MESSAGES/messages.po
  static/
    js/                   Page-specific JS modules (one file per feature area)
    css/                  Stylesheets
    vendor/               Bootstrap, Leaflet, HTMX, etc. — NOT committed to git
  templates/
    base.html             Master layout; loads ALL JS and CSS unconditionally
    <blueprint>/          Per-blueprint Jinja2 templates
    partials/             Reusable partial templates

  <blueprint>/            Feature blueprints (routes.py + __init__.py each):
    aircraft · airworthiness · auth · config · demo · documents · expenses
    flights · hangar · maintenance · pilots · reservations · services
    share · snags · squawk · users

babel.cfg                 pybabel extraction config (project-root-relative paths)
docker/                   Production docker-compose.yml + .env.example
docs/
  implementation_plan.md  Phase-by-phase feature checklist — tick boxes when done
  backlog.md              Live "still to do" list — remove items once implemented
  development.md          Full dev setup guide
  dev-i18n.md             i18n workflow and pybabel commands
pyproject.toml            Ruff + mypy config
pytest.ini                Test config (pythonpath = app, addopts = -n auto)
.coveragerc               Coverage config (source = app)
.githooks/pre-push        Hook: ruff, mypy, bandit, migrations, translations, coverage
scripts/
  update_i18n.sh          Full extract → update → compile cycle
  check_translations.py   Validates .po completeness (called by hook + CI)
  check_migrations.py     Validates Alembic migration chain
  run-tests-with-coverage.sh
  install_vendor_assets.py
  take_screenshots.py     Generates docs/screenshots/ via Playwright
```

---

## Working rules

### Python style
- Ruff is the linter and formatter. Config in `pyproject.toml`. No manual style debates —
  ruff is the authority.
- Type annotations required on all new functions. mypy is run in CI.
- All security-sensitive code gets a bandit review (runs in CI). Avoid `# nosec` unless
  genuinely necessary and commented with a reason.

### Naming conventions
- Blueprint directories and URL prefixes match (e.g. blueprint `aircraft` → `/aircraft/`).
- Test files describe the feature under test, never a phase number:
  `test_crew_and_easa_fields.py` ✓ — `test_phase16.py` ✗
- Alembic migration revision IDs must be genuinely random hex (12 chars). Generate with
  `python3 -c "import secrets; print(secrets.token_hex(6))"`. Never use sequential IDs.
- JS files: one per feature area, named after the feature (`aircraft_form.js`, not `form.js`).

### Schema changes
Every change to `app/models.py` that alters the database schema requires an accompanying
Alembic migration in `app/migrations/versions/`. Run `scripts/check_migrations.py` to
confirm the chain is valid.

### Translations
Every user-visible string must be wrapped in `_()` (Python) or `{{ _('…') }}` (Jinja).
After any change, update all non-English locales (`fr`, `nl`) and recompile. The
pre-push hook and CI both validate completeness — zero untranslated or fuzzy strings
are allowed.

### New environment variables
Every new `OPENHANGAR_*` variable read via `os.environ` must be added to
`docs/configuration.md`: a row in the master variable list table, plus a
`###` subsection (allowed values/format, default, effect, use case) in the
relevant section. Grep the codebase for existing `os.environ.get("OPENHANGAR_...")`
calls before introducing a new variable — an existing one may already cover
the need (e.g. `OPENHANGAR_SW_ENABLED` already exists to force-enable the PWA
service worker in dev mode; see `docs/development.md`).

### Documentation maintenance
- `docs/implementation_plan.md`: tick `- [x]` and add ✅ to the phase heading when complete.
- `docs/backlog.md`: remove items as they are implemented. This is a live "still to do"
  list, not a history.

### What not to touch without human approval
- `docker/docker-compose.yml` and `.env.example` — production deployment config.
- `.github/workflows/ci.yml` — CI pipeline.
- `app/static/vendor/` — managed by `install_vendor_assets.py`, not hand-edited.
- Existing Alembic migrations — never alter a committed migration; always add a new one.
- `app/translations/*.po` lines you did not add — do not hand-reformat or reorder entries.
  Running `scripts/update_i18n.sh` is fine even if it reflows the whole file (see
  "Adding 1–10 strings" below) — that's the sanctioned tool doing its job, not you
  editing lines you shouldn't.

---

## LLM-specific guidance

### JS architecture — the fundamental constraint

`base.html` sets `<body hx-boost="true">`. HTMX intercepts all link clicks and form
submissions, swaps only `<body>`, and fires `htmx:afterSettle` on completion.

HTMX is configured with `allowScriptTags: false`. **Inline `<script>` blocks in the
swapped body are silently never executed.** This is not a style preference — they
literally do not run after an hx-boost navigation.

**Rule: no `<script nonce>` blocks in any child template, ever.**

Only two templates may contain `<script nonce>`:
- `base.html` itself (CSRF injection, HTMX runtime config, anniversary confetti).
- `share/public.html` (standalone page, does not extend `base.html`).

### JS module pattern
Every page-specific JS file lives in `app/static/js/` and is loaded from `base.html`
unconditionally (the always-load list, after `wb_config.js`). Each file:

1. Wraps everything in an IIFE.
2. Guards on the root element: `if (!el || el.dataset.ohInited) return; el.dataset.ohInited = '1';`
3. Registers both `DOMContentLoaded` and `htmx:afterSettle` listeners so the module
   re-initializes after every hx-boost body swap.

```javascript
// Template for a new feature JS file:
(function () {
  function init() {
    var el = document.getElementById('my-root-element');
    if (!el || el.dataset.ohInited) return;
    el.dataset.ohInited = '1';
    // … feature logic …
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
})();
```

### Passing Jinja values to JS — data bridges, not inline scripts
- **Scalar values**: `data-*` attributes on the root element the JS already queries.
  ```html
  <div id="my-widget" data-aircraft-id="{{ aircraft.id }}">
  ```
- **Objects / arrays**: a `<script type="application/json">` block with an `id`.
  These are non-executable (no nonce needed) and read with `JSON.parse(el.textContent)`.
  ```html
  <script type="application/json" id="my-data">{{ my_dict | tojson }}</script>
  ```
- **Translated row templates**: `<template id="row-tpl">` rendered by Jinja (translated
  labels baked in), cloned by JS with `tmpl.content.cloneNode(true)`.

### `hx-boost="false"` — required on these link types
| Link type | Reason |
|-----------|--------|
| Logout link | Full page reload needed to cleanly reset all browser state |
| Theme toggle / language switcher | Update `<html data-bs-theme>` / `<html lang>` — root attrs HTMX doesn't touch |
| `/health` status link | Returns JSON, not HTML |
| Binary download links (zip, GIF, etc.) | Non-HTML response |

### Three questions to ask before finishing any template change
1. Does the change introduce a `<script nonce>` block? → Extract to a `.js` file in
   `app/static/js/`, add it to the always-load list in `base.html`, add `htmx:afterSettle` init.
2. Does any new link/button serve a non-HTML response or needs to clear session? → Add
   `hx-boost="false"`.
3. Do any Jinja values need to reach JS? → Use a JSON data block or `data-*` attribute.

### Role and operating-model gating
- Use the `@require_role(Role.PILOT, Role.ADMIN, …)` decorator (from `utils.py`) on routes.
- Gate template sections with `{% if current_user.role in [Role.ADMIN, …] %}`.
- Use `current_user.tenant.operating_model` to gate features by operating model.

Roles are defined in `app/models.py:Role`: `ADMIN · OWNER · PILOT · MAINTENANCE · RENTER · VIEWER`.

Two roles also set boolean flags on `User` that flow into the Jinja template context:
- `User.is_pilot = True` → template var `is_pilot` — gates pilot nav links and pages
- `User.is_maintenance = True` → template var `is_maint` — gates maintenance nav

Templates use `{% if is_pilot %}` / `{% if is_maint %}`, not a role check. New
pilot/maintenance features need both: the `@require_role` decorator on the route and
the `{% if is_pilot/is_maint %}` guard in the template.

### i18n in Python code
- Always wrap user-visible strings in `_()`. Import: `from flask_babel import gettext as _`.
- `_()` inside f-string expressions is NOT reliably extracted by pybabel. Assign first:
  ```python
  label = _("My string")
  html = Markup(f"<strong>{escape(label)}</strong>")
  ```
- Plurals: `ngettext("one item removed.", "%(n)s items removed.", count, n=count)`

  The `.po` format for plurals requires three entries:
  ```po
  msgid "one item removed."
  msgid_plural "%(n)s items removed."
  msgstr[0] "…singular translation…"
  msgstr[1] "…plural translation…"
  ```

- `.mo` compiled files are **gitignored** — they are compiled at Docker build time and
  in CI. Never commit `.mo` files. If they are missing locally, run:
  ```bash
  .venv/bin/pybabel compile -f -d app/translations
  ```

**Adding 1–10 strings** (faster than running the full extract cycle): manually append
the `msgid`/`msgstr` blocks to both `fr` and `nl` `.po` files, then compile:
```bash
.venv/bin/pybabel compile -f -d app/translations
```
Use `bash scripts/update_i18n.sh` only when syncing after many changes or to pick up
strings you may have missed.

**Formatting: always single-line (`--no-wrap`) msgstr, never wrapped.** The script
passes `--no-wrap`, so anything you add by hand should match — one physical line per
`msgstr`, however long. Weblate's own PO writer wraps at ~79 columns, so a file last
touched by a Weblate sync will look wrapped; running the script on it then reflows
*every* entry back to single-line, producing a diff that's almost entirely line-noise.
That's expected — verify nothing was actually lost by comparing `msgid`/`msgstr` pairs
(e.g. via `polib`), not raw line counts. See `docs/dev-i18n.md` for details.

### French typography (applies to all `fr` msgstr entries)
Use U+202F NARROW NO-BREAK SPACE (not a regular space, not U+00A0) before `: ; ! ? »`
and after `«`, and between a number and its unit (`20 Mo`). Use the actual character,
not `&nbsp;`.

---

## Validation

### Required for every change
- **Tests**: 100% line coverage enforced. Every new code path needs a test.
  ```bash
  bash scripts/run-tests-with-coverage.sh
  ```
- **Translations**: zero untranslated or fuzzy strings.
  ```bash
  .venv/bin/python scripts/check_translations.py
  ```
- **Migrations**: if `models.py` changed, migration chain must be valid.
  ```bash
  .venv/bin/python scripts/check_migrations.py
  ```
- **Lint + types**: ruff + mypy must pass clean.

### What "done" means
A change is done when all of the above pass AND:
- All UI-visible strings are translated in `fr` and `nl`.
- `docs/implementation_plan.md` is updated if a plan phase was completed.
- `docs/backlog.md` has the item removed if it was listed there.
- A commit message (conventional commits format) is ready for the human to run.

---

## Common pitfalls

### Inline scripts silently dropped after hx-boost navigation
If a page-specific JS feature works on first load but breaks after navigating away and
back: you have an inline script. Extract it to `app/static/js/` following the module
pattern above.

### pybabel extraction must run from project root
`babel.cfg` uses project-root-relative paths. Running pybabel from inside `app/` will
produce an empty `.pot` with no extracted strings.
```bash
# Must be run from the repo root, not from app/:
bash scripts/update_i18n.sh
```

### `_()` inside f-strings not extracted
```python
# WRONG — pybabel cannot see this:
flash(f"Aircraft {escape(_('deleted'))}")

# RIGHT:
msg = _("deleted")
flash(f"Aircraft {escape(msg)}")
```

### Sequential Alembic revision IDs break reproducibility
IDs like `a1b2c3d4e5f6` or `000001` have historically caused merge conflicts and chain
validation failures. Always generate with `secrets.token_hex(6)`.

### Vendor assets not committed to git
`app/static/vendor/` is in `.gitignore`. After a fresh clone (or when
`requirements/package-lock.json` changes) run:
```bash
python3 scripts/install_vendor_assets.py
```
If vendor assets are missing, template rendering fails with 404s on static files.

### pre-push hook not installed
The hook is at `.githooks/pre-push` but git won't use it until configured:
```bash
git config core.hooksPath .githooks
```
CI runs the same checks — missing the hook just means you find out later.

### e2e tests need a live server and `--e2e` flag
The e2e suite (Playwright) is skipped by default. Run with:
```bash
.venv/bin/pytest tests/e2e/ --e2e --override-ini='addopts=' -v
```

### Dev container does not auto-apply new migrations
The dev Docker Compose setup volume-mounts `app/` for live reload, but that
only covers Python/template code — it does **not** re-run `alembic upgrade`
when a new migration is added. After adding a model change + migration,
restart the web container, otherwise affected pages 500 with
`UndefinedColumn` because the running process still has the old schema.

### TOTP login form auto-submits on the 6th digit
`app/static/js/totp_autosubmit.js` calls `form.requestSubmit()` as soon as
all 6 digits are entered — the submit button is never clicked in practice.
Any Playwright/e2e automation that fills the TOTP field and then clicks
"submit" is racing the auto-submit; the click can land after the page has
already navigated. Fill the field and wait for navigation instead of
clicking submit.

### Screenshot generation workflow
`scripts/take_screenshots.py` drives a headless browser against
`docs/screenshots/manifest.yml`, one entry per screenshot (URL template,
viewport, optional setup steps). Some seeded views need query params to
show non-empty data — e.g. the cost dashboard is empty by default and needs
`?period=0` to render the seeded cost entries. When adding a new screenshot,
check whether the target page needs a similar param before assuming the
manifest entry is broken.

---

## Examples

### Adding a new feature to an existing blueprint
```
1. Add/modify model in app/models.py
2. Generate migration: python3 -c "import secrets; print(secrets.token_hex(6))"
   → create app/migrations/versions/<id>_description.py
3. Add route in app/<blueprint>/routes.py, gated with @require_role(…)
4. Add template in app/templates/<blueprint>/my_feature.html (no <script nonce>)
5. If JS is needed: create app/static/js/my_feature.js (IIFE + guard + afterSettle)
   and add <script nonce="…" src="…my_feature.js"> to the always-load list in base.html
6. Wrap all new UI strings in _(), add translations to fr and nl .po files
7. Write tests covering 100% of new lines
```

### Adding a new navigation link
```html
<!-- WRONG — hx-boost will try to swap the body with whatever this returns -->
<a href="/some/binary/download">Download</a>

<!-- RIGHT — binary/JSON/session-clearing links need hx-boost="false" -->
<a href="/some/binary/download" hx-boost="false">Download</a>

<!-- Normal HTML page link — no attribute needed, body-swap is correct -->
<a href="{{ url_for('aircraft.list') }}">Aircraft</a>
```

### Passing a translated dict to JS
```html
<!-- In template (no nonce needed — type="application/json" is non-executable) -->
<script type="application/json" id="status-labels">{{ {
  'active': _('Active'),
  'retired': _('Retired')
} | tojson }}</script>
```
```javascript
// In app/static/js/my_feature.js
var dataEl = document.getElementById('status-labels');
if (dataEl && !dataEl.dataset.ohInited) {
  dataEl.dataset.ohInited = '1';
  var labels = JSON.parse(dataEl.textContent);
}
```

### Bad edit example — what not to do
```html
<!-- WRONG: inline script in a child template — will not run after hx-boost navigation -->
{% block scripts %}
<script nonce="{{ csp_nonce() }}">
  document.getElementById('my-select').addEventListener('change', function() { … });
</script>
{% endblock %}
```

---

## Escalation / questions

**Ask the human before proceeding when:**
- A schema change is large or destructive (dropping columns, renaming tables).
- A new operating model or role needs to be introduced.
- A change affects the CI pipeline, Docker config, or `.githooks/pre-push`.
- You are about to push, force-push, or run `git reset --hard`.
- A dependency upgrade (Python package or vendor asset) has breaking changes.
- A change touches `app/translations/` .po files in a way that might corrupt existing entries.

**What to include in a handoff to a human:**
- Which files were modified and why.
- The proposed commit message (conventional commits format).
- Any untested edge cases or open questions.
- Whether translations need manual review for French typography.
- Whether `docs/implementation_plan.md` or `docs/backlog.md` needs updating.
