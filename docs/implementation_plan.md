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

---

## Phase 2 — Aircraft Management (basic CRUD)

Goal: a user can add planes and attach an engine and propeller through the UI.

- [ ] Aircraft list page (per tenant) — shows registration, type, status placeholder
- [ ] Add aircraft form — registration, make/model, year (engine/prop can be added after)
- [ ] Aircraft detail page — shows linked engine(s) and propeller(s)
- [ ] Add/edit engine form linked to an aircraft
- [ ] Add/edit propeller form linked to an aircraft
- [ ] Delete aircraft (with cascade to components)
- [ ] Basic auth guard — redirect unauthenticated users to login

---

## Phase 3 — Basic Flight Logging

Goal: a user can record a flight against an aircraft.
Minimal fields only; logbook refinement comes later.

- [ ] `FlightEntry` model — aircraft FK, date, departure airfield, arrival airfield, hobbs start/end
- [ ] Log flight form (one page, minimal fields)
- [ ] Flight list per aircraft (date, route, hobbs delta)
- [ ] Aircraft total hobbs derived automatically from flight entries
- [ ] Route tests for flight creation and listing

---

## Phase 4 — Basic Maintenance Tracking

Goal: define when maintenance is due (by date or by hours) and see its status.

- [ ] `MaintenanceTrigger` model — aircraft FK, name, type (calendar / hours), threshold value
- [ ] `MaintenanceRecord` model — trigger FK, date performed, notes
- [ ] Add trigger form (hard date or N hours since last service)
- [ ] Trigger list per aircraft — shows OK / due soon / overdue based on current hobbs or date
- [ ] Mark trigger as serviced (creates a `MaintenanceRecord`)
- [ ] Route tests for trigger CRUD and status calculation

---

## Phase 5 — Real Dashboard

Goal: replace placeholder cards with live data.

- [ ] Fleet overview — real list of aircraft with computed status colour
- [ ] Per-aircraft status: green (all OK) / yellow (due ≤ 30 days or ≤ 10% hours) / red (overdue)
- [ ] Recent flights panel — last 5 flights per aircraft
- [ ] Upcoming maintenance panel — next 5 items sorted by urgency
- [ ] Quick stats — total aircraft, flights this month, open alerts

---

## Phase 6 — Logbook & Flight Detail Expansion

Goal: upgrade flight entries to full logbook quality.

- [ ] Additional flight fields — pilot (free text), duration (auto-calculated), notes
- [ ] Tach start/end (separate from hobbs)
- [ ] Hobbs/tach photo attachment (file upload, stored locally)
- [ ] Airframe logbook view — all entries for an aircraft
- [ ] Engine logbook view — entries for a specific engine (hours since new / since last overhaul)
- [ ] Propeller logbook view — entries for a specific propeller

---

## Phase 7 — Cost Tracking

Goal: track what it costs to operate each aircraft.

- [ ] `Expense` model — aircraft FK, date, type (fuel / parts / insurance / other), amount, unit (L/gal/€/$)
- [ ] Add expense form (per flight or standalone)
- [ ] Expense list per aircraft — filterable by type and period
- [ ] Cost-per-hour calculation over a configurable period (default 12 months)
- [ ] Fuel cost per flight (optionally entered at log-flight time)

---

## Phase 8 — Email Notifications

Goal: proactively alert owners about upcoming and overdue maintenance.

- [ ] SMTP configuration via environment variables
- [ ] `NotificationSetting` model — tenant-level thresholds (usage %, days-before, stored in DB)
- [ ] Background job / scheduler (APScheduler or similar) wired into the container
- [ ] Monthly summary email — items due in next 3 months
- [ ] 90 % usage warning email for hours-based triggers
- [ ] 7-day reminder for calendar-based hard times
- [ ] Immediate overdue alert when threshold is exceeded

---

## Phase 9 — Document & Photo Uploads

Goal: attach documents and photos to aircraft, components, and log entries.

- [ ] `Document` model — owner type (aircraft / component / entry), file path, metadata, sensitive flag
- [ ] Upload form (drag-and-drop on desktop, camera on mobile)
- [ ] Document list per aircraft/component — visible/sensitive toggle
- [ ] Sensitive documents hidden from viewer/renter roles
- [ ] Storage path configurable via env var (host-mounted volume)

---

## Phase 10 — Multi-user & Club Features

Goal: support more than one user per tenant, with proper role enforcement.

- [ ] User management UI — invite user by email, assign role, revoke access
- [ ] Role enforcement on all routes (owner / viewer permissions checked server-side)
- [ ] User profile page — change password, manage TOTP
- [ ] Multiple owners per aircraft (with share % — optional, v1.1+)

---

## Phase 11 — Backup & Restore

Goal: automated daily encrypted backup so operators can recover from data loss.

- [ ] Encrypted ZIP produced by a scheduled job (key from env var)
- [ ] Backup written to a configurable host-mounted folder
- [ ] `BackupRecord` model — path, timestamp, checksum
- [ ] Restore procedure documented in `docs/`

---

## v2+ (future, not scheduled)

- Reservations / rentals — hourly bookings, per-plane minimum hours, approval workflow
- Pilot logbook — medical/SEP tracking, passenger/night legality checks
- Offline mobile sync and telemetry import (ADS-B / GPS traces)
- External integrations — ICS export, SMS, accounting, parts suppliers
- Advanced reporting — CSV/PDF exports, cost-per-hour, fleet health report
- Hosted SaaS offering, audit logs, advanced RBAC (mechanics, CAMO, safety manager)
- Install-time mode wizard (pilot-only / owner-operator / club)
