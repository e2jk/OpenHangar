# OpenHangar — Open-source aircraft ops manager for owner-operators and clubs
Open-source aircraft ops manager for owner-operators and clubs — fleet maintenance (ADs/SBs/lifed parts), rental management, and pilot log with passenger/night currency checks.

## Elevator pitch
OpenHangar is a self-hosted, permissively licensed platform for pilots, owner-operators, and clubs to log flights, model aircraft, track maintenance triggers, and manage basic costs and documents; v1 focuses on a single-tenant-ready, modular aircraft model and flight logging with expandable multi-tenant and rental features planned for later versions.

## Goals & Principles
- Day‑one objectives
  1. Provide reliable electronic logbooks (airframe/engine/prop) and flight logging (hobbs/tach) for aircraft.
  2. Allow owners to model aircraft (airframe, engines, props, avionics) and define maintenance triggers (calendar, time/hours, cycles).
  3. Present a clear, actionable dashboard (green/yellow/red) and calendar/list of upcoming and overdue maintenance.
- Coding principles
  - Permissive OSS, modular design, mobile‑first responsive web UI.
  - Self‑hosted Docker deployment for privacy and control.
  - Minimal external dependencies; pragmatic use of standard libraries and a UI framework (e.g., Bootstrap).
  - Security-first: email/password + mandatory TOTP (2FA); encrypted backups.

## Audience & Personas
- Owner‑operator (primary v1): configure plane(s), log flights, track maintenance, upload documents, track costs.
- Pilot (solo): lightweight logbook usage with minimal plane metadata (onboarding mode).
- Club admin / Flying school (future): multi-plane, multi-user, rentals and bookings, role granularity.
- Renter, Mechanic, Instructor (planned v2+ roles).

## High-level Scope
- v1 (what must be delivered at first)
  - Modular aircraft model: airframe + 1..n engines + 1..n propellers + avionics entries.
  - Electronic logbooks for airframe, engines, props (separate, with combined views).
  - Flight logging with hobbs/tach entries (engine and flight times), ability to attach a photo as proof of hobbs reading.
  - Maintenance triggers: calendar-based, time/hours, cycles, with multiple triggers per component.
  - Calendar and list views of triggers (with estimated time-to-next based on last 3 months usage).
  - Dashboard per-plane with status color, recent usage, upcoming maintenance.
  - Costs at plane level: period-based (e.g., insurance) and punctual (fuel, parts); unit transparently convertible (L / gal).
  - Multi-tenant database readiness (but v1 UI operates on single tenant use-case).
  - Authentication: email/password + mandatory TOTP (2FA).
  - Email notifications: monthly summary of next 3 months, 90% usage warnings, 7-day and immediate overdue alerts (thresholds are stored in DB and can be changed per user/airplane [v2+]).
  - Responsive web UI (mobile-first). PC for data review/modeling.
  - Encrypted daily backup file produced for host-side archival.
