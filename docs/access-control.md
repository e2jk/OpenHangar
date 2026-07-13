# OpenHangar — Access Control & Permissions

This page describes the access-control model as implemented: roles,
per-user capability flags, per-aircraft permission masks, and how a user's
effective permissions are resolved.

---

## Roles

Each user is assigned exactly one role per tenant:

| Role | What they can do |
|---|---|
| **Admin** | Full access to everything; same as Owner plus system configuration |
| **Owner** | Full access to aircraft, flights, maintenance, documents, expenses, and reservations; can manage users and invitations |
| **Pilot** | Log flights, view own logbook, create reservations; no aircraft/component edits |
| **Maintenance** | View and update maintenance triggers and records, edit aircraft and components; no flight logging |
| **Viewer** | Read-only |
| **Student** | Like Pilot, but with a reduced default permission set; full student flows (instructor sign-off, supervised bookings) arrive with the Flying School phase |
| **Instructor** | Like Pilot, plus full maintenance read access; full instructor flows arrive with the Flying School phase |

---

## Capability flags

Independent of the role, each user carries three flags, editable from the
user management page:

- **`is_pilot`** — enables pilot-specific flows: personal logbook entries,
  reservations, pilot-level flight logging. An owner or maintenance user who
  also flies can enable this to unlock those flows without changing their role.
- **`is_maintenance`** — enables maintenance-specific flows: edit aircraft
  details and components, add and edit maintenance tasks.
- **`view_only`** — when true, suppresses **all** write capabilities regardless
  of role, flags, or any explicit permission mask.

---

## Per-aircraft access

Aircraft access is controlled per user through two kinds of grants:

| Grant | Meaning |
|---|---|
| Per-aircraft (`UserAircraftAccess`) | Access to one named aircraft |
| All aircraft (`UserAllAircraftAccess`) | Access to every existing aircraft **and any aircraft added in the future** in the tenant |

Each grant carries an optional **`permissions_mask`** bitmask. When the mask
is not set, the defaults for the user's role apply (see the matrix below).
An explicit mask overrides the role defaults in both directions — it can
grant more or restrict further.

The available permission bits:

| Bit | Action |
|---|---|
| `view_aircraft` | See the aircraft in lists and open its detail page |
| `edit_aircraft` | Edit registration, make/model, specs |
| `read_maintenance_full` | Read full maintenance records including intervals and service history |
| `read_maintenance_limited` | Read a scrubbed view: overdue and due-soon items only, no intervals, no service history |
| `write_maintenance` | Add and edit maintenance triggers and records |
| `edit_components` | Add, edit, and remove components |
| `write_logbook` | Log flights against this aircraft |
| `reserve_aircraft` | Create reservations for this aircraft |

### Resolution order

A user's effective permission mask for a given aircraft is resolved as:

1. **Admin bypass** — Admin role → all bits.
2. **Owner** — Owner role → all bits.
3. **All-aircraft grant** — if present, use its mask (role default when the
   mask is unset).
4. **Per-aircraft grant** — if present, use its mask (role default when the
   mask is unset).
5. **No grant** — for the remaining roles, no access row means **no access**
   to that aircraft. (Tenant-level checks that are not about a specific
   aircraft fall back to the role's default mask.)

Finally, if the user has `view_only`, all write bits (`edit_aircraft`,
`write_maintenance`, `edit_components`, `write_logbook`, `reserve_aircraft`)
are stripped from the resolved mask.

### Default permissions per role

Used whenever a grant exists without an explicit mask:

| Role | `view_aircraft` | `edit_aircraft` | `read_maintenance_full` | `read_maintenance_limited` | `write_maintenance` | `edit_components` | `write_logbook` | `reserve_aircraft` |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| admin | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| owner | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ |
| pilot | ✓ | — | — | ✓ | — | — | ✓ | ✓ |
| student | ✓ | — | — | ✓ | — | — | — | — |
| instructor | ✓ | — | ✓ | — | — | — | ✓ | ✓ |
| maintenance | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | — |
| viewer | ✓ | — | ✓ | — | — | — | — | — |

---

## Managing access from the UI

All of this is managed from **Configuration → Users** (admin/owner only):

- Role assignment and the `is_pilot` / `is_maintenance` / `view_only` toggles
  per user.
- A per-aircraft **permission editor**: a checkbox grid with one column per
  permission bit and quick-preset buttons for the common role profiles.
- A **"Grant access to all aircraft"** toggle that creates the all-aircraft
  grant described above.

---

## Maintenance view levels

Maintenance data is served at one of three levels depending on the resolved
permission:

- **Full** (`read_maintenance_full`) — all trigger and record fields,
  including intervals and complete service history.
- **Limited** (`read_maintenance_limited`) — overdue and due-soon items only;
  interval and service-history columns are hidden and a banner explains the
  reduced view. This is the default for pilots and students.
- **None** — neither read bit → no maintenance data.

---

## Flight logging rules

**Managed aircraft** (registered in the tenant):
- Requires the `write_logbook` bit for that aircraft.
- The unified flight form creates an aircraft logbook entry, and a personal
  pilot logbook entry when the user selects a pilot role (PIC / Dual) on the
  form.

**External aircraft** (not managed in this OpenHangar instance):
- Available to any user with pilot capability, regardless of aircraft grants.
- Creates a personal pilot logbook entry only; no aircraft record is created.

---

## Planned extensions

- **Student / instructor flows** — instructor sign-off on student solo
  entries, instructor-approved bookings, and student progress tracking are
  part of the Flying School phase (see the
  [implementation plan](implementation_plan.md)).
- The pilot logbook remains **private to the holder** in all cases; opt-in
  sharing with instructors/admins is tracked in [`backlog.md`](backlog.md).
