# Aircraft detail page — layout & access by role

This document is the authoritative reference for **which sections appear** on the
aircraft detail page and **in what order**, depending on the viewer's role.
It is the design spec; the template (`app/templates/aircraft/detail.html`) must
stay consistent with it.

---

## Role → template flag mapping

| Role | `is_owner` | `is_pilot` | `is_maint` | `is_crew` |
|------|:----------:|:----------:|:----------:|:---------:|
| Admin / Owner | ✓ | ✓ | ✓ | ✓ |
| Pilot / Renter | | ✓ | | ✓ |
| Instructor | | ✓ | | ✓ |
| Maintenance | | | ✓ | ✓ |
| Student | | | | ✓ |
| Viewer | | | | |

> Note: the code sets `is_maint = true` for Instructor at the flag level, but
> for layout purposes Instructors are treated identically to Pilot/Renter.
> Students are currently treated the same as Pilot/Renter; a dedicated student
> layout is deferred to a later phase.

---

## Grounding elements

The following conditions ground the aircraft and are **always surfaced
prominently** regardless of role, via the red alert banner at the top of the
page:

- One or more unresolved **grounding snags**
- **Insurance expired**
- *(planned)* A maintenance trigger that has reached "overdue" status on a
  life-limited component should also be treated as a grounding condition

---

## Visibility matrix

| Section | Owner/Admin | Pilot / Renter / Instructor / Student | Maintenance | Viewer |
|---------|:-----------:|:-------------------------------------:|:-----------:|:------:|
| Grounded banner | ✓ | ✓ | ✓ | ✓ |
| Active Known Points | ✓ log + resolve | ✓ log + resolve | ✓ log + resolve | read-only |
| Insurance | ✓ edit | ✓ | ✓ | ✓ |
| Maintenance | ✓ full | ✓ full read | ✓ full | ✓ full read |
| Components | ✓ full (edit) | **text summary only** | ✓ full (read) | hidden |
| Recent Flights | ✓ log | ✓ log | read-only | read-only |
| Reservations | ✓ manage | ✓ new | hidden | read-only |
| Mass & Balance | ✓ configure + calc | ✓ calc | read-only | read-only |
| Expenses | ✓ | hidden | hidden | hidden |
| Documents | ✓ upload | read-only | read-only | read-only |
| Share Links | ✓ | hidden | hidden | hidden |

### Component display detail

- **Owner/Admin** — full component cards: make/model, S/N, time at install,
  installed/removed dates, logbook and edit actions.
- **Maintenance** — same cards, read-only (no edit/delete buttons).
- **Pilot / Renter / Instructor / Student** — compact text-only summary per
  type group, e.g. *"Lycoming IO-360-L2A (installed 2021-03-15)"*.  Useful
  when consulting the POH for performance data.  No S/N, no actions.
- **Viewer** — components section hidden entirely.

---

## Section order by profile

### Owner / Admin

| # | Left column (`col-lg-6`) | Right column (`col-lg-6`) |
|---|--------------------------|---------------------------|
| 1 | Active Known Points | Maintenance |
| 2 | Components | *(full width)* |
| 3 | Recent Flights | Reservations |
| 4 | Mass & Balance | Expenses |
| 5 | Insurance | Documents |
| 6 | Share Links | *(full width)* |

### Pilot / Renter / Instructor / Student

| # | Left column (`col-lg-6`) | Right column (`col-lg-6`) |
|---|--------------------------|---------------------------|
| 1 | Active Known Points | Insurance *(or full-width if no insurance set)* |
| 2 | Mass & Balance | Reservations |
| 3 | Recent Flights | Maintenance |
| 4 | Components summary | *(full width)* |
| 5 | Documents | *(full width)* |

### Maintenance

| # | Left column (`col-lg-6`) | Right column (`col-lg-6`) |
|---|--------------------------|---------------------------|
| 1 | Active Known Points | Maintenance |
| 2 | Components | *(full width)* |
| 3 | Recent Flights | *(full width)* |
| 4 | Insurance | Documents |

### Viewer

| # | Left column (`col-lg-6`) | Right column (`col-lg-6`) |
|---|--------------------------|---------------------------|
| 1 | Active Known Points | Maintenance |
| 2 | Recent Flights | Reservations |
| 3 | Insurance | Documents |

---

## Implementation notes

- The Jinja template uses `current_role` (the raw `Role` enum value) alongside
  the boolean flags to drive section order.  A top-level `{% if %}` block
  selects which layout to render; the sections themselves are shared macros to
  avoid HTML duplication.
- Sections that are completely hidden for a role are **not fetched** from the
  database in the route — queries for expenses, share tokens, and full component
  data should be skipped when the role does not need them.
- This document should be updated whenever a section is added, removed, or
  re-ordered on the detail page.
