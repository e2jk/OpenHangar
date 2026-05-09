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
- [ ] [`docs/logbook_airplane.md`](logbook_airplane.md) updated to reflect final field names and implementation decisions

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

**Deferred to Phase 19b:**
- [x] Wrap all remaining user-facing strings in `_()` across all templates and routes
- [x] `translations/fr/LC_MESSAGES/messages.po` — complete French translation (581 strings)
- [x] `.mo` files compiled and committed
- [x] Language selector with flags and dropdown instead of buttons
- [ ] Weblate setup documentation
- [ ] GitHub Actions for `.pot` sync and `.mo` compilation
- [ ] `pybabel compile` added to Dockerfile
- [ ] Translation completeness test (`polib`)

---

## Phase 20 — Pilot Logbook Auto-population

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

## Phase 21 — Photo EXIF & Arrival Time Auto-fill

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

## Phase 22 — Multi-user & Club Features

Goal: support more than one user per tenant, with proper role enforcement.

- [ ] User management UI — invite user by email, assign role, revoke access
- [ ] Role enforcement on all routes (owner / viewer permissions checked server-side)
- [ ] User profile page — change password, manage TOTP
- [ ] Multiple owners per aircraft (with share % — optional, v1.1+)
- [ ] (assuming the share % is done) Owner billing dashboard — per‑aircraft bank‑account reconciliation and owner statements: compute chargeable hours × hourly rate, apportion by owner share, show running "amount due" per co‑owner, support manual reconciliation against a single aircraft bank account, and downloadable statements (CSV/PDF) with export metadata (period, exporter, tenant).
- [ ] Extend dev seed with additional users: one owner, one viewer — to exercise role-based access

---

## Phase 23 — Reservations & Rentals

Goal: allow clubs and schools to manage aircraft bookings and billing.

- [ ] `Reservation` model — aircraft FK, pilot FK, start/end datetime, status (pending / confirmed / cancelled), notes
- [ ] Booking calendar view per aircraft — month/week grid, colour-coded by status
- [ ] Create / edit / cancel reservation from the calendar or aircraft detail page
- [ ] Per-aircraft minimum booking duration (stored in DB, editable by owner)
- [ ] Owner approval workflow — reservation starts as "pending", owner confirms or declines
- [ ] Cost estimation at booking time based on current hourly rate (from Expense data)
- [ ] Conflict detection — prevent overlapping confirmed reservations
- [ ] Dev seed: two weeks of reservations across all seed aircraft
- [ ] Route tests: CRUD, conflict detection, approval flow, calendar rendering

---

## Phase 24 — Offline Mobile Sync & Telemetry Import

Goal: allow data entry when connectivity is unreliable and enrich logs with GPS/ADS-B data.

- [ ] Progressive Web App (PWA) manifest and service worker for offline caching of the flight-entry form
- [ ] Local IndexedDB queue for offline flight entries; sync to server on reconnect
- [ ] GPX / IGC file import — parse track, auto-fill departure/arrival ICAO, compute flight time equivalent from elapsed time
- [ ] ADS-B CSV import (e.g. from OpenSky) — match by registration, create FlightEntries
- [ ] Duplicate detection on import (same date + departure + arrival already exists)
- [ ] Dev seed: one aircraft with an imported GPX track attached to a flight entry
- [ ] Route tests: import endpoints, duplicate detection, sync conflict resolution

---

## Phase 25 — External Integrations

Goal: connect OpenHangar to the tools operators already use.

- [ ] ICS calendar export — one feed URL per aircraft, includes reservations and maintenance due dates
- [ ] Webhook outbox — configurable POST on key events (flight logged, maintenance overdue, reservation confirmed)
- [ ] Accounting CSV export — standard format (date, description, amount, VAT rate) for fuel and parts
- [ ] Parts vendor search — configurable URL template per aircraft type; "find part" link from maintenance trigger detail
- [ ] Route tests: ICS feed structure, webhook delivery, accounting CSV columns

---

## Phase 26 — Email Notifications

Goal: proactively alert owners about upcoming and overdue maintenance.

- [ ] `NotificationSetting` model — tenant-level thresholds (usage %, days-before, stored in DB)
- [ ] Background job / scheduler (APScheduler or similar) wired into the container
- [ ] Monthly summary email — items due in next 3 months
- [ ] 90 % usage warning email for hours-based triggers
- [ ] 7-day reminder for calendar-based hard times
- [ ] Immediate overdue alert when threshold is exceeded
- [ ] Extend dev seed with notification settings pre-configured for the seed tenant

---

## Phase 27 — Advanced Reporting & Exports

Goal: give owners and clubs actionable summaries they can share or archive.

- [ ] Alembic migration pipeline + migration test harness — initialize Alembic with a baseline revision, add CI checks, and include tests that apply/rollback migrations against a fresh DB to safeguard live upgrades.
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

---

## Phase 28 — Hosted SaaS & Advanced RBAC

Goal: support a multi-tenant hosted offering with fine-grained permissions and full audit trail.

- [ ] Tenant self-registration flow — sign-up, email verification, first-user bootstrapping
- [ ] Advanced roles: Mechanic (write maintenance records, read-only flights), CAMO (approve maintenance closures), Safety Manager (read-all, no write), Instructor (manage reservations + pilot logbooks)
- [ ] Audit log — append-only table recording every write operation (who, what, when, before/after snapshot)
- [ ] Audit log viewer in Configuration page — filterable by user, model, date range
- [ ] Tenant data export (GDPR) — owner can download all tenant data as a ZIP archive
- [ ] Tenant deletion with cascading wipe and confirmation guard
- [ ] Usage metering hooks (seat count, storage bytes) — foundation for future billing integration
- [ ] Route tests: role enforcement for each new role, audit log completeness, data-export contents