- v2+ (planned)
  - Multi-plane, multi-owner workflows; rentals/reservations, per-hour/day pricing and booking.
  - Pilot logbook features, date tracking for medical/SEP endorsements, passenger/night legality checks. Looking forward (when will I not be able to fly with passengers if I don't fly in the coming period)
  - Offline mobile data entry + sync, ADS‑B / telemetry import, GPS-assisted flight traces.
  - External integrations (calendar ICS export, SMS, accounting, parts suppliers).
  - Advanced reporting, CSV/PDF exports, audit logs, hosted SaaS offering, role granularity (accountable manager, safety manager, mechanics).

## Core Concepts / Domain Model (summary)
- Tenant / Organization — multi‑tenant capable DB scoping.
- User — roles: Owner, Admin, User/Renter, Viewer. Users can belong to multiple tenants (for example, a renter could rent the plane from 2 owners that manager their planes in separate tenants - obviously each owner must allow that renter to access his plane...).
- Aircraft — modular assembly:
  - Airframe (registration, serial, model)
  - Engine(s) (make, model, serial, hobbs/TT)
  - Propeller(s)
  - Avionics (optional metadata)
  - Placeholders allowed for lightweight onboarding.
- Component — any trackable unit (avionics, ELT, life‑limited parts).
- LifedPart / PartInstance — part with lifing rules.
- Trigger — rule linked to a component (calendar date, hours/cycles threshold, condition), with parametrizable thresholds and notifications.
- Logbooks — AirframeLog, EngineLog, PropLog, combined views, each entry may link to maintenance actions and documents.
- FlightEntry — timestamp, hobbs/tach start & end, flight duration auto-calculated, photos (linked proofs), pilot (optional), costs (fuel).
  - When related to a plane, consider flight time; when related to a pilot, consider engine time
- Expense — plane-level or flight-level cost entries (type, amount, date, units).
- Reservation (future) — booking entry with start/end, minimum hours per-day setting.
- Notifications & Settings — per-tenant parameters stored in DB, editable later via UI.

## Key User Flows (concise)
- Onboarding (v1): admin creates tenant → creates first user (owner) → creates aircraft (lightweight or full model) → start logging flights.
- Log flight / hobbs photo: user enters hobbs/tach value, attaches photo, links to FlightEntry; system updates component totals, evaluates triggers, and queues notifications.
- Create maintenance trigger: owner defines trigger(s) for a component (date or hours/cycles), system shows triggers on calendar and estimates time-to-next from recent usage.
- View dashboard: owner visits aircraft dashboard to view color status, recent flights, last 30‑day hours, and list of upcoming/past‑due items.
- Manage owners: add secondary owners with % share and primary contact flag. Owners have edit rights; renters/viewers limited.

## Onboarding & Mode Profiles
- Install-time onboarding wizard (v2 feature to implement): select a mode to simplify UI:
  - Pilot-only (logbook focus): minimal plane metadata (reg, type) stored as placeholders.
  - Owner‑operator: full plane metadata, logbooks, maintenance triggers.
  - Club (owners only) / Club (with renters) / Flight school: reveal relevant features (members, rentals, bookings).
- All modes use the same data model; non-required fields are hidden but preserved so expansions are seamless.

## v1 / Functional Feature List
- Aircraft management
  - Create/edit aircraft with modular components (airframe, engines, props, avionics).
  - Lightweight placeholders for minimal onboarding.
- Maintenance scheduling
  - Calendar view + list view (upcoming / past‑due).
- Logbooks and flight logging
  - Separate airframe/engine/prop logbooks (with combined views in v2+).
  - Create FlightEntry with hobbs/tach start & end, duration, and optional link to pilot.
  - Photo upload attached to flight/hobbs entry; store entered numeric hobbs with link to photo.
- Photo/document uploads
  - Upload and attach documents (POH, autopilot/manuals, invoices, scanned maintenance paperwork) to aircraft, individual components and to logbook entries and maintenance records.
  - Default visibility: documents are visible to renters/viewers; each uploaded document can be marked as "sensitive" to hide it from renter/viewer roles while remaining visible to owners/admins.
  - Document metadata: title, type, upload date, uploader, visibility flag, and optional notes; documents searchable in aircraft/component context.
- Cost tracking
  - Plane-level expenses: periodic (insurance) and punctual (fuel/parts).
  - Unit transparency L/gal and conversion; cost-per-hour calculations over configurable period (1 year default).
- Multi-tenant readiness
  - DB schema supports organizations/tenants; UI defaults to single-tenant flow initially.
- Authentication & security
  - Email/password sign up + mandatory TOTP 2FA.
- Notifications
  - Email channel only.
  - Configurable thresholds persisted in DB (not hardcoded). Default behaviors:
    - Monthly summary of items coming in next 3 months.
    - Immediate notification at 90% usage of a usage-based trigger.
    - Reminder 7 days before a hard-time.
    - Immediate notification when usage-based trigger is exceeded.
- Responsive web UI
  - Mobile-first data entry screens; desktop for modeling and review.
- Backups
  - Daily encrypted zip placed in configured folder; host-side process handles archival.

## v2 / Future Features (condensed)
- Aircraft management: Multiple owners with share % and primary contact.
- Maintenance triggers:
  - Create multiple triggers per component: calendar, hours, cycles.
  - Trigger evaluation uses historical usage (estimate next occurrence from last 3 months).
- Reservations & rentals: hourly/day bookings, per-plane minimum hours, owner approval workflow.
- Pilot-centric logbook: pilot profiles, medicals, SEP endorsements, passenger/night legality monitoring and checks.
- Offline mobile sync and telemetry imports (ADS‑B/ELT traces).
- Integrations: ICS calendar export, calendar sync, SMS, accounting tools, parts vendors.
- Advanced reporting & exports: CSV, PDF, reports (cost-per-hour, fleet health, pilot currency matrix).
- Hosted SaaS option, scaling and role granularization (mechanics, accountable manager).

## Non-functional Requirements
- Security: mandatory 2FA TOTP, secure password storage, encrypted backup artifacts (key via Docker env/config).
- Privacy: self-hosted; data stored on tenant server. (No hosted telemetry in v1.)
- Dependences: keep external libraries minimal; use Flask, Postgres or MariaDB, Bootstrap; bundle into Docker.
- Performance & scale: v1 tuned for small deployments (1–10 planes, few dozen users); architecture should permit scaling later.
- Backup & restore: daily encrypted backups; restore procedure documented.

## Internationalization & Regulatory Context
- Multi-language support: app designed for i18n; development and default UI in English, with localization framework enabling additional languages to be added and selected per-tenant or per-user.  
- Regulatory contexts: support both EASA and FAA conventions (date formats, units, regulatory labels like AD vs. Airworthiness Directive mapping, and jurisdiction-specific fields); tenant-level setting selects regulatory context so UI labels, reports, and compliance terminology adapt accordingly.

## System Architecture (high-level)
- Components
  - Backend: Flask app providing REST/JSON APIs and server-rendered pages as needed.
  - Database: Postgres (preferred) or MariaDB—single DB supporting tenant scoping.
  - Frontend: responsive JS + Bootstrap (or similar) for mobile-first UX.
  - File storage: local filesystem within container with configurable mount for persistence.
  - Background worker: simple scheduler/worker for email notifications and periodic estimations.
- Deployment
  - Single Docker image (Flask + static assets), DB container (or managed DB), optional worker container.
  - Environment configuration via Docker env vars (DB URL, mail server, backup key path).
  - Backup job: container writes encrypted zip to host-mounted folder; external process archives it.

## Data Model (summary of key entities)
- Tenant (id, name, settings)
- User (id, email, password_hash, totp_secret, global_roles)
- TenantUser (user_id, tenant_id, role)
- Aircraft (id, tenant_id, registration, model, placeholders_flag, owners)
- Component (id, aircraft_id, type[engine/prop/avionics/etc.], make, model, serial, hobbs_counter_id)
- LifedPart / PartInstance (id, component_id, serial, lifing_rules)
- Trigger (id, component_id, type[calendar/hours/cycles], threshold_value, notification_settings)
- LogbookEntry (id, type[airframe/engine/prop], aircraft_id, component_id?, date, notes, docs)
- FlightEntry (id, aircraft_id, pilot_user_id?, hobbs_start, hobbs_end, tach_start, tach_end, photo_id, duration)
  - when FlightEntry.aircraft_id present → counts as aircraft/flight time; when FlightEntry.pilot_user_id present → counts as engine/pilot time
- Expense (id, aircraft_id, date, amount, currency, unit, type)
- Document / Photo (id, owner, path, metadata, visibility_role_mask, sensitive_flag)
- NotificationSetting (tenant_id, thresholds JSON)
- BackupRecord (id, path, created_at, checksum)

## API & Integration Notes (for codegen guidance)
Suggested core API endpoints:
- Auth: /auth/register, /auth/login, /auth/totp-verify
- Tenant: /tenant (create, settings)
- Users: /users (CRUD, invite)
- Aircraft: /aircraft (CRUD, add component)
- Components: /aircraft/:id/components
- Logbooks: /aircraft/:id/logbook, /logbook/:id/entries
- FlightEntries: /aircraft/:id/flights (create/list)
- Triggers: /aircraft/:id/triggers
- Notifications: /notifications (list), background endpoints to evaluate triggers and send emails
Important constraints:
- Parametrizable thresholds and notification windows must read from DB at runtime.
- Multi‑tenant scoping: every query must be tenant‑scoped; user can belong to multiple tenants.
- Owners with edit rights vs renters with limited rights must be enforced server-side.

## UI / UX Notes
- Mobile-first entry screens:
  - Quick flight entry: hobbs/tach values + photo capture + optional notes.
  - Minimal friction: pre-focus hobbs field, camera access on mobile.
- Dashboard:
  - Single-plane status color (green/yellow/red) computed from trigger states.
  - Key stats: current hobbs, last 30‑day hours, recent flights list, upcoming maintenance list.
- Maintenance views:
  - Calendar view with fixed dates & estimated time-to-threshold for usage-based triggers.
  - List view sorted by urgency (past due → due soon → scheduled).
- Aircraft modeling:
  - Progressive disclosure for advanced fields; placeholders for minimal onboarding.
- Document management UI
  - With visibility toggle at upload time and a permissions-aware document list per aircraft/component.
  - Sensitive docs are indicated and hidden for renter/view roles.
- Settings:
  - Notification thresholds editable (future UI), default values stored in DB for v1.

## Roadmap & Milestones
- MVP (v1)
  - Repo skeleton, Docker setup, DB schema, basic auth + TOTP, aircraft CRUD, flight logging (+photo), triggers evaluation, email notifications, dashboard pages.
- v1.1
  - Expense tracking, owner shares, combined logbook views, backup hook.
- v2
  - Reservations/rentals, pilot logbook & legality checks, offline sync prototype, integrations.
- v3+
  - Hosted offering, advanced RBAC, audit logs, heavy reporting.

## Contribution & Development Guide (brief)
- Repo structure (suggested)
  - /backend (Flask app)
  - /frontend (static assets, JS)
  - /migrations (DB migrations)
  - /docker (Dockerfile, compose)
  - /dev_seed (default JSON to seed dev DB)
  - /docs (design docs, runbook)
- How to run locally (dev)
  - Docker-compose up (Flask, DB, worker). Dev seed loader reads JSON to create starter data.
- Coding standards
  - Python 3.11+, Flask for HTTP layer, SQLAlchemy for ORM, simple JS + Bootstrap on frontend.
- Tests
  - Unit tests for core models and trigger evaluation; integration tests for main flows.

## Feedback & Governance
- Feature requests and discussion via GitHub Issues and PRs.
- Roadmap decisions guided by maintainers + community votes from pilots (issue reactions or a simple voting label flow).
- Contributor Code of Conduct and lightweight maintainers policy.

## Appendix
- Glossary
  - Hobbs: flight hour meter, used to track aircraft usage.
  - Lifed part: a component with a finite operational life measured in hours/cycles/calendar.
  - AD (Airworthiness Directive): mandatory regulatory action.
  - SB (Service Bulletin): manufacturer advisory.
  - CAMO: Continuing Airworthiness Management Organisation.
  - POH: Pilot’s Operating Handbook: the aircraft‑specific manual with limitations, procedures, performance, weight & balance, and systems info for safe operation.
- Default configuration (v1)
  - Notification defaults stored in DB: monthly summary window = 3 months, usage 90% warn, 7-day date reminder.
  - Minimum-day rental default = 3 hours (stored, editable later).
- Dev seed
  - Provide a JSON file in /dev_seed with an example tenant, one aircraft (placeholder), one user (owner), sample triggers and a few flight entries to ease local testing.
