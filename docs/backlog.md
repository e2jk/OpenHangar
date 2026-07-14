# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

Phase 35 added a PWA with camera capture and offline queuing, which covers the
main mobile use-cases (quick flight entry, Hobbs photo, offline ramp use) with
no second codebase.

A native app (React Native or Flutter) would only add meaningful value if two
conditions are met:

1. **Background push notifications** — Phase 34 email notifications are the
   current channel; native push requires APNs/FCM integration and app store
   distribution, which is a significant ongoing maintenance burden.
2. **Deep offline** — the IndexedDB sync queue implemented in Phase 35 should cover
   typical connectivity gaps; native SQLite would only matter for extended
   offline periods unlikely in an aviation context.

Prerequisite: Phase 35 (PWA + offline sync) has shipped. Re-evaluate
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
  "Native mobile app" item above and in the Phase 35 planning notes.

---

## Pilots: personal minimums

A way for a pilot to define, revise, and consult their **personal minimums**
— the self-imposed operating limits (stricter than the legal ones) that
guide go/no-go decisions. Today these live outside the app as a hand-made
document; keeping them next to the logbook makes them easier to revise as
experience grows and opens the door to recency-based nudges.

A real-world example of the content (one page):

- Header: revision date + experience basis ("based on 88 flight hours /
  VFR only") — both derivable from the pilot logbook at revision time.
- Free-text principle sections: a credo, decision-making rules (e.g. the
  "three strikes" rule), additional commitments.
- Quantified minimums: max wind / gust differential / crosswind, cruise
  altitude without oxygen, ceilings tiered by mission profile (pattern,
  local < 50 nm, short XC < 100 nm, long XC), day/night visibility, fuel
  reserve on landing, minimum runway length at unfamiliar fields, night
  rules.
- Recency commitments: manoeuvres practice every N months; "comfort zone"
  rules (no unfamiliar airports if not flown in 30 days, instructor flight
  after 60 days).
- Meta-rule: minimums are never changed on the day of a flight.

External references (for suggested content, not to be copied verbatim):
- FAA, [*Getting the Maximum from Personal Minimums*](https://www.faasafety.gov/files/gslac/library/documents/2006/Oct/9091/Developing%20Personal%20Minimums.pdf)
  — the canonical six-step worksheet: weather categories (VFR / MVFR / IFR /
  LIFR with day/night ceiling & visibility), wind & turbulence (surface wind,
  gusts, crosswind component), performance factors (shortest runway, highest
  terrain, highest density altitude), and PAVE-based adjustment rules
  ("if fatigued / unfamiliar aircraft / unfamiliar airport → add at least
  500 ft to ceiling, ½ mile to visibility, 500 ft to runway; subtract
  5 kts from winds").
- FAA [Personal Minimums Worksheet](https://www.faa.gov/newsroom/safety-briefing/personal-minimums-worksheet)
  and AOPA [Personal Minimums Contract (VFR)](https://www.aopa.org/-/media/Files/AOPA/Home/Pilot-Resources/Personal-Mins-Contracts/Personal-Minimums-Contract-VFR.pdf).
- Review cadence per FAA guidance: revisit whenever certification, training
  or experience changes significantly, and at least once a year.

**Data model** (all in `app/models.py`, one new Alembic migration with a
`secrets.token_hex(6)` revision ID):

- `PersonalMinimumsRevision` — `id`, `user_id` (FK `users.id`,
  `ondelete="CASCADE"`), `revision_number` (int, per-user sequence starting
  at 1; unique together with `user_id`), `status`
  (`draft` / `active` / `superseded` — string constants class, same pattern
  as `LogbookEntryType`), `published_on` (Date, nullable — set at publish),
  `experience_hours` (Numeric(6,1), nullable — auto-stamped at publish from
  the logbook), `experience_note` (String(128), free text, e.g. "VFR only"),
  `created_at` / `updated_at`. At most one `draft` and one `active` per
  user — enforce in route logic and assert in tests (no DB partial-unique
  constraint needed).
- `PersonalMinimumsSection` — `id`, `revision_id` (FK, CASCADE), `title`
  (String(128)), `sort_order` (Integer, default 0). Relationship on the
  revision ordered by `sort_order` (same pattern as `AircraftPhoto`).
- `PersonalMinimumsItem` — `id`, `section_id` (FK, CASCADE), `label`
  (String(128)), `value` (Text, free-form), `sort_order`, `semantic_tag`
  (String(64), nullable), `numeric_value` (Numeric(8,2), nullable).
- `PersonalMinimumsTag` — a constants class (not a table) defining the v1
  semantic vocabulary: `MAX_DAYS_SINCE_LAST_FLIGHT`,
  `MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT`, `MANOEUVRES_PRACTICE_INTERVAL_MONTHS`,
  `MIN_FUEL_RESERVE_MINUTES`. Form validation: when a tag is set,
  `numeric_value` is required (that is the machine-readable side; `value`
  stays the human-readable text). Untagged items are display-only.

Privacy: strictly per-pilot, exactly like `PilotProfile` — every query
filters on the logged-in user's id; accessing another user's revision id
returns 404. No tenant/admin visibility. No `tenant_id` column needed
(user-scoped, as `pilot_logbook_entries`).

**Versioning workflow:**

- First visit (no revisions): an empty state offering three starters —
  *blank*, *light*, *full* (see below) — creating revision 1 as `draft`.
- "Revise" on the active revision deep-copies it (sections + items) into a
  new `draft` with `revision_number = max + 1`. Drafts are editable and
  deletable; `active` and `superseded` revisions are immutable (server-side:
  reject edits on non-draft revisions, not just hidden buttons).
- "Publish" on a draft: stamps `published_on = today` and
  `experience_hours` = the pilot's total flight time reusing
  `_compute_totals_sql()` in `app/pilots/routes.py` (already excludes FSTD
  time), flips the current `active` to `superseded`, the draft to `active`.
  The confirm dialog restates the meta-rule ("minimums are never changed on
  the day of a flight"); if the pilot has a `FlightEntry` or a reservation
  dated today, strengthen that warning text in the same dialog — warn only,
  never block.
- History view lists all revisions newest-first (revision number, date,
  hours at publish) — each opens read-only, so the evolution stays readable.

**Routes** (extend `app/pilots/routes.py`; decorators `@login_required` +
`@require_pilot_access` like `/pilot/profile`; nav link under the Pilots
menu gated `{% if is_pilot %}`):

```
GET  /pilot/minimums                     active revision (or starter chooser)
POST /pilot/minimums/create              body: starter=blank|light|full
GET  /pilot/minimums/history             list of revisions
GET  /pilot/minimums/revision/<id>       read-only render of any own revision
POST /pilot/minimums/revise              copy active → new draft
GET  /pilot/minimums/edit                edit the draft (single edit page)
POST /pilot/minimums/section/...         add / edit / delete / move up / move down
POST /pilot/minimums/item/...            add / edit / delete / move up / move down
POST /pilot/minimums/publish             draft → active (confirm dialog)
POST /pilot/minimums/delete-draft
GET  /pilot/minimums/print               print-optimised one-page view
```

Reordering: plain form posts swapping `sort_order` with the neighbour
(up/down buttons) — no JS required; hx-boost handles the round-trip.

**Starter content** (a module-level function returning the structure, with
labels wrapped in `_()` and evaluated at request time so they land in the
pilot's locale; values are left empty with suggested `placeholder=` examples
so nothing prescriptive is stored):

- *Light*: *Winds* (max surface wind, max gust differential, max crosswind);
  *Weather* (minimum ceiling day/night, minimum visibility day/night);
  *Fuel* (fuel reserve at landing — tagged `MIN_FUEL_RESERVE_MINUTES`).
- *Full*, everything in light plus: *Guiding principles* (credo items, e.g.
  "I never have to fly", "seek reasons not to fly", flight-plan/route
  commitments); *Pre-flight checklists* (PAVE, IMSAFE as free-text
  reminders); *Ceilings by mission profile* (pattern work, local < 50 nm,
  short XC < 100 nm, long XC > 100 nm); *Cruise altitude without oxygen*;
  *Runway length at unfamiliar fields*; *Night flying rules*;
  *Decision-making rules* (three-strikes pre-flight NO-GO / in-flight
  TERMINATE); *Recency commitments* (manoeuvres practice every N months —
  tagged `MANOEUVRES_PRACTICE_INTERVAL_MONTHS`; familiar-airports-only after
  N days without flying — tagged `MAX_DAYS_SINCE_LAST_FLIGHT`; instructor
  flight after N days without flying — tagged
  `MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT`); *Adjustments* (free-text PAVE-style
  add/subtract rules per the FAA worksheet above).

All starter sections and items are ordinary rows once created — editable,
removable, reorderable; the starter is a convenience, not a schema.

**Recency nudges** (only for tags derivable from the pilot logbook):

- Days since last flight: `max(PilotLogbookEntry.date)` for the pilot.
- Days since last instructor flight: `max(date)` where `function_dual > 0`.
- `MANOEUVRES_PRACTICE_INTERVAL_MONTHS` and `MIN_FUEL_RESERVE_MINUTES` are
  **not** automatically checkable in v1 — display-only for now; note this
  in the edit form help text.
- Surfacing: a dismissable warning banner on the pilot logbook page and on
  the "Log a flight" form when a tagged threshold is exceeded, plus a new
  notification type `PERSONAL_MINIMUMS_RECENCY` in
  `NotificationType` (`app/models.py`) — requires `is_pilot`, default ON,
  no `threshold_days` (the thresholds live in the tagged items) — evaluated
  in `run_daily_checks()` in `app/services/notification_service.py`,
  following the existing expiry-digest pattern (notify once per breach, not
  daily; reuse the existing dedupe mechanism).
- The "Log a flight" flow shows the active minimums **read-only** in a
  collapsible panel (day-of-flight immutability: consult, not edit).

**Output / frontend:**

- Templates in `app/templates/pilots/`: `minimums_view.html`,
  `minimums_edit.html`, `minimums_history.html`, `minimums_print.html`.
  No `<script nonce>` blocks anywhere (hx-boost rule).
- Prefer zero new JS. If the edit form needs the tag → numeric-value field
  toggle client-side, add `app/static/js/personal_minimums.js` following
  the IIFE + `ohInited` + `htmx:afterSettle` module pattern and register it
  in `base.html`'s always-load list.
- Print view: print-oriented CSS (`@media print`), two-column layout
  mirroring the paper document pilots carry, targeting one A4 page; header
  = "Personal minimums for <name> — revised <date>, based on <hours> flight
  hours <experience note>". "Download as PDF" is the browser's print dialog
  until a PDF pipeline exists (Phase 44) — label the button accordingly.
- Demo seed: one pilot with an active revision **and** one superseded
  revision (so the history/evolution feature is visible in demo mode).

**Validation & tests** (feature-named test file, e.g.
`test_personal_minimums.py`; 100 % coverage as usual; all new UI strings
translated to `fr` + `nl`, French U+202F typography):

- Starter creation: blank/light/full produce the expected sections/items.
- Privacy: another authenticated user gets 404 on every revision id and
  cannot mutate sections/items across users.
- Lifecycle: only one draft + one active at a time; publish stamps date and
  logbook hours and supersedes the previous active; editing a non-draft is
  rejected server-side; draft deletable, active not.
- Tag validation: setting a tag without `numeric_value` is rejected.
- Reorder: up/down swaps `sort_order`; rendering follows `sort_order`.
- Recency: last-flight and last-instructor-flight (`function_dual > 0`)
  derivations correct; banner shown when exceeded; notification fired once
  and deduped; nothing fires for pilots without tagged items or without an
  active revision.
- Print view renders; flight-log form shows the read-only panel.
- `docs/user-guide.md`: add a "Personal minimums" subsection under the
  pilot features; add a screenshot manifest entry
  (`docs/screenshots/manifest.yml`) for the view page.

---

## Maintenance: landings-based triggers

`MaintenanceTrigger` supports calendar and engine-hours types only. Some
inspection items in light GA are landing-count based rather than hour based —
e.g. tyre and landing-gear inspections, or glider-tow hook checks scheduled
every N launches.

The data foundation already exists: `FlightEntry.landing_count` is recorded
per flight (Phase 16), so a cumulative landing count per aircraft is derivable
with a simple sum.

Design notes:
- Add `due_landings` + `interval_landings` columns to `MaintenanceTrigger`
  (mirroring the existing `due_engine_hours` / `interval_hours` pair) and a
  `landings` trigger type.
- `status()` compares the aircraft's cumulative landing count against
  `due_landings`; "due soon" at ≥ 90 % (same convention as hours triggers).
- Marking as serviced advances `due_landings` by `interval_landings`.
- Entries with no `landing_count` recorded simply do not advance the counter —
  worth a hint on the trigger form that this type relies on landings being
  logged consistently.

Why deferred: calendar + hours cover the vast majority of piston-GA
maintenance schedules; add when a concrete landing-based item shows up.

---

## Maintenance: due-date projection from utilization trend

Hours-based triggers show "due at X h", but an owner plans on a calendar —
"when do I need to book the shop?" is a date question, not an hours question.

Future enhancement: compute a rolling utilization rate per aircraft (e.g.
average engine hours per week over the last 90 days) and project the calendar
date at which each hours-based trigger will reach its due value. Show the
projected date, clearly marked as an estimate, on the per-aircraft trigger
list and the fleet maintenance overview (Phase 13), letting hours-based
triggers sort meaningfully in the chronological view instead of being pushed
to the end as undated items.

This would also make `MAINTENANCE_DUE_SOON` notifications more actionable:
today the hours criterion fires at ≥ 90 % of the hours limit, which for a
low-utilization aircraft can mean months of lead time noise or, for a
high-utilization one, too little warning; a projected-date threshold ("due in
~3 weeks at current usage") matches how shop appointments are actually booked.

Why deferred: needs a sensible minimum-data guard (an aircraft flown twice in
90 days produces a meaningless trend) and careful UI wording so the estimate
is never mistaken for a real due date.

---

## Reports: annual utilization & insurance-renewal summary

Per aircraft, for a selectable period (default rolling 12 months, or an
arbitrary policy year): engine hours and flight hours flown, number of
flights, landings, fuel added, oil added. Insurance renewals commonly ask
for hours flown in the past policy year and expected hours for the next;
today this requires manually summing logbook pages.

Candidate to fold into Phase 44 (Advanced Reporting & Exports) as an
additional report; kept here as a separate item so it isn't lost if Phase 44
is trimmed, since all the underlying data already exists.
