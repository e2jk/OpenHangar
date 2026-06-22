# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

## Navigation: native-app-feel for the 4 bottom-nav pages

Page navigation currently triggers a full browser cycle: the server renders the
page, the browser re-parses CSS, re-runs all scripts, and repaints everything.
This is felt most on mobile (especially in the installed PWA) but applies to
desktop too. Two complementary layers fix this without duplicating templates or
building a SPA — and both work in any modern browser regardless of whether the
PWA is installed.

**Layer 1 — Service Worker stale-while-revalidate (eliminates network latency)**

The existing PWA service worker is extended to cache the 4 bottom-nav routes
(e.g. `/`, `/aircraft/`, `/flights/`, `/pilots/`). On every tap:
- The SW returns the cached HTML immediately (~0 ms).
- In the background it fetches a fresh copy from the server and updates the
  cache for the next visit.

No template changes required. The server always renders full pages as today.

**Layer 2 — HTMX `hx-boost` (eliminates navigation overhead)**

Adding `hx-boost="true"` to `<body>` (one attribute in `base.html`) causes
HTMX to intercept all link clicks:
- Instead of a full browser navigation, HTMX calls `fetch()` for the new URL.
- The SW intercepts that `fetch()` and returns the cached HTML instantly.
- HTMX swaps only the `<body>` content — navbar, bottom nav, and footer never
  re-render or flicker. CSS and JS stay loaded.
- `history.pushState` keeps the URL bar correct.

The result: tapping a bottom-nav link feels like switching tabs in a native app.

**Layer 3 — `<link rel="prefetch">` (pre-warms the cache)**

While the user is on one bottom-nav page, `<link rel="prefetch">` tags for the
other three are emitted so the browser fetches and caches them during idle time.
When HTMX makes its `fetch()` call, the SW already has a fresh copy.

**Layer 4 — ETag / 304 (optional refinement)**

Once the SW is serving from cache, the main fetch never reaches the server.
ETags matter only for the background revalidation leg — the SW sends
`If-None-Match` and gets a 304 (empty body) if nothing changed. Lower priority
than the above three layers.

**Prerequisites:**

- **Move all page-specific inline `<script>` blocks to external `.js` files.**
  This is required, not optional: when HTMX fetches page B and swaps its
  `<body>` into page A's DOM, inline scripts in that body carry page B's
  per-request nonce. The active CSP is still from page A (different nonce), so
  those scripts silently fail. External files are covered by `script-src 'self'`
  and always execute regardless of which page initiated the fetch.

  Dynamic Jinja2 data (template variables, translated strings) that currently
  lives inside inline scripts must be passed to external JS via a non-executable
  JSON block — the browser never runs these, so no nonce is required:
  ```html
  <script type="application/json" id="page-data">
    {"flightId": {{ flight.id }}, "label": {{ _('Review') | tojson }}}
  </script>
  ```
  The external JS reads it with:
  ```javascript
  const data = JSON.parse(document.getElementById('page-data').textContent);
  ```

**Implementation notes:**

- HTMX is loaded as a static file (`/static/js/htmx.min.js`). Only `hx-boost`
  is used; no `hx-on:*` or eval-dependent features that would conflict with CSP.
- After an HTMX body swap, Bootstrap components (modals, dropdowns, tooltips)
  that attach to DOM elements need re-initialisation; listen for the
  `htmx:afterSwap` event and call `bootstrap.Tooltip.getOrCreateInstance(…)`
  etc. for elements in the new content.
- The bottom-nav "active" link highlight must be updated after each swap;
  HTMX fires `htmx:pushedIntoHistory` with the new URL, which can be used to
  toggle the `active` class on the correct nav item.
- The View Transitions API (`document.startViewTransition(…)`) can be wired
  into the `htmx:beforeSwap` / `htmx:afterSwap` events for a smooth animated
  cross-fade on Chrome/Android and Safari 18+. Optional progressive enhancement.
- **Disable the service worker in development.** The SW registration script in
  `base.html` should be conditional on `not config.DEBUG` so that template and
  CSS changes are visible immediately without clearing browser storage:
  ```html
  {% if not config.DEBUG %}
  <script nonce="{{ csp_nonce() }}">
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
  </script>
  {% endif %}
  ```
  In production, `sw.js` should include a version constant at the top. Bumping
  it on each release forces browsers to install the new SW and flush stale
  cached HTML — without this, users could be stuck on old page structure after
  an upgrade.

**Browser support:**

