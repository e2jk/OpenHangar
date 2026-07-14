# OpenHangar — Implementation Plan

Phases are meant to be delivered incrementally.
Each phase produces something usable end-to-end before the next one adds depth.
Check boxes are ticked as items are completed.

---

## Phase 0 — Foundation ✅

- [x] Project structure, Docker Compose, dev/prod entrypoint
- [x] Flask app factory, environment validation (`FLASK_ENV`)
- [x] PostgreSQL integration (`db.create_all`, dev seed)
- [x] Authentication: setup wizard (account + optional TOTP), login (two-step), logout
- [x] Multi-tenant DB schema (`Tenant`, `User`, `TenantUser`, roles)
- [x] Three-state home page: landing (fresh install) / welcome (initialised) / dashboard (logged in)
- [x] Navbar adapts to auth state; env badge for dev/test
- [x] pytest suite with SQLite in-memory fixtures

---

## Phase 1 — Aircraft & Component Models (DB only) ✅

Goal: define the core domain models before building any UI,
so every later phase has a stable foundation to build on.

- [x] `Aircraft` model — registration, make/model, year, placeholder flag, tenant FK
- [x] `Component` model — generic typed component linked to an aircraft
  - `type` stored as plain string (no DB enum) so new types never require a migration
  - Built-in types in `ComponentType`: `engine`, `propeller`, `avionics`
  - `position` field for multi-engine aircraft ("left" / "right" / …)
  - `time_at_install` (hours on component when installed)
  - `installed_at` / `removed_at` lifecycle dates — `removed_at = NULL` means currently installed
  - `extras` JSON column for type-specific attributes (blade count, TBO, firmware version, …)
- [x] DB tables created via `create_all` (Alembic migrations deferred to Phase 2+)
- [x] Unit tests for model relationships, constraints, history tracking, and cascade deletes
- [x] Extend dev seed with sample aircraft (single-engine and multi-engine) with components attached

---

## Phase 2 — Aircraft Management (basic CRUD) ✅

Goal: a user can add planes and attach an engine and propeller through the UI.

- [x] Aircraft list page (per tenant) — shows registration, type, status placeholder
- [x] Add aircraft form — registration, make/model, year (components can be added after)
- [x] Aircraft detail page — shows linked components grouped by type
- [x] Add/edit component form linked to an aircraft
- [x] Delete aircraft (with cascade to components)
- [x] Basic auth guard — `login_required` decorator redirects unauthenticated users to login
- [x] Extend dev seed with a realistic fleet: 2–3 aircraft with engines, propellers, and one multi-engine example (done in Phase 1 seed)

---

## Phase 3 — Basic Flight Logging ✅

Goal: a user can record a flight against an aircraft.
Minimal fields only; logbook refinement comes later.

- [x] `FlightEntry` model — aircraft FK, date, departure airfield, arrival airfield, hobbs start/end
- [x] Log flight form (one page, minimal fields)
- [x] Flight list per aircraft (date, route, hobbs delta)
- [x] Aircraft total hobbs derived automatically from flight entries
- [x] Route tests for flight creation and listing
- [x] Extend dev seed with a plausible flight history (≥ 10 entries spread across aircraft)

---

## Phase 4 — Basic Maintenance Tracking ✅

Goal: define when maintenance is due (by date or by hours) and see its status.

- [x] `MaintenanceTrigger` model — aircraft FK, name, type (calendar / hours), threshold value
- [x] `MaintenanceRecord` model — trigger FK, date performed, notes
- [x] Add trigger form (hard date or N hours since last service)
- [x] Trigger list per aircraft — shows OK / due soon / overdue based on current hobbs or date
- [x] Mark trigger as serviced (creates a `MaintenanceRecord`)
- [x] Route tests for trigger CRUD and status calculation
- [x] Extend dev seed with maintenance triggers in all three states: OK, due soon, and overdue

---

## Phase 5 — Real Dashboard ✅

Goal: replace placeholder cards with live data.

- [x] Fleet overview — real list of aircraft with computed status colour
- [x] Per-aircraft status: green (all OK) / yellow (due ≤ 30 days or ≤ 10% hours) / red (overdue)
- [x] Recent flights panel — last 5 flights per aircraft
- [x] Upcoming maintenance panel — next 5 items sorted by urgency
- [x] Quick stats — total aircraft, flights this month, open alerts
- [x] Verify dev seed covers all dashboard states: at least one aircraft green, one yellow, one red

---

## Phase 6 — Public Demo Deployment ✅

Goal: publish the app as a live demo anyone can try without signing up.
See [`docs/demo-deployment.md`](demo-deployment.md) for the full technical spec.

- [x] Add `demo` as a valid `FLASK_ENV` value (entrypoint + app validation)
- [x] In demo mode: always show landing page to unauthenticated visitors (skip the "welcome back" state)
- [x] Landing page CTA replaced by "Try the demo" button → `POST /demo/enter` — no login form, no credentials
- [x] Logout in demo mode returns to landing page; `demo_slot_id` preserved in session so the same slot is restored on re-entry
- [x] One isolated tenant per demo slot (20 slots); visitor is silently assigned a free slot via session
- [x] Demo mode restrictions: no new-user creation, no password/TOTP changes
- [x] Demo seed script — reuses dev seed fleet data (`_seed_helpers.py`) multiplied across all 20 slots
- [x] Wipe-and-refresh script (`demo/refresh.sh`) callable by cron:
  - Checks GHCR for a newer image; pulls and rebuilds if found
  - Always wipes the demo DB and restarts the container fresh with demo seed
  - Prunes dangling Docker images after each pull to prevent disk exhaustion
  - Bundled inside the Docker image (`/app/demo-scripts/`) and exported to the host via bind-mount on container start; cron always runs the version shipped with the current image
- [x] Pre-wipe banner: if any slot had a login in the last 20 min, show countdown to next wipe
- [x] Configure a URL for the "Get Started" button on the landing page that gets published as a GitHub page to point to a published demo website. If not defined, the "Get Started" button must be deactivated there (nothing to get started with...)
- [x] GHCR CI workflow (`.github/workflows/publish.yml`) — publish image on every merge to `main`
- [x] Extend demo seed with rich data so the app looks lived-in on first visit

---

## Phase 7 — Logbook & Flight Detail Expansion ✅

Goal: upgrade flight entries to full logbook quality.

- [x] Additional flight fields — pilot (free text), duration (auto-calculated), notes
- [x] Tach start/end (separate from hobbs)
- [x] Hobbs/tach photo attachment (file upload, stored locally)
- [x] Airframe logbook view — all entries for an aircraft
- [x] Engine logbook view — entries for a specific engine (hours since new / since last overhaul)
- [x] Propeller logbook view — entries for a specific propeller
- [x] Extend dev seed flight entries with pilot names, notes, and tach data

---

## Phase 8 — Cost Tracking ✅

Goal: track what it costs to operate each aircraft.

- [x] `Expense` model — aircraft FK, date, type (fuel / parts / insurance / other), amount, unit (L/gal/€/$)
- [x] Add expense form (per flight or standalone)
- [x] Expense list per aircraft — filterable by type and period
- [x] Cost-per-hour calculation over a configurable period (default 12 months)
- [x] Fuel cost per flight (optionally entered at log-flight time)
- [x] Extend dev seed with a year of mixed expense records (fuel, parts, insurance) across aircraft

---

## Phase 9 — Document & Photo Uploads ✅

Goal: attach documents and photos to aircraft, components, and log entries.

- [x] `Document` model — owner type (aircraft / component / entry), file path, metadata, sensitive flag
- [x] Upload form (drag-and-drop on desktop, camera on mobile)
- [x] Document list per aircraft/component — visible/sensitive toggle
- [x] Sensitive documents hidden from viewer/renter roles
- [x] Storage path configurable via env var (host-mounted volume)
- [x] Extend dev seed with placeholder document records (files bundled in the repo under `dev_seed_docs/`)

---

## Phase 10 — Backup & Restore ✅

Goal: automated daily encrypted backup so operators can recover from data loss.

- [x] Encrypted ZIP produced by a scheduled job (key from env var)
- [x] Backup written to a configurable host-mounted folder
- [x] Uploaded documents included in the ZIP under `uploads/`
- [x] `BackupRecord` model — path, timestamp, checksum
- [x] Restore procedure documented in `docs/`
- [x] Extend dev seed with a seeded `BackupRecord` to verify the backup list UI renders correctly

---

## Phase 11 — Read-only Share Link ✅

Goal: share a live, passwordless view of an aircraft's status with people who have no
account — e.g. a maintenance shop, a visiting pilot, or a club notice board.

- [x] `ShareToken` model — aircraft FK, random 8-char token, access level (summary / full), created_at, revoked_at
- [x] Public route `GET /share/<token>` — no login required; returns 404 for unknown or revoked tokens
- [x] Two access levels: **summary** (status badges, maintenance item names only) and **full** (adds due dates, hobbs values, recent flights and non-sensitive documents)
- [x] Page served with `X-Robots-Tag` header and `<meta>` tag to prevent crawler indexing
- [x] Token management UI on the aircraft detail page: generate (modal with access level choice), view active tokens, revoke
- [x] QR code generated server-side (`qrcode` library), downloadable as PNG
- [x] Dev seed: OO-PNH with a summary token, OO-ABC with a full token
- [x] Route tests: valid token, revoked token, access-level gating, noindex header, QR endpoint

---

## Phase 12 — Snag List ("Open Ends") ✅

Goal: pilots can log defects noticed during or after a flight so the next crew is
aware of known issues before departure, and mechanics know what needs fixing.

- [x] `Snag` model — aircraft FK, title, description, reporter, reported_at, resolved_at, grounding flag
- [x] Aircraft gains a derived "grounded" state when any unresolved grounding snag exists
- [x] Grounded aircraft shows a persistent red banner on its detail page and a distinct "GROUNDED" badge on the dashboard and aircraft list (overrides maintenance status colour)
- [x] Snag entry available standalone from the aircraft detail page and from the full snag list page
- [x] "Active Known Points" panel on the aircraft detail page listing all open snags
- [x] Closing a snag requires a brief resolution note; closed snags are archived, not deleted
- [x] Grounding snags surface in the dashboard's Alerts panel above scheduled triggers
- [x] Dev seed covers: one aircraft with a grounding snag, one with a non-grounding snag, one clean
- [x] Route tests: snag CRUD, grounding propagation to aircraft status, dashboard ordering

---

## Phase 13 — Fleet Maintenance Overview ✅

Goal: a single page giving a fleet-wide picture of all maintenance obligations and open
defects — the "morning briefing" view an operator or CAMO inspector would want.

**By-type view** (default tab / section):
- [x] Grounding snags section — all open grounding snags across all aircraft, red alert style; links to each aircraft's snag list
- [x] Open snags section — all non-grounding open snags fleet-wide; links to each aircraft's snag list
- [x] Maintenance timeline section — all triggers across all aircraft, full list (not capped); columns: aircraft, item, type, due date/hobbs, status badge; link to service form and per-aircraft maintenance list
- [x] Links to per-aircraft snag list and full maintenance history within each section
- [x] Sorting: snags by `reported_at` ascending (oldest on top); maintenance triggers by urgency band (overdue → due soon → OK), then by `due_date` ascending within each band; hours-based triggers (no reliable date) sorted after all calendar-dated triggers within their band

**Chronological view** (second tab / toggle):
- [x] Single unified list of alerts only (grounding snags, open snags, overdue and due-soon triggers), sorted by date ascending — oldest/most-overdue on top
- [x] Calendar-dated items sort by their due date; hours-based triggers (no reliable date) pushed to the end of the list
- [x] Each row labelled by type (Grounding / Snag / Maintenance) with appropriate badge colour
- [x] Same per-aircraft action links as the by-type view

**Common:**
- [x] "All clear" empty state when no open snags and no overdue/due-soon triggers
- [x] Route accessible from the "Maintenance" navbar link; view toggle uses btn-group for clear active/inactive visibility
- [x] Dev seed covers the full range of states so both views render non-trivially
- [x] Route tests: page renders with mixed fleet data, both views accessible

---

## Phase 14 — Email Infrastructure ✅

Goal: establish the full email-sending stack so that every later phase that needs
to send a message (welcome email, maintenance alert, reservation confirmation, …)
has a working, tested foundation to call into.

