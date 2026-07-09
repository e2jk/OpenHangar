l# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

---

## PWA: Window Controls Overlay

Replace the browser's generic title bar in the installed standalone app with a
custom one, giving space for breadcrumbs, the aircraft selector, or a quick
"Log Flight" button where the title bar would otherwise be wasted chrome.

Implementation: add `display_override` to the manifest and handle the overlay
in CSS/JS.

**Manifest change** in `pwa_manifest()`:
```python
"display_override": ["window-controls-overlay", "standalone"],
"display": "standalone",   # fallback for browsers that don't support the override
```

**CSS** — the overlay exposes three env variables:
```css
.titlebar {
    position: fixed;
    top: env(titlebar-area-y, 0);
    left: env(titlebar-area-x, 0);
    width: env(titlebar-area-width, 100%);
    height: env(titlebar-area-height, 33px);
    -webkit-app-region: drag;   /* makes it draggable like a native title bar */
    app-region: drag;
}
.titlebar button, .titlebar a {
    -webkit-app-region: no-drag;
    app-region: no-drag;
}
```

**Detecting overlay mode** in JS (to show/hide the custom bar):
```js
if (navigator.windowControlsOverlay?.visible) {
    document.querySelector('.titlebar').hidden = false;
}
navigator.windowControlsOverlay?.addEventListener('geometrychange', () => {
    // re-layout if the overlay area changes (e.g. window resize)
});
```

Notes:
- Only supported on Chrome/Edge desktop; the `display_override` fallback chain
  means mobile and other browsers get normal `standalone` mode unchanged.
- The title bar content should be minimal and must be flagged with
  `hx-boost="false"` on any links if the rest of the page uses hx-boost, to
  avoid partial-page replacement of title bar content.

---

## PWA: Share Target — complete expense / maintenance / flight photo flows

The manifest `share_target`, `/pwa/shared` disambiguation page, and the
"aircraft document" upload flow are fully implemented in `app/pwa/routes.py`.

The three remaining destinations currently redirect to the relevant section
with a flash message; the shared file is not carried forward to the form.
To complete them, the shared file (stored in a temp dir, path in
`session["share_pending"]`) needs to be passed into each destination's upload
form. Approaches per destination:

- **Expense receipt** (`expenses.add_expense`): store the temp path in session;
  the expense add form picks it up as a pre-attached receipt image/PDF.
  Requires the expenses form to support a receipt attachment field first.
- **Maintenance record** (`maintenance.list_triggers`): same session-stash
  approach, pre-attaching to the service notes or a new attachment field.
- **Flight photo** (`flights.log_flight`): stash in session under a key like
  `share_flight_photo`; `/flights/new` reads it and pre-fills one of the
  counter photo inputs.

---

## PWA: File Handling

Let the OS offer OpenHangar as an option when the user opens a `.csv` or `.pdf`
file, so a downloaded logbook export or maintenance record can be imported
without navigating to the app manually.

**Manifest change** in `pwa_manifest()`:
```python
"file_handlers": [
    {
        "action": "/import",
        "accept": {
            "text/csv": [".csv"],
            "application/pdf": [".pdf"],
        },
    }
]
```

**JS handler** (in `static/js/pwa.js` or a dedicated `file-handling.js`):
```js
if ('launchQueue' in window) {
    window.launchQueue.setConsumer(async (launchParams) => {
        if (!launchParams.files.length) return;
        for (const fileHandle of launchParams.files) {
            const file = await fileHandle.getFile();
            if (file.type === 'text/csv') {
                // redirect to logbook import page with file pre-loaded
                window.location.href = '/logbook/import';
                // persist file in sessionStorage or IndexedDB for the import page
            } else if (file.type === 'application/pdf') {
                // redirect to document upload page with file pre-loaded
                window.location.href = '/documents/upload';
            }
        }
    });
}
```

Notes:
- `launchQueue` is Chrome/Edge only; the manifest key is ignored silently by
  other browsers.
- File handles from `launchQueue` are `FileSystemFileHandle` objects; call
  `.getFile()` to get the `File` blob, then pass it to the existing upload form
  via a `DataTransfer` trick or by directly `fetch()`-ing the upload endpoint.
- The `/import` action URL must exist as a real route (can render a page that
  immediately hands off to the right sub-flow based on the file type).

---

## PWA: Web Share API