| Feature | Chrome/Brave | Firefox | Safari |
|---------|-------------|---------|--------|
| HTMX `hx-boost` | ✅ | ✅ | ✅ |
| Service Worker | ✅ | ✅ | ✅ |
| `<link rel="prefetch">` | ✅ | ✅ | ✅ |
| View Transitions (optional) | ✅ | ⚠️ partial | ✅ 18+ |

Why deferred: requires adding HTMX as a JS dependency and extending the service
worker caching strategy. The improvement benefits all browsers and all form
factors, but is most felt on mobile where the installed PWA raises native-app
expectations. Worth implementing once the core feature set is stable.

---

## Config page: one-click upgrade trigger

Allow the instance admin to trigger a version upgrade from the config page,
without needing shell access to the host.

**Proposed mechanism — trigger file on a shared volume:**

1. The admin clicks "Upgrade to new version" on the config page.
2. The Flask app writes a small JSON file to a well-known path inside a
   dedicated Docker volume mount, e.g. `/upgrade/trigger`:
   ```json
   {"triggered_by": "admin@example.com", "triggered_at": "2026-06-22T10:00:00Z"}
   ```
3. A host-side cron job (e.g. every minute) runs a watcher script:
   - Checks for `/host/path/to/upgrade/trigger`.
   - **Atomically renames** it to `trigger.running` before doing anything else
     (prevents a second cron tick from double-triggering).
   - Runs the upgrade sequence: backup → `docker compose pull` →
     `docker compose up -d --force-recreate`.
   - Writes a result file (`/upgrade/result`) with exit code + timestamp so
     the app can display the outcome on next page load.
   - Logs the full output to `/upgrade/upgrade.log` for audit purposes.
4. Immediately after writing the trigger file, the Flask response redirects
   back to the config page with a flag that activates an **"upgrade in
   progress" banner** (reuse the same full-width alert style as the demo
   wipe banner in `base.html`):
   - Banner text: "Upgrade in progress — please do not refresh this page.
     The application will reload automatically when it is back online."
   - A JS `pollUntilReady()` loop (same pattern as `demo_wipe_banner`'s
     `pollUntilReady`) pings `/health` every 5 s.
   - While the container is being recreated the health endpoint will be
     unreachable; `fetch` will reject and the loop retries.
   - Once `/health` responds with HTTP 200 again, `window.location.reload()`
     is called — the user lands on the freshly upgraded config page.
   - If the poll runs for more than 5 minutes without a successful response,
     the banner switches to an error state: "Upgrade is taking longer than
     expected — check the host upgrade log."

**Why this approach rather than alternatives:**
- **Docker socket** (`/var/run/docker.sock`): mounting it into the app
  container grants effective root on the host — a compromised app becomes a
  full host compromise. Not acceptable.
- **Watchtower**: auto-pulls on a schedule, no admin intent involved.
  Unsuitable for a security-conscious self-hosted deployment.
- **Webhook from CI**: requires the host to be publicly reachable; adds
  external dependency.
- **SSH key in the container**: wider blast radius than a trigger file; a
  leaked key can run arbitrary host commands.

The trigger-file approach limits the blast radius to "the host-side watcher
script runs," which already has the minimum privilege needed (docker group).

**Security analysis:**

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Only admins can write the trigger file | Low | Flask route must be gated behind `@require_role("admin")` — same as all other config-page actions |
| Symlink attack on the trigger path | Low | Watcher script should check `os.path.isfile()` (not follow links) and verify the file is owned by the expected UID before acting |
| Double-trigger (two cron ticks overlap) | Low | Atomic rename (`trigger` → `trigger.running`) before the upgrade starts; watcher exits immediately if `trigger.running` already exists |
| Upgrade leaves service down | Medium | Watcher writes `result` file; add a health-check retry loop before declaring success; optionally keep the previous image tag for rollback |
| Malicious content in trigger file | Low | Watcher ignores the file's content entirely — only its existence matters; the JSON is informational only |
| Unprivileged process on host reads the volume path | Low | Host mount directory should be `chmod 700`, owned by the Docker-running user |
| Admin triggers upgrade during active use | Low | Config page should warn "the service will restart and active sessions will be lost"; optionally show active-session count |
| Upgrade pulls a tampered image | Medium | Consider pinning to a digest (`image: ghcr.io/…@sha256:…`) or using cosign verification in the watcher script; out of scope for initial implementation |