**Configuration (env vars, consistent with the rest of the app's infrastructure config):**
- [x] SMTP settings read from environment variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS` (default true), `SMTP_FROM_ADDRESS`, `SMTP_FROM_NAME`
- [x] Configuration page gains an "Email" section showing which env vars are set (values masked; password shown as "Set"/"Not set" only) and their current status — read-only display, no form to edit (operators configure via their Docker Compose / `.env` file); unset vars show a "Not set" indicator, vars with a default show the default value labelled as such
- [x] "Send test email" button — sends a plain-text probe to the logged-in user's address and flashes success/failure with the SMTP error message if any

**Email service (`services/email_service.py`):**
- [x] `send_email(to, subject, text_body, html_body=None)` — reads SMTP settings from env, connects via `smtplib`, sends a `MIMEMultipart` message; raises `EmailNotConfiguredError` if `SMTP_HOST` is unset, `EmailSendError` on SMTP failure
- [x] Jinja2-based HTML email template (`templates/email/base_email.html`) — branded header, body slot, footer with unsubscribe note placeholder
- [x] Plain-text fallback always included (accessibility + spam-filter hygiene)
- [x] `EmailNotConfiguredError` and `EmailSendError` custom exceptions; callers catch and flash a user-friendly message rather than crashing

**Demo / dev:**
- [x] Demo mode disables outbound email entirely (guard in `send_email` checks `FLASK_ENV`)
- [x] Route tests: test-email endpoint (mocked SMTP via `unittest.mock`), `send_email` unit tests covering the not-configured and SMTP-error paths

**Multi-tenant note:**
SMTP is intentionally shared across all tenants — it is operator infrastructure, not
per-tenant config.  A single `SMTP_FROM_ADDRESS` and `SMTP_FROM_NAME` are set by the
instance operator via env vars and apply to all outgoing mail.  Per-tenant branding
(subject prefix, display sender name) is handled at the application layer in Phase 34,
not by changing the transport configuration.

**Inbound email (future):**
- Receiving email (invoices, AD/STC notifications forwarded by airworthiness bodies) would require either a self-hosted MTA (Postfix + procmail) or a webhook from a transactional mail provider (Mailgun, SendGrid inbound parse). Tracked in [`docs/backlog.md`](backlog.md); the architecture decision (self-hosted vs. provider webhook) should be made when the use-cases are better defined.

---

## Phase 15 — Counter Renaming & Maintenance Fix ✅

Goal: correct the maintenance hour source (currently using flight time instead of
engine time) and lay the DB foundation for the full logbook refinement.
No visible UI changes beyond the aircraft settings page.
Documented in [`docs/logbook_airplane.md`](logbook_airplane.md).

**`FlightEntry` column renames:**
- [x] Rename `hobbs_start` / `hobbs_end` → `flight_time_counter_start` / `flight_time_counter_end`
- [x] Rename `tach_start` / `tach_end` → `engine_time_counter_start` / `engine_time_counter_end`
- [x] Rename photo fields: `hobbs_photo` → `flight_counter_photo`, `tach_photo` → `engine_counter_photo`

**`Aircraft` model — fix maintenance hour source:**
- [x] `Aircraft.total_hobbs` property renamed to `total_engine_hours` — now reads from `engine_time_counter_end` (tach/engine time, which is the correct basis for maintenance scheduling); previously incorrectly used the flight time counter
- [x] Add `Aircraft.total_flight_hours` property — reads from `flight_time_counter_end` (for display and pilot logbook use)
- [x] `MaintenanceTrigger.due_hobbs` column renamed to `due_engine_hours`; `status()` method updated accordingly

**Aircraft-level logbook settings (new fields on `Aircraft`):**
- [x] `regime` — `EASA | FAA` (default `EASA`); controls which logbook fields are required vs optional
- [x] `has_flight_counter` — bool (default `True`); whether the aircraft has a separate airspeed-activated flight time counter
- [x] `flight_counter_offset` — Numeric(3,1) (default `0.3`); tenths of an hour subtracted from engine time to derive flight time on tach-only aircraft
- [x] Aircraft settings UI updated to expose these three fields with inline help text (see [`docs/logbook_airplane.md`](logbook_airplane.md))

**Migration:**
- [x] Alembic migration: rename `hobbs`/`tach` columns, rename `due_hobbs`, add three new `Aircraft` columns

**Dev seed:**
- [x] Seed flight entries updated with renamed counter fields
- [x] At least one seed aircraft configured as tach-only (`has_flight_counter = False`)

**Tests:**
- [x] Maintenance hour source: `total_engine_hours` uses engine counter; `total_flight_hours` uses flight counter; trigger `status()` uses engine hours
- [x] Aircraft settings: `regime`, `has_flight_counter`, `flight_counter_offset` persist correctly
- [x] Existing flight list and logbook views still render after column renames

---

## Phase 16 — FlightCrew, EASA Fields & Logbook UI ✅

Goal: complete the EASA AMC1 ORO.MLR.110-compliant journey log — crew model,
full set of per-flight fields, revised entry form, and updated logbook view.

**New `FlightCrew` model:**
- [x] `id` PK, `flight_id` FK → `FlightEntry` (cascade delete), `user_id` FK → `User` (nullable — null for external/visiting pilots), `name` String (always stored), `role` String (`PIC | IP | SP | COPILOT`), `sort_order` Integer
- [x] Up to 2 crew members per flight entry; enforced in the form, not at DB level
- [x] `user_id` link enables Phase 17 pilot logbook to query "all flights I was crew on"
- [x] Remove `pilot` String field from `FlightEntry`; migrate existing values to a `FlightCrew` record with `role = PIC`

**New `FlightEntry` fields:**
- [x] `departure_time` — Time, UTC, nullable (EASA col 7)
- [x] `arrival_time` — Time, UTC, nullable (EASA col 8)
- [x] `flight_time` — Numeric(4,1), nullable; auto-derived from counter difference or `engine_time − flight_counter_offset` (tach-only); manually overridable
- [x] `nature_of_flight` — String(100), nullable; free text with pre-seeded suggestions (Local / Navigation / Training / IFR / Night / Ferry / Other)
- [x] `passenger_count` — Integer, nullable
- [x] `landing_count` — Integer, nullable

**Counter pre-fill logic:**
- [x] On new flight entry, `engine_time_counter_start` and `flight_time_counter_start` pre-filled from the previous entry's end values
- [x] First entry for an aircraft: start values left blank (user enters manually)
- [x] If a start value ever differs from the previous entry's end value, show a discrepancy warning

**UI:**
- [x] Revised flight entry form with all fields grouped logically: Date / Crew / Route / Times / Counters / Nature & Passengers / Notes / Photos / Fuel
- [x] Nature of flight — `<input list>` with `<datalist>`: pre-seeded suggestions + previously used free-text values for that aircraft
- [x] Times displayed and entered in UTC with a clear label
- [x] For tach-only aircraft (`has_flight_counter = False`): flight counter fields hidden; `flight_time` auto-computed from `engine_time − flight_counter_offset`
- [x] Logbook view updated to display columns matching the EASA journey log layout

**Migration:**
- [x] No Alembic (project uses `create_all`); demo mode drop/recreate handles schema changes

**Documentation:**
- [x] [`docs/logbook_airplane.md`](logbook_airplane.md) updated to reflect final field names and implementation decisions

**Dev seed:**
- [x] Crew records created for existing seed entries (solo PIC; C172 cross-country has a second COPILOT crew member)
- [x] `nature_of_flight`, departure/arrival times, and landing counts added to seed entries

**Tests:**
- [x] `FlightCrew` model: cascade delete, nullable `user_id`, `sort_order`
- [x] Counter pre-fill: start values populated from previous entry; blank on first entry
- [x] Flight time derivation: from counter difference; from `engine − offset` (tach-only); manual override wins
- [x] Nature of flight: free-text value stored; returned in suggestions on subsequent entries for same aircraft
- [x] View tests: revised form renders all new fields; new fields saved correctly; two crew members

---

## Phase 17 — Pilot Profile & Manual Logbook ✅

Goal: give each pilot their own EASA-compliant personal logbook that works as
a standalone manual tool — entries can be created and maintained entirely by
hand, including flights on aircraft not managed in OpenHangar.
Documented in [`docs/logbook_pilot.md`](logbook_pilot.md).

**`PilotProfile` model:**
- [x] `user_id` FK → `User` (1:1), `license_number` String nullable, `medical_expiry` Date nullable, `sep_expiry` Date nullable
- [x] Pilot profile page — view and edit own profile from the user menu

**`PilotLogbookEntry` model:**
- [x] Core fields: `pilot_user_id` FK → `User`, `date`, `aircraft_type` / `aircraft_registration` (text; always free text at this phase)
- [x] `flight_id` FK → `FlightEntry`, nullable; `SET NULL` on `FlightEntry` deletion so the pilot's record is preserved
- [x] Route fields: `departure_place` / `departure_time` (UTC), `arrival_place` / `arrival_time` (UTC)
- [x] `pic_name` — String, free text
- [x] Operational conditions: `night_time`, `instrument_time` — Numeric(4,1) hours
- [x] Landings: `landings_day`, `landings_night` — Integer counts
- [x] Time columns: `single_pilot_se`, `single_pilot_me`, `multi_pilot` — Numeric(4,1) hours
- [x] `total_flight_time` — Numeric(4,1), derived: `single_pilot_se + single_pilot_me + multi_pilot`
- [x] Function columns: `function_pic`, `function_copilot`, `function_dual`, `function_instructor` — Numeric(4,1) hours
- [x] `remarks` — Text, nullable

**Manual entry form:**
- [x] Standalone entry form — all fields manually entered; aircraft registration and type are free text
- [x] Accessible from the pilot's logbook view ("Add entry" button)

**Pilot logbook view:**
- [x] Chronological list of all `PilotLogbookEntry` records for the logged-in pilot
- [x] Running totals row (dynamically computed): Night, Instruments, Day landings, Night landings, S/E, M/E, Multi-pilot, Total flight time, PIC, Co-pilot, Dual, Instructor
- [x] Logbook is private to the holder — no other user (including admins) can view it; opt-in sharing is tracked in [`docs/backlog.md`](backlog.md)

**Dev seed:**
- [x] Pilot profiles for seed users
- [x] At least 2 standalone entries per seed pilot

**Tests:**
- [x] `PilotLogbookEntry` model: `SET NULL` on `FlightEntry` deletion, running totals computation
- [x] Manual entry: create, edit, delete; all fields persist correctly
- [x] Route tests: logbook list and totals row, add/edit/delete entry

---

## Phase 18 — Pilot Currency & Legality Checks ✅

Goal: derive currency status, medical validity, and legality checks from pilot
logbook data and surface warnings on the dashboard.

**Currency calculations:**
- [x] Passenger currency — count take-offs and landings in rolling 90-day window; warn when < 3
- [x] Night currency — count night take-offs and landings in rolling 90-day window; warn when < 3
- [x] Medical expiry — warn on dashboard when < 90 days remaining
- [x] SEP endorsement expiry — warn on dashboard when < 90 days remaining

**Forward-looking legality checks:**
- [x] "Approaching currency gap" warning: show the date by which the pilot must fly again to keep passenger/night currency, and the current shortfall
- [x] Dashboard panel: currency summary card for the logged-in pilot (medical, SEP, passenger currency, night currency) — colour-coded green/yellow/red

**Dev seed:**
- [x] At least one seed pilot with medical expiry < 90 days
- [x] At least one pilot approaching passenger currency lapse (last 3 qualifying flights > 60 days ago)

**Tests:**
- [x] Passenger and night currency: rolling 90-day window, boundary conditions (exactly 3, fewer than 3)
- [x] Medical/SEP expiry: warning injected at < 90 days; no warning at ≥ 90 days
- [x] Forward-looking gap: correct deadline date and shortfall count
- [x] Dashboard warning injection

---

## Phase 19 — Internationalisation (i18n) Infrastructure ✅

Goal: set up Flask-Babel, user language preference, locale selector, locale-aware
date formatting, and language switcher. Full string wrapping and French translation
are deferred to Phase 19b.

**Flask-Babel setup:**
- [x] Add `Flask-Babel` dependency (`flask-babel>=4.0.0`)
- [x] `babel.cfg` extraction config covering `[python: **.py]` and `[jinja2: **/templates/**.html]`
- [x] `pybabel extract` → `app/translations/messages.pot` committed to repo (8 navbar strings wrapped as proof of concept)
- [x] Navbar strings wrapped in `_()` as proof of concept; full wrap deferred to Phase 19b
- [x] English (`en`) as source language and automatic fallback for any untranslated string

**User language preference:**
- [x] `User` model gains a `language` VARCHAR(8) column (BCP 47 tag, e.g. `en`, `fr`); default `en`
- [x] Flask-Babel locale selector reads `user.language` when authenticated; falls back to `Accept-Language` header
- [x] Language switcher in the navbar — EN/FR buttons; selecting one saves the preference to `User.language` in the DB
- [x] Preference persists across requests (stored in DB)

**Locale-aware formatting:**
- [x] Dates rendered via Flask-Babel `format_date()` in pilot logbook — respects locale (e.g. "mai" in French)
- [x] `format_date`, `format_datetime`, `format_decimal` injected into Jinja globals
- [x] `<html lang="...">` attribute reflects active locale

**Dev seed:**
- [x] Second dev user `pierre@openhangar.dev` with `language = 'fr'`

**Tests:**
- [x] Language switcher: GET `/set-language/fr` updates `User.language` in DB
- [x] Locale selector: authenticated user with `language='fr'` sees French month names in logbook
- [x] Unauthenticated request with `Accept-Language: fr` gets `lang="fr"` in HTML
- [x] Date formatting: English → "May", French → "mai" in logbook dates
- [x] HTML `lang` attribute matches user locale

**Phase 19b — Full String Coverage & French Translation ✅**
- [x] Wrap all remaining user-facing strings in `_()` across all templates and routes
- [x] `translations/fr/LC_MESSAGES/messages.po` — complete French translation (581 strings)
- [x] `.mo` files compiled and committed
- [x] Language selector with flags and dropdown instead of buttons
- [x] Weblate setup documentation (`docs/dev-i18n.md`)
- [x] GitHub Actions for `.pot` sync and `.mo` compilation (added to `ci.yml`)
- [x] `pybabel compile` added to Dockerfile
- [x] Translation completeness test (`polib`) — `TestTranslationCompleteness` in `test_i18n.py`

---

## Phase 20 — Mass & Balance ✅

Goal: allow owners to define the weight & balance envelope for each aircraft
and compute the loaded CG for a given flight, flagging any out-of-envelope condition.

**Aircraft W&B configuration:**
- [x] `WeightBalanceConfig` model — aircraft FK, empty weight (kg), empty CG arm (m from datum), max take-off weight, forward CG limit, aft CG limit, fuel unit (L/gal); optional per-aircraft datum note
- [x] `WeightBalanceStation` model — config FK, label, arm (m), max weight kg (non-fuel stations), capacity L or gal (fuel stations), is_fuel flag
- [x] CRUD UI on the aircraft detail page — add/edit/delete stations; edit envelope limits and fuel unit (`/wb/config`); station limit label updates dynamically (kg ↔ L/gal) based on fuel checkbox
- [x] Dev seed: realistic W&B config for OO-PNH (C172S, Avgas, 262.5 L capacity) and OO-GRN (Robin DR-401, Jet-A1, 160 L capacity)

**In-flight CG calculation:**
- [x] W&B entry form: fuel stations use volume inputs (L or gal) with `step=0.25` and `max=capacity`; non-fuel stations use weight inputs (kg); fuel entry shows "≈ X kg" equivalent live
- [x] `GAL_TO_L = 3.78541` conversion; CG route converts volume → kg using `volume × FUEL_DENSITY[fuel_type] × (GAL_TO_L if gal else 1)`
- [x] Server-side capacity validation: volume > capacity triggers a validation error
- [x] `station_weights` JSON stores volume (L/gal) for fuel stations and kg for non-fuel stations
- [x] Real-time CG computation (client-side JS): total weight, moment sum → loaded CG; green OK / red OUT overlay
- [x] W&B calculation list page — date, label, total weight, loaded CG, in-envelope badge; edit and delete actions
- [x] Aircraft detail page shows the last computed CG and whether it was in-envelope
- [x] Optional ability to link a FlightEntry with a W&B entry

**Envelope diagram:**
- [x] Canvas envelope chart (client-side JS): forward/aft CG limits and MTOW plotted as a green polygon; loaded point overlaid in green (in envelope) or red (out of envelope)

**Tests:**
- [x] CG calculation: given known station weights/volumes → correct total weight and CG moment
- [x] Envelope check: point inside envelope → OK; aft of limit → out-of-envelope
- [x] CRUD: add/edit stations and config limits — all persist correctly; fuel station stores capacity, non-fuel stores max_weight
- [x] Flight link: W&B entry links to FlightEntry; link set to NULL when flight is deleted
- [x] Fuel volume → kg: 100 L avgas = 72 kg in total weight; 10 gal × GAL_TO_L × 0.72 verified
- [x] Capacity validation: volume > capacity shows error; negative volume shows error

---

## Phase 21 — Multi-user ✅

Goal: support more than one user per tenant, with role-based access control enforced server-side on every route.

**Roles:**
- [x] Three additional roles alongside Owner: **Pilot/Renter** (can log flights and view all records; cannot edit aircraft configuration or manage costs), **Maintenance** (can view and update maintenance logs; cannot log flights or edit aircraft data), **Viewer** (read-only access across the tenant)
- [x] Role enforcement on all aircraft, maintenance, flight, expense, and document routes

**Invitation flow:**
- [x] `UserInvitation` model — token (UUID), tenant FK, target role, expires_at, accepted_at
- [x] User management UI — admin invites a user via a time-limited URL (always shown in the UI, also sent by email if SMTP is configured); admin can reassign roles and revoke access
- [x] Accept-invitation route — renders a password-setup form; on submit creates `TenantUser` and marks invitation accepted

**Profile:**
- [x] User profile page — change password, manage TOTP (verify it works for all roles, not just Owner)

**Dev seed:**
- [x] Extend dev seed with two additional users: one Pilot/Renter and one Maintenance user to exercise role-based access

**Demo environment:**
- [x] Each demo slot seeds two users into the same tenant: one Owner and one Pilot/Renter, so both perspectives share the same fleet and data
- [x] Landing page shows two "Try the Demo" buttons side by side: **Try as Owner** and **Try as Renter**; each enters the demo slot under the corresponding user account
- [x] The existing single demo entry point (`/demo/enter`) is extended with a `role=` parameter (`owner` or `renter`); the landing page buttons pass this parameter

**Tests:**
- [x] Invitation: creation, expiry enforcement, acceptance, duplicate-acceptance rejection
- [x] Role enforcement: representative routes checked for each role — allowed actions succeed, forbidden actions return 403
- [x] Demo entry: entering as owner lands on the owner account; entering as renter lands on the renter account of the same tenant; renter cannot access owner-only routes

---

## Phase 22 — Reservations & Rentals ✅

Goal: allow an owner to manage aircraft bookings for pilot/renters, with conflict detection and cost estimation.

- [x] `Reservation` model — aircraft FK, pilot FK, start/end datetime, status (pending / confirmed / cancelled), notes
- [x] Booking calendar view per aircraft — month/week grid, colour-coded by status
- [x] Create / edit / cancel reservation from the calendar or aircraft detail page
- [x] Per-aircraft minimum and maximum booking duration (stored in DB, editable by owner)
- [x] Owner approval workflow — reservation starts as "pending", owner confirms or declines
- [x] Cost estimation at booking time based on current hourly rate (defined at the aircraft level); as of Phase 36, falls back to the cost dashboard's computed wet rate when no manual rate is set, with a divergence warning on the booking settings page when a manual rate drifts more than 10% from the computed one
- [x] Conflict detection — prevent overlapping confirmed reservations
- [x] Dev seed: two weeks of reservations across all seed aircraft

**Tests:**
- [x] CRUD: create, edit, cancel reservation — all persist correctly
- [x] Conflict detection: overlapping confirmed reservations rejected
- [x] Approval flow: pending → confirmed / declined by owner
- [x] Calendar rendering: reservations appear in the correct slots

---

## Phase 23 — Granular Roles & Per-Aircraft Access Control ✅

Goal: replace the flat five-role model with a richer profile-type + permission-mask system that supports fine-grained per-aircraft grants, an "access to all aircraft" flag (including aircraft added in the future), and the groundwork for student and instructor profiles (full flows in Phase 26). A central `AuthorizationService` replaces ad-hoc role checks scattered across blueprints.

**Profile model:**
- [x] `is_pilot` boolean on user — enables pilot-specific flows: personal logbook, reservations, pilot-level flight logging
- [x] `is_maintenance` boolean — enables maintenance-specific flows: edit aircraft details/components, add/edit maintenance tasks
- [x] `view_only` boolean — suppresses all write capabilities regardless of other flags; supersedes `is_pilot` / `is_maintenance` when true
- [x] Student and instructor profile types: data model only (added `STUDENT` and `INSTRUCTOR` to `Role` enum); full permission flows and UI deferred to Phase 26
- ~~Add `profile_type` column replacing the current `Role` enum~~ — design changed; `Role` enum was extended with STUDENT/INSTRUCTOR instead

**Aircraft access model:**
- [x] `permissions_mask` bitmask on `UserAircraftAccess`; `PermissionBit` constants class with all eight bits and per-role defaults
- [x] All-aircraft grant: `UserAllAircraftAccess(user_id, tenant_id)` model grants access to every existing and future aircraft in a tenant
- [x] Admin users implicitly bypass all access checks (admin bypass is step 1 in the evaluation order)
- [x] `permissions_mask` bits: `view_aircraft`, `edit_aircraft`, `read_maintenance_full`, `read_maintenance_limited`, `write_maintenance`, `edit_components`, `write_logbook`, `reserve_aircraft`
- [x] Dev seed: `is_pilot`/`is_maintenance` flags set; all-planes row added for admin user
- ~~Migrate existing per-aircraft access rows to an explicit mask~~ — N/A; `permissions_mask` is nullable and falls back to role defaults by design

**Authorization service:**
- [x] Central `AuthorizationService` in `app/services/authorization.py` — `effective_mask()`, `can()`, `maintenance_view_level()`
- [x] Evaluation order: (1) admin bypass → (2) all_planes row → (3) per-aircraft row → (4) profile-type defaults; `view_only` strips write bits at the end
- [x] Role presets in `PermissionBit.ROLE_DEFAULTS` — explicit masks override defaults in both directions
- ~~Replace remaining ad-hoc `require_role()` calls with `AuthorizationService.can()`~~ — deferred; low urgency refactor, 50 call sites

**Enforcement rules:**
- [x] `view_maintenance`: `maintenance_view_level()` returns `full` / `limited` / `none`; limited view shows only overdue/due-soon items, hides interval and service-history columns
- [x] `log_flight` on a managed aircraft: `require_pilot_access` guard applied; covers INSTRUCTOR role and `is_pilot` flag
- ~~`log_flight` on an external aircraft: pilot logbook entry only~~ — already handled by architecture; `PilotLogbookEntry` and `FlightEntry` are separate models
- ~~`reserve_aircraft`: enforce `reserve_aircraft` bit; students denied~~ — deferred to Phase 26 (student/instructor flows)

**Frontend:**
- [x] User management UI: `is_pilot` / `is_maintenance` / `view_only` toggles per user (auto-submit checkboxes)
- [x] Per-aircraft permission editor: checkbox grid with per-bit columns; quick-preset buttons (`/config/users/<id>/permissions`)
- [x] "Grant access to all aircraft" toggle (`UserAllAircraftAccess`)
- [x] Maintenance view: limited view banner + hidden columns for pilots/students
- ~~Reservation UI: show booking controls only when user holds `reserve_aircraft` bit~~ — deferred to Phase 26

**Tests:**
- [x] Permission evaluation: `effective_mask` and `can()` for each role and access pattern; `view_only` strips write bits
- [x] all_planes: pilot with `UserAllAircraftAccess` sees full fleet in aircraft list
- [x] Limited DTO: pilot gets limited view (overdue/due-soon only), owner gets full view
- [x] Override: custom `permissions_mask` on per-aircraft row takes effect

---

## Phase 24 — CI & Code-Quality Hardening ✅

Goal: lock in the quality gains already made and close the remaining gaps in linting, security scanning, supply-chain hygiene, and pipeline strictness — chipping away one item at a time.

**Code quality**
- [x] Add **Ruff** to CI (linting + import sorting) and fail the build on violations; add ruff to pre-commit
- [x] Add **Ruff formatter** check to CI so formatting divergence blocks merges
- [x] Add **mypy** type-checking step to CI (start in lenient/non-strict mode and ratchet) ✅
- [x] **mypy strict mode** — ratchet complete: `strict = true` in `pyproject.toml`; all 39 source files pass with zero errors ✅
- [x] Add **bandit** Python security linter to CI; fail on HIGH severity findings ✅
- [x] Add local pre-push checks for ruff and bandit via `.githooks/pre-push`; hadolint stays CI-only (too slow/heavy for a local hook) ✅

**Docker hardening**
- [x] Add **hadolint** Dockerfile linting step to CI
- [x] Refactor `docker/Dockerfile` into a **multi-stage build** (build stage for compile-time deps, lean runtime stage) to shrink the final image and reduce Trivy surface
- [x] Flip Trivy **`exit-code`** from `'0'` to `'1'` so HIGH/CRITICAL unfixed vulns block CI

**Supply chain / dependency hygiene**
- [x] Add **`.github/dependabot.yml`** for automated pip and GitHub Actions version-update PRs
- [x] Add **SBOM generation** (Syft / CycloneDX) to the Docker job and attach the SBOM to each release artifact

**Process / governance**
- [x] Enforce **coverage threshold** (`--cov-fail-under=100`) in `pytest.ini` so a coverage regression blocks CI ✅
- [x] Make the **translation check hard-fail** (exit non-zero) instead of emitting a warning and continuing, docs: document pre-push translation hook in development.md
- [x] Add a **`CODEOWNERS`** file mapping sensitive paths (routes, auth, migrations) to required reviewers ✅

---

## Phase 25 — Production Readiness (v1) ✅

Goal: close the gaps that prevent a safe first production deployment for a single-operator
self-hosted instance. No new features — only hardening, correctness, and operational confidence.

**Database schema migrations (Alembic):**
- [x] Initialise Alembic with a baseline revision that matches the current schema exactly
- [x] Wire `alembic upgrade head` into the Docker entrypoint so every container restart applies pending migrations automatically
- [x] Add a CI step that applies all migrations against a fresh PostgreSQL DB and runs the test suite on top, confirming the migrated schema is equivalent to `create_all`
- [x] Document the migration workflow in `docs/development.md` (how to generate a new revision, how to test it locally, what happens on first deploy vs. upgrade)

**Backup & restore verification:**
- [x] Run a full end-to-end restore drill: take a backup ZIP from a running instance, restore it to a clean DB, and confirm all data (flights, documents, maintenance records) is intact
- [x] Fix any gaps found; update `docs/backup_restore.md` with exact commands and expected output (fixed critical bug: restore docs used `hashlib.sha256` but backup uses HKDF-SHA256; added regression tests in `test_backup.py`; add `postgresql-client` to Dockerfile so `pg_dump` is available)
- [x] Add a CI smoke-test that produces a backup against a real PostgreSQL DB using `flask backup-now`, asserts "Backup OK:" in output, confirming `pg_dump` runs end-to-end

**Documentation review**
- [x] Review all user-focused documentation to ensure completeness/correctness (fixed stale `db.create_all()` reference in `docs/self-hosting.md` → Alembic; all other docs verified accurate)
- [x] gap (Phase 16): `docs/logbook_airplane.md` already reflects the final column names (`flight_time_counter_*`, `engine_time_counter_*`) and the `regime` / `has_flight_counter` / `flight_counter_offset` aircraft settings — no changes needed

**Rate limiting & brute-force protection:**
- [x] Decided approach: Traefik `RateLimit` middleware applied at the reverse-proxy level on `/login` — no application code changes required
- [x] Added Traefik labels to `docker/docker-compose.yml` (5 req/min steady, burst 10, per source IP) and documented the snippet with a nginx note in `docs/self-hosting.md`
- [x] Added brute-force section to `SECURITY.md` explaining the infrastructure-layer approach and linking to the self-hosting guide

---

## Phase 26 — Onboarding Wizard & Adaptive UI ✅

Goal: deliver a "wow" first-run experience for a fresh self-hosted install —
a focused, friendly setup flow that gets the instance ready in minutes and
lands the user on a dashboard already tailored to their context. Every choice
made here is reversible and clearly labelled as such, so operators feel free
to answer quickly rather than agonising over the perfect answer.

**UX principles for the wizard:**
- Each screen asks at most one or two things; no long forms
- Every question carries a one-line reassurance: *"You can change this later in Settings"*
- Tone is warm and personal throughout ("Let's get your hangar set up")
- Progress indicator shows which step the user is on and how many remain
- The wizard is **not accessible in demo mode** (`FLASK_ENV=demo` → redirect to home)

**Instance bootstrap — first visit to an empty database (step 1):**
- [x] Detect empty database (no users exist) and redirect any request to `/setup`; in demo mode `/setup` redirects to the demo home instead
- [x] Setup screen collects: full name, email address, password (with confirmation), optional TOTP enrollment (QR code + verification token before proceeding)
- [x] Submitting creates the `Tenant`, the first `User`, and a `TenantUser` record with the Owner role in a single transaction
- [x] `/setup` redirects to `/config/` (or to `/login` if not authenticated) once a user exists

**Operating-context questionnaire (steps 2–3 — immediately after account creation):**
- [x] **Primary-use question (step 2):** two large, friendly cards — *"I manage aircraft"* (track flights, maintenance, documents, costs) and *"Pilot logbook only"* (keep a personal flight record); labelled *"You can always expand this later in Settings"*
  - *Pilot logbook only* → `operating_model = sole_pilot`; wizard ends here and goes straight to the dashboard — no aircraft count question, no operating model detail; aircraft and maintenance modules hidden from navbar but accessible if they revisit Settings
  - *I manage aircraft* → continue to step 3

- [x] **Aircraft management detail (step 3, manage-aircraft path only):**
  - *How many aircraft do you plan to manage?* — numeric input (1 or more); labelled *"You can add more any time"*; drives adaptive UI (1 = single-aircraft simplifications, >1 = full fleet view)
  - *How would you describe your operation?* — clearly-worded cards: **Sole operator** / **Shared ownership** / **Flight club** / **Flight school**; labelled *"You can update this in Settings"*
  - *Flight club* selected → inline follow-up: *What is your club called?* (stored in `TenantProfile.club_name`; used by Phase 29)
  - *Flight school* selected → inline follow-up: *What is your school called?* (stored in `TenantProfile.school_name`; reserved for a future phase)
  - *Shared ownership* selected → inline follow-up: invite co-owners (see multi-invite below); labelled *"You can invite more people later"*
  - *Renting or lending to others?* — Yes / No toggle; labelled *"You can change this any time"*

- [x] All answers stored in `TenantProfile` immediately — later phases build on these values rather than asking again

**Multi-user invite (upgrade to existing `UserInvitation` flow):**
- [x] Extend `UserInvitation` with a `display_name` field (the name entered by the person doing the inviting; used to greet the invitee on the claim page)
- [x] Replace the existing single-invite form with a dynamic multi-row form: each row collects name and role (Admin / Owner); rows can be added or removed before submitting; one `UserInvitation` record and token is created per row in a single submission
- [x] This multi-invite form is available standalone from the Configuration / user management page, not only from the wizard
- [x] Generated invite URLs are shown in a summary after submission for the inviter to copy and send; each URL encodes only the token
- [x] When an invitee visits their URL they are greeted by name ("Welcome, Sophie!"), then complete account creation: email, password, optional TOTP — the name is pre-filled and editable
- [x] Tokens remain single-use and expire after 7 days; expired or already-claimed tokens redirect to login with an explanatory message
- [x] The wizard's shared-ownership co-owner step renders this same multi-invite form inline, pre-labelled for the shared-ownership context

**Tenant profile model (foundation for future phases):**
- [x] `TenantProfile` model (or JSON column on `Tenant`) with fields: `operating_model` (enum: **sole_pilot** / sole_operator / shared_ownership / flight_club / flight_school), `planned_aircraft_count` (integer; null for sole_pilot), `allows_rental` (bool), `club_name` (string; flight_club), `school_name` (string; flight_school), `organisation_name` (string; shared_ownership, used by Phase 28)
- [x] `UserInvitation` extended with `display_name` (the name entered by the first owner during the wizard) so the claim page can greet the invitee by name
- [x] Configuration page exposes the full profile for review and editing after initial setup

**Adaptive UI based on profile:**
- [x] *Sole pilot* (`operating_model = sole_pilot`): aircraft, maintenance, and expense modules hidden from navbar; dashboard shows pilot logbook summary and a gentle prompt — *"Want to track an aircraft too? Add one in Settings"*
- [x] *Single aircraft* (`planned_aircraft_count = 1`): suppress fleet-count card on dashboard; "Add aircraft" quick-action disappears from dashboard once one aircraft exists (still accessible from `/aircraft/`); dashboard links directly to that aircraft's detail page
- [x] *Multi-aircraft* (`planned_aircraft_count > 1`): "Add aircraft" quick-action stays on dashboard until the planned count is reached, then moves to `/aircraft/` only
- [x] *No-rental profile*: Reservations module hidden from navbar (Phase 22; surfaced only when `allows_rental = true`)
- [x] *Sole operator, single aircraft*: offer a quick-log widget directly on the dashboard

**Tests:**
- [x] `/setup` accessible on empty DB; redirects to `/config/` (or `/login`) once a user exists; redirects to demo home in demo mode
- [x] Transaction atomicity: partial failures during bootstrap leave DB unchanged
- [x] TOTP step optional; when completed the secret is stored and immediately active
- [x] `TenantProfile` persists all answers correctly for every operating model combination
- [x] Dashboard quick-action visibility follows `planned_aircraft_count` and actual aircraft count correctly
- [x] Multi-invite: submitting N rows creates N `UserInvitation` records atomically; each token is single-use; claiming a token creates the user, pre-fills the display name, and marks the token consumed; expired tokens show an explanatory message
- [x] Multi-invite form works identically from the Configuration page and from within the wizard

---

## Phase 27 — Document Improvements ✅

Goal: make documents a first-class feature — attach files to pilot profiles and insurance records, improve the upload experience with live title suggestions, and let users view PDFs and images inline instead of always downloading.

**Pilot profile documents:**
- [x] Pilot profile page gains a "Documents" section: upload and manage files typed as **License** (pilot certificate scan) or **Medical certificate** (class 1/2/LAPL scan)
- [x] Each document stores: file, title (free text with suggestions — see below), document type, `valid_until` date (optional), and the existing sensitive flag
- [x] Expiry warning: if `valid_until` is set and within 90 days, show a badge on the pilot profile page and surface the alert on the pilot's dashboard currency card

**Aircraft insurance certificate:**
- [x] Insurance section on the aircraft detail page gains an "Attach certificate" upload button
- [x] The uploaded file is stored as a `Document` linked to the aircraft with type `insurance_certificate`; it is automatically associated with the aircraft's current `insurance_expiry` date
- [x] Only one active certificate per aircraft; uploading a new one marks the previous as superseded (file kept in storage)
- [x] Certificate displayed inline in the Insurance section using the viewer below

**"As you type" title suggestions:**
- [x] Document upload title field shows a suggestion dropdown on focus; filters as the user types; field remains free text and accepts any value
- [x] Suggestions come from existing `Document` titles for the same tenant and `owner_type` (aircraft / pilot / component), delivered by a lightweight `/documents/title-suggestions?q=…&owner_type=…` endpoint (JSON list, up to 10 results, case-insensitive prefix match)

**Inline document viewer:**
- [x] Document list items open an inline viewer on click:
  - **PDF**: `<iframe>` or PDF.js modal; "Download" button below the viewer
  - **Images** (JPEG, PNG, WEBP): `<img>` in a modal; "Download" button below
  - **Word / Excel / other**: no viewer — clicking triggers a direct download
- [x] Viewer available from all document lists: aircraft documents, pilot profile documents, component documents

**"Download all documents" button:**
- [x] Aircraft detail Documents section gains a **Download all documents** button; the server builds a ZIP archive containing all visible documents for that aircraft (non-sensitive only for pilots/viewers; all for owners/admins) and serves it as `aircraft-<reg>-documents.zip`
- [x] ZIP includes a `manifest.txt` listing each file's title, document type, upload date, and filename

**Dev seed:**
- [x] Seed pilot profiles with one License and one Medical certificate document (PDF placeholder files bundled under `dev_seed_docs/`)
- [x] Seed OO-PNH with an insurance certificate document linked to its insurance expiry date
- [x] Clean up all dev seed documents (more PDF or images instead of .txt files)

**Tests:**
- [x] Pilot profile documents: License and Medical types save with correct `owner_type`; visible only to the holder and admins
- [x] Insurance certificate: upload links correctly to the aircraft's insurance expiry; previous certificate marked superseded; new upload replaces it in the Insurance section display
- [x] Title suggestions: returns prefix-matched results; empty query returns up to 10 most-recent titles; results scoped to tenant and `owner_type`
- [x] Inline viewer: PDF and image MIME types return the modal/iframe response; unsupported types trigger a direct download
- [x] Download-all ZIP: role-appropriate files included; sensitive documents excluded for pilots/viewers; `manifest.txt` present with correct entries

---

## Phase 28 — Pilot Logbook Import ✅

Goal: allow a pilot to bulk-import their existing logbook from a CSV or Excel file, with an interactive column-mapping step that is remembered for future re-imports from the same source format.

The reference format studied during design is a standard EASA-layout Excel logbook with the following structure:
- **Row 1**: Merged group headers (`DEPARTURE & ARRIVAL`, `LANDINGS`, `AIRCRAFT CATEGORY`, `OPERATIONAL CONDITIONS`, `PILOT FUNCTION`, `PAGE SUBTOTALS`) — not the real column names
- **Row 2**: Actual column headers: `DATE dd/mm/yy`, `AIRCRAFT TYPE`, `AIRCRAFT registration number`, `FROM`, `TIME` (×2 — departure and arrival, same name), `TO`, `PIC NAME`, `NO. ISTR. APPR.`, `DAY`, `NIGHT` (landings), `SE`, `ME`, `CROSS-COUNTRY`, `DAY`, `NIGHT` (operational), `PIC`, `CO-PIC`, `DUAL RECEIVED`, `TOTAL FLIGHT TIME`; plus page-subtotal columns
- **Row 3+**: Data rows, newest-first; some rows are page-subtotal accumulators (cells contain `timedelta` objects rather than `time` — must be skipped)
- Duration cells are `datetime.time` objects (e.g. 42 min = `time(0,42)`); time-of-day cells are stored as `"HH:MM"` strings

**Import model:**
- [x] `LogbookImportMapping` model — pilot user FK, a JSON blob storing the mapping between source column names (with position index to disambiguate duplicate names such as two `TIME` columns) and `PilotLogbookEntry` fields, a `source_fingerprint` (hash of the normalised header row) so the same format is recognised on re-upload; created_at timestamp
- [x] `LogbookImportBatch` model — pilot user FK, import timestamp, row count, skipped count, mapping FK; links to the created `PilotLogbookEntry` rows so an import can be reviewed or rolled back as a unit

**Upload & header detection:**
- [x] Accept CSV (any delimiter auto-detected via Python `csv.Sniffer`) and `.xlsx` / `.xls` files; reject other formats with a clear error
- [x] Auto-detect the header row: scan the first 20 rows; the header is the first row where ≥ 50 % of non-empty cells are non-numeric strings and the row has at least 4 non-empty cells — this skips rows 1 and merged-title rows while correctly identifying row 2 in the EASA Excel layout
- [x] Strip embedded newlines, leading/trailing whitespace, and normalise to lowercase for matching; append a positional suffix (`_2`, `_3`) to duplicate column names so that e.g. two `TIME` columns become `time` and `time_2`
- [x] Detect and mark subtotal rows before presenting the mapping: a row is flagged as a subtotal if the cell that maps to `date` contains a `timedelta` value, is empty, or contains text like "TOTAL" — subtotal rows are silently excluded from import and counted separately

**Column mapping UI:**
- [x] After upload, present a mapping page: one dropdown per source column, pre-filled by fuzzy-matching source names to `PilotLogbookEntry` fields (`date`, `aircraft_type`, `aircraft_registration`, `departure_place`, `departure_time`, `arrival_place`, `arrival_time`, `pic_name`, `night_time`, `instrument_time`, `landings_day`, `landings_night`, `single_pilot_se`, `single_pilot_me`, `multi_pilot`, `function_pic`, `function_copilot`, `function_dual`, `function_instructor`, `remarks`); unmapped columns default to *ignore*
- [x] Built-in alias table for common column names found in real logbooks: `FROM`→`departure_place`, `TO`→`arrival_place`, `TIME` (first)→`departure_time`, `TIME` (second)→`arrival_time`, `SE`→`single_pilot_se`, `ME`→`single_pilot_me`, `PIC`→`function_pic`, `CO-PIC`→`function_copilot`, `DUAL RECEIVED`→`function_dual`, `NIGHT` (under OPERATIONAL CONDITIONS)→`night_time`, `DAY` (under LANDINGS)→`landings_day`, `NIGHT` (under LANDINGS)→`landings_night`, `DATE dd/mm/yy`→`date`, `AIRCRAFT TYPE`→`aircraft_type`, `AIRCRAFT registration number`→`aircraft_registration`, `PIC NAME`→`pic_name`; columns with no mapping default to *ignore* (`NO. ISTR. APPR.`, `CROSS-COUNTRY`, subtotal columns)
- [x] If a `LogbookImportMapping` with a matching `source_fingerprint` already exists, pre-fill the mapping dropdowns from the saved mapping (with a notice "recognised from a previous import — please verify")
- [x] If no exact fingerprint match is found but the user has at least one previous `LogbookImportMapping`, compute column-overlap scores (case-insensitive, stripping whitespace) between the new file's normalised header and each saved mapping's stored column list; if the best-scoring mapping covers ≥ 60 % of the new file's columns, pre-fill from that mapping with a notice "No exact format match — closest previous mapping applied, please review"; if no saved mapping reaches the 60 % threshold, fall back to pure alias-based auto-mapping as if no prior mapping existed
- [x] Validate that at least `date` is mapped before allowing the user to proceed; show a preview of the first 5 data rows with the proposed mapping applied so the user can spot mis-mapped columns

**Opening-hours offset:**
- [x] Option on the mapping confirmation page: "I already had hours before this file starts" — the user enters cumulative totals for each time category (SE, ME, night, IFR, PIC, dual, instructor); these are saved as a single synthetic `PilotLogbookEntry` with `remarks = "Opening balance (imported)"` dated one day before the earliest imported entry
- [x] Alternatively the user may leave all offsets at zero if the file represents their complete history

**Import execution:**
- [x] Parse each data row using the confirmed mapping; skip rows where the mapped `date` cell cannot be parsed (count and report skipped rows) and subtotal rows (counted separately)
- [x] Date values: accept `datetime.datetime` objects from Excel, ISO strings, and common European formats (`dd/mm/yy`, `dd/mm/yyyy`)
- [x] Time-of-day values (`departure_time`, `arrival_time`): accept `"HH:MM"` strings and Python `time` objects from Excel
- [x] Duration fields (`night_time`, `function_pic`, etc.): accept Python `datetime.time` objects (Excel stores 42 min as `time(0,42)`), decimal hours (`1.5`), and `"HH:MM"` strings
- [x] Each successfully parsed row creates a `PilotLogbookEntry` with `flight_id = NULL` and `source = "import"`; the import source is stored so imported entries are distinguishable from manually-entered ones in the logbook view
- [x] On completion show a summary: rows imported, subtotal rows skipped, other rows skipped (with reason per row), opening-balance entry if applicable; save the mapping as a `LogbookImportMapping` for future re-use
- [x] Batch rollback: a "Delete this import" action on the import history page removes all entries belonging to that `LogbookImportBatch` in one operation

**Import history:**
- [x] Import history page (accessible from the pilot profile): lists past batches with date, row count, subtotals skipped, and source filename; allows rollback and re-download of the mapping as JSON

**Tests:**
- [x] Header auto-detection: header found at row 1 (simple CSV), row 2 (EASA Excel with group-header row), and not found (error)
- [x] Duplicate column disambiguation: two `TIME` columns → `time` and `time_2`, correctly mapped to departure and arrival
- [x] Subtotal row detection: rows with `timedelta` date cells are excluded and counted as subtotals, not errors
- [x] Built-in alias matching: `FROM`→`departure_place`, `SE`→`single_pilot_se`, `PIC`→`function_pic`, etc.
- [x] Mapping fingerprint: second upload with identical headers pre-fills from saved mapping (exact match)
- [x] Fuzzy fallback: upload with ≥ 60 % column overlap but different fingerprint → closest saved mapping proposed with "please review" notice; upload with < 60 % overlap → alias-only auto-mapping, no prior mapping proposed
- [x] Opening-balance entry: created one day before earliest row; totals match user input
- [x] Duration parsing: `datetime.time(0, 42)` → 0.7 h decimal; `"1:24"` → 1.4 h; `"1.5"` → 1.5 h
- [x] Skipped-row reporting: rows with unparseable dates counted separately from subtotal rows
- [x] Rollback: all entries in a batch deleted; none remain after rollback

---

## Phase 29 — Instance Super Admin & Multi-Tenant Provisioning ✅

Goal: introduce a lightweight "instance admin" concept that lets a single OpenHangar installation serve multiple independent tenants, while keeping the solo-user experience completely unchanged.

**Design principle:** the instance admin is infrastructure, not a resident. They provision tenants and handle emergencies, but do not need a seat inside every tenant. When only one tenant exists and the current user is both instance admin and tenant owner, the UI collapses into the familiar single-settings experience — no new concepts surface.

**Model changes:**
- [x] Add `is_instance_admin` boolean column (default `False`) to `User`; set to `True` for the very first user created (in the setup wizard)
- [x] Alembic migration for the new column - including handling the case where an existing admin needs to be upgrade to instance admin with this update
- [x] `require_instance_admin` decorator in `utils.py` (mirrors `login_required`; returns 403 if `current_user.is_instance_admin` is false)

**Setup wizard:**
- [x] After creating the first user, set `is_instance_admin = True` on that user — no UI change needed, happens silently

**Instance admin UI (visible only when `is_instance_admin`):**
- [x] "Tenants" section in the config/settings page, shown only when the logged-in user is instance admin; hidden for all other users regardless of their per-tenant role
- [x] Tenant list: name, creation date, number of users, number of aircraft, active/inactive status
- [x] Create tenant form: tenant name, operating model (reuse existing `TenantProfile` fields), admin email — creates the `Tenant`, its `TenantProfile`, and sends an invitation to the specified email as OWNER of that tenant
- [x] Deactivate / reactivate tenant: sets an `is_active` flag on `Tenant`; deactivated tenants cannot log in (enforced in `login_required` / session setup)
- [x] "Reset tenant admin password" action: instance admin can trigger a one-time password reset for any OWNER-role user of any tenant — generates a short-lived signed token (same mechanism as the existing invite flow) and displays it on screen (no email required, so the instance admin can relay it out-of-band); the token forces a password change on first use

**Solo-user guard:**
- [x] When `Tenant.query.count() == 1` and the logged-in user is that tenant's OWNER, the Tenants section is omitted from the settings page — no multi-tenant UI surfaces for a single-tenant install - do allow for a single user environment to upgrade to multi-tenant.

**Tests:**
- [x] `require_instance_admin` blocks non-instance-admin users with 403
- [x] Setup wizard sets `is_instance_admin` on the first user; subsequent users are not marked
- [x] Create tenant: new `Tenant` + `TenantProfile` + `UserInvitation` (OWNER role) are created; response redirects to tenant list
- [x] Deactivate tenant: subsequent login attempt by a user of that tenant is rejected
- [x] Password reset token: valid token forces password-change form; expired/used token is rejected; only instance admin can generate one
- [x] Solo-guard: Tenants section absent from settings when only one tenant exists

---

## Phase 30 — Airplane GPS Log Import ✅

Goal: allow a pilot or aircraft owner to upload a GPS track file (GPX from SkyDemon/ForeFlight or a Garmin GTN 750 CSV export), automatically derive flight segments from the track, create aircraft logbook entries, render a per-flight map, and optionally cross-populate the pilot logbook.

The reference files studied during design:
- **SkyDemon GPX**: standard GPX 1.1; `<trkseg>` with `<trkpt lat lon>`, `<ele>` (metres MSL), `<speed>` (m/s — *not* knots), `<time>` (UTC ISO-8601); 5-second sample interval; track `<name>` contains departure–arrival airport names e.g. `"EBNM NAMUR  Suarlée - EBAW ANTWERPEN  Deurne"`; speed is 0.0 during ground time
- **SkyDemon KML**: `gx:Track` format; timestamps in sub-millisecond UTC; coordinates in `lon lat alt` order (reversed from GPX); useful as a fallback but GPX is preferred
- **SkyDemon `.flightlog`**: proprietary binary format — not supported
- **Garmin GTN 750 CSV**: 3-row header — row 1 is `#airframe_info` metadata; row 2 is unit labels; row 3 is column names (`Lcl Date`, `Lcl Time`, `UTCOfst`, `Latitude`, `Longitude`, `AltMSL`, `GndSpd` in kt, `IAS`, `HDG`, `TRK`, `COM1`, `COM2`, `NAV1`, `NAV2`, `GPSfix`, plus 25+ other avionics channels); 1-second sample rate; early rows have blank lat/lon and `GPSfix = NoSoln` (GPS acquiring) — only rows with `GPSfix` of `3D` or `3DDiff` carry valid position; filename encodes departure ICAO: `log_YYMMDD_HHMMSS_ICAO.csv`

**Supported file formats:**
- [x] GPX 1.1 (SkyDemon, ForeFlight, most aviation apps) — primary format
- [x] Garmin GTN 750 / G1000 CSV — 3-row header, local time with UTC offset, `GndSpd` column in kt, only `3D`/`3DDiff` GPS-fix rows used
- [x] KML with `gx:Track` (SkyDemon secondary export) — parsed as fallback when GPX is unavailable
- [x] Format is auto-detected: `.gpx` → XML sniff for `<gpx`; `.csv` → sniff for `#airframe_info` on row 1; `.kml` → XML sniff for `<kml`; unsupported formats (e.g. `.flightlog`) rejected with a clear error
- [x] Upload form accepts multiple files simultaneously (`<input type="file" multiple>`); each file is parsed and classified independently, then all results are presented together in a single chronological review step

**Parsing specifics:**
- [x] GPX: extract `(lat, lon, elevation_m, speed_ms, time_utc)` per trackpoint; convert speed from m/s to kt (×1.944)
- [x] Garmin CSV: skip 3-header rows; combine `Lcl Date` + `Lcl Time` + `UTCOfst` into a UTC timestamp; use `Latitude` / `Longitude` / `AltMSL` / `GndSpd`; extract departure ICAO from filename if present; ignore all other columns (store a selection as raw metadata in the batch record for future use)
- [x] KML: parse `<when>` timestamps and `<gx:coord>` (lon lat alt); derive speed from consecutive point distance/time since no explicit speed field

**File classification (per file, before segment detection):**
- [x] After parsing, classify each file into one of three categories based on its speed profile:
  - `flight` — at least one continuous window of ≥ 30 s where ground speed exceeds 30 kt (clearly airborne)
  - `ground_movement` — ground speed never exceeds 30 kt for 30 s, but does exceed 5 kt at some point (taxiing, ground runs, fuel stop); this includes both "fuel-stop with engine off" files and "engine-start / PFD-boot before departure" files that have meaningful ground movement
  - `empty` — speed never exceeds 5 kt throughout the entire file (avionics started on a stationary aircraft, e.g. to export logs from a previous flight)
- [x] `empty` files are silently skipped; their filenames are noted in the import summary ("1 file skipped — no movement detected")
- [x] `ground_movement` files are merged with an adjacent `flight` file if the two files' time ranges are within 30 minutes of each other (i.e., the ground-movement file ends ≤ 30 min before a flight file starts, or starts ≤ 30 min after a flight file ends); when merged, the block-off/block-on of the combined entry extends to cover the ground-movement file's full time range
- [x] A `ground_movement` file with no adjacent flight within the 30-minute window is presented as a standalone entry with block-off/block-on from the file and 0 airborne time; labeled "Ground movement only" in the review UI; the user may keep it (creates a logbook entry with hobbs time but 0 flight time) or discard it

**Flight-segment detection:**
- [x] Merge all trackpoints into a chronological list; split into segments where ground speed stays below 30 kt for ≥ 5 minutes (GPX/KML sources have 5-second intervals; Garmin has 1-second intervals — apply the same logic)
- [x] For each segment: block-off = first trackpoint of segment; takeoff = first sample above 30 kt; landing = last sample above 30 kt; block-on = last trackpoint of segment — all four timestamps stored
- [x] Garmin-specific: only use rows with `GPSfix` in `{3D, 3DDiff}` for takeoff/landing detection; ignore `NoSoln` rows at start (GPS acquiring)
- [x] Present detected segments to the user for review before saving: show departure time, arrival time, raw duration, and the resolved ICAO codes; allow the user to edit ICAO codes and delete spurious segments (e.g. ground manoeuvring at taxi speed that is mis-detected as a flight); ground-movement-only entries are shown separately at the bottom of the review list

**ICAO airport resolution:**
- [x] Resolve the nearest ICAO airport to the first and last GPS fix of each segment using a bundled lightweight airport database (OurAirports `airports.csv`, filtered to ICAO-coded airports)
- [x] Accept match if the nearest airport is within 5 km; otherwise leave the field blank and prompt the user
- [x] GPX track name hint: parse `ICAO NAME — ICAO NAME` patterns from the SkyDemon track name as a secondary resolution signal

**Time rounding preference:**
- [x] Aircraft configuration page gains a **Logbook time precision** toggle: *1/10 h (6-minute increments, EASA standard)* vs. *1 minute* — default is 1/10 h
- [x] Flight duration = block-off to block-on; rounded up to the nearest applicable increment for the logbook entry; raw GPS duration stored separately
- [x] Example: 42 min raw → 0.7 h (1/10 h mode) or 42 min (minute mode); 39 min raw → 0.7 h (1/10 h, rounds up from 6.5 increments)

**Aircraft logbook entries:**
- [x] Each confirmed segment creates a flight entry linked to the aircraft: departure ICAO, block-off time, arrival ICAO, block-on time, duration (rounded), source = `"gps_import"`
- [x] `AircraftLogImportBatch` model: aircraft FK, filename, import timestamp, format detected, number of segments found/imported; rollback deletes all linked entries

**Pilot logbook cross-population:**
- [x] Checkbox on the import confirmation page: **"I was PIC for all flights in this file"** — creates a `PilotLogbookEntry` per segment with aircraft registration, type, departure/arrival ICAO, departure/arrival time, `function_pic` = duration; `single_pilot_se` or `single_pilot_me` set based on aircraft category; night/IFR/landing fields left blank for the pilot to complete
- [x] Created pilot entries belong to the same `AircraftLogImportBatch` and roll back together

**Per-flight map:**
- [x] Each segment's full track is stored as a GeoJSON `LineString` in the batch record (coordinates downsampled to ≤ 500 points if needed to limit storage)
- [x] Altitude and ground speed encoded as GeoJSON `properties` arrays for colour rendering
- [x] Aircraft detail page and flight entry page render the track on a Leaflet map; colour gradient by altitude (or ground speed if altitude unavailable)

**Cumulative aircraft map (foundation):**
- [x] Aircraft detail page gains a **Flight tracks** tab showing all stored tracks overlaid as semi-transparent polylines — visual weight accumulates on frequently-flown routes; this is the foundation for the FlySto-style heatmap in a later phase

**Tests:**
- [x] GPX parsing: speed conversion m/s→kt correct; trackpoints extracted with correct UTC times
- [x] Garmin CSV: 3-row header skipped; `NoSoln` rows excluded; UTC timestamp correctly assembled from `Lcl Date` + `Lcl Time` + `UTCOfst`; ICAO extracted from filename
- [x] KML parsing: `gx:coord` lon/lat order handled; speed derived from consecutive points
- [x] Multi-file upload: two files submitted together → both parsed; results merged into one chronological review list
- [x] File classification: file with speed always < 5 kt → `empty`, skipped; file with speed peaking at 20 kt → `ground_movement`; file with ≥ 30 s above 30 kt → `flight`
- [x] Ground-movement merging: `ground_movement` file ending 10 min before a `flight` file → merged into one entry with extended block-off; `ground_movement` file 2 hours before a flight → not merged, shown as standalone
- [x] Standalone ground entry: `ground_movement` file with no adjacent flight → review entry labeled "Ground movement only", creates 0-airborne-time logbook entry when confirmed
- [x] Flight-segment detection: single segment; two segments separated by ≥ 5 min ground stop
- [x] ICAO resolution: airport within 5 km matched; airport 10 km away returns no match
- [x] Time rounding: 42 min → 0.7 h (1/10 h mode); 42 min → 42 min (minute mode); 39 min → 0.7 h (rounds up)
- [x] PIC cross-population: pilot entries created with correct fields when checked; not created when unchecked
- [x] Rollback: all aircraft entries, pilot entries, and GeoJSON data deleted together
- [x] GeoJSON downsampling: track > 500 points is reduced; start and end points preserved

---

## Phase 31 — Unified Flight Entry: Other Aircraft & GPS Autofill ✅

Goal: allow pilots to log flights in aircraft not maintained in this OpenHangar instance, and make GPS data an optional autofill step on the manual flight form, so both entry paths (manual and GPS import) lead to the same set of outcomes (aircraft logbook entry, pilot logbook entry, GPS track) without requiring all three.

**"Other aircraft" for manual flight logging:**
- [x] The manual "Log a flight" form gains a toggle at the top: **"Aircraft not in this OpenHangar instance"**. When selected: free-text make / model / registration fields (stored in the existing `aircraft_type` and `aircraft_registration` columns on `PilotLogbookEntry`); no `FlightEntry` is created; only a `PilotLogbookEntry` is written
- [x] Pilot role is mandatory in this mode; the "Not flying" option is removed (nothing to record if you were not the pilot on an off-system aircraft)
- [x] When the toggle is off, form behaviour is unchanged from the current manual flow

**"Other aircraft" for GPS import:**
- [x] The GPS import upload page gains the same toggle
- [x] When selected: no `FlightEntry` is created; a `PilotLogbookEntry` is created from each confirmed segment's GPS data; GPS tracks are discarded after import (no aircraft to attach them to)
- [x] Pilot role (PIC / Dual+student) is mandatory; "Not flying" option is removed
- [x] Rollback deletes the `PilotLogbookEntry` records linked to the batch; no `FlightEntry` exists to unlink

**GPS autofill hint on manual flight form:**
- [x] When an aircraft is already selected on the "Log a flight" form, display a small callout: *"Have a GPS file for this flight? Upload it first — it will autofill times and route."* — links to the GPS import upload page with the aircraft pre-selected, skipping the aircraft-selector step
- [x] No new backend logic required; this is a UX cross-promotion only

**Tests:**
- [x] "Other aircraft" manual: submission with free-text aircraft fields → `PilotLogbookEntry` created, no `FlightEntry`; `aircraft_type` and `aircraft_registration` populated correctly
- [x] "Other aircraft" mandatory role: "Not flying" option absent in rendered form; submission without role selection rejected
- [x] "Other aircraft" GPS import: `PilotLogbookEntry` created from GPS data; no `FlightEntry`; GPS track not persisted to DB
- [x] "Other aircraft" GPS rollback: batch deletion removes the pilot logbook entries created by the batch
- [x] Normal aircraft selected: manual and GPS import behaviour unchanged from Phase 30
- [x] GPS autofill link: callout rendered when aircraft is selected; link href includes correct `aircraft_id` parameter

---

## Phase 31b — Unified Flight Entry: Full Integration ✅

Goal: replace the separate aircraft-logbook and pilot-logbook creation flows with a single "Log a flight" form that writes both records in one operation, stores GPS tracks as a standalone model linkable from either log type, and makes duplicate detection a first-class concern throughout.

**Schema — GpsTrack model:**
- [x] New `GpsTrack` model: `id`, `source_filename`, `block_off_utc`, `block_on_utc`, `departure_icao`, `arrival_icao`, `geojson` (JSON), `created_at`; no FK to aircraft or pilot — it is a free-standing record
- [x] Add `gps_track_id` (nullable FK → `GpsTrack`) to `FlightEntry`; drop the existing `track_geojson` column (data migrated into new table)
- [x] Add `gps_track_id` (nullable FK → `GpsTrack`) to `PilotLogbookEntry`
- [x] Migration: for each `FlightEntry` where `track_geojson IS NOT NULL`, insert a `GpsTrack` row and back-fill the new FK; then drop the column

**Unified flight form (`/flights/new`):**
- [x] New blueprint-level route (not under `/aircraft/` or `/pilot/`) accepting an optional `aircraft_id` query parameter for pre-selection
- [x] Aircraft section: dropdown of managed aircraft owned by the current tenant + "Other aircraft" option that reveals free-text make/model and registration fields
- [x] Optional GPS file upload (single file, single flight): on upload, parse the file server-side and return auto-filled date, departure/arrival ICAOs, and block times; user can override any auto-filled value
- [x] Hobbs/tach counter fields: shown only when a managed aircraft is selected
- [x] Pilot role selector: PIC / Dual / None — controls whether a `PilotLogbookEntry` is created; "None" is allowed when the pilot is an observer or wants aircraft-only logging
- [x] Clear summary below the form showing what will be created: "Aircraft log entry" (only if managed aircraft) and/or "Pilot logbook entry" (only if PIC or Dual)
- [x] Duplicate detection before submit: check for overlapping `block_off_utc`/`block_on_utc` (if GPS provided) or same date + departure + arrival ICAO on the same aircraft/pilot. If a match is found: show a warning identifying the existing entry, offer "Attach GPS track only" (no new record created, no time fields changed — existing logged times remain authoritative; a small notice is shown that GPS times differ if they do) or "Create anyway"
- [x] When GPS is attached to an existing entry: store the `GpsTrack` and set the FK; do not overwrite `departure_time`, `arrival_time`, `flight_time`, or hobbs/tach fields; show inline notice if GPS timestamps differ from the logged values

**Edit form:**
- [x] `/flights/<flight_entry_id>/edit`: same template as `/flights/new`, pre-populated from the `FlightEntry` and its linked `PilotLogbookEntry` (if any)
- [x] `/pilot/logbook/<entry_id>/edit`: same template, pre-populated from the `PilotLogbookEntry` and its linked `FlightEntry` (if any, via `flight_id` FK)
- [x] When both records are linked: saving updates both atomically; changing times on one side changes both
- [x] Removing pilot role on edit: offer "detach pilot log entry" (keeps `PilotLogbookEntry` as standalone, clears `flight_id` FK) or "delete pilot log entry"

**GPS track for pilot-only log:**
- [x] In "other aircraft" mode a GPS file can still be uploaded; `GpsTrack` is stored and linked via `PilotLogbookEntry.gps_track_id`; no `FlightEntry` is created

**Navigation entry points:**
- [x] Navbar: "Log a flight" button linking to `/flights/new`, visible to all roles that can log flights (Owner, Admin, User/Renter)
- [x] Aircraft detail page "Add flight" link → `/flights/new?aircraft_id=<id>`
- [x] Pilot logbook "Add entry" link → `/flights/new` (no aircraft pre-selection)

**Tests:**
- [x] Unified form — managed aircraft + PIC: `FlightEntry` and `PilotLogbookEntry` both created and linked
- [x] Unified form — managed aircraft + None role: `FlightEntry` created, no `PilotLogbookEntry`
- [x] Unified form — other aircraft + PIC: `PilotLogbookEntry` created with free-text fields, no `FlightEntry`
- [x] Unified form — GPS auto-fill: parsed values pre-populate form fields; user override persisted correctly
- [x] Unified form — duplicate detected (GPS): warning shown; "attach only" sets `gps_track_id` without changing time fields; existing logged times preserved
- [x] Unified form — duplicate detected (manual): warning shown on date+ICAO match; "create anyway" proceeds
- [x] GPS track pilot-only: `GpsTrack` linked to `PilotLogbookEntry.gps_track_id`; no `FlightEntry` created
- [x] Edit — linked pair: changing flight time updates both `FlightEntry` and `PilotLogbookEntry`
- [x] Edit — remove pilot role → detach: `PilotLogbookEntry.flight_id` cleared; entry still exists standalone
- [x] Edit — remove pilot role → delete: `PilotLogbookEntry` deleted; `FlightEntry` unchanged
- [x] Migration: existing `FlightEntry.track_geojson` rows migrated to `GpsTrack`; FK back-filled; column dropped
- [x] Navbar: "Log a flight" link rendered for Owner/Admin/Renter; absent for Viewer

---

## Phase 32 — Test Hardening: Template Coverage & Browser Tests ✅

Goal: eliminate the class of regression where a model refactor silently breaks a
Jinja2 template (or client-side JavaScript) that is never exercised by existing
tests because the test fixture doesn't create the right data combination.

Two distinct problems are solved here:

1. **Stale template attribute errors** — a Python attribute is renamed or moved to
   a related model; Jinja2 catches the `AttributeError` and returns `Undefined`;
   the template renders fine until a real user hits a code path that uses that
   attribute with actual data.
2. **Untested JavaScript behaviour** — AJAX flows, dynamic form changes, and
   client-side validation are invisible to the Flask test client; regressions
   in these are only found manually or in production.

---

### Part A — Jinja2 `StrictUndefined` in test and development

**What:** Switch the Jinja2 environment to `StrictUndefined` when
`FLASK_ENV` is `development` or `testing`. With `StrictUndefined`, any
access to an attribute that does not exist on the Python object
(e.g. `entry.track_geojson` after it was moved to `entry.gps_track.geojson`)
raises `UndefinedError` immediately rather than silently returning a falsy
value. Bugs surface at template render time, not at `| tojson` or in the
browser.

This is different from `None`-valued attributes: `entry.gps_track` returning
`None` (a valid SQLAlchemy relationship) is still safe — only truly missing
attributes raise. No template changes are needed; only `init.py` and
`conftest.py` are touched.

- [x] In `create_app()`, after the existing `app.jinja_env.globals.update(…)`
  call, add:
  ```python
  if app.config.get("TESTING") or os.environ.get("FLASK_ENV") == "development":
      from jinja2 import StrictUndefined
      app.jinja_env.undefined = StrictUndefined
  ```
- [x] Run the full test suite; fix any templates where valid optional attributes
  are accessed without a prior `{% if %}` guard. (All 1754 tests passed clean — no template fixes needed.)

---

### Part B — "Full-data" template smoke tests

**What:** For every route that renders a non-trivial template (one with
`{% if obj.optional_relationship %}` blocks), add a test variant that creates
maximally-populated fixtures — all optional FK relationships set, all optional
fields filled. This ensures every `{% if %}` branch in every template is entered
at least once during a test run.

Concrete gaps identified so far (and now closed):

- `/aircraft/<id>/tracks` — needs a `FlightEntry` with a linked `GpsTrack` ✅ (fixed in Phase 31b)
- `/aircraft/<id>/flights/<id>` — needs a `FlightEntry` with a linked `GpsTrack` ✅ (fixed in Phase 31b)

Gaps closed in `tests/test_full_data_templates.py`:

- [x] `aircraft/flight_detail.html` — `entry.source == 'gps_import' and entry.gps_import_batch`
- [x] `flights/flight_form.html` — `flight.gps_track` (edit form, GPS track already linked + `source_filename`)
- [x] `pilots/entry_detail.html` — `entry.flight_id and entry.flight`
- [x] `pilots/entry_form.html` — `entry.gps_track` (edit form, GPS track already linked + `source_filename`)
- [x] `aircraft/detail.html` — `last_wb_entry.label` (W&B entry with a label)
- [x] `flights/logbook_component.html` — `component.removed_at`

---

### Part C — Playwright end-to-end tests for JavaScript behaviour

**What:** Playwright is already a dev dependency (used by
`scripts/take_screenshots.py`). Extend it to cover the interactive
client-side features that the Flask test client cannot reach.

Priority targets (highest regression risk, hardest to test otherwise):

- [x] **AJAX GPS parse**: upload a valid GPX file → fields auto-fill without
  page reload; date, route, and times match the GPX content.
- [x] **GPS parse: form-state preservation**: manually fill crew name and notes,
  then upload a GPS file; verify those fields are still populated after parse.
- [x] **"Other aircraft" dropdown**: select the "Aircraft not in this instance"
  option → warning banner appears; select a real aircraft → warning disappears.
- [x] **Duplicate-flight banner**: submit a flight that matches an existing entry
  → duplicate warning is shown with the correct date and route.
- [x] **Logbook toggle**: on the flight creation form, toggle "not logging in my
  pilot logbook" → pilot-logbook-only fields hide/show correctly.

Infrastructure:

- [x] Add a `tests/e2e/` directory with a shared `conftest.py` that starts the
  Flask dev server (or uses the existing Docker container) and provides a
  `page` fixture via `playwright.sync_api`.
- [x] Gate E2E tests behind a `--e2e` pytest flag so they do not run in the
  standard coverage suite (they are slow and require a live server).
- [x] Document in `docs/development.md` how to run E2E tests locally and in CI.

---

### Tests

- [x] Part A: `StrictUndefined` enabled; full suite still passes at 100 %
- [x] Part B: every template with optional-relationship conditionals has a
  "full-data" smoke test; documented in a comment or fixture name
- [x] Part C: Playwright suite covers the five JS flows listed above; runs
  green in CI behind the `--e2e` flag

---

## Phase 33 — Aircraft Airworthiness Requirements Tracker ✅

**Goal:** enable pilots and operators to track all airworthiness-related documents
applicable to their aircraft: Airworthiness Directives (ADs), Service Bulletins (SBs),
Safety Information Bulletins (SIBs), the Airworthiness Review Certificate (ARC), and
installed STCs. Each aircraft component is mapped to one or more EASA Safety
Publications Tool nodes; a periodic sync job queries those nodes for new documents and
surfaces them for review. Users mark each document as complied, not applicable,
deferred, or open a question for their maintenance org.

Initial scope: EASA Safety Publications Tool (ADs, SIBs) and manual entry (SBs, ARC).
FAA and manufacturer-portal sync deferred to backlog until there is demand.

To be documented in [`docs/airworthiness_requirements.md`](airworthiness_requirements.md).

---

### Document Types

| Type | Description | Source | Mandatory? |
|---|---|---|---|
| `ad` | Airworthiness Directive | EASA sync | Yes |
| `mandatory_sb` | Service Bulletin mandated by an AD | AD reference → manual entry | Yes (via AD) |
| `sb` | Recommended Service Bulletin | Manual entry or future manufacturer sync | No |
| `sib` | Safety Information Bulletin | EASA sync (same portal, different doc type) | No |
| `arc` | Airworthiness Review Certificate | Manual entry | Yes — annual renewal (Part-ML) |
| `stc_installed` | Installed Supplemental Type Certificate | Manual entry | Record only |

The compliance status workflow (`pending_review` → `complied` / `not_applicable` /
`deferred` / `question`) applies to all types except `stc_installed`, which is a
record of what is physically installed and has no compliance action.

---

### Data Model

**`AircraftComponent`** — one row per physical component of an aircraft:
- `aircraft_id` → Aircraft
- `component_type` — `airframe` | `engine` | `propeller` | `avionics` | `other`
- `description` — free text (e.g. "TAE 125-02-114", "MTV-6-A/190-69")

**`EASASourceNode`** — one or more rows per `AircraftComponent`; each row is a
three-level path in the EASA AD taxonomy tree:
- `component_id` → AircraftComponent
- `tc_holder_node_id` / `tc_holder_name` — level-0 node (TC holder)
- `type_node_id` / `type_name` — level-1 node (type family)
- `model_node_id` / `model_name` — level-2 leaf node (specific model)
- `last_synced_at`

Multiple nodes per component handle cases where a component carries ADs under more
than one entry in the EASA tree (e.g. airframe base TC plus an installed STC that
itself has ADs).

**`AirworthinessDocument`** — one row per document per source node (or per manual
entry):
- `doc_type` — see table above
- `reference` — document identifier, e.g. `AD 2023-0048`, `SB TAE 125-0185`, `EASA.A.S.01380`
- `title` — short description (manually entered or scraped)
- `source_node_id` → EASASourceNode (NULL for manual entries)
- `doc_url` — link to EASA page or manufacturer document
- `expiry_date` — set for `arc` (date ARC expires); NULL for other types
- `first_seen_at`

**`AirworthinessDocumentStatus`** — tracks compliance state per document per aircraft:
- `aircraft_id` → Aircraft
- `document_id` → AirworthinessDocument
- `status` — `pending_review` | `complied` | `not_applicable` | `deferred` | `question`
- `notes` — free text (reason for N/A, question for maintenance org, work order ref, etc.)
- `compliance_date` — set when status = `complied`
- `next_review_date` — set when status = `deferred` or when `arc` approaches expiry

**`InstalledSTC`** — simple registry of STCs installed on an aircraft (no status
workflow; presence/absence is the record):
- `aircraft_id` → Aircraft
- `stc_number` — e.g. `EASA.A.S.01380`
- `title` — e.g. "TAE 125-02-114 diesel engine installation"
- `tc_holder` — e.g. "CEAPR"
- `installation_date`
- `notes`

---

### EASA API

The EASA Safety Publications Tool exposes two unauthenticated endpoints:

**Tree browser** — `POST https://ad.easa.europa.eu/json/` with body `node=<id>`
Returns JSON array of child nodes with `id`, `text`, `cls` (`type_noads` = no
documents at this level, `model` = leaf with documents), `leaf`. Use to enumerate
TC holders → types → models and discover node IDs when adding new components.

**Document search** — `POST https://ad.easa.europa.eu/search/advanced/result/` with
form fields `fi_basket[]=<model_node_id>` (plus `fi_action=advanced`,
`fi_tree=<path>`, remaining fields empty). Returns HTML listing applicable documents
(ADs and SIBs). The sync job parses document references from this response and diffs
against stored `AirworthinessDocument` records for that node.

---

### Known Node Mapping — Robin DR-401 155CDI (DR 400/140B)

The DR-401 155CDI is registered under the DR 400/140B type certificate (converted
from a gasoline DR 400/140B to diesel via STC EASA.A.S.01380). Three EASA source
nodes, six known ADs:

**Airframe — DR 400/140B**
- TC holder: CEAPR (node `10804`)
- Type: DR 400 (node `14407`)
- Model: DR 400/140B (node `22941`)
- ADs: 2014-0002, 2018-0017, 2018-0018, 2023-0048

**Engine — TAE 125-02-114** (Continental Aerospace Technologies GmbH, formerly Thielert)
- TC holder: Continental GmbH (node `10806`)
- Type: TAE125 (node `14343`)
- Model: TAE 125-02-114 (node `22251`)
- ADs: 2012-0116

**Propeller — MTV-6-A**
- TC holder: MT-Propeller Entwicklungen GmbH (node `10856`)
- Type: MTV-6 (node `24711`)
- Model: MTV-6-A (node `24755`)
- ADs: 2006-0345R

**Installed STCs** (seed into `InstalledSTC`):
- `EASA.A.S.01380` — TAE 125-02-114 diesel engine installation (CEAPR)

**ARC** — annual renewal under EASA Part-ML; seed one `AirworthinessDocument`
(doc_type `arc`) with the current expiry date and a `pending_review` status.

**Continental SBs** — Continental Aerospace Technologies publishes engine service
bulletins at `https://continental.aero/service-bulletins/` (searchable by engine
model, freely downloadable, no login). These are not yet synced automatically; enter
applicable SBs manually for now. Future backlog: add a sync source for this portal.

---

### Sync Job

**`EASASyncJob`** background task:
- [x] Runs daily (configurable via `OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR`); also triggerable manually per aircraft
- [x] For each `EASASourceNode`:
  - [x] POST to EASA document search endpoint with the node's `model_node_id`
  - [x] Parse document references and types from HTML response
  - [x] For each reference not yet in `AirworthinessDocument` for this node: insert
        new record and create `AirworthinessDocumentStatus` (`pending_review`) for
        every aircraft that has this node via its component
  - [x] Update `last_synced_at`
- [x] Error handling: log HTTP errors; if a node has not synced successfully in 72
      hours, log a `[AIRWORTHINESS]` warning (email alert deferred to Phase 34)
- [x] Exponential backoff on consecutive failures; respect EASA server rate limits
      (courtesy 2 s delay between requests; max once per 24 h per node; backoff = min(2^errors, 7) days)

---

### User-Facing Features ✅

**Aircraft airworthiness panel** (on aircraft detail page):
- [x] Summary counts by status: pending review, complied, not applicable, deferred, question
- [x] Filterable list: filter by status via buttons in the Documents card header
- [x] Status update form: change status, add notes, set compliance date, deferral
      date, or ARC expiry date
- [x] Visual urgency: ARC expiry within 60 days highlighted in amber; expired dates
      in red; deferred items past `next_review_date` flagged in red
- [x] "Last synced" timestamp per component node
- [x] Installed STCs panel: read-only list of `InstalledSTC` records for the aircraft

**Manual entry:**
- [x] "Add document manually" form — covers SBs, ARC renewals, and any directive not
      yet in the EASA portal; `source_node_id` = NULL
- [x] Manual entries participate in the same status workflow as synced documents

**Periodic email notifications:** deferred to Phase 34 — per-aircraft opt-in to a weekly
digest (new `pending_review` documents; `deferred` items near `next_review_date`; ARC
expiring within 60 days; `question` items older than 30 days).

---

### Initial Seeding

- [x] Insert `AircraftComponent` + `EASASourceNode` rows for the DR-401 155CDI
      (three components, three nodes, using the node IDs above)
- [x] Seed the 6 known ADs directly in dev seed (with mixed statuses)
- [x] Insert `InstalledSTC` record for EASA.A.S.01380
- [x] Insert `AirworthinessDocument` (doc_type `arc`) with current expiry date
- [x] Dev seed: one aircraft with mixed statuses across all document types (pending,
      complied, question) to exercise the full dashboard

---

### Tests ✅

- [x] **Sync job:** mock EASA document search response, verify new documents inserted
      and `pending_review` statuses created; verify no duplicates on re-sync; verify
      SIBs and ADs both parsed and stored with correct `doc_type`
- [x] **AirworthinessDocumentStatus:** CRUD, all status transitions, date field
      persistence, unique constraint (one status row per aircraft × document)
- [x] **Dashboard:** summary counts accurate by status and type; manual entries appear
      alongside synced documents
- [x] **InstalledSTC:** create, list, delete; does not appear in status workflow
- [x] **Manual entry:** form submission, persists correctly, participates in status
      workflow
- [x] **Sync error handling:** HTTP failure increments error state, alert fires after
      72 h without successful sync; exponential backoff tested

---

### Notes & Dependencies

- **EASA API stability:** the POST endpoints and node ID scheme are undocumented and
  could change. Node IDs appear stable (numeric, assigned at creation). If the
  response format changes, the HTML parser in the sync job will need updating.
- **No deadline data in search response:** AD compliance deadlines are not available
  in structured form from the EASA search result; they require scraping the individual
  AD detail page or manual entry.
- **Rate limiting & ethics:** EASA data is public; add a 2 s courtesy delay between
  requests and poll no more than once per 24 h per node.
- **Backlog:**
  - FAA DRS support — if FAA-registered aircraft are added by users
  - AD detail scraping — extract compliance deadline from individual EASA AD pages
  - Continental SB sync — `continental.aero/service-bulletins/` is publicly accessible;
    add as a second source type alongside `EASASourceNode`
  - Robin Aircraft SBs — not publicly accessible; inbound email forwarding (separate
    backlog item) is the likely path
  - TBO and life-limited components — e.g. engine TBO (hours and cycles for TAE 125),
    propeller TBO, component replacement intervals; different data shape from
    documents (running counter vs. limit); warrants a dedicated sub-phase
  - Additional data sources contributed by users (community feed registry)

---

## Phase 34 — Email Notifications ✅

Goal: proactively alert every user — owner, pilot, or maintenance — about events and
deadlines that matter to their role, with per-user control over which notifications
they receive.

### Data model

**System defaults** are coded constants (not DB rows) — one `(enabled, threshold_days)`
pair per `NotificationType`.  They represent the out-of-the-box behaviour on a fresh
install and are the baseline every new user starts from.

**Lookup order (highest wins):** user preference → tenant default → system default.

- [x] `NotificationType` enum — one value per notification listed below; stored as a
      string column so new types can be added without a migration.
- [x] `NotificationPreference` model — `user_id` (FK), `notification_type`, `enabled`
      (bool), `threshold_days` (int, nullable).  A row exists only when the user has
      explicitly changed something; missing row = fall through to the next level.
      Any user can write their own rows for types applicable to their role.
- [x] `TenantNotificationDefault` model — `tenant_id` (FK), `notification_type`,
      `enabled` (bool), `threshold_days` (int, nullable).  Admin-only; lets the fleet
      manager shift the baseline for all users on that tenant (e.g. set
      `MAINTENANCE_DUE_SOON` threshold to 14 days instead of the system default of 30).
      Only types applicable to the admin role are configurable here.

### First-run welcome email

When email is first functional on an instance, send a one-time welcome email to
the instance owner (the OWNER-role user of the first tenant) to confirm that
outbound mail is working and provide a brief orientation.

- [x] `SystemFlag` model (or reuse an existing key-value settings table if one
      exists) — stores a boolean `welcome_email_sent` flag persisted in the DB.
- [x] On application startup (in `create_app()`), after the scheduler is wired
      up: if `SMTP_HOST` is set **and** `welcome_email_sent` is not set → attempt
      to send the welcome email; on success set the flag.  On SMTP failure, log a
      warning and leave the flag unset so the next restart retries.
- [x] Welcome email content: greets the admin by name, confirms email delivery is
      working, links to the configuration page and documentation.  Sent from the
      global `SMTP_FROM_NAME` / `SMTP_FROM_ADDRESS` (no tenant branding applied —
      this fires before any tenant customisation is expected to be in place).
- [x] Demo mode and `FLASK_ENV != production` suppress the send (same guard as
      other email).
- [x] Test: flag absent + SMTP configured → email sent and flag set; flag already
      set → no send; SMTP failure → flag stays unset, warning logged.

### Per-tenant email branding

SMTP transport is shared (operator env vars — see Phase 14 multi-tenant note).
Branding is applied at the application layer, per tenant:

- [x] Add `email_subject_prefix` (nullable `String(64)`) to `TenantProfile`.  If set,
      every outgoing email for that tenant has `[{prefix}] ` prepended to the subject
      line (e.g. `[Dev Hangar] Maintenance overdue: OO-PNH`).  If null, no prefix is
      added.  Configurable by the tenant admin on the config page.
- [x] Derive a **display sender name** from the existing `TenantProfile` name fields
      (`club_name` → `school_name` → `organisation_name` → tenant name, first non-null).
      Used as the `From` display name in the format `"{display_name} (via OpenHangar)"`,
      overriding the global `SMTP_FROM_NAME` env var for that tenant's emails.  No new
      column needed — derived at send time.  If none of the name fields are set, fall
      back to the global `SMTP_FROM_NAME`.
- [x] Both the prefix and the derived display name are applied inside
      `notification_service.py` before calling `email_service.send_email`, keeping
      `send_email` itself unaware of tenancy.

### Infrastructure

- [x] Background scheduler runs a daily digest pass at a fixed configurable time
      (env var `OPENHANGAR_NOTIFICATION_TIME` in HH:MM format, default `07:00` UTC) and an
      immediate-event pass triggered inline after state-changing route actions
      (snag created, reservation confirmed/cancelled, etc.).
- [x] Shared `services/notification_service.py` — `send_notification(user, type, context)`
      resolves the effective preference via the three-level lookup and delegates to
      `email_service.send_email`.  Guards checked in order before any email is sent:
      1. SMTP not configured → silent no-op.
      2. Demo mode → no-op (same guard as Phase 14).
      3. User preference (or resolved default) has this type disabled → no-op.
      4. **Content guard** — each `NotificationType` defines a `has_content(context) → bool`
         function; if it returns `False` the notification is silently dropped.  Prevents
         sending functionally empty emails (e.g. a weekly digest with no new items, a
         medical-expiry alert when no expiry date is recorded, a reservation reminder for
         a reservation that has since been cancelled, a due-soon alert when no triggers
         are actually within the threshold).  The content guard runs *after* preference
         resolution so the preference check still short-circuits before any DB query.
- [x] **Email health tracking** — `send_email` updates two `SystemFlag` values after
      every send attempt:
      - `email_last_success_at` (datetime) — set on any successful send.
      - `email_consecutive_failures` (int) — incremented on `EmailSendError`, reset to 0
        on success.
      A "degraded" state is defined as: `email_last_success_at` is set (so we know it
      worked before) **and** `email_consecutive_failures ≥ 1`.  Every failure is also
      logged at WARNING level with the SMTP error detail for console debugging.
- [x] **Admin UI health indicator** — when the degraded state is detected, show:
      - A dismissible warning banner on the email section of `/config/` (always visible
        there when degraded, even after dismissal — re-appears on next page load).
      - A subtle badge or icon in the navigation bar visible to admins only, so the
        alert surfaces even if they do not visit the config page.
      The banner shows the failure count, the timestamp of the last success, and a link
      to the config page.  It disappears automatically once a send succeeds (flag reset).
- [x] Notification preference UI: `/config/notifications/` — **available to every user**,
      regardless of role; each user sees the union of all types they are eligible for
      (determined by their implied capabilities, not their raw role label — see below).
      Admins additionally see a "Fleet defaults" section for `TenantNotificationDefault`.

### Capability-based type visibility

Each user has a single role per tenant, but roles imply multiple capabilities via the
existing `is_owner`, `is_pilot`, and `is_maint` flags already computed in the template
context (see `init.py`):

| Role | is_owner | is_pilot | is_maint |
|---|---|---|---|
| Admin | ✓ | ✓ | ✓ |
| Owner | ✓ | ✓ | ✓ |
| Instructor | — | ✓ | ✓ |
| Pilot / Student | — | ✓ | — |
| Maintenance | — | — | ✓ |
| Viewer | — | — | — |

A user sees a notification type if they have **any** of the capabilities that type
requires.  An Admin or Owner therefore sees every category — they are simultaneously
owner, pilot, and maintenance.  No special-casing for "combined roles" is needed; the
capability flags handle it automatically.

### Notification types and defaults

System defaults apply when neither the user nor the tenant admin has set a preference.
"Default ON" means enabled out of the box; "Default OFF" means opt-in.
Every user can toggle and adjust thresholds for any type visible on their preferences page.

Required capabilities are listed as the minimum needed; any role that implies that
capability also qualifies.

**Maintenance (`is_owner` required):**
| Type | Trigger | Default |
|---|---|---|
| `MAINTENANCE_OVERDUE` | A hard-time or calendar trigger crosses its due threshold | ON |
| `MAINTENANCE_DUE_SOON` | Trigger within `threshold_days` (default 30) of due date, or ≥ 90 % of hours limit | ON |
| `MAINTENANCE_MONTHLY_SUMMARY` | Monthly digest of all triggers: overdue, due in 3 months, recently completed | OFF |

**Aircraft status (`is_owner` or `is_maint`):**
| Type | Trigger | Default | Requires |
|---|---|---|---|
| `INSURANCE_EXPIRY` | Aircraft insurance expires within `threshold_days` (default 30) | ON | is_owner |
| `GROUNDING_SNAG_OPENED` | A grounding snag is created on any aircraft | ON | is_owner or is_maint |
| `SNAG_REPORTED` | Any new snag reported (including non-grounding) | OFF | is_owner or is_maint |

**Airworthiness (`is_owner` required):**
| Type | Trigger | Default | Requires |
|---|---|---|---|
| `AIRWORTHINESS_DIGEST` | Weekly digest: new `pending_review` docs; `deferred` items near review date; ARC expiring within 60 days; `question` items > 30 days old | OFF | is_owner |
| `ARC_EXPIRY` | ARC expiry date within `threshold_days` (default 60) | ON | is_owner |
| `EASA_SYNC_FAILURE` | EASA source node has not synced successfully in 72 h | ON | Admin role only |

**Reservations (all authenticated users):**
| Type | Trigger | Default | Requires |
|---|---|---|---|
| `RESERVATION_CONFIRMED` | A reservation is confirmed (sent to the requester) | ON | any |
| `RESERVATION_CANCELLED` | A reservation is cancelled (sent to the requester) | ON | any |
| `RESERVATION_REMINDER` | Reminder `threshold_days` before the reservation (default 1 day) | OFF | any |
| `RESERVATION_REQUEST` | A new reservation request needs review | ON | is_owner |

**Pilot-personal (`is_pilot` required; each user sees only their own data):**
| Type | Trigger | Default |
|---|---|---|
| `PILOT_MEDICAL_EXPIRY` | Medical certificate expires within `threshold_days` (default 60) | ON |
| `PILOT_SEP_EXPIRY` | SEP rating expires within `threshold_days` (default 60) | ON |
| `PILOT_CURRENCY_EXPIRY` | 90-day passenger-carrying currency falls below 3 landings (Phase 18 check) | ON |
| `PILOT_LOGBOOK_WEEKLY` | Weekly personal flight summary (hours, sectors, aircraft flown) | OFF |

### Notification preference UI

- [x] `/config/notifications/` — accessible to every logged-in user; grouped by category;
      toggle (on/off) per type; editable `threshold_days` (or `threshold_pct` for
      `MAINTENANCE_DUE_SOON`) field for expiry/due-soon types.
- [x] Capability-filtered view: each category is shown only if the user has the required
      capability; a user with `is_owner=True` and `is_pilot=True` (e.g. Admin or Owner)
      sees all categories at once — no special-casing needed.
- [x] "Fleet defaults" section (admin only): tenant-level overrides of system defaults,
      applied to all users on the tenant who have not set a personal preference.

### Dev seed & tests

- [x] Extend dev seed: system defaults satisfy this out of the box; no explicit seed
      rows needed since missing rows fall back to system defaults automatically.
- [x] Unit tests: `email_service` health tracking — success resets failure count; failure
      increments it and logs at WARNING; degraded state detected correctly; success after
      degraded clears the banner condition.
- [x] Unit tests: `notification_service` correctly reads preferences and falls back to
      tenant defaults; disabled types produce no email; SMTP not configured → silent no-op;
      content guard returns `False` for each empty-content scenario (empty digest, missing
      expiry date, cancelled reservation) and suppresses the send.
- [x] Per-type integration tests: each trigger condition fires the correct notification to
      the correct recipients only; threshold_days override respected.
- Airworthiness digest tests: deferred to backlog alongside the feature itself
  (`AIRWORTHINESS_DIGEST` notification type not yet implemented).

---

## Phase 35 — Progressive Web App (PWA) ✅

Goal: make OpenHangar installable as a standalone app on mobile devices and functional when connectivity is unreliable, covering the main ramp use-cases (quick flight entry, Hobbs photo, offline queuing) without a native app codebase.

- [x] **PWA manifest** (`/manifest.json`) — app name, short name, theme and background colours, `display: standalone`; `<link rel="manifest">` in the base template
- [x] **App icons** — maskable icon set (SVG source) from the OpenHangar logo; `purpose: maskable` for Android adaptive icons; `<link rel="apple-touch-icon">` for iOS
- [x] **Service worker** — cache-first strategy for static assets (CSS, JS, fonts); network-first for navigation routes; static offline fallback page (`/static/pwa/offline.html`) served from cache when network is unavailable; SW served from `/sw.js` with `Service-Worker-Allowed: /` header; CSP `worker-src` updated to include `'self'`
- [x] **"Add to home screen" prompt** — listen for `beforeinstallprompt`; surface a non-intrusive install bar below the demo banner the first time the user visits on mobile; dismissal persisted in `localStorage`
- [x] **Camera capture** — `capture="environment"` on all three photo inputs (flight counter, engine counter, fuel) in the flight-entry form; opens rear camera directly on mobile; ignored on desktop
- [x] **IndexedDB offline queue** — serialise a submitted flight entry (including File Blobs) into `openhangar-offline` IndexedDB store when offline; replay via `online` event and SW `sync` tag on reconnect; queue-badge in nav bar shows pending count
- [x] **Conflict detection on sync** — `/api/check-flight-duplicate` endpoint checks (aircraft + date + departure + arrival) before submitting a queued entry; conflict modal with Discard / Keep options
- [x] **Offline status indicator** — red "Offline" badge in the navbar brand when offline; orange queue-count badge when entries are waiting to sync
- [x] **Tests** — 31 unit tests in `tests/test_pwa.py` covering manifest, SW endpoint, duplicate API, CSP, assets, base template wiring, and camera-capture attribute; Playwright offline smoke test requires a running server (see e2e suite) — iOS 16+ background-sync fallback requires manual verification

---

## Phase 36 — Aircraft Operating Cost Dashboard ✅

Goal: give sole operators (and, later, co-owners) a clear view of the true
all-in hourly cost of operating an aircraft by splitting expenses into fixed
(pro-rated by time) and operating (usage-based) categories and dividing the
totals by hours actually flown. This phase also introduces the two-tier expense
categorisation that Phase 39 (Shared Ownership) builds on.

**Two-tier expense categorisation:**
- [x] Add `expense_category` field (enum: `fixed` / `operating`) to the expense/cost model
- [x] **Fixed expenses** (insurance, hangar, annual inspection, ARC renewal, taxes) — accrue regardless of hours flown; pro-rated to the selected period when their coverage span is longer than the period
- [x] **Operating expenses** (fuel, oil, routine maintenance tied to hours) — scale directly with usage
- [x] **Excluded from hourly rate**: per-flight pilot costs such as landing fees, handling charges, and navigation fees — these vary by destination, belong on the flight record, and are not included in the dashboard calculation (implemented via the existing `Expense.flight_entry_id` link rather than a new field)

**Per-aircraft cost summary card:**
- [x] Display on the aircraft detail page (or a dedicated "Costs" tab), for a configurable rolling period — implemented as a dedicated `/aircraft/<id>/costs` page, linked from the aircraft detail Expenses section
- [x] Default period: last rolling 12 months (today − 365 days), so the denominator is always a full year of hours — not a partial calendar year that would inflate the per-hour figure in early January
- [x] Table shows: fixed costs (amount + per-hour), fuel & oil (amount + per-hour), variable maintenance (amount + per-hour), total wet rate (amount + per-hour), hours flown in period — shown as fixed / operating / reserve / total wet rate stat cards, with a fuel & oil / variable maintenance sub-breakdown of the operating total in a separate row
- [x] Fixed costs pro-rated when the selected period is shorter than the expense's coverage period (e.g. an annual insurance premium paid in January contributes only half its value to a July–December view)
- [x] Engine overhaul reserve accrual (if configured) surfaced as a separate "reserve contribution" line, making the cost-per-hour inclusive of future scheduled overhaul — configured as `Aircraft.reserve_hourly_rate` via the aircraft edit form

**Relationship to Phase 39 (Shared Ownership):**
Phase 39 reuses `expense_category` introduced here. For co-owners: fixed costs
are split by share percentage; operating costs are charged to the pilot who
actually flew. The cost card gains a co-owner scope in Phase 39.

**Tests:**
- [x] `expense_category` enum: valid values accepted; invalid value rejected
- [x] Fixed cost pro-rating: annual premium → correct fraction applied for a mid-year period
- [x] Operating cost attribution: landing fees excluded from hourly rate calculation
- [x] Cost card totals: fixed per-hour + operating per-hour = total per-hour, to two decimal places
- [x] Rolling 12-month window: period boundaries computed correctly from today's date
- [x] Zero-hours edge case: no division-by-zero when no flights logged in the period

---

## Phase 37 — Rental Operations (Owner-Operator) ✅

Goal: close the rental loop for a sole operator who rents or lends their aircraft
to other pilots. Today the flow stops at a confirmed reservation with an *estimated*
cost (Phase 22); everything after — verifying the renter is qualified, dispatching
the aircraft, reconciling the hours actually flown, charging and settling — happens
outside the app. This phase makes rental an end-to-end flow:
authorize → reserve → check out → fly → check in → charge → settle.

> Relationship to Phases 38 and 39: co-owner billing (38) and club billing (39)
> need the same primitives — charges, immutable payments, running balances, and
> period statements. Whichever of the three phases is implemented first should
> establish a shared billing/statement service that the other two reuse.

Built on [`billing_service_design.md`](billing_service_design.md) (ledger
core shared with Phases 38/39).

**Renter authorization & qualification tracking:**
- [x] `RenterAuthorization` model — tenant FK, renter user FK, aircraft FK (nullable = whole fleet), authorized-by user FK, granted date, expiry date (nullable), checkout flight date, notes
- [x] Owner records which qualifications were verified and when (licence seen, medical valid until, checkout flight completed) — these are owner-entered facts, deliberately not automatic reads of the renter's private pilot profile; where the renter has uploaded licence/medical documents visible to admins (Phase 27), the form links to them for convenience
- [x] Signed rental agreement upload — stored as a `Document` linked to the authorization
- [x] Reservation guard: a renter creating a reservation without a valid (non-expired) authorization for that aircraft gets a warning; a per-tenant setting escalates the warning to a hard block
- [x] New notification type `RENTER_AUTHORIZATION_EXPIRY` (requires `is_owner`, default ON, `threshold_days` 30) — fires when an authorization or a verified medical/licence date approaches expiry

**Rental rates & terms (extends `AircraftBookingSettings`):**
- [x] `rate_basis` — which counter is billed: engine time or flight time
- [x] `rate_type` — wet / dry; displayed on the booking form, reservation detail, and charges
- [x] `min_hours_per_day` — minimum billed hours per calendar day for multi-day bookings (standard rental practice); both the booking-time estimate and the final charge respect it
- [x] Fuel reimbursement (wet rates): renter records fuel bought away from base (existing per-flight fuel uplift fields plus a fuel expense with receipt); the amount is credited on the rental charge

**Dispatch — check-out / check-in:**
- [x] `DispatchRecord` model linked to a reservation — check-out captures: counter readings, fuel state, "walk-around done" and "open snags acknowledged" confirmations (the renter is shown the active snag list and must acknowledge it), timestamp and user
- [x] Check-in captures: counter readings on return, fuel state, and a prompt to report any new snags
- [x] Check-in counter delta is compared against the flight entries logged during the rental window; a discrepancy warning is shown when they differ
- [x] Check-out on a grounded aircraft is blocked (owner/admin can override with an explicit confirmation)

**Reservation ↔ flight reconciliation:**
- [x] Nullable `reservation_id` FK on `FlightEntry`; the flight form pre-links the flight when the logged-in pilot holds a confirmed reservation covering the flight time
- [x] Reservation detail shows estimated vs actual: booked hours, hours actually flown (from linked flights / dispatch counter delta), estimated cost vs final charge

**Rental charges & settlement:**
- [x] `RentalCharge` model — reservation FK, renter FK, billable hours, rate snapshot, fuel credits, manual adjustments, total; drafted automatically at check-in from the counter delta and rate settings; owner reviews and finalizes
- [x] Renter account: running balance per renter (finalized charges − payments); payments recorded manually and immutable once saved — corrections via counter-entry (same principle as Phase 39 reconciliation)
- [x] Renter statement export — CSV per renter per period: opening balance, itemised charges by flight, fuel credits, payments, closing balance; PDF with the same content if a PDF pipeline exists by then (Phase 45) (CSV only — no PDF pipeline exists yet)
- [x] Renter-facing view: a renter sees their own charges, payments, and balance; owners/admins see all renters

**Availability guards (benefit all operating models, not only rental):**
- [x] Creating or confirming a reservation on a grounded aircraft (unresolved grounding snag) shows a prominent warning; per-tenant setting escalates to a hard block
- [x] `MaintenanceDowntime` model — owner-entered planned unavailability window (e.g. shop appointment); behaves like a confirmed reservation in conflict detection and is rendered on the booking calendar in a distinct style
- [x] When a grounding snag is opened, notify pilots holding upcoming confirmed reservations on that aircraft — new notification type `RESERVATION_AIRCRAFT_GROUNDED` (any authenticated role, default ON)

**Dev seed:**
- [x] One renter user with an active authorization and one with an expired authorization
- [x] One completed rental cycle (reservation → dispatch → flight → finalized charge → payment), one charge awaiting review, one maintenance downtime window

**Tests:**
- [x] Authorization guard: renter without (or with expired) authorization is warned or blocked per tenant setting; owner books freely; valid authorization passes
- [x] Rate terms: `min_hours_per_day` applied to multi-day estimates and charges; `rate_basis` selects the correct counter delta; wet/dry label rendered
- [x] Dispatch: check-out stores counters and acknowledgements; grounded aircraft blocks check-out (override works); check-in delta vs logged flights raises discrepancy warning
- [x] Reconciliation: flight auto-links to the covering reservation; estimated vs actual figures correct
- [x] Charges: draft generated at check-in; fuel credit subtracts; finalized charge immutable; balance = finalized charges − payments; counter-entry corrections work
- [x] Statement export: per-flight rows and totals correct; opening + charges − credits − payments = closing
- [x] Downtime: overlapping reservation rejected exactly like a confirmed-reservation conflict; downtime visible on the calendar
- [x] Grounding notification: holders of upcoming confirmed reservations notified when a grounding snag opens; past and cancelled reservations not notified

---

## Phase 38 — Offline Logbook Editing

Goal: make the airframe logbook fully workable during extended offline
periods (e.g. a long flight with no connectivity): entries of any aircraft
whose logbook was browsed while online are cached automatically — no
explicit "take offline" step — and can be reviewed and corrected offline in
a dedicated workbench; changes upload automatically on reconnect, with
per-field conflict resolution when the server copy changed in the meantime.
Extended (38h–38l) to cover the pilot logbook too: the current user's own
entry linked to a flight is edited alongside it, and their standalone
entries (manual, FSTD, other-fleet flights) get their own offline
workbench — plus a generic guard so any other form fails cleanly offline
instead of silently.

Detailed design (data flow, endpoints, IndexedDB schema, conflict rules,
delivery order 38a–38l): [`phase38_offline_logbook_spec.md`](phase38_offline_logbook_spec.md).
The spec is authoritative; the checklist below tracks delivery. 38a–38g are
the deadline-critical airframe-logbook path; 38h–38l are the additive
pilot-logbook extension.

**Server (38a–38b):**
- [ ] Canonical serialization of the editable `FlightEntry` field set (single authority used by snapshot, conflict scan, and sync response); no schema change, no migration
- [ ] Snapshot API `GET /api/offline/aircraft/<id>/logbook` (tenant-scoped, JSON, sorted, includes read-only meta) + `GET /api/offline/csrf` (fresh token — stored tokens expire after 1 h) + `@api_login_required` returning JSON 401
- [ ] Validation extraction: `parse_flight_fields` / `apply_flight_fields` shared by the edit form and the sync API — zero behaviour change to the form (existing tests green)
- [ ] Sync API `POST /api/offline/flights/<id>/sync` — complete field set + base values; per-field conflict detection (base vs local vs current); all-or-nothing apply; duplicate guard with `force_duplicate`; translated validation errors

**Client data layer (38c):**
- [ ] IndexedDB v2: `snapshots` + `outbox` stores (one merged record per flight, base values preserved); shared `OhOffline` module (`offline_db.js`); `navigator.storage.persist()`
- [ ] Automatic background snapshot refresh when browsing a logbook online; frozen while that aircraft has pending edits
- [ ] Service worker: pattern-based page caching for logbook/workbench/changes routes + `OH_PRECACHE` message so one online visit to a logbook suffices; no reliance on the Background Sync API (Firefox/Brave targets)

**Workbench (38d):**
- [ ] `/aircraft/<id>/logbook/offline` — shell template + client-rendered editable table from the snapshot; works identically online (edits save immediately) and offline (edits queue)
- [ ] Live counter-continuity highlighting (start ≠ previous end, both counters), client-side validation mirroring the server rules, row status chips
- [ ] Clear offline indication: persistent "working offline" banner with pending count, on top of the existing navbar badges

**Offline-changes page (38e):**
- [ ] `/offline/changes` — lists every pending change (base → new, per field) incl. legacy Phase 35 queued new-flight entries; discard/revert actions; combined navbar queue badge links here
- [ ] Sync engine: serialized flush on page load / `online` event / after each edit; fresh CSRF per batch; live progress and end summary; session-expired state that preserves the queue and prompts re-login
- [ ] Per-field conflict resolution UI: base / my offline value / current online value, radio choice per conflicting field, resubmit with rebased values; duplicate "save anyway" flow

**Phase 35 queue fixes (38f):**
- [ ] Offline-queued *edit* form submits replay to the flight's edit URL (today: hardcoded `/flights/new` → duplicate entry)
- [ ] Replay fetches a fresh CSRF token (today: stale token → silent permanent 400)
- [ ] Legacy queue failures surfaced on `/offline/changes` instead of failing silently

**Docs & e2e (38g):**
- [ ] Playwright offline e2e: cache → offline edit → reconnect → auto-sync; conflict both-ways; changes page; 38f regression
- [ ] User-guide "Working offline" section (auto-caching, workbench, conflicts, browser notes: Firefox tab OK, Android install optional, log in before departing); screenshot manifest entries

**Pilot logbook server API (38h):**
- [ ] Canonical serialization of the editable `PilotLogbookEntry` field set; validation extraction (`parse_pilot_fields` / `apply_pilot_fields`) shared by the pilot forms and the sync APIs — zero behaviour change to existing forms
- [ ] Linked entries: optional `pilot` payload riding inside the 38a snapshot and 38b sync endpoints — user-entered subset only, derived fields recomputed from the flight server-side via a helper shared with the online form; one commit, one outbox record per flight; `pilot_missing` status when the link was removed server-side
- [ ] Standalone entries: `GET /api/offline/pilot/logbook` snapshot + `POST /api/offline/pilot/logbook/<id>/sync` — full per-field conflict detection, scoped to `flight_id IS NULL` rows only

**Pilot logbook client + UI (38i):**
- [ ] IndexedDB v3 (stores can only be added during a version bump): `pilot_snapshot` + `pilot_outbox` stores; `outbox` records gain an optional `pilot` sub-object
- [ ] "My logbook" section on aircraft-workbench rows for linked entries — user-entered subset editable, derived fields read-only; disabled placeholder when no linked entry exists
- [ ] `/pilot/logbook/offline` — standalone-entry workbench, FSTD-aware columns, no continuity checks; auto-snapshot + SW precache wired the same way as the aircraft workbench

**Offline-changes page extended (38j):**
- [ ] Third card family (standalone pilot-logbook edits) + inline pilot sub-diff on aircraft-logbook cards; independent per-field conflict resolution across flight and pilot fields; `pilot_missing` notice with "keep flight changes" action

**Cross-cutting offline-submit guard (38k):**
- [ ] Generic `submit`/`htmx:sendError` guard on any form without `data-oh-offline-aware`: friendly "you're offline" message instead of a raw failed request, on maintenance forms and any other non-offline-aware page; `flight_form.html` opts out (its offline submits are queued by the Phase 35 machinery)

**Docs & e2e for the pilot logbook (38l):**
- [ ] Playwright e2e: linked pilot-field edits + conflicts, standalone/FSTD entry edits, three-source changes page, 38k guard behaviour
- [ ] User-guide additions: pilot workbench, "My logbook" section, what's still not offline-capable; screenshot manifest entries

---

## Phase 39 — Shared Ownership

Goal: support an aircraft jointly owned by multiple individuals, each holding a defined share percentage, with two distinct cost apportionment models (fixed costs split by share; operating costs charged to the flying pilot), capital account tracking per co-owner, and downloadable owner statements.

> Reference: AOPA [*Guide to Aircraft Co-Ownership*](https://www.aopa.org/go-fly/aircraft-and-ownership/buying-an-aircraft/pilots-guide-to-co-ownership) — Articles 3, 9, 36–39 define the financial model that shapes this phase.

**Ownership model:**
- [ ] `AircraftOwner` model — aircraft FK, user FK, share percentage (Numeric, two decimal places), buy-in amount (initial capital contribution); validated so share percentages sum to exactly 100 % per aircraft; editable by Owner/Admin role
- [ ] Share percentage is financial only — each co-owner always has exactly one vote regardless of share size (no voting-weight column needed)
- [ ] Aircraft detail page shows the ownership breakdown: name, share percentage, and buy-in amount per co-owner

**Two-tier expense model:**

The AOPA guide distinguishes two fundamentally different cost types that must not be merged:

- **Fixed expenses** (insurance, hangar/tie-down, annual inspection, taxes) — recurring costs that do not depend on how much any individual flies; split among co-owners in proportion to their *share percentage*
- **Operating expenses** (flight hours × hourly rate, fuel, oil changes, wear-and-tear maintenance) — usage-based costs charged to the *pilot who actually flew*, not apportioned by share

> The `expense_category` field (`fixed` / `operating`) is introduced in
> Phase 36 (Aircraft Operating Cost Dashboard). Phase 39 extends the same
> categorisation to the co-owner apportionment logic below.

- [ ] Fixed-expense billing: for each fixed cost record, compute each co-owner's liability as `amount × (share_pct / 100)`
- [ ] Operating-expense billing: flight hours flown by a co-owner are charged at the per-aircraft hourly rate directly to that co-owner's balance; non-flying co-owners owe nothing for those hours

**Capital accounts & billing dashboard:**
- [ ] `CoOwnerCapitalAccount` — tracks each co-owner's running balance: starts at buy-in amount; reduced by their share of fixed costs and their own operating costs; increased by payments received
- [ ] Valuation date snapshots — ability to record a point-in-time net asset value per co-owner (e.g., end-of-year); stored as an immutable `CoOwnerValuationSnapshot` record so statement history is reproducible
- [ ] Co-owner billing dashboard — per aircraft, shows each co-owner's: hours flown (current period), fixed cost liability (share-apportioned), operating cost liability (usage-based), total payments received, and current capital account balance
- [ ] Overdue balance flag — highlight any co-owner balance that has been negative for more than 30 days (configurable per tenant); intended as a visual warning, not an automated enforcement action

**Reconciliation:**
- [ ] Manual reconciliation: record a payment against a co-owner's capital account (amount, date, free-text note, recorded-by user); adjusts the account balance immediately
- [ ] Payments are immutable once saved; corrections are made by recording a counter-entry

**Reserve / overhaul fund (stretch goal — may slip to Phase 40):**
- [ ] `CoOwnerReserveFund` — per-aircraft fund with a configurable per-hour or per-month contribution rate; each co-owner's share of contributions deducted from their capital account; fund balance visible on the dashboard
- [ ] Intended to cover large scheduled expenses (engine overhaul, propeller) without special assessments

**Downloadable co-owner statement:**
- [ ] CSV export per co-owner per period: opening balance, fixed cost charges (itemised), operating cost charges (itemised by flight), payments received, closing balance, reserve fund contributions if applicable
- [ ] Statement header records: export date, exporter name (current user), period start/end, aircraft registration
- [ ] PDF export with the same content (if PDF generation is already available in the codebase; otherwise CSV only)

**Tests:**
- [ ] Share validation: shares must sum to exactly 100 %; partial assignments (e.g., 60 % + 30 % only) are rejected
- [ ] Share validation: a single owner at 100 % is valid (sole operator edge case)
- [ ] Fixed-cost apportionment: known fixed expense → each co-owner's liability matches `amount × share_pct / 100` to two decimal places; sum of liabilities equals total expense
- [ ] Operating-cost attribution: hours flown by co-owner A do not appear on co-owner B's balance
- [ ] Capital account arithmetic: buy-in + payments − fixed liabilities − operating liabilities = current balance
- [ ] Overdue flag: balance negative for ≤ 30 days → no flag; > 30 days → flagged
- [ ] Valuation snapshot: recorded balance is immutable; subsequent transactions do not alter past snapshots
- [ ] Statement export: correct totals, correct per-owner rows, metadata (export date, exporter, period) present; opening + charges − payments = closing balance

---

## Phase 40 — Flying Club

Goal: support the flying-club operating model, where the club is the sole aircraft owner and members share access under a common membership structure.

**Membership:**
- [ ] `ClubMembership` model — tenant FK, user FK, membership type (Full / Student / Honorary), valid_from, valid_until, annual_fee
- [ ] Membership management UI — list active and expired members, add or renew membership, suspend a member
- [ ] Membership expiry enforced: expired members cannot log new flights or create reservations (Phase 22)

**Club billing:**
- [ ] Member-specific hourly rates per aircraft (e.g. full-member rate vs. student rate)
- [ ] Monthly billing summary per member: flights, total hours, charges at applicable rate, membership dues; downloadable statement

**Dev seed:**
- [ ] Club-mode seed: one tenant with three members and two shared aircraft

**Tests:**
- [ ] Membership expiry: expired member blocked from booking and flight logging, but can still view their past billing information
- [ ] Billing: correct rate applied per membership type; summary totals accurate

---

## Phase 41 — Flying School

Goal: support the flight-school operating model, where instructors deliver dual-instruction flights to students, with per-student progress tracking and instructor-specific permissions. The same model covers independent instructors operating on a single aircraft with a small number of private students — no formal school structure required.

**Instructor role:**
- [ ] New **Instructor** role: can approve flight log entries, record dual-instruction flights, and view all student logbooks within the tenant
- [ ] Instructor assignment per aircraft: only assigned instructors may approve solo reservations for that aircraft (builds on Phase 22 approval workflow)

**Student role:**
- [ ] New **Student** role, distinct from Pilot/Renter: students cannot create reservations independently — all bookings (dual sessions and supervised solo flights) must be initiated or approved by an assigned instructor
- [ ] Instructor sign-off required on solo flight entries for students: flight is marked pending until an instructor countersigns (free text + timestamp)

**Student management:**
- [ ] `StudentProfile` model — user FK, training programme (e.g. PPL / LAPL / IR), assigned instructor FK, start_date, target_hours
- [ ] Student progress view: hours logged (dual / solo / total), distance to licence target, list of qualifying flights

**Dual-instruction flights:**
- [ ] Dual-instruction entry in the aircraft logbook automatically creates paired `PilotLogbookEntry` records for both the student (SP) and the instructor (IP)

**Dev seed:**
- [ ] School-mode seed: one tenant with two instructors, four students at different stages, and a mixed history of dual and solo flights

**Tests:**
- [ ] Student role: cannot create a reservation without instructor; booking blocked after instructor unassigned
- [ ] Instructor role: can approve entries and record dual flights; cannot modify aircraft configuration
- [ ] Student progress: hour totals and solo/dual split are accurate
- [ ] Paired logbook entries: dual flight creates correct SP and IP entries

---

## Phase 42 — Pilot Logbook Auto-population

Goal: auto-populate the pilot logbook from aircraft logbook entries so that
logging a flight on the aircraft form fills both logbooks in one step.

**Auto-population from `FlightEntry`:**
- [ ] When a `FlightEntry` is saved with a registered crew member, automatically create or update the corresponding `PilotLogbookEntry`
- [ ] Derivation rules:
  - Aircraft fields ← `FlightEntry.aircraft` (type, registration)
  - Times ← `FlightEntry` (departure/arrival place and time from Phase 16)
  - `pic_name` ← `FlightCrew[role=PIC]` for that flight
  - `total_flight_time` ← `FlightEntry.flight_time`
  - Function column ← mapped from the holder's `FlightCrew.role` (PIC→function_pic, COPILOT→function_copilot, SP→function_dual, IP→function_instructor)
  - Single vs multi engine ← derived from aircraft engine count in the `Component` table
- [ ] All auto-filled values remain editable by the pilot before saving

**Unified flight entry form:**
- [ ] The aircraft flight entry form (Phase 16) gains a "My logbook" collapsible section when the logged-in user appears in the crew list — pilot-specific fields (night/instrument time, function) appear alongside the aircraft fields
- [ ] On save: `FlightEntry` + one `PilotLogbookEntry` per registered crew member created atomically
- [ ] Linked entries in the pilot logbook view show a link icon to the corresponding aircraft logbook entry

**Dev seed:**
- [ ] Linked entries auto-created from existing seed `FlightEntry` records (including at least one IP+SP dual entry)

**Tests:**
- [ ] Auto-population: `FlightEntry` save → correct `PilotLogbookEntry` derived fields for all columns
- [ ] Function mapping: each `FlightCrew` role maps to the correct function column
- [ ] Single vs multi engine derivation from aircraft component configuration
- [ ] Unified form: pilot logbook section appears when logged-in user is in crew list; hidden otherwise
- [ ] Atomic save: `FlightEntry` rollback also rolls back the `PilotLogbookEntry`

---

## Phase 43 — Photo EXIF & Arrival Time Auto-fill

Goal: extract the arrival time automatically from counter photos so pilots
don't need to type it in after every flight.

**EXIF timestamp extraction:**
- [ ] On counter photo upload, extract EXIF `DateTimeOriginal` tag → suggest as arrival time (converted to UTC, floored to nearest 0.1 h); user can accept or override
- [ ] If EXIF tags are absent, attempt to parse a timestamp from the original filename (common patterns: `IMG_YYYYMMDD_HHmmss`, `YYYY-MM-DD HH.mm.ss`, etc.) as a fallback
- [ ] No OCR of counter values yet (tracked in [`docs/backlog.md`](backlog.md))

**Tests:**
- [ ] Known-good JPEG with EXIF `DateTimeOriginal` → correct UTC arrival suggestion, floored to 0.1 h
- [ ] JPEG with stripped EXIF but timestamp in filename → correct fallback suggestion
- [ ] JPEG with neither EXIF nor recognisable filename → no suggestion, no error

---

## Phase 44 — External Integrations

Goal: connect OpenHangar to the tools operators already use.

- [ ] ICS calendar export — one feed URL per aircraft, includes reservations and maintenance due dates
- [ ] Webhook outbox — configurable POST on key events (flight logged, maintenance overdue, reservation confirmed)
- [ ] Accounting CSV export — standard format (date, description, amount, VAT rate) for fuel and parts
- [ ] Parts vendor search — configurable URL template per aircraft type; "find part" link from maintenance trigger detail
- [ ] ADS-B CSV import — (e.g. from OpenSky Network) — upload a CSV export, match rows by registration to an aircraft in the hangar, create `FlightEntry` records; duplicate detection (same date + departure + arrival already exists) before committing
- [ ] ADS-B pre-fill suggestion — (opt-in, off by default) — when opening the new-flight form, query an external ADS-B registry for a recent flight by the selected aircraft; if found, offer to pre-fill departure/arrival airports and date/time; user must confirm before any data is written
- [ ] Route tests: ICS feed structure, webhook delivery, accounting CSV columns, ADS-B import duplicate detection

---

## Phase 45 — Advanced Reporting & Exports

Goal: give owners and clubs actionable summaries they can share or archive.

- [ ] Airframe / engine / propeller logbook PDF export (per aircraft or per component)
- [ ] Cost report PDF — period-selectable, grouped by type, with cost-per-hour
- [ ] Fleet health summary — one-page printable status sheet for all aircraft
- [ ] CSV export for expenses, flight entries, and maintenance triggers
- [ ] Pilot currency matrix — table of all pilots vs. currency checks (SEP, night, medical)
- [ ] Route tests: export endpoints return correct content-type and non-empty payloads
- [ ] Quick handover pack — per-aircraft snapshot for handover/notice boards:
  - Generates a one‑page web view and a printable PDF containing: aircraft status colour, current hobbs/engine hours, last 5 flights (date/route/hours), open snags (grounding first), next 5 maintenance items, and links to essential non-sensitive documents.
  - This one-page/PDF can be publicly shared, this is defined at the aircraft level (default: turned off)
  - If public sharing enabled: create a printable QR code (PNG) that links to the aircraft's public PDF or web snapshot; QR + very short instructions packaged in a sized PDF suitable for printing and attaching to the aircraft (e.g., cockpit placard).
  - Share-link / PDF respects document visibility (sensitive docs excluded) and enforces token access for full views.
  - Route tests: snapshot web view renders, PDF generation returns correct content-type and includes expected sections, QR resolves to correct tokenized share URL, and printable PDF layout fits standard paper sizes.
- [ ] Export official-format logbook to Excel — per-pilot or per-aircraft XLSX export that maps fields to the jurisdiction‑specific official logbook columns (EASA / FAA mode), preserves column types/headers, includes running totals and export metadata (exporter, timestamp, tenant), and respects privacy/visibility rules (sensitive docs/entries excluded).
- [ ] **Download all aircraft information as ZIP** — per-aircraft archive bundling: PDF airframe/engine/propeller logbook exports, current maintenance snapshot (PDF), open snags list, cost summary, and all accessible documents (Phase 27 visibility rules); served as `aircraft-<reg>-export-<date>.zip`; respects role-based visibility (sensitive documents excluded for non-owners)

---

## Phase 46 — Hosted SaaS & Advanced RBAC

Goal: support a multi-tenant hosted offering with fine-grained permissions and full audit trail.

- [ ] Tenant self-registration flow — sign-up, email verification, first-user bootstrapping
- [ ] Advanced roles: Mechanic (write maintenance records, read-only flights), CAMO (approve maintenance closures), Safety Manager (read-all, no write), Instructor (manage reservations + pilot logbooks)
- [ ] Audit log — append-only table recording every write operation (who, what, when, before/after snapshot)
- [ ] Audit log viewer in Configuration page — filterable by user, model, date range
- [ ] Tenant data export (GDPR) — owner can download all tenant data as a ZIP archive
- [ ] Tenant deletion with cascading wipe and confirmation guard
- [ ] Usage metering hooks (seat count, storage bytes) — foundation for future billing integration
- [ ] Route tests: role enforcement for each new role, audit log completeness, data-export contents
