# OpenHangar — Demo Mode: Design & Architecture

This document describes the design decisions behind the demo mode: how visitors are isolated,
how the home page behaves, and the security model.

**For deployment and operational instructions, see [`demo/README.md`](../demo/README.md).**

---

## Goals

- Anyone can try the full app without creating an account.
- One visitor's actions (deleting a plane, wiping flights) cannot affect another visitor.
- The instance always looks "lived-in" — rich seed data on every restart.
- The database is wiped and reseeded on a fixed schedule so it never accumulates junk.

---

## Home page behaviour in demo mode

The normal three-state routing logic (`landing → welcome → dashboard`) is
overridden in demo mode:

| State | Normal behaviour | Demo behaviour |
|---|---|---|
| No users in DB | Landing page | *(never happens — seed runs on startup)* |
| Users exist, not logged in | Welcome-back page | **Landing page** |
| Logged in | Dashboard | Dashboard |

The landing page is always shown to unauthenticated visitors so the demo CTA
is always visible, regardless of whether users exist in the database.

---

## Visitor isolation — demo slot pool

Rather than a single shared account, the demo maintains **N isolated slots**
(default: 20). Each slot is a complete, independent tenant with its own set of
aircraft, flights, maintenance records, etc.

### First visit (no session)

1. The app shows the **landing page** with a **"Try the demo"** button.
2. Clicking it calls `POST /demo/enter`, which assigns a free slot (least-recently-used)
   and stores its ID in `session["demo_slot_id"]`, then sets `session["user_id"]` to that
   slot's demo user.
3. The visitor is redirected directly to the dashboard — **no login form ever shown**.

### Logout

- `session["user_id"]` is cleared (as usual).
- `session["demo_slot_id"]` is **preserved** — the visitor keeps their slot.
- The visitor is redirected to the landing page (not the welcome page).

### Returning after logout

If a visitor with a `demo_slot_id` in their session clicks "Try the demo"
again, `/demo/enter` recognises the existing slot and restores `session["user_id"]`
for it — **no new slot is allocated**. The visitor continues exactly where
they left off until the next 3-hour wipe.

### After a wipe

The DB wipe invalidates all slot user IDs. On the next `/demo/enter` call, the
stale `demo_slot_id` is not found in the DB, so a fresh slot is assigned and
`demo_slot_id` is updated in the session.

---

## Demo mode restrictions

When `FLASK_ENV=demo` the following actions are blocked (HTTP 403 or redirect
with a flash message):

| Blocked action | Reason |
|---|---|
| Create a new user / setup wizard | Prevents polluting the slot pool |
| Change password | Prevents locking other visitors out |
| Add / remove TOTP | Same reason |

Everything else (add aircraft, log flights, record maintenance, etc.) works
normally within the visitor's own slot.

---

## Demo seed

The demo seed is a superset of the dev seed, multiplied across all N slots.
Each slot gets the same rich dataset defined in `backend/app/_seed_helpers.py` —
the single source of truth for fleet data shared by both `dev_seed.py` and `demo_seed.py`.
Enriching the dev seed automatically enriches every demo slot.

Each slot contains:
- One tenant (`Demo Hangar #N`) and one demo user (no TOTP, password never surfaced)
- 3 aircraft covering all dashboard states: OVERDUE, DUE SOON, and OK

---

## Pre-wipe banner

The app tracks the timestamp of the last login per demo slot via `DemoSlot.last_activity_at`.

If **any** slot had a logged-in visitor in the **last 20 minutes**, the app injects
a dismissible banner at the top of every page:

> ⚠ The demo database resets in **X h Y min Z s**. Any changes you make will be lost.

The countdown is calculated client-side from `DEMO_NEXT_WIPE_UTC`, which is
written to the `.env` file by `demo/refresh.sh` before each wipe.

---

## Security considerations

- Demo users have no real passwords surfaced to the UI.
- All demo data is entirely synthetic — no real aircraft registrations,
  real serial numbers, or real personal data.
- The demo instance runs in a dedicated Docker container with no access to
  production data.