**Watcher script skeleton (`upgrade-watcher.sh`):**
```bash
#!/usr/bin/env bash
set -euo pipefail
TRIGGER=/path/to/upgrade/trigger
RUNNING=/path/to/upgrade/trigger.running
RESULT=/path/to/upgrade/result
LOG=/path/to/upgrade/upgrade.log

[ -f "$TRIGGER" ] || exit 0
mv "$TRIGGER" "$RUNNING"           # atomic; second cron tick exits above

{
  echo "=== Upgrade triggered at $(date -u +%FT%TZ) ==="
  cd /path/to/docker-compose-dir
  docker compose pull
  docker compose up -d --force-recreate
  echo "EXIT:0"
} >> "$LOG" 2>&1 && echo "ok $(date -u +%FT%TZ)" > "$RESULT" \
                 || echo "fail $(date -u +%FT%TZ)" > "$RESULT"

rm -f "$RUNNING"
```

**docker-compose addition:**
```yaml
volumes:
  - ./upgrade:/upgrade   # host dir, writable by app, readable by watcher
```

Why deferred: requires host-side setup (cron entry, watcher script, volume
path) documented for deployers; the current upgrade path (pull + recreate by
hand) is adequate until there is demand for one-click upgrades.

---

## Pilot logbook: FSTD / simulator sessions

EASA AMC1 FCL.050 includes a dedicated column 10 for synthetic training device
(FSTD / simulator) sessions. These sessions are currently logged in the Remarks
field only.

Design notes (Option A — flag + fields on the existing model):
- Add `entry_type` (string, `"flight"` / `"fstd"`, default `"flight"`) to
  `PilotLogbookEntry`. When `"fstd"`, the row is a simulator session.
- Add `fstd_type` (nullable string: `FFS` / `FTD` / `FNPT` / `BITD` / `AATD`)
  and `fstd_duration` (Numeric 4,1, nullable) to the same table. Exercises stay
  in the existing `remarks` field — no additional column needed.
- FSTD rows are **excluded from all flight-time totals** (`single_pilot_se`,
  `single_pilot_me`, `multi_pilot`, etc.) — they are not flight hours.
- The logbook footer accumulates FSTD time separately in its own "Sim" column,
  matching the EASA logbook layout where column 10 runs alongside columns 1–9.
- Flight-specific fields (aircraft, counters, dep/arr airports) are left NULL
  for FSTD rows; the entry form shows/hides fields based on `entry_type`.
- FSTD entries appear inline in the chronological logbook list (same table),
  rendered in a distinct visual style to distinguish them from flight entries.

---

## Pilot logbook: timezone detection from ICAO airfield location

Counter photo EXIF timestamps are in local time; OpenHangar currently converts
them to UTC using the browser's reported timezone offset. This is unreliable for:
- Flights that cross a timezone boundary (departure and arrival in different zones).
- Pilots entering data from a different location than where they flew.

Future enhancement: look up the UTC offset for the departure and arrival ICAO
codes using a timezone-by-coordinates database (e.g. `timezonefinder` Python
library against the OurAirports dataset). Use the departure airfield timezone
to convert the EXIF timestamp to UTC, and flag if departure and arrival timezones
differ so the pilot can confirm.

---

## Logbook: OCR auto-fill from counter photos

When a pilot uploads a photo of their instrument panel at the end of a flight
(engine time counter + flight time counter), automatically extract:

- The counter readings (end values for both timers)
- The photo timestamp (from EXIF metadata) — used to derive arrival time (UTC,
  floored to nearest 0.1 h) and from there departure time and full flight times

Approach:
- EXIF timestamp extraction is straightforward (no ML needed) and is implemented
  in Phase 15 as the first step.
- When EXIF tags are absent (some phones strip metadata, or photos are
  transferred via messaging apps that scrub EXIF), the original filename often
  contains a timestamp — e.g. `IMG_20240615_173842.jpg` or
  `2024-06-15 17.38.42.jpg`. Parsing common filename patterns is a low-effort
  fallback that should be attempted before giving up on auto-fill.
