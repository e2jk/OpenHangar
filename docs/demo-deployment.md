# OpenHangar — Demo Deployment

This document describes how the public demo instance works: what it does,
how it is seeded, how visitors are isolated from each other, and how the
host server keeps it fresh.

The demo is activated by setting `FLASK_ENV=demo`.

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
2. Clicking it calls `POST /demo/enter`, which assigns a free slot and stores
   its ID in `session["demo_slot_id"]`, then sets `session["user_id"]` to that
   slot's demo user.
3. The visitor is redirected directly to the dashboard — **no login form ever
   shown**.

### Logout

- `session["user_id"]` is cleared (as usual).
- `session["demo_slot_id"]` is **preserved** — the visitor keeps their slot.
- The visitor is redirected to the landing page (not the welcome page).

### Returning after logout

If a visitor with a `demo_slot_id` in their session clicks "Try the demo"
again, `/demo/enter` recognises the existing slot and restores `session["user_id"]`
for it — **no new slot is allocated**.  The visitor continues exactly where
they left off until the next 3-hour wipe.

### After a wipe

The DB wipe invalidates all slot user IDs.  On the next `/demo/enter` call, the
stale `demo_slot_id` is not found in the DB, so a fresh slot is assigned and
`demo_slot_id` is updated in the session.

Slots are not permanently locked to a visitor — after the 3-hour wipe every
visitor starts fresh with a fully seeded slot.

---

## Demo mode restrictions

When `FLASK_ENV=demo` the following actions are blocked (return HTTP 403 or
silently redirect with a flash message):

| Blocked action | Reason |
|---|---|
| Create a new user / setup wizard | Prevents polluting the slot pool |
| Change password | Prevents locking other visitors out |
| Add / remove TOTP | Same reason |
| Export or download any data | Not meaningful in a demo |

Everything else (add aircraft, log flights, record maintenance, etc.) works
normally within the visitor's own slot.

---

## Demo seed

The demo seed is a superset of the dev seed, multiplied across all N slots.
Each slot gets:

- One tenant (`Demo Hangar #N`)
- One demo user (`demo-N@openhangar.demo`, no TOTP, password not surfaced)
- The same rich dataset as the dev seed: 2 aircraft, components, flights,
  maintenance triggers in all three states (OK / due soon / overdue)

The seed is run by `backend/app/demo_seed.py`, called by the entrypoint when
`FLASK_ENV=demo` (instead of the regular dev seed).

The dev seed and demo seed share the aircraft/flight/maintenance helper
functions from a common `_seed_helpers.py` module to avoid duplication.

---

## Pre-wipe banner

The app tracks the timestamp of the last login per demo slot in a lightweight
way (a `DemoActivity` table or a Redis key — TBD at implementation time).

If **any** slot had a logged-in visitor in the **last 20 minutes**, the
application injects a dismissible banner at the top of every page:

> ⚠ The demo database resets in **X minutes**. Any changes you make will be lost.

The countdown is calculated from the next scheduled wipe time, which is stored
in a server-side environment variable (`DEMO_NEXT_WIPE_UTC`) updated by the
refresh script.

---

## Refresh script — `demo/refresh.sh`

Run by cron every 3 hours on the host. Steps:

```
1. Check Docker Hub for a newer image digest than the currently running one.
   - Uses: docker pull --quiet ghcr.io/e2jk/openhangar:latest
   - Compares image ID before/after pull.
2. If a newer image was pulled, rebuild the container (docker compose up -d --build).
   Otherwise, only wipe the DB.
3. Set DEMO_NEXT_WIPE_UTC env var to (now + 3h) in the compose .env file.
4. Run the demo seed inside the container:
     docker exec openhangar-app flask seed-demo
5. Log the wipe timestamp.
```

The script is idempotent — safe to run manually at any time.

### Suggested cron entry (host)

```cron
# Wipe and refresh demo every 3 hours, offset to avoid the :00 spike
7 */3 * * * /opt/openhangar/demo/refresh.sh >> /var/log/openhangar-demo.log 2>&1
```

---

## Docker Hub publishing

A GitHub Actions workflow (`.github/workflows/publish.yml`) builds and pushes
the image to Docker Hub on every merge to `main`:

```
ghcr.io/e2jk/openhangar:latest
ghcr.io/e2jk/openhangar:<git-sha>
```

The refresh script always pulls `:latest`, so the demo automatically picks up
new releases within 3 hours of a merge.

---

## Environment variables for demo mode

| Variable | Purpose |
|---|---|
| `FLASK_ENV` | Set to `demo` to activate demo mode |
| `DEMO_SLOT_COUNT` | Number of isolated visitor slots (default: `20`) |
| `DEMO_NEXT_WIPE_UTC` | ISO-8601 timestamp of next scheduled wipe; written by refresh script |

---

## What the landing page shows in demo mode

The landing page (`landing.html`) is always shown to unauthenticated visitors
when `FLASK_ENV=demo`.  The existing "Get Started" CTA (which normally points
to `/setup`) is replaced by the demo entry block:

```
┌─────────────────────────────────────────┐
│  👋  This is a live demo.               │
│                                         │
│  You get your own private sandbox.      │
│  No sign-up needed.                     │
│                                         │
│  [  Try the demo  ]                     │
│                                         │
│  ⚠ Resets every 3 hours — next reset   │
│    in 2 h 41 min.                       │
└─────────────────────────────────────────┘
```

The countdown is rendered server-side from `DEMO_NEXT_WIPE_UTC`.

The "Try the demo" button posts to `/demo/enter`.  There is no login form,
no username, no password — the visitor is logged in silently and lands on
the dashboard.

After logout, the visitor is returned to this same landing page.  The "Try
the demo" button reappears and clicking it restores the same slot (until the
next wipe).

---

## Security considerations

- Demo users have no real passwords surfaced to the UI.
- The slot assignment endpoint (`/demo/enter`) rate-limits by IP to prevent
  slot exhaustion (one slot per IP per session).
- All demo data is entirely synthetic — no real aircraft registrations,
  real serial numbers, or real personal data.
- The demo instance runs in a dedicated Docker network with no access to
  production data.