Allow users to share a flight summary or an aircraft document to any app
registered in the OS share sheet (email, messaging, AirDrop, etc.) from within
OpenHangar. No manifest change required.

**Where to add share buttons:**
- Flight detail page (`/flights/<id>`) — share a text summary of the flight
  (date, route, duration, aircraft). If the flight has a GPS track, optionally
  attach the track still image (PNG) as a file — fetch
  `/flights/<id>/track/image.png`, convert to a `File` blob, and pass as
  `files: [blob]` to `navigator.share()`. Requires the single-flight still
  image item below.
- Aircraft detail page (`/aircraft/<id>`) — share the aircraft name + type.
- Pilot logbook / aircraft logbook — a "Share my tracks" button that attaches
  the existing all-tracks GIF (`/pilot/tracks/animation.gif` or
  `/<id>/tracks/animation.gif`) as a file.
- Document detail page — share a link to the document (if the instance is
  publicly reachable) or trigger a file share of the PDF blob.

**JS pattern** (add to the relevant page's external JS file):
```js
async function shareItem(data) {
    if (!navigator.share) return;   // not supported; hide the button in CSS
    try {
        await navigator.share(data);
    } catch (err) {
        if (err.name !== 'AbortError') throw err;
    }
}

// Example for a flight summary:
document.querySelector('#share-flight')?.addEventListener('click', () => {
    shareItem({
        title: document.title,
        text: `${aircraftReg} · ${flightDate} · ${depIcao}→${arrIcao} · ${duration}h`,
        url: window.location.href,
    });
});

// Example for attaching a track image as a file:
async function shareWithTrackImage(imageUrl, shareData) {
    if (!navigator.share) return;
    try {
        const resp = await fetch(imageUrl);
        const blob = await resp.blob();
        const file = new File([blob], 'track.png', { type: blob.type });
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
            await navigator.share({ ...shareData, files: [file] });
            return;
        }
    } catch (_) {}
    await navigator.share(shareData);  // fallback: share without file
}
```

**Conditionally show the Share button** (CSS, no JS flicker):
```css
.share-btn { display: none; }
```
```js
if (navigator.share) document.querySelector('.share-btn')?.classList.remove('d-none');
```

Notes:
- `navigator.share` requires a secure context (HTTPS) and a user gesture.
- File sharing (`files: [blob]`) works on Chrome Android and Safari iOS;
  desktop support is narrower — always fall back to text/URL share if
  `navigator.canShare({ files })` returns false.
- The `url` field should be the canonical page URL; the user's instance may be
  on a private network and the link may not resolve for recipients.

---

## GPS track: UI links for single-flight image and GIF

Routes `GET /flights/<id>/track/image.png` and `GET /flights/<id>/track/animation.gif`
exist in `app/flights/routes.py`. What remains:

**UI links (flight edit/detail page):** Add download links in
`app/templates/flights/flight_form.html`, shown only when `flight.gps_track` is not
None. Both links must carry `hx-boost="false"` (binary responses). Pattern mirrors
the all-tracks GIF links on the pilot/aircraft logbook pages.

**Server-side render cache:** Routes already set `Cache-Control: public,
max-age=31536000, immutable` and `ETag: "<gps_track_id>"` (browser/proxy cache).
For zero re-render cost on cache miss, add nullable `LargeBinary` columns
`cached_png` and `cached_gif` to `GpsTrack` (with Alembic migration). On first
request: render, store in DB, return. On subsequent requests: read bytes from DB
directly. GPS track data never changes once saved, so no invalidation is ever needed.

---

## GPS track: inline preview in new/edit flight form

When a GPX file is selected and parsed (the existing `parse-gps` AJAX flow
already returns the GeoJSON), show a thumbnail of the route immediately — before
the user saves the flight.

**Recommended approach (two phases):**

1. **Before save — client-side canvas preview (instant, no server round-trip):**
   After `parse-gps` succeeds, the form JS already has the `geojson` coordinates.
   Draw the lat/lon pairs on a hidden `<canvas>` element using the HTML5 Canvas API
   (plain background, no map tiles — just the track shape). Show/hide the canvas
   alongside the "GPS parsed" success banner. This is fast, requires no new
   endpoint, and gives immediate visual feedback.

2. **After save (edit form only) — server-rendered PNG:**
   When editing a flight that already has a GPS track (`fe.gps_track` is not None),
   display the track image inline using the `GET /flights/<id>/track/image.png`
   endpoint in an `<img>` tag. Cache headers (`immutable`) mean the browser serves
   this from cache on subsequent edits.

No new server endpoint needed for phase 1. Phase 2 requires only a template
change in `app/templates/flights/flight_form.html`.

---

## PWA: Push Notifications + App Badging

Send system-level notifications for maintenance-due and document-expiry events
(complementing or replacing the current email channel), and badge the app icon
with a count of overdue items.

**Components needed:**

1. **VAPID key pair** — generate once at deploy time:
   ```
   py-vapid --gen --applicationServerKey
   ```
   Store public/private keys as env vars `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY`
   and `VAPID_CLAIM_EMAIL`.

2. **Subscription endpoint** (`/api/push/subscribe`, POST):
   ```python
   @bp.route("/api/push/subscribe", methods=["POST"])
   @login_required
   def push_subscribe():
       sub = request.get_json()
       # store sub["endpoint"], sub["keys"]["p256dh"], sub["keys"]["auth"]
       # in a new PushSubscription model linked to TenantUser
       ...
   ```

3. **New model** `PushSubscription` in `app/models.py`:
   ```python
   class PushSubscription(Base):
       __tablename__ = "push_subscriptions"
       id: Mapped[int] = mapped_column(primary_key=True)
       tenant_user_id: Mapped[int] = mapped_column(ForeignKey("tenant_users.id"))
       endpoint: Mapped[str] = mapped_column(Text)
       p256dh: Mapped[str] = mapped_column(String(256))
       auth: Mapped[str] = mapped_column(String(64))
       created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
   ```
   Requires an Alembic migration.

4. **Push sender** (reuse the existing notification scheduler loop in
   `app/notification_service.py`):
   ```python
   from pywebpush import webpush, WebPushException
   webpush(
       subscription_info={"endpoint": sub.endpoint,
                          "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
       data=json.dumps({"title": "Maintenance due", "body": "...", "badge": 3}),
       vapid_private_key=VAPID_PRIVATE_KEY,
       vapid_claims={"sub": f"mailto:{VAPID_CLAIM_EMAIL}"},
   )
   ```
   Dependency: `pywebpush` (add to `requirements.txt`).

5. **Service worker `push` handler** in `app/static/js/sw.js`:
   ```js
   self.addEventListener('push', event => {
       const data = event.data?.json() ?? {};
       event.waitUntil(
           self.registration.showNotification(data.title ?? 'OpenHangar', {
               body: data.body,
               icon: '/static/icons/icon.svg',
               badge: '/static/icons/icon-maskable.svg',
           })
       );
       if ('setAppBadge' in self.navigator && data.badge != null) {
           self.navigator.setAppBadge(data.badge);
       }
   });

   self.addEventListener('notificationclick', event => {
       event.notification.close();
       event.waitUntil(clients.openWindow(event.notification.data?.url ?? '/'));
   });
   ```

6. **Subscription flow in the browser** (add to `static/js/pwa.js`):
   ```js
   async function subscribeToPush(vapidPublicKey) {
       const reg = await navigator.serviceWorker.ready;
       const sub = await reg.pushManager.subscribe({
           userVisibleOnly: true,
           applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
       });
       await fetch('/api/push/subscribe', {
           method: 'POST',
           headers: {'Content-Type': 'application/json'},
           body: JSON.stringify(sub),
       });
   }
   ```
   Trigger `subscribeToPush()` from a user-initiated action (e.g. "Enable
   push notifications" toggle in Settings) — do not prompt on first visit.

7. **App Badging** — clear the badge when the app is opened:
   ```js
   if ('clearAppBadge' in navigator) navigator.clearAppBadge();
   ```
   Call this in the SW `activate` or from a page `visibilitychange` handler.

Notes:
- `pywebpush` sends the push via the browser vendor's push service
  (FCM for Chrome, Mozilla Push for Firefox) — no direct connection to the
  user's device, and no data leaves the server other than the encrypted payload.
- Failed pushes (410 Gone = subscription expired) should delete the
  `PushSubscription` row to avoid accumulating stale records.
- Users must opt in; the browser will show a native permission prompt.
  Gate the UI behind a `'PushManager' in window` check.
- App Badging (`navigator.setAppBadge`) is supported on Chrome/Edge desktop
  and Safari 16.4+; ignore gracefully elsewhere.

---

## PWA: Periodic Background Sync

Let the installed PWA wake up nightly (without a server push) to fetch upcoming
maintenance due dates and set the app badge, keeping the icon count fresh even
if the user has not opened the app that day.

**Service worker** (`app/static/js/sw.js`):
```js
self.addEventListener('periodicsync', event => {
    if (event.tag === 'maintenance-badge') {
        event.waitUntil(updateMaintenanceBadge());
    }
});

async function updateMaintenanceBadge() {
    const res = await fetch('/api/badge-count');
    if (!res.ok) return;
    const { count } = await res.json();
    if ('setAppBadge' in self.navigator) {
        count > 0 ? self.navigator.setAppBadge(count) : self.navigator.clearAppBadge();
    }
}
```

**Registration** (in `static/js/pwa.js`, after push permission is granted):
```js
const reg = await navigator.serviceWorker.ready;
if ('periodicSync' in reg) {
    const status = await navigator.permissions.query({ name: 'periodic-background-sync' });
    if (status.state === 'granted') {
        await reg.periodicSync.register('maintenance-badge', { minInterval: 24 * 60 * 60 * 1000 });
    }
}
```

**New API endpoint** (`/api/badge-count`, GET, login-required):
```python
@app.route("/api/badge-count")
@login_required
def api_badge_count():
    # count overdue maintenance items + expired documents for the current user's tenants
    count = ...
    return jsonify({"count": count})
```

Notes:
- Periodic Background Sync is **Chrome/Edge only** (not Firefox, not Safari).
  It requires the PWA to be installed and the browser to determine the site is
  engaged with (visit frequency heuristic). It is a progressive enhancement —
  no fallback needed; the badge simply won't update when the app is closed on
  unsupported browsers.
- The OS controls the actual sync interval; `minInterval` is a hint, not a
  guarantee.
- Implement Push Notifications first; Periodic Background Sync is a complement
  for users who have not granted push permission.

---

## PWA: Background Sync (offline flight logging)

Queue a flight log entry written while offline (e.g. at a remote airfield with
no connectivity) and automatically replay it to the server when connectivity
returns, without requiring the user to retry manually.

**Service worker** (`app/static/js/sw.js`):
```js
self.addEventListener('sync', event => {
    if (event.tag === 'flight-log-sync') {
        event.waitUntil(replayQueuedFlights());
    }
});

async function replayQueuedFlights() {
    const db = await openIDB();
    const queued = await db.getAll('flight-queue');
    for (const entry of queued) {
        const res = await fetch('/flights/new', {
            method: 'POST',
            body: entry.formData,   // serialised FormData stored in IDB
        });
        if (res.ok || res.status < 500) {
            await db.delete('flight-queue', entry.id);
        }
        // 5xx: leave in queue, SW will retry on next sync event
    }
}
```

**Client-side interception** (in `static/js/flight_log.js`):
```js
flightForm.addEventListener('submit', async event => {
    if (!navigator.onLine) {
        event.preventDefault();
        const fd = new FormData(flightForm);
        await queueFlightOffline(fd);   // store in IndexedDB
        const reg = await navigator.serviceWorker.ready;
        await reg.sync.register('flight-log-sync');
        showToast(_('Saved offline — will sync when back online'));
    }
    // if online, let the form submit normally
});
```

**IndexedDB helper** (small utility in `static/js/idb.js`):
- Open a database `openhangar-offline` with an object store `flight-queue`.
- Each record: `{ id: auto, formData: serialisedFields, timestamp: Date.now() }`.
- `FormData` cannot be stored directly in IDB; serialise to a plain object
  (`Object.fromEntries(fd.entries())`) or use a multipart blob.

Notes:
- Background Sync is supported in Chrome/Edge and Firefox (behind a flag);
  not yet in Safari (as of 2026). On unsupported browsers, the SW `sync` event
  never fires — add a fallback that retries on the next `online` event instead:
  ```js
  window.addEventListener('online', () => reg.sync.register('flight-log-sync'));
  ```
- The server endpoint must be idempotent or deduplicate on a client-generated
  UUID included in the queued payload, to guard against double-submit if the
  sync fires but the response is lost.
- This feature is a prerequisite for the full offline mode described in the
  "Native mobile app" item above and in the Phase 40 planning notes.

---

## UI: scroll position resets to top after other in-place actions on long pages

Fixed for the config page's "refresh version check" button: because
`<body hx-boost="true">` (`app/templates/base.html:48`), htmx treats any
`<form method="post">` submit as a boosted navigation, and its
`scrollIntoViewOnBoost: true` default scrolls `document.body` to the top after
every swap — even when the action's own result (an updated badge, a new row,
a removed item) renders right next to the button the user just clicked,
somewhere down the page. Fixed via `hx-swap="innerHTML show:none"` on that one
form (suppresses htmx's default scroll for that specific action only) plus a
new reusable `data-scroll-anchor="<id>"` mechanism in `app/static/js/ui.js`
that scrolls back to a named element after the swap settles.

The same bug shape exists elsewhere and hasn't been fixed yet. Candidates,
same tiering as investigated:

**Tier 1 — same template family as the fixed bug** (`app/templates/config/settings.html`):
- "Run back-fill" (aircraft_type_icao) — form at `settings.html:393-398`,
  route `config.backfill_aircraft_type_icao` (`app/config/routes.py:919-949`)
- "Run back-fill" (pilot log → flight entries) — form at `settings.html:407-412`,
  route `config.backfill_pilot_log_to_flight_entries` (`app/config/routes.py:952-980`)
- "Upgrade now" (borderline — the success path triggers a restart with its own
  banner, but the two guard-clause paths behave exactly like the version-check
  bug) — form at `settings.html:431-436`, route `config.trigger_upgrade`
  (`app/config/routes.py:571-596`)

**Tier 2 — `app/templates/aircraft/detail.html`** (1060 lines, guaranteed long
regardless of data volume):
- Delete photo — `detail.html:765-773` → `aircraft.delete_photo`
  (`app/aircraft/routes.py:1986-2010`)
- Upload photo — `detail.html:734-744` → `aircraft.upload_photo`
  (`app/aircraft/routes.py:1911-1965`)
- Upload insurance certificate — `detail.html:610-621` (auto-submit) →
  `documents.upload_insurance_cert` (`app/documents/routes.py:565-609`)
- Revoke share link — `detail.html:896-903` → `share.revoke_token`
  (`app/share/routes.py:80-94`)
- Delete component — `detail.html:317-325` → `aircraft.delete_component`
  (`app/aircraft/routes.py:606-624`)

**Tier 3 — depth is data-dependent, not structural** (lower priority — triage
if a real hangar's data volume makes these deep enough to matter):
- Backup now / Run first backup — `settings.html:177-182` and `253-257` →
  `config.run_backup_now` (`app/config/routes.py:457-466`)
- Airworthiness dashboard per-aircraft actions (sync EASA, delete document,
  delete EASA source node, delete STC) — `app/templates/airworthiness/dashboard.html`
  → `app/airworthiness/routes.py` (`trigger_sync`, `delete_document`,
  `delete_node`, `delete_stc`)
- Users list per-row toggles (all-planes, aircraft access, capability flags,
  role select) — `app/templates/users/list.html` → `app/users/routes.py`
  (`toggle_all_planes`, `update_aircraft_access`, `update_user_flags`,
  `change_role`)

Suggested order: Tier 1 first (explicitly the same page the bug was reported
on), then Tier 2 (structurally guaranteed long page), Tier 3 as needed.

---

## Flights: bulk import of historical airframe logbook (CSV / Excel)

Phase 28 gives pilots a rich CSV/Excel import for the **pilot** logbook, and
the pilot-log→flight-entries backfill (`config.backfill_pilot_log_to_flight_entries`)
covers the owner's own flights. But an operator migrating years of paper or
spreadsheet **aircraft** records has no direct path: flights flown by other
pilots, previous owners, or instructors never pass through any pilot's
personal logbook, so counters (engine/flight time continuity), landings, and
maintenance-relevant history can't be brought in.

Design notes:
- Reuse the Phase 28 machinery wholesale: header auto-detection, column
  mapping UI with fingerprint memory, subtotal-row skipping, batch model with
  rollback — mapped onto `FlightEntry` fields (date, crew name, route,
  counters, flight time, landings) for one selected aircraft.
- Counter continuity: imported rows should be validated the same way the
  entry form pre-fills counters, with a per-row warning (not a hard error)
  when start ≠ previous end — historical logs often have small corrections.
- Crew: store the free-text pilot name as a `FlightCrew` row with
  `user_id = NULL` (the model already supports external pilots, Phase 16).
- An "opening counters" option (analogous to Phase 28's opening-hours offset)
  for operators who only want to import from a cutover date forward.

