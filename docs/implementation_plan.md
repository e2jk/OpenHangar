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

## Phase 9 — Document & Photo Uploads

Goal: attach documents and photos to aircraft, components, and log entries.

- [ ] `Document` model — owner type (aircraft / component / entry), file path, metadata, sensitive flag
- [ ] Upload form (drag-and-drop on desktop, camera on mobile)
- [ ] Document list per aircraft/component — visible/sensitive toggle
- [ ] Sensitive documents hidden from viewer/renter roles
- [ ] Storage path configurable via env var (host-mounted volume)
- [ ] Extend dev seed with placeholder document records (files bundled in the repo under `dev_seed/docs/`)

---

## Phase 10 — Multi-user & Club Features

Goal: support more than one user per tenant, with proper role enforcement.

- [ ] User management UI — invite user by email, assign role, revoke access
- [ ] Role enforcement on all routes (owner / viewer permissions checked server-side)
- [ ] User profile page — change password, manage TOTP
- [ ] Multiple owners per aircraft (with share % — optional, v1.1+)
- [ ] Extend dev seed with additional users: one owner, one viewer — to exercise role-based access

---

## Phase 11 — Backup & Restore

Goal: automated daily encrypted backup so operators can recover from data loss.

- [ ] Encrypted ZIP produced by a scheduled job (key from env var)
- [ ] Backup written to a configurable host-mounted folder
- [ ] `BackupRecord` model — path, timestamp, checksum
- [ ] Restore procedure documented in `docs/`
- [ ] Extend dev seed with a seeded `BackupRecord` to verify the backup list UI renders correctly

---

## Phase 12 — Snag List ("Open Ends")

Goal: pilots can log defects noticed during or after a flight so the next crew is
aware of known issues before departure, and mechanics know what needs fixing.

- [ ] `Snag` model — aircraft FK, title, description, reporter, reported_at, resolved_at, grounding flag
- [ ] Aircraft gains a derived "grounded" state when any unresolved grounding snag exists
- [ ] Grounded aircraft shows a persistent red banner on its detail page and a distinct "GROUNDED" badge on the dashboard and aircraft list (overrides maintenance status colour)
- [ ] Snag entry available from the Log Flight form (inline, optional) and standalone from the aircraft detail page
- [ ] "Active Known Points" panel on the aircraft detail page listing all open snags
- [ ] Closing a snag requires a brief resolution note; closed snags are archived, not deleted
- [ ] Grounding snags surface in the dashboard's urgent maintenance panel above scheduled triggers
- [ ] Dev seed covers: one aircraft with a grounding snag, one with a non-grounding snag, one clean
- [ ] Route tests: snag CRUD, grounding propagation to aircraft status, dashboard ordering

---

## Phase 13 — Read-only Share Link

Goal: share a live, passwordless view of an aircraft's status with people who have no
account — e.g. a maintenance shop, a visiting pilot, or a club notice board.

- [ ] `ShareToken` model — aircraft FK, random 12-char token, access level (summary / full / documents), created_at, revoked_at
- [ ] Public route `GET /share/<token>` — no login required; returns 404 for unknown or revoked tokens
- [ ] Two access levels: **summary** (status badges, active snags, no exact values) and **full** (adds due dates, hobbs values, last-serviced dates)
- [ ] Page served with `noindex` / `nofollow` headers and meta tag to prevent crawler indexing
- [ ] Token management UI on the aircraft detail page: generate, view current token, revoke
- [ ] QR code generated server-side (`qrcode` library) and shown in a modal, downloadable as PNG
- [ ] Rate limiting on the public endpoint to deter token enumeration
- [ ] Dev seed: one aircraft with a summary token, one with a full token
- [ ] Route tests: valid token, revoked token, access-level gating, noindex header, QR endpoint

---

## Phase 14 — Email Notifications

Goal: proactively alert owners about upcoming and overdue maintenance.

- [ ] SMTP configuration via environment variables
- [ ] `NotificationSetting` model — tenant-level thresholds (usage %, days-before, stored in DB)
- [ ] Background job / scheduler (APScheduler or similar) wired into the container
- [ ] Monthly summary email — items due in next 3 months
- [ ] 90 % usage warning email for hours-based triggers
- [ ] 7-day reminder for calendar-based hard times
- [ ] Immediate overdue alert when threshold is exceeded
- [ ] Extend dev seed with notification settings pre-configured for the seed tenant

---

## v2+ (future, not scheduled)

- Reservations / rentals — hourly bookings, per-plane minimum hours, approval workflow
- Pilot logbook — medical/SEP tracking, passenger/night legality checks
- Offline mobile sync and telemetry import (ADS-B / GPS traces)
- External integrations — ICS export, SMS, accounting, parts suppliers
- Advanced reporting — CSV/PDF exports, cost-per-hour, fleet health report
- Hosted SaaS offering, audit logs, advanced RBAC (mechanics, CAMO, safety manager)
- Install-time mode wizard (pilot-only / owner-operator / club)
