# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

## HTMX regression tests — gaps in current coverage

The tests added in the HTMX hardening session cover body-swap navigation,
`htmx:afterSettle` re-init, history restore (Back/Forward), CSP violations,
and modal cleanup. The items below are the remaining high-risk gaps, ordered
by likelihood to catch a real silent regression.

All tests belong in `tests/e2e/test_htmx_boost.py`. Each item below is
self-contained and can be implemented in isolation.

### 5. No duplicate event listeners after repeated A→B→A navigation

If `data-oh-inited` is not properly reset after history restore, `init()` runs
twice on the same element, doubling the event listeners. The symptom is subtle:
a form `change` event triggers its callback twice (double API calls, double
flash messages). Proxy test: set a counter on `window`, navigate A→B→Back→B
and verify the counter incremented by exactly 1 per `init()` call.

**Test skeleton:**
```python
class TestNoDoubleEventListeners:
    def test_fuel_hint_updates_exactly_once_per_change(self, logged_in_page, live_server_url):
        page = logged_in_page
        # Full load → navigate away → Back → navigate away again → Back
        page.goto(f"{live_server_url}/aircraft/new")
        page.wait_for_load_state("networkidle")
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")
        page.go_back()
        page.wait_for_load_state("networkidle")
        # Instrument the textContent setter to count updates
        page.evaluate(
            "() => {"
            "  window.__hintUpdates = 0;"
            "  var el = document.getElementById('fuel_type_hint');"
            "  var orig = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');"
            "  Object.defineProperty(el, 'textContent', {"
            "    set(v) { window.__hintUpdates++; orig.set.call(this, v); },"
            "    get() { return orig.get.call(this); }"
            "  });"
            "}"
        )
        page.locator("#fuel_type").select_option("mogas")
        page.wait_for_function("() => window.__hintUpdates > 0", timeout=3000)
        updates = page.evaluate("() => window.__hintUpdates")
        assert updates == 1, (
            f"fuel hint textContent was set {updates} times on a single select change — "
            "duplicate event listeners detected (data-oh-inited guard failed on history restore)"
        )
```

### 6. Bootstrap tooltips cleaned up on body swap

Hovering a Bootstrap tooltip-enabled element appends a `.tooltip` div to
`<body>`. If the user navigates while a tooltip is visible, the `htmx:beforeSwap`
handler must remove it — otherwise it persists on the next page. Currently the
handler strips `.modal-backdrop` but not `.tooltip`.

**Fix needed in `ui.js`** before writing the test: add
`document.querySelectorAll('.tooltip').forEach(el => el.remove());`
alongside the `.modal-backdrop` removal in the `htmx:beforeSwap` handler.

**Test skeleton:**
```python
class TestTooltipCleanupOnNavigation:
    def test_tooltip_removed_before_htmx_swap(self, logged_in_page, live_server_url):
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")
        # Inject a Bootstrap tooltip div (simulates an open tooltip)
        page.evaluate(
            "() => {"
            "  var t = document.createElement('div');"
            "  t.className = 'tooltip show';"
            "  document.body.appendChild(t);"
            "}"
        )
        assert page.evaluate("() => !!document.querySelector('.tooltip')"), "Test setup failed"
        page.evaluate("() => document.querySelector('a.navbar-brand').click()")
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => !document.querySelector('.tooltip')"), (
            ".tooltip div survived hx-boost swap — htmx:beforeSwap cleanup missing for tooltips"
        )
```

### 7. URL bar correct after body swap

After an hx-boost navigation, `window.location.href` must reflect the new
page's URL. If `hx-push-url` is misconfigured or disabled, the address bar
stays on the old URL while the body shows the new page — breaking bookmarks,
refresh, and direct links.

**Test skeleton:**
```python
class TestUrlAfterBodySwap:
    def test_url_updates_to_new_page_after_htmx_navigation(self, logged_in_page, live_server_url):
        page = logged_in_page
        page.goto(f"{live_server_url}/aircraft/")
        page.wait_for_load_state("networkidle")
        page.evaluate("window.__sentinel = 'alive'")
        page.locator("a.navbar-brand").click()
        page.wait_for_url(f"{live_server_url}/", timeout=10000)
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => window.__sentinel === 'alive'"), "Not a body swap"
        assert page.url == f"{live_server_url}/", (
            f"URL did not update after hx-boost navigation — got {page.url!r}"
        )
```

### 8. External links and `target="_blank"` are not intercepted by hx-boost

`hx-boost="true"` on `<body>` intercepts ALL link clicks unless the link opts
out. External links and `target="_blank"` links must not be body-swapped —
the response would be an external HTML page rendered inside the app shell.
The sentinel should be destroyed (full navigation or new tab, not a swap).

**Test skeleton:**
```python
class TestExternalLinksNotIntercepted:
    def test_target_blank_link_not_intercepted_by_hxboost(self, logged_in_page, live_server_url):
        """A link with target=_blank must open in a new tab, not as a body swap."""
        page = logged_in_page
        page.goto(f"{live_server_url}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("window.__sentinel = 'alive'")
        # Find a target=_blank link (e.g. in the footer or help section)
        blank_links = page.locator("a[target='_blank']")
        if blank_links.count() == 0:
            pytest.skip("No target=_blank links found on the dashboard")
        # A new tab opens — the current page must not have been body-swapped
        with page.context.expect_page():
            blank_links.first.click()
        # Original page sentinel must survive (swap did not happen here)
        assert page.evaluate("() => window.__sentinel === 'alive'"), (
            "Sentinel destroyed — target=_blank link triggered a body swap "
            "instead of opening a new tab"
        )
```

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
