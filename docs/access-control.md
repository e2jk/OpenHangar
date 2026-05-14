# OpenHangar — Access Control & Permissions

> **⚠ This page describes the target access-control model planned for Phase 23.**
> The current application implements a simplified version of this model.
> See [Current behaviour](#current-behaviour) for what is available today.

---

## Current behaviour

OpenHangar currently uses a flat five-role model.  Each user is assigned exactly one role per tenant:

| Role | What they can do |
|---|---|
| **Admin** | Full access to everything; same as Owner plus system configuration |
| **Owner** | Full access to aircraft, flights, maintenance, documents, expenses, and reservations; can manage users and invitations |
| **Pilot** | Log flights, view own logbook, create reservations; no aircraft/component edits |
| **Maintenance** | View and update maintenance triggers and records; no flight logging |
| **Viewer** | Read-only across the tenant |

Per-aircraft access is enforced for the Pilot, Maintenance, and Viewer roles: an owner must explicitly grant access to each aircraft when inviting the user.  Admin and Owner roles always see every aircraft in the tenant.

---

## Target model (Phase 23 — not yet implemented)

The sections below describe the full permission model that will replace the flat five-role system.  Nothing on this page after the **Current behaviour** section is implemented yet.

---

## Profile types

Each user will have one **profile type** plus two optional capability flags.

| Profile type | Typical use | Default `is_pilot` | Default `is_maintenance` |
|---|---|:---:|:---:|
| `admin` | System/tenant administrator | optional | optional |
| `owner` | Aircraft owner | optional | optional |
| `pilot` | Pilot / renter | ✓ | — |
| `student` | Student pilot | ✓ | — |
| `instructor` | Flight instructor | ✓ | configurable |
| `maintenance` | Mechanic / CAMO staff | — | ✓ |
| `viewer` | Read-only observer | — | — |

**`is_pilot`** — enables pilot-specific flows: personal logbook entries, reservations, pilot-level flight logging.  An owner or maintenance user who also flies can set `is_pilot = true` to unlock these flows without changing their primary profile type.

**`is_maintenance`** — enables maintenance-specific flows: edit aircraft details and components, add and edit maintenance tasks.

**`view_only`** — when true, suppresses all write capabilities regardless of `is_pilot` / `is_maintenance`.

---

## Per-aircraft access

Aircraft access is controlled per user via an `AircraftAccess` record.  Two access types exist:

| `access_type` | Meaning |
|---|---|
| `specific` | Access to one named aircraft only |
| `all` | Access to every existing aircraft **and any aircraft added in the future** |

Admin users bypass the access table entirely — they always have global full access.

Each access record also carries a **`permissions_mask`** bitmask with the following bits:

| Bit | Action |
|---|---|
| `view_aircraft` | See the aircraft in lists and open its detail page |
| `edit_aircraft` | Edit registration, make/model, specs |
| `read_maintenance_full` | Read full maintenance records including serial numbers, history, and sensitive notes |
| `read_maintenance_limited` | Read a scrubbed view: open/active items only, no serial numbers, no sensitive history |
| `write_maintenance` | Add and edit maintenance triggers and records |
| `edit_components` | Add, edit, and remove components |
| `write_logbook` | Log flights against this aircraft |
| `reserve_aircraft` | Create reservations for this aircraft |

A user's effective permission for a given aircraft is resolved in this order:

1. **Admin bypass** — profile_type `admin` → allow all.
2. **all_planes row** — if an `access_type='all'` row exists for the user, use its `permissions_mask`.
3. **Per-aircraft row** — look up the row for this specific aircraft and use its `permissions_mask`.
4. **Aircraft owner match** — if the user is recorded as an owner of this aircraft, grant owner-equivalent permissions.
5. **Profile-type defaults** — fall back to the preset mask for the user's profile type.

Explicit per-aircraft masks override profile-type defaults in both directions (they can grant more or restrict further than the default).

---

## Role capability matrix

The table below shows the **default** permission mask loaded for each profile type.  An administrator can adjust individual bits per aircraft.

| Profile | `view_aircraft` | `edit_aircraft` | `read_maintenance_full` | `read_maintenance_limited` | `write_maintenance` | `edit_components` | `write_logbook` | `reserve_aircraft` |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| admin | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| owner | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| pilot | ✓ | — | — | ✓ | — | — | ✓ | ✓ |
| student | ✓ | — | — | ✓ | — | — | limited¹ | — |
| instructor | ✓ | — | ✓ / limited² | — | limited² | — | ✓ | ✓ |
| maintenance | ✓ | ✓ | ✓ | — | ✓ | ✓ | if `is_pilot` | — |
| viewer | ✓ | — | ✓ | — | — | — | — | — |

**Notes:**
1. Student flight logging may require instructor sign-off; entries are marked pending until countersigned.
2. Instructor maintenance access is configurable per tenant — typically read-full for training aircraft; write access optional.

---

## Maintenance view levels

The system returns one of two data shapes depending on the user's permission:

**`MaintenanceFullDTO`** — returned for admin, owner, maintenance, and viewers with `read_maintenance_full`:
- All trigger and record fields, including serial numbers, component IDs, and free-text notes.

**`MaintenanceLimitedDTO`** — returned for pilots and students with `read_maintenance_limited`:
- Open and active items only (no closed/archived history).
- Serial numbers, component IDs, and notes flagged sensitive are scrubbed.
- The user can see that a maintenance item exists and when it is due, but not the detailed service history.

---

## Flight logging rules

**Managed aircraft** (aircraft registered in the tenant):
- Requires `write_logbook` permission for that aircraft (from `AircraftAccess` or aircraft-owner match).
- Creates both an aircraft logbook entry and a personal pilot logbook entry when `is_pilot = true`.
- Logging on behalf of another pilot requires instructor, owner, or admin permissions.
- Student entries may require instructor sign-off (configurable per tenant).

**External aircraft** (not managed in the tenant):
- Available to users with `is_pilot = true`, regardless of `AircraftAccess`.
- Creates a personal pilot logbook entry only; no aircraft record is created.

---

## Authorization service (planned API)

```python
AuthorizationService.can(user, action, aircraft=None) -> bool
AuthorizationService.allowed_view(user, action, aircraft=None) -> DTO | False
```

**Actions:** `view_aircraft`, `edit_aircraft`, `view_maintenance`, `edit_maintenance`,
`log_flight`, `reserve_aircraft`, `assign_aircraft_access`, `instructor_signoff`.

---

## Relationship to Phase 26 (Flying School)

The student and instructor profile types are defined in Phase 23 (data model only) and fully activated in **Phase 26 — Flying School**:

- Instructor assignment per aircraft, instructor sign-off workflow for student solo entries.
- Student progress tracking, training programme targets, dual-instruction flight records.

See the [implementation plan](implementation_plan.md#phase-26--flying-school) for details.