- Counter value extraction requires either OCR (e.g. Tesseract, or a vision
  API such as Claude's image understanding) or a dedicated instrument-reading
  model. The analogue dial format of many tach timers makes this non-trivial.
- Photo upload should always be encouraged but never strictly mandatory — pilots
  flying without a smartphone, or in areas without connectivity, must still be
  able to log a flight manually.

Why deferred: requires either a cloud vision API dependency or a self-hosted OCR
pipeline; the UX for correcting mis-reads also needs careful design. Good
candidate for a standalone phase once the core logbook is stable.

---

## Pilot logbook: opt-in sharing with instructors / admins

By default a pilot's logbook and currency data are private to the holder.
A future enhancement would let the pilot opt in to sharing a limited view
with designated users (flight school administrator, instructor, club safety
officer).

Design notes:
- A per-pilot checkbox in the Pilot Profile: "Share my logbook summary with
  admins and instructors in this organisation" — unchecked by default.
- When checked, admins/instructors see a read-only summary: total hours per
  category, currency check results (green/yellow/red), and medical/SEP expiry
  status. Full logbook entries (individual flights, remarks) remain private.
- The setting is revocable by the pilot at any time; revoking it immediately
  removes the shared view for all other users.
- This is a prerequisite for a multi-pilot currency matrix in the flight school
  context — do not implement the matrix view until this consent mechanism exists.

Why deferred: requires the multi-user phase (Phase 18) to land first so the
role model (admin / instructor) is stable, and needs careful GDPR review before
exposing any personal health data (medical expiry) to other users.

---

## Logbook: counter continuity discrepancy detection

Each flight entry's counter start values are pre-filled from the previous
flight's end values and are not directly editable in the UI. However, direct
database manipulation (or a future API call) could introduce a mismatch where
a flight's start value differs from the previous flight's end value, breaking
the continuity of the running total.

Future enhancement: scan all flight entries per aircraft and flag any entry
where `engine_time_counter_start` ≠ previous entry's `engine_time_counter_end`
(or same for flight time counter). Surface these discrepancies on the aircraft
detail page and in a dedicated admin view, requiring an explicit acknowledgement
or correction before the logbook can be considered complete.

---

## Email notifications: airworthiness digest (`AIRWORTHINESS_DIGEST`)

A new notification type that sends a weekly digest summarising the airworthiness
status across all aircraft in the tenant's hangar. Intended for owners and
maintenance roles who want a single consolidated view rather than individual
per-event alerts.

Proposed digest content:
- Pending or deferred airworthiness documents (AD/SB/ARC items not yet actioned)
- Upcoming ARC expiry dates (within the configured threshold)
- Stale open questions on the airworthiness tracker (no activity in N days)

Implementation sketch: add `NotificationType.AIRWORTHINESS_DIGEST` to the
enum and `_check_airworthiness_digest()` to `notification_service.py`, scheduled
to run weekly (e.g. every Monday) from the daily notification loop.  The daily
loop would check `weekday() == 0` before calling it, or the scheduler could be
extended to support weekly cadence.

Why deferred: the per-event airworthiness notifications (`AIRWORTHINESS_REVIEW_DUE`)
are the higher-value alert; the digest is a nice summary but requires the
airworthiness module to be more fully populated before it provides useful signal.

---

## Security log-watcher container (companion to in-process alerting)

The in-process `SecurityAlertHandler` (implemented in `app/security_alerts.py`)
cannot fire if the app crashes or is killed. A complementary log-watcher
container covers that gap.

**Approach — shared log volume (no Docker socket required):**

The app writes security events to `/logs/security.log` (volume-mounted). A
minimal sidecar container tails the file and fires alerts via the same
`OPENHANGAR_ALERT_*` env vars when it detects a `[SECURITY]` line.

```yaml
volumes:
  - ./logs:/logs          # shared between openhangar and log-watcher

log-watcher:
  image: python:3.14-slim
  volumes:
    - ./logs:/logs
  environment:
    - OPENHANGAR_ALERT_NTFY_TOPIC_URL
    - OPENHANGAR_ALERT_EMAIL_TO
    - OPENHANGAR_ALERT_WEBHOOK_URL
    - OPENHANGAR_SMTP_HOST
    # ... other OPENHANGAR_SMTP_* vars
```

**Why not Docker socket?** Mounting `/var/run/docker.sock` gives the sidecar
effective root on the host — too high a price for a log-watching use case.

**Prerequisite:** implement the in-process handler first and validate the alert
channels work end-to-end. The watcher is a follow-up hardening step.

---

## UI: show a degraded-JS warning banner

Some browser extensions (e.g. Privacy Badger, uBlock Origin in strict mode)
inject scripts or block requests in ways that trigger CSP violations, which can
silently break JS features without any visible feedback to the user.

Desired behaviour: display a non-intrusive banner like "Some features may not
work correctly — a browser extension may be interfering" if the page detects
that JS is degraded. The banner must not flicker on normal page loads.

Implementation sketch (no-flicker approach):
- In the very first inline `<script>` in `<head>` (the theme-init script, which
  already runs before any extension can block later scripts), set
  `document.documentElement.setAttribute('data-js-ok', '1')`.
- Add a hidden `<div id="js-warn-banner">` to `base.html` immediately after
  `<body>`, before any other content.
- CSS rule: `html:not([data-js-ok]) #js-warn-banner { display: block; }` — the
  banner only appears if the attribute was never set, i.e. the first script was
  itself blocked (no-JS or very aggressive blocker).
- For extension-caused mid-page CSP violations (the more common case), a
  `window.addEventListener('securitypolicyviolation', ...)` handler could
  reveal the banner at runtime without flickering.

---

## Config page: show number of releases behind when an update is available

Currently the update badge shows only the latest version number. It would be
more informative to say "3 releases behind" so the admin knows roughly how much
they have missed.

**Preferred implementation — static `versions.json` on GitHub Pages:**

A CI step (triggered on every release) publishes a small JSON file to GitHub
Pages, e.g.:

```json
["2.6.0", "2.5.1", "2.5.0", "2.4.0", ...]
```

The app fetches this URL instead of the GitHub Releases API. Counting versions
ahead of `current_version` is then a simple list comparison — no pagination, no
API rate-limit concerns, no authentication required.

**Alternative — GitHub Releases API with `?per_page=100`:**

A single call to `/repos/e2jk/OpenHangar/releases?per_page=100` returns up to
100 releases without pagination. Simpler CI-wise (no Pages setup), but depends
on the GitHub API and rate-limits (60 unauthenticated req/hour). Sufficient
until the project has more than 100 releases.

**Not recommended — GHCR tag listing:** requires an authenticated API call
even for public packages.

Either way, store the result in an `AppSetting` (`versions_behind`) and display
it alongside the existing update badge on the config page.

---

## GIF export: download all formats at once

Add a "Download all formats" option to the GIF export modal that triggers all
four variants (landscape/portrait × low-res/high-res) sequentially, without
requiring the user to open the modal four times.

Two delivery approaches to decide between when implementing:
- **Sequential blob downloads**: JS fetches each variant one at a time and
  triggers a `<a download>` save for each. Simple to implement, no new server
  endpoint, but results in 4 files landing in the browser's download folder.
  A progress indicator ("Generating 2 / 4…") on the trigger button would be
  needed to avoid the UI looking frozen during the slow high-res renders.
- **Server-side ZIP**: a new `/gif/all.zip` endpoint generates all four variants
  and streams them in a `zipfile`. Cleaner single-file download, but adds
  backend complexity and a longer wait before anything arrives.

---

## Demo: dynamic slot expansion

When all demo slots are busy (current behaviour: show a "demo full" page with HTTP 503),
automatically create additional slots on demand instead of turning visitors away.

Possible approach:
- When the LRU slot is still warm, provision N extra slots (e.g. 20 more) by calling the
  same `seed_fleet()` helper used by the regular seed.
- Track dynamically-created slots separately so the wipe/refresh script can clean them up
  without disturbing the base pool.
- Cap total slots via a `DEMO_MAX_SLOTS` env var to avoid unbounded growth under traffic spikes.

Why deferred: 20 concurrent demo users is generous for current traffic levels, and the
added complexity (variable slot counts, wipe-script changes, cap enforcement) is not
justified yet.

---

## Email: inbound email processing

Receiving email into OpenHangar would enable use-cases such as:
- Invoices forwarded directly into cost tracking
- AD/STC notifications forwarded from airworthiness bodies auto-linked to
  the relevant aircraft or component

Two implementation approaches; the choice should be made when the use-cases
are better defined:
- **Self-hosted MTA** (e.g. Postfix + procmail): no external dependency, but
  adds significant operational complexity to a self-hosted deployment.
- **Transactional mail provider webhook** (e.g. Mailgun inbound parse,
  SendGrid inbound parse): simpler integration, but introduces an external
  service dependency and requires a publicly reachable endpoint.

Why deferred: the use-cases are not yet well-defined enough to make the
architecture decision; outbound email (Phase 14) must be stable first.

---

## Native mobile app

Phase 40 adds a PWA with camera capture and offline queuing, which covers the
main mobile use-cases (quick flight entry, Hobbs photo, offline ramp use) with
no second codebase.

A native app (React Native or Flutter) would only add meaningful value if two
conditions are met:

1. **Background push notifications** — Phase 34 email notifications are the
   current channel; native push requires APNs/FCM integration and app store
   distribution, which is a significant ongoing maintenance burden.
2. **Deep offline** — the IndexedDB sync queue planned in Phase 40 should cover
   typical connectivity gaps; native SQLite would only matter for extended
   offline periods unlikely in an aviation context.

Prerequisite: Phase 40 (PWA + offline sync) should ship first. Re-evaluate
after real-world usage reveals whether the PWA gaps are felt in practice.
