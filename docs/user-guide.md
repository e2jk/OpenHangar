# OpenHangar — User Guide

---

## Who is OpenHangar for?

| Persona | Primary use |
|---|---|
| **Owner-operator** *(v1 primary)* | Configure aircraft, log flights, track maintenance, upload documents, manage costs |
| **Pilot (solo)** | Lightweight personal logbook with minimal aircraft metadata |
| **Club admin / Flying school** *(future)* | Multi-aircraft, multi-user, rentals, bookings, and role granularity |
| **Renter / Mechanic / Instructor** | Planned v2+ roles |

---

## Key features

- **Fleet management** — model airframe, engines, props, and avionics; lightweight placeholders for quick onboarding.
- **Maintenance tracking** — calendar, hours, and cycles-based triggers with a clear green/yellow/red dashboard status.
- **Flight logging** — hobbs/tach entries, optional photo proofs of instrument readings, automatic logbook updates.
- **Pilot logbook** — personal logbook with EASA FCL.050 column mapping and passenger/night currency tracking.
- **Document management** — attach PDFs and photos to aircraft, components, and logbook entries; sensitive-document controls hide files from renter/viewer roles.
- **Cost tracking** — per-flight and periodic expenses; L/gal unit conversion; cost-per-hour calculations.
- **Encrypted backups** — AES-256-GCM daily backups with SHA-256 verification (see [backup & restore guide](backup_restore.md)).
- **Multi-language** — English, French, Dutch; language selectable per user.

---

## Getting around

Once logged in, the **Dashboard** gives you a fleet overview: status badges per aircraft, recent flights, maintenance alerts, and pilot currency summary.

The navbar provides access to:

| Section | What you can do |
|---|---|
| **Aircraft** | Manage fleet, component details, snags |
| **Flights** | Log and browse flight entries |
| **Maintenance** | View and manage maintenance triggers |
| **Documents** | Upload and browse documents |
| **Expenses** | Log and review costs |
| **Pilot** | Personal logbook and pilot profile |
| **Configuration** | Backups and email settings *(administrators)* |

---

## Key user flows

### First-time setup

1. An administrator creates the organisation and the first user account (owner).
2. Add aircraft — choose lightweight (registration only) or full model (airframe + engines + props + avionics).
3. Define maintenance triggers for each component (date-based, hours-based, or cycles-based).
4. Start logging flights.

### Logging a flight

1. Navigate to **Flights → Log flight**.
2. Enter hobbs/tach start and end values; attach a photo of the instrument if desired.
3. Save — the system updates component totals and re-evaluates all maintenance triggers automatically.

### Monitoring maintenance

- The dashboard shows a colour status badge (green / yellow / red) per aircraft.
- The Maintenance list view sorts items by urgency: overdue → due soon → scheduled.
- Overdue items also appear as alerts on the dashboard.

### Managing documents

Upload any PDF, image, or document from the Aircraft or Component detail page.
Mark a document **sensitive** at upload time to hide it from renter/viewer roles
while keeping it visible to owners and admins.

---

## Roles & access control

OpenHangar uses a role-based model combined with per-aircraft access grants.

| Role | Summary |
|---|---|
| **Admin** | Full access to everything including system configuration |
| **Owner** | Full access to fleet, maintenance, flights, and user management |
| **Pilot** | Log flights, create reservations; access limited to assigned aircraft |
| **Maintenance** | View and update maintenance; access limited to assigned aircraft |
| **Viewer** | Read-only; access limited to assigned aircraft |

When inviting a user, the owner selects their role and checks which aircraft they are allowed to access.  Admin and Owner roles automatically see every aircraft.

> A more granular permission model — profile types, per-aircraft permission bits, and an "access to all aircraft" option — is planned for a future release.
> See the [access control reference](access-control.md) for the full target model and role capability matrix (⚠ not yet implemented).

---

## Logbook reference

- [Aircraft logbook guide](logbook_airplane.md) — field definitions, EASA vs FAA columns, counter types.
- [Pilot logbook guide](logbook_pilot.md) — personal logbook fields, EASA FCL.050 mapping, currency rules.

---

## Glossary

| Term | Definition |
|---|---|
| **Hobbs** | Flight hour meter used to track aircraft usage |
| **Lifed part** | A component with a finite operational life measured in hours, cycles, or calendar time |
| **AD** | Airworthiness Directive — mandatory regulatory action issued by an aviation authority |
| **SB** | Service Bulletin — manufacturer advisory (may or may not be mandatory) |
| **CAMO** | Continuing Airworthiness Management Organisation |
| **POH** | Pilot's Operating Handbook — the aircraft-specific manual with limitations, procedures, and performance data |
| **EASA** | European Union Aviation Safety Agency |
| **FAA** | Federal Aviation Administration (United States) |
| **TOTP** | Time-based One-Time Password — used for two-factor authentication (2FA) |

---

## Contributing translations

OpenHangar is available in English, French, and Dutch. If you'd like to help
translate it into another language, or improve an existing translation, visit
[hosted.weblate.org](https://hosted.weblate.org/engage/openhangar/) — no technical knowledge or
Git access required, just a free Weblate account.
