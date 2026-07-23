# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

## Public showcase page per aircraft ("brag page")

A public, no-login page an owner can send to friends/family — photos and
high-level type info, nothing operational. Distinct from the existing share
link (`share/routes.py`, `ShareToken.access_level` = `summary`/`full`), which
is aimed at co-owners/renters/mechanics and exposes real status data
(airworthiness, maintenance triggers, flight/cost history). This is a third,
lighter tier with a different audience and different content:

- **Photos**: the existing `AircraftPhoto` gallery (already has `sort_order`),
  shown large/carousel-style rather than as a management list.
- **High-level type info**: make/model/year, maybe a short free-text
  "about this aircraft" blurb (new nullable column on `Aircraft`, or reuse an
  existing notes field if one already fits) — no counters, no
  maintenance/document data at all.
- Explicitly **not** included: flight logs, maintenance status, costs,
  documents, pilot names, or anything from the `summary`/`full` share tiers.

URL: `/showcase/<registration>/<token>` — the registration makes the link
readable/brandable (and registrations are public info anyway), but the
opaque token remains the actual access control, so the page stays
individually revocable and isn't guessable/enumerable from the registration
alone. A registration change (sale/re-registration) would break existing
links under this scheme — acceptable since re-sharing is cheap for this
casual use case.

Implementation sketch:
- New `access_level` value (e.g. `"showcase"`) on `ShareToken`, or a separate
  token model/table if reusing `ShareToken` muddies the existing
  summary/full semantics — decide once the template/route split is clear.
- New route + template in `share/`, e.g. `share/showcase.html`, following the
  same standalone-page pattern as `share/public.html` (no `base.html`
  extension, so it's one of the two templates allowed a `<script nonce>` if
  any JS is needed for a photo carousel).
- Owner-facing UI: a way to generate/copy/revoke this link, alongside the
  existing share-link management on the aircraft detail page.

---

## Offline form guard: warn up front, don't disable fields

`offline_form_guard.js` currently only blocks at submit time — you can fill in
an entire guarded form while offline and only find out it can't be saved when
you hit Save. Add an early warning instead: a banner at the top of any
guarded form, shown the moment it's known to be offline (on load, and on the
`offline` event if it fires while you're on the page), so the dead end is
obvious before time is invested rather than after.

Deliberately **not** disabling the fields themselves, for two reasons:

- **The reverse transition isn't reliable enough to lock the UI on.** The
  browser's `online` event doesn't reliably fire in every browser/devtools
  scenario — this session's stuck-offline-badge bug (fixed by adding an
  `htmx:afterSettle` fallback in `pwa.js`) was exactly that. A form that
  disables its fields on `offline` has no equivalent fallback to re-enable
  them if `online` never fires; the user would be stuck with a dead form
  until a reload, which is worse than today's submit-time-only guard, which
  self-heals the moment connectivity is actually back by submit time.
- **Drafting still has value even when a save is known to be impossible** —
  e.g. jotting down counter readings at the aircraft right after landing,
  before deciding whether to redo the entry via a workbench instead.

Applies broadly (every form the guard already covers), not just the flight
form — this is a general improvement to `offline_form_guard.js` itself, not
tied to the "consolidate on the workbenches" work above.

---

## Offline editing: consolidate on the workbenches, add "new row"

Two independent offline-editing paths exist today for the same domain objects:
the classic single-flight form (`/flights/new`, `/flights/<id>/edit`, and the
standalone `/pilot/logbook/new`/`/pilot/logbook/<id>/edit`) queues a blind
full-record resubmission via IndexedDB's `queue` store, while the aircraft and
pilot **offline workbenches** (`/aircraft/<id>/logbook/offline`,
`/pilot/logbook/offline`) use a proper snapshot + per-field diff/conflict-
resolution model (`outbox`/`pilot_outbox`), but are edit-only — no "add a new
flight" capability.

The workbench model is the more capable one and already partially solves the
hardest part of this: `offline_workbench.js` can render a nested `pilot` sub-
diff inline on an aircraft-log row (`PILOT_FIELDS`, its own base/delta), and
the backend's `sync_flight` route already applies both the `FlightEntry` and
its linked `PilotLogbookEntry` atomically from one sync call
(`apply_linked_pilot_entry` in `flights/routes.py`). Plan:

1. **Add a repeatable "add new row" action to both workbenches** — a blank,
   editable row that can be pressed multiple times to queue several new
   flights before syncing.
2. **Make the classic form offline-inert** for both add and edit (it stays the
   ergonomic path for *online* use — autocomplete, GPS import, etc.). Concretely,
   mirror the pattern `offline_form_guard.js` already applies to every other
   non-offline-aware form on the site, rather than inventing a new mechanism:
   - Fields stay fully enabled/editable — nothing is disabled or read-only.
     The offline check only happens at submit time (`navigator.onLine` inside
     the `submit` handler), not proactively on page load, so a connection
     that returns before you hit Save isn't penalized.
   - What's blocked is the submit itself: `e.preventDefault()`, then an inline
     alert instead of the request going out. Unlike the generic sitewide
     guard text, this one is form-specific and links to the actual workbench
     to use instead — the aircraft workbench if a tracked aircraft is
     selected in the form, the pilot workbench if "other aircraft"/none is.
   - Whatever was typed is **not** preserved or queued anywhere — same as
     every other guarded form today; the user re-enters it via the
     workbench's new "add row".
   - Mechanically: drop `data-oh-offline-aware` from `flight_form.html` and
     the standalone `entry_form.html` so they stop opting out of the generic
     guard, and delete the bespoke `pwa.js` queue machinery outright instead
     of leaving it as dead code — the `_flightForm` submit intercept,
     `_syncQueue`/`_syncEntry`/`_submitEntry`/`_showConflict`, and the
     `queue` store's read side in `offline_changes.js`'s `renderQueueCard`.
3. **Reuse the existing create endpoints for sync**, not the outbox's
   delta-sync route (there's nothing to diff against for a brand-new record):
   - `/flights/new` for aircraft-log rows (already creates the submitter's own
     linked `PilotLogbookEntry` in the same transaction when pilot fields are
     present — see `create_pilot` handling in `flights/routes.py`).
   - `/pilot/logbook/new` for standalone pilot-log rows.
   - Run the existing `/api/check-flight-duplicate` safety net before each
     replay, same as the legacy queue already does (no `exclude_flight_id` —
     these are genuinely new records).

### The three cases a new row can represent

1. **Tracked aircraft + you're also the pilot** — new row gets the same
   inline pilot sub-diff the edit view already shows (night/instrument time,
   landings, PIC name, time overrides); synced via `/flights/new` with
   `create_pilot` on, which creates both records together.

2. **Tracked aircraft only, no pilot entry of yours** — aircraft-fields-only
   row; synced via `/flights/new` with `create_pilot` off. **On the "linked to
   another pilot's logbook we can't see" discrepancy**: this isn't a new risk
   the feature introduces — `edit_flight` already scopes the linked-entry
   lookup to `pilot_user_id == uid` (`flights/routes.py`), so today, online,
   editing a shared flight's times/route never touches a *different* crew
   member's own linked entry; that pilot's derived fields only refresh the
   next time they themselves touch that flight. The workbench must preserve
   exactly this boundary — never query or write a `PilotLogbookEntry` that
   isn't the current user's — rather than inventing new cross-pilot
   propagation. Ordinary same-flight conflicts (someone else changed the
   aircraft-log fields before you synced) are already covered by the existing
   outbox base/diff mechanism; nothing extra needed there.

3. **Standalone pilot-only entry** (rental/training, no fleet aircraft) — no
   aircraft-side interaction at all; synced via `/pilot/logbook/new`. The
   simplest case.

Explicitly **out of scope** for this: linking an *existing*, already-created
flight to your own pilot logbook for the first time while offline — the
user-guide currently calls this out as one of the few things not available
offline, and this plan doesn't change that (it only covers *newly created*
rows, which are inherently linked from birth in case 1).

### Documentation

`docs/user-guide.md`'s "Working offline" section (~line 135) currently states
outright that "creating ... logbook entries (aircraft or pilot)... require[s]
a connection." That line — and the screenshots it references
(`offline_workbench.png`, `offline_pilot_workbench.png`) — need updating once
this ships, along with a line covering the classic form's new offline-inert
message. Re-run `scripts/take_screenshots.py` for both workbench screenshots
if the UI gains a visible "add row" control.

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

Note: Phase 38 (Offline Logbook Editing) plans a client-side variant of this
check in its offline workbench; this item remains the server-side/admin view.

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
2. **Deep offline** — Phase 35 shipped the offline queue for new entries and
   Phase 38 (Offline Logbook Editing) plans full offline browsing/editing of
   the airframe logbook with conflict resolution; native SQLite would only
   matter for scenarios beyond even that.

Prerequisite: Phases 35 and 38 (PWA + deep offline). Re-evaluate
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

Candidate to fold into Phase 45 (Advanced Reporting & Exports) as an
additional report; kept here as a separate item so it isn't lost if Phase 45
is trimmed, since all the underlying data already exists.

---

## Process: e2e suite de-flaking (fixture hardening)

The e2e suite has a history of intermittent failures (see commits `0ff5f20`,
`39422a0`, `bd780a2`, `8a86c87`, `c0460e9`). The flaky offline-logbook e2e
suite was removed in `a3959d6` pending this work; tasks 1 and 2 below are
prerequisites for reintroducing it (task 7). Tasks are ordered by value and
are independently committable — one task per commit, and after each task run
the full e2e suite three consecutive times locally
(`bash scripts/run-tests-with-coverage.sh --e2e`) and confirm CI's
`browser-tests-seeded-crawl` and `browser-tests-seeded-rest` jobs pass.

All work is in `tests/e2e/`, `app/dev_seed.py`, and `scripts/` — no app
behaviour changes, no migrations, no translations. Test files keep their
feature-based names. **Any `.github/workflows/ci.yml` edit (tasks 1 and 3)
must be explicitly approved by the maintainer first.**

### 1. Unify the two seed paths (single source of truth)

Today `tests/e2e/conftest.py` builds the `SEED` id dict two different ways:

- **In-process mode** (no `E2E_BASE_URL`): runs `dev_seed.seed()`, then
  creates e2e-only extras inline (two future-dated deletable `FlightEntry`
  rows `fe_del1`/`fe_del2`, a linked `PilotLogbookEntry` for the admin's most
  recent flight, a standalone FSTD `PilotLogbookEntry`, and
  `UserInvitation`/`PasswordResetToken` rows with the fixed tokens
  `e2e-crawl-invite-token`/`e2e-crawl-reset-token`), then queries ORM objects
  directly.
- **Docker/CI mode** (`E2E_BASE_URL` set): reads `tests/e2e/seed.json`
  written by `scripts/generate_routes.py --seed-out`, which samples
  *pre-existing* dev-seed rows — the extras above don't exist there, so some
  ids are `None` (tests skip silently) and destructive tests delete real
  seed rows. Silent fallbacks like `_s("aircraft_id_3", "aircraft_id")` can
  also alias two logically distinct fixtures to the same aircraft. This
  drift is what broke CI three ways in `c0460e9`.

Fix — make the database the single source of truth for both modes:

1. Move the e2e-extras block out of `conftest.py` into a new function
   `_seed_e2e_extras()` at the end of `app/dev_seed.py`, called from
   `seed()` only when `os.environ.get("OPENHANGAR_E2E_SEED") == "1"`.
   Reuse the exact object definitions currently in `conftest.py` (search
   for "E2E-only extras"). `dev_seed.py` is omitted in `.coveragerc`, so
   this adds no coverage obligation.
2. In-process mode: set `os.environ["OPENHANGAR_E2E_SEED"] = "1"` in
   `conftest.py` before `_dev_seed()` runs, and delete the inline extras
   block.
3. Docker/CI mode: add `-e OPENHANGAR_E2E_SEED=1` to the `$E2E_WEB`
   `docker run` in both `browser-tests-seeded-crawl` and
   `browser-tests-seeded-rest` jobs of `.github/workflows/ci.yml`
   (⚠ maintainer approval required).
4. Extend `_query_samples()` in `scripts/generate_routes.py` to also emit
   the extras' ids, queried by their distinguishing properties (future
   date + registration for the deletable flights, `entry_type == FSTD` for
   the standalone entry, the two fixed token strings). Emit them under the
   exact key names `conftest.py` uses (`fe_del1`, `fe_del2`,
   `pe_linked_id`, `pe_standalone_fstd_id`, `invite_token`, `reset_token`).
5. Replace *both* SEED-building blocks in `conftest.py` with one code path:
   in-process mode imports and calls `_query_samples(app)` directly
   (add `scripts/` to `sys.path` or move `_query_samples` into a small
   shared module) instead of hand-querying ORM objects; Docker mode keeps
   reading `seed.json` (same dict, produced by the same function).
6. Remove the fallback-key mechanism (`_s(key, fallback_key)`): once the
   extras are guaranteed in both modes, a missing id is a bug — `assert`
   the required keys are non-None at session start so it fails loudly with
   a clear message rather than skipping or aliasing.

Acceptance: zero e2e tests skipped for missing seed ids in either mode;
destructive tests consume only the synthetic future-dated rows.

### 2. Log in once per session (Playwright storage state) + TOTP window guard

Every fixture that logs in as admin types a TOTP code, which has two race
conditions: (a) a code computed just before typing can expire mid-submit
when it straddles the 30-second window boundary; (b) the app has TOTP
**replay protection** (`app/auth/routes.py`, log tag `auth.totp.replay`),
so two fresh admin logins within one 30-second window reject the second.

1. Add a module-level helper `_admin_login(page, live_server_url)` in
   `conftest.py` containing the current login sequence from
   `logged_in_page`, prefixed with a window guard so the code is never
   typed with <3 s of validity left:
   ```python
   remaining = 30 - (time.time() % 30)
   if remaining < 3:
       time.sleep(remaining + 0.2)
   ```
   Keep the existing fallback (explicit submit click if auto-submit
   doesn't navigate within 5 s).
2. Add a session-scoped fixture `admin_storage_state(browser_context, live_server_url, tmp_path_factory)`:
   open a temporary context, `_admin_login(...)` once, save
   `context.storage_state(path=...)`, close the context, return the path.
3. Rewire `logged_in_page` (and the shared `page` fixture's authenticated
   consumers) to create their context/page with
   `storage_state=admin_storage_state` instead of logging in — the TOTP
   dance then happens exactly once per session.
4. Keep `fresh_logged_in_page` doing a real login via `_admin_login()`:
   it is used by logout-flow tests, and reusing a shared state there is
   unsafe if logout ever invalidates the session server-side. It no longer
   collides with other logins thanks to the window guard + single shared
   login.
5. `fresh_viewer_page` is unchanged (viewer account has no TOTP).

Acceptance: grep shows exactly two call sites performing TOTP entry
(`admin_storage_state` and `fresh_logged_in_page`); full suite green 3×.

### 3. Failure observability: per-test Playwright traces + screenshots

CI failures currently offer only pytest text output. Add:

1. The standard pytest hook in `tests/e2e/conftest.py` to expose test
   outcome to fixtures:
   ```python
   @pytest.hookimpl(hookwrapper=True)
   def pytest_runtest_makereport(item, call):
       outcome = yield
       rep = outcome.get_result()
       setattr(item, f"rep_{rep.when}", rep)
   ```
2. In `browser_context`, start tracing once:
   `context.tracing.start(screenshots=True, snapshots=True)`. In the
   `page` fixture (and the `fresh_*` fixtures), wrap each test in a chunk:
   `tracing.start_chunk(title=request.node.nodeid)` before yield; after
   yield, if `getattr(request.node, "rep_call", None)` failed, call
   `tracing.stop_chunk(path="test-results/e2e/<sanitized-nodeid>.zip")`
   plus `page.screenshot(path=...)`, else `tracing.stop_chunk()` (discard).
3. Add `test-results/` to `.gitignore`.
4. In `.github/workflows/ci.yml`, add an `actions/upload-artifact` step
   with `if: failure()` uploading `test-results/e2e/` to all three e2e
   jobs: `browser-tests-seeded-crawl`, `browser-tests-seeded-rest`, and
   `browser-tests-fresh-db` (⚠ maintainer approval required).

View traces with `playwright show-trace <file>.zip`.

### 4. Reduce `networkidle` reliance (incremental, one file per commit)

`wait_for_load_state("networkidle")` appears ~145 times; it is both slow
(≥500 ms idle wait each) and racy — HTMX fires `htmx:afterSettle` on a
timer *after* network goes idle (see the comment in
`test_htmx_boost.py::test_widget_reinitializes_via_aftersettle`). Replace
it with event-based waits:

1. In `conftest.py`, add to every created context (put it next to the
   `_block_external_network(context)` calls):
   ```python
   context.add_init_script(
       "document.addEventListener('htmx:afterSettle',"
       " () => { window.__ohSettleCount = (window.__ohSettleCount || 0) + 1; });"
   )
   ```
2. Add a helper:
   ```python
   def click_and_settle(page, locator, timeout=10000):
       before = page.evaluate("() => window.__ohSettleCount || 0")
       locator.click()
       page.wait_for_function(
           f"() => (window.__ohSettleCount || 0) > {before}", timeout=timeout
       )
   ```
3. Conversion rules, applied one test file per commit (start with
   `test_htmx_boost.py`, the biggest offender):
   - hx-boost click + `networkidle` → `click_and_settle(...)`.
   - `page.goto(...)` + `networkidle` → plain `page.goto(...)` followed by
     an auto-retrying `expect(locator).to_be_visible()` on the element the
     test actually uses next.
   - Raw `assert` on page content immediately after a wait → convert to
     `playwright.sync_api.expect()` where the assertion targets a locator.
   - `page.wait_for_timeout(...)` sleeps (10 occurrences) → replace with a
     settle/`expect` wait; keep only where the test intentionally verifies
     that *nothing* happens (e.g. the action-cell no-navigation test).
4. Run the converted file 3× in a row before committing.

### 5. Replace fixed-sleep server startup with a readiness poll

Both in-process servers (`live_server` and `fresh_server` in
`tests/e2e/conftest.py`) do `time.sleep(0.8)` after starting the Flask
thread. Replace each with a poll of the `/health` endpoint
(up to ~15 s, 0.1 s interval, `urllib.request.urlopen(..., timeout=1)`
in a `try/except`), failing the fixture with a clear message on timeout.

### 6. Optional: local disposable-Docker e2e runner (CI-mode repro)

Locally the suite runs in-process against SQLite (both `live_server` and
`fresh_server`, when no Docker env vars are set), while CI runs
Docker + PostgreSQL for all three e2e jobs — `browser-tests-seeded-crawl`
and `browser-tests-seeded-rest` (dev-seeded, split across two jobs purely
for CI wall-time) and `browser-tests-fresh-db` (empty DB,
`test_setup_flow.py` only) — so CI-mode-only failures (like the seed.json
issues fixed in `c0460e9`) can't be reproduced locally today. Add
`scripts/run-e2e-docker.sh` + a compose file (e.g. `docker/compose.e2e.yml`:
`postgres:18-alpine` + the app built from the repo Dockerfile with
`OPENHANGAR_ENV=development`, `OPENHANGAR_E2E_SEED=1`, port published on an
ephemeral localhost port, isolated project name `-p openhangar-e2e`) that
mirrors the seeded jobs' steps: wait for the container healthcheck →
`scripts/generate_routes.py --seed-out tests/e2e/seed.json` →
`pytest --e2e` with `E2E_BASE_URL` and `E2E_ALLOW_DESTRUCTIVE=1` →
`docker compose down -v`. Lower priority now that the local suite is
green; only worth doing when a CI-mode-only failure next needs local
debugging. (A similar runner for `browser-tests-fresh-db`, i.e. a second
disposable Postgres + the app in `OPENHANGAR_ENV=production`, would be a
natural follow-up if empty-DB CI failures ever need local repro too.)

### 7. Reintroduce the offline-logbook e2e suite

After tasks 1 and 2 land, restore the suite removed in `a3959d6`
(`git show a3959d6^:tests/e2e/test_offline_logbook.py`), port its fixtures
to the new helpers (`admin_storage_state`, `click_and_settle`,
seed extras from task 1 instead of ad-hoc ids), and validate with at least
three consecutive full-suite runs locally plus green CI
`browser-tests-seeded-crawl`/`browser-tests-seeded-rest` jobs before
proposing the commit.

---

## CI: continuous fuzzing harness (Atheris, no ClusterFuzzLite/Docker)

The [OpenSSF Scorecard](https://securityscorecards.dev/viewer/?uri=github.com/e2jk/OpenHangar)
Fuzzing check currently scores 0/10 ("no fuzzer integrations found"). A prior
attempt using ClusterFuzzLite (commits `361fef3`..`fc015e2`, 2026-05-13) was
reverted after repeated Docker build-context/`COPY` failures around
`.clusterfuzzlite/Dockerfile`: the official python-lang integration docs
assume `COPY . $SRC/<project>` reliably has the whole repo as build context,
which didn't hold through the `build_fuzzers` GitHub Action here even after
setting `project-src-path: ${{ github.workspace }}`. The workaround (inlining
the three fuzzer harnesses as `RUN cat > ... heredoc` blocks directly in the
Dockerfile, to bypass `COPY` entirely) was judged too much ongoing maintenance
for three small harnesses, so the whole thing was reverted rather than kept
running in that form.

**Key finding that changes the calculus:** Atheris (the fuzzing engine
ClusterFuzzLite uses for Python) does not need Docker or the OSS-Fuzz
toolchain at all — it is a plain pip package with wheels for cp312–cp314,
`manylinux2014_x86_64` (matches CI's `ubuntu-latest` + Python 3.14 exactly).
Verified locally: `pip install atheris` into the project venv, a harness that
imports the real `_safe_next` from `app/reservations/routes.py` directly (the
same import pattern `tests/test_documents.py` already uses for `_safe_join`),
`atheris.Fuzz()` for 5 seconds → 647k real executions, ~131k execs/sec, no
Docker, no crashes. Going plain-Atheris sidesteps the Docker/build-context
problem entirely rather than working around it.

Also checked how Scorecard's Fuzzing check actually decides pass/fail
(`checks/raw/fuzzing.go` in `ossf/scorecard`): for Python it just looks for
the literal string `import atheris` in a tracked `*.py` file — it does not
require ClusterFuzzLite specifically and does not re-run CI to verify
anything. A plain-Atheris setup satisfies the Scorecard check for free once
real harnesses exist in the repo, as a side effect of them being genuinely
useful rather than as the goal — the point is to actually fuzz.

### Plan

1. **`requirements/fuzz.txt`** (new, hash-pinned like the other
   `requirements/*.txt` files) — pins `atheris` only. Deliberately **not**
   folded into `requirements/dev.txt`: Atheris ships Linux-only wheels (no
   macOS/Windows), so adding it there would break `pip install -r
   requirements/dev.txt` for contributors on those platforms. Only the
   fuzzing CI job installs this file.
2. **`fuzz/`** (new top-level dir) — one harness per target, importing the
   real app function rather than re-implementing its logic (mirroring how
   `tests/test_documents.py:1790` already imports `_safe_join` directly).
   Each harness asserts the actual security invariant, not just "doesn't
   crash" — e.g. "result never has a URL scheme or netloc", "joined path
   never escapes the root".

   **Phase 1 — prove the pipeline** (land first, validate a real GitHub
   Actions run end to end before expanding):
   - `reservations.routes._safe_next` — open-redirect guard (the one proven
     out locally above).
   - `documents.routes._safe_join` — path-traversal guard for uploaded-file
     storage paths.
   - `documents.routes._safe_path_component` — filename sanitization.

   **Phase 2+ — expand aggressively** once Phase 1 is green in real CI. Go
   broad across every place untrusted input is parsed, not just security
   guards — a crash or hang is a bug worth finding even outside a security
   boundary. Candidate targets, roughly in the order they're likely to be
   easy to wire up:
   - ~~`pilots/logbook_import.py`'s column-fingerprint/header-matching
     logic~~ **Done (2026-07-23).** Landed as two harnesses rather than one
     (`fuzz/fuzz_logbook_parse_file.py` for the CSV/XLSX-bytes entry point,
     `fuzz_logbook_value_parsers.py` for the `parse_date_value`/
     `parse_time_value`/`parse_duration_value`/`parse_int_value` cell
     parsers) plus a GPS one (see below) — importing the real functions
     directly rather than reimplementing CSV/XLSX structure by hand. Found
     two real bugs before this was even pushed (same pattern as Phase 1's
     `_safe_next` finding):
     1. `_parse_csv` didn't catch `csv.Error` — the stdlib `csv` reader
        itself can raise it (e.g. "new-line character seen in unquoted
        field") on certain byte sequences, causing an unhandled 500 on
        upload instead of the documented `ValueError`. Fixed with a
        try/except around `list(reader)`.
     2. `parse_int_value`'s string branch parsed `"-2"` via
        `int(float("-2"))` **without** the negative-value guard the
        int/float branches already had, and separately raised
        `OverflowError` (not `ValueError`) on strings like `"1e400"` that
        overflow to `inf`. Both fixed; a negative or overflowing
        `landings_day`/`landings_night`/`landing_count`/`passenger_count`
        cell is now rejected like any other malformed value.

     Also discovered mid-implementation: plain `@atheris.instrument_func` on
     the harness (Phase 1's pattern) only instruments the wrapper, not the
     imported app code — coverage plateaued at 2 basic blocks and Atheris was
     effectively fuzzing blind. Wrapping the target import in
     `with atheris.instrument_imports():` fixed this (verified locally: cov
     2 → 51+, genuine corpus-driven exploration). Worth retrofitting to the
     Phase 1 harnesses at some point, but not done here to keep this change
     scoped to the new targets.
   - ~~GPS/GPX/KML track import parsing~~ **Done (2026-07-23).**
     `fuzz/fuzz_gps_import.py` imports `parse_gps_file(data, filename)` from
     `aircraft/gps_import.py` directly (no Flask dependency, unlike the
     logbook import above — no app-context setup needed). ~1.8M executions
     in local smoke testing, no crash found yet; GPX/KML need well-formed
     XML-ish structure to explore deeply, which blind/corpus mutation alone
     reaches slowly — the persisted CI corpus should help over time.
   - ~~Numeric/date parsing in `flights/routes.py` and `pilots/routes.py`~~
     **Done (2026-07-23), no refactor needed after all.** Discovered while
     starting the planned refactor: `flights/form_parsing.py` and
     `pilots/form_parsing.py` already exist as standalone, importable
     `parse_flight_fields`/`parse_pilot_fields`/`parse_linked_pilot_fields`
     functions (shared between the online forms and the offline sync API) —
     the "needs a refactor first" note below was written without having
     found these yet. `fuzz/fuzz_flight_form_parsing.py` and
     `fuzz_pilot_form_parsing.py` import them directly. Found two real bugs
     before pushing:
     1. Six fields in `parse_flight_fields` (`flight_time`,
        `passenger_count`, `landing_count`, `fuel_added_qty`,
        `fuel_remaining_qty`, `oil_added_l`) assigned the parsed value
        *before* validating its sign, then raised `ValueError` to reject a
        negative one — but the `except` block only appended an error
        message without resetting the field back to `None`, so a negative
        input still came back in the returned `values` dict alongside the
        rejection error. Both current call sites already check `errors`
        before persisting `values`, so this wasn't reaching the database in
        practice, but it violated the function's own contract and a
        counter-derived variant of the same issue *was* a real behavioural
        difference (next point). Fixed by resetting each field to `None` in
        its `except` block.
     2. Relatedly, `flight_time` derived from
        `flight_time_counter_end - flight_time_counter_start` had no clamp,
        unlike the sibling engine-counter branch which already did
        `max(0.0, raw_diff)` — an end-before-start counter pair produced a
        large *negative* `flight_time` (still returned to the caller
        alongside the counter-order error). Fixed with the same clamp.
     3. `pilots/form_parsing.py`'s `_parse_time` split `"HH:MM"` and passed
        both halves through the unbounded `int()`, then only caught
        `(ValueError, AttributeError)` around the `datetime.time()`
        constructor — but `time()` is C-backed and raises `OverflowError`
        (not `ValueError`) once the value no longer fits a C `long`, e.g. an
        hour string of 20+ digits. Reachable via the pilot logbook form's
        departure/arrival time fields and the offline sync API. Fixed by
        also catching `OverflowError`.

     Also discovered mid-implementation: `models.py`'s own dependency
     chain (Flask, Jinja2, Babel, Werkzeug, …) is large enough that
     unscoped `atheris.instrument_imports()` on a harness importing
     anything that pulls in `models` took **~55s of one-time setup** before
     fuzzing even started — a meaningful tax against the 120s push-triggered
     budget. `atheris.instrument_imports(include=["<dotted.module.name>"])`
     scopes instrumentation to just the target module (submodules of any
     listed package are included automatically, per Atheris's own docs) —
     verified locally this cuts setup to under 1s with no meaningful loss in
     coverage-guided exploration of the function actually being fuzzed.
     Applied to these two new harnesses; retrofitting the pre-existing ones
     is tracked separately below.
   - ~~`maintenance/routes.py`'s trigger/service-record parsing~~ **Done
     (2026-07-23).** New `maintenance/form_parsing.py` (matching the
     flights/pilots pattern), extracting `_save_trigger`/`service_trigger`'s
     inline validation into standalone `parse_trigger_fields`/
     `parse_service_fields`. Along the way, used the clean
     "helper returns `Optional` directly" pattern (`_parse_positive_int`,
     `_parse_nonneg_float`, etc.) rather than the flights/form_parsing.py
     "assign-then-raise-then-except" pattern whose bug this same session
     already fixed there — avoiding reintroducing the same class of bug
     here. `fuzz/fuzz_maintenance_form_parsing.py` fuzzes both functions
     directly; 2.3M executions locally, no crash found. New direct unit
     tests (`tests/test_maintenance_form_parsing.py`) for 100% coverage of
     the new module — existing route-level tests only reached 93% (never
     sent a genuinely non-numeric `interval_days`/`due_engine_hours`/
     `interval_hours`, only out-of-range numeric ones).
   - ~~`secure_filename`/extension-allowlist logic at every upload site~~
     **Audited (2026-07-23), no new harness.** Manually checked all ~15
     `secure_filename` call sites across `documents`, `pilots`, `aircraft`,
     `expenses`, `config`, `flights` for a missing filename `None`-guard
     (`secure_filename(None)` raises `TypeError`, and `FileStorage.filename`
     is `None` — not `""` — when a multipart part omits `filename=`
     entirely). Not really a fuzzing target in its own right: beyond
     `secure_filename` itself (werkzeug's own well-tested code, not ours),
     the surrounding logic is just `os.path.splitext(...)[1].lower()` plus a
     set-membership check — stdlib composition with no custom parsing, and
     the real path-safety logic (`_safe_join`/`_safe_path_component`) is
     already fuzzed since Phase 1. Found one real gap from the manual read:
     `flights.routes._save_upload` called `secure_filename(file.filename)`
     with no guard at all — safe today only because its one caller already
     checks `photo_file.filename` truthy first, but the function itself had
     no defense if called another way. Fixed with the same `... or ""`
     fallback already used at every other guarded call site, with a
     regression test (`tests/test_flights.py::TestSaveUploadNoneFilename`)
     constructing a real `werkzeug.datastructures.FileStorage(filename=None)`
     directly, since the guarded route can't reach this path itself.
   - ~~Backup file format parsing (the AES-256-GCM backup header/envelope,
     parsed *before* decryption)~~ **Done (2026-07-23).** The nonce/ciphertext
     split before decryption turned out to already be robust everywhere
     (plain byte slicing, and every `AESGCM(...).decrypt(...)` call was
     already wrapped in a broad `except Exception`) — the real gap was
     *after* decryption: `app/init.py`'s `restore_backup_command` (the
     actual CLI restore path) parsed the decrypted zip inline with **no
     error handling at all**, while `services/backup_verification.py`'s
     `verify_backup_record` (read-only post-backup check) already handled
     the same structure defensively. A malformed/truncated archive, or one
     simply missing `openhangar.sql`, would crash the restore CLI with a
     raw `zipfile.BadZipFile`/`KeyError` traceback instead of a clean error.
     Extracted the shared logic into new `app/services/backup_format.py`
     (`parse_backup_archive`, `BackupArchiveError`) — used by both callers
     now, `require_metadata=True` preserving verification's stricter
     original requirement (restore's own original behaviour already
     tolerated a missing manifest) so neither caller's behaviour changed,
     only restore's error handling improved to match verification's.
     Also folds in verification's CRC (`zf.testzip()`) and
     "does this look like a pg_dump" (`_SQL_DUMP_MARKER`) checks — both new,
     stronger guarantees for the restore path than it had before.
     `fuzz/fuzz_backup_format.py` fuzzes the shared function directly (8.4M
     executions locally, no crash — cov plateaus quickly without a seed
     corpus, same known limitation as the GPX/XLSX binary-format harnesses).
     New `tests/test_backup_format.py` for 100% direct coverage, plus a CLI
     regression test in `tests/test_backup.py` for the
     `BackupArchiveError` path. Found two test fixtures
     (`test_backup.py`'s `_make_valid_dump()`,
     `tests/functional/test_journey_backup_restore.py`'s
     `_FAKE_SQL_DUMP`) using a placeholder marker string that only ever
     worked because restore's original code never checked it — updated
     both to the real pg_dump marker now that restore validates it too.
   - Any other hand-rolled parser/validator found by grepping for
     `request.get_json()`, `request.form.get(...)` followed by manual
     type coercion, or regex-based input validation outside what WTForms/
     SQLAlchemy already validates — audit for these opportunistically as
     Phase 2+ work proceeds rather than front-loading a complete list now.
3. **`.github/workflows/fuzzing.yml`** (new) — plain `ubuntu-latest` job(s),
   no Docker, no ClusterFuzzLite actions:
   - `actions/checkout` + `actions/setup-python` pinned to `3.14` (match
     `ci.yml`).
   - `pip install --require-hashes -r requirements/fuzz.txt` plus whatever
     runtime deps the harnesses transitively import (Flask/Werkzeug etc.,
     already in `requirements/runtime.txt`).
   - **One matrix job per harness** (`strategy.matrix.harness: [...]`) rather
     than looping through all harnesses sequentially in a single job — this
     is what keeps wall-clock time roughly constant as the target list grows
     from 3 to dozens: adding a harness adds a parallel matrix leg, not more
     serial time. Each leg runs its own harness for a fixed
     `-max_total_time=N`.
   - Trigger: `push` to `main` (i.e. after a PR merges), not `pull_request` —
     matches the pattern most other workflows here use (`codeql.yml`,
     `scorecard.yml` both trigger on push to `main`, not on every PR), and
     means this workflow mechanically cannot affect whether a PR can merge.
   - Cadence/duration: each harness fuzzes for ~90–120s per merge to `main`
     (all legs run in parallel, so total wall time stays close to that
     regardless of harness count — comparable to, not longer than, the
     existing ~7–8 min `lint-and-test` job). On a weekly `schedule`, each
     harness gets a much deeper budget (~20 min / `-max_total_time=1200`),
     same matrix, same parallelism.
   - **Corpus caching** via `actions/cache`, one cache per harness so a
     slow-to-explore harness doesn't evict a fast one's corpus:
     - Corpus lives at `fuzz/corpus/<harness>/` (gitignored; only populated
       in CI), passed as the positional corpus-dir argument to the harness
       (standard libFuzzer/Atheris convention — new interesting inputs are
       written there automatically as the harness runs).
     - Use the split `actions/cache/restore` + `actions/cache/save` actions
       (not the combined `actions/cache`) so the save step can run with
       `if: always()` — a harness that finds a crash fails the job, but the
       corpus growth from that run should still be kept.
     - Key: `fuzz-corpus-<harness>-${{ github.run_id }}`, restore-keys:
       `fuzz-corpus-<harness>-` (the standard "growing cache" pattern —
       always restores the most recent prior run's corpus for that harness,
       always saves a new snapshot under a unique key). GitHub evicts old
       entries under the repo's overall cache-size cap; no manual pruning
       needed.
   - **On a crash, findings surface three ways**, all gated on `if: failure()`:
     1. Atheris writes a `crash-<hash>` repro file to the working directory;
        a follow-up step re-runs the harness once against that file (a
        single deterministic execution, not another fuzzing pass) to
        capture a clean traceback into `crash-repro.log`.
     2. **Job summary**: `crash-repro.log` is written straight into
        `$GITHUB_STEP_SUMMARY`, so the failed run's page shows the actual
        traceback without downloading anything.
     3. **Security tab**: `.github/scripts/fuzz_crash_to_sarif.py` (new,
        mirrors the existing `.github/scripts/pip_audit_to_sarif.py`
        pattern) converts `crash-repro.log` into a minimal SARIF 2.1.0
        document — `ruleId` = harness name, location = the deepest
        `app/`-relative traceback frame (falls back to the harness file
        itself if none found) — uploaded via
        `github/codeql-action/upload-sarif` with `category:
        fuzz-<harness>` (needs `security-events: write` on the job).
        GitHub dedupes by rule+location per ref, same as CodeQL/bandit/
        pip-audit already do in this repo, so a recurring unfixed crash
        across weekly runs doesn't spam.
     4. The raw `crash-<hash>` file is also uploaded as a downloadable
        artifact via `actions/upload-artifact`, for full local repro.
   - **Never blocks the release/CI gate**: this workflow is intentionally
     independent of `ci.yml`. A crash surfaces via the three channels above
     but must not fail `lint-and-test` or otherwise block merging/tagging a
     release. See "Failure policy" below for the reasoning.
   - Top-level `permissions: read-all`, per this repo's existing workflow
     convention (see `ci.yml`).
   - **Requires explicit maintainer approval before merging**, per AGENTS.md
     "What not to touch without human approval" (any `.github/workflows/`
     change).

### Decisions (resolved 2026-07-21)

- **Cadence**: push-to-`main` (post-merge) + weekly batch, both via the
  per-harness matrix above — ~90–120s/harness per merge (parallel, so total
  time doesn't grow with harness count), ~20 min/harness weekly. Explicitly
  not capped at a token "60s" — comparable in spirit to the ~7–8 min the rest
  of CI already takes. Triggered on push to `main` rather than
  `pull_request`, matching most other workflows here — see the failure
  policy below for why this also makes "non-blocking" true mechanically, not
  just by convention.
- **Findings surface via the Security tab + job summary**, not an
  auto-filed GitHub Issue — SARIF upload gets free dedup by rule+location
  (matching the CodeQL/bandit/pip-audit precedent already in this repo),
  where auto-filing an issue would need its own dedup logic to avoid
  re-filing the same unfixed crash every week. Revisit issue-filing later if
  the Security tab alone doesn't get enough attention in practice.
- **Corpus persistence**: yes, via `actions/cache`, split restore/save with
  `if: always()` on save and a per-harness growing-cache key (design above).
  Judged not too complex to justify skipping.
- **Target list**: start with the Phase 1 three-harness "safe minimum" to
  validate the actual GitHub Actions run end to end, then expand
  aggressively per the Phase 2+ candidate list above — deliberately broad
  surface, not just the original three security guards.

**Phase 1 already shipped a finding.** The `fuzz_safe_next` harness found a
real bug in local validation before this was even pushed: `_safe_next()`
called `urlparse(next_url)` unguarded, and `urlparse("//[")` raises
`ValueError: Invalid IPv6 URL` (malformed IPv6-bracket syntax) — reachable
via an HTTP request's `next` parameter, e.g. `?next=//[`, causing an
unhandled 500 instead of the intended fallback redirect. Fixed in
`app/reservations/routes.py` (`try`/`except ValueError` around the
`urlparse` call, falling back like any other rejected `next` value), with a
regression test in `tests/test_reservations.py`. Left in as a concrete
example of what this class of harness is for, and as validation that the
Phase 1 harnesses execute real, meaningful test cases rather than just
proving the CI plumbing works.

### Failure policy: fuzzing findings are non-blocking

A fuzzing crash/assertion failure does **not** block a PR merge or a release
— and, since this workflow triggers on push to `main` rather than on the PR
itself (see "Cadence" above), it cannot mechanically affect whether a PR is
mergeable in the first place. Reasoning:

- Today OpenHangar ships with zero fuzzing coverage — the app is already
  released "as is" with respect to whatever fuzzing might find. Making
  fuzzing suddenly release-blocking the moment it's introduced would be a
  strictly worse gate than the status quo (a red, unrelated CI check
  blocking an otherwise-ready release), not a safer one.
- Fuzzers can find things that are not real, user-facing security bugs — an
  `AssertionError` in a harness can just as easily mean the harness's
  invariant was too strict, or Atheris explored a genuinely unreachable
  input (e.g. a value the calling route already rejects via WTForms
  validation before the fuzzed function ever sees it) as it can mean a real
  bug. Every finding needs a human to triage: is the input actually
  reachable from an HTTP request, and does the failure matter? Auto-blocking
  releases on unreviewed fuzzer output would incentivize weakening harness
  assertions rather than fixing real issues.
- Mechanically, this workflow is already a separate `.yml` from `ci.yml`
  (see above) — it has no `needs:`/status-check relationship to the jobs
  branch protection actually requires, so this is the natural default, not
  an extra step.
- Revisit this once the harness set has run long enough to build confidence
  it doesn't produce noisy/false-positive failures — at that point, specific
  *proven-reliable* harnesses could graduate to blocking if desired. Not a
  day-one decision.

### Fuzz coverage report (added 2026-07-23)

`scripts/fuzz_coverage_report.py` replays each harness's persisted corpus
through its real `TestOneInput` under `coverage.py` (Atheris's own bytecode
instrumentation disabled via monkeypatch for the replay, so it doesn't
interfere with `coverage.py`'s line tracing), restricted to the specific
modules the harnesses target (`_TARGET_MODULES` in that script) rather than
a broad `app/*` glob — a broad glob also picks up whatever those modules
transitively import at module level (`models.py`, `utils.py`), which
"covers" highly just from class/def bodies executing at import time, not
from anything a harness actually exercises.

Wired into `ci.yml`'s `lint-and-test` job (same job that already generates
the pytest `htmlcov/`/`coverage.xml`/badge): installs `requirements/fuzz.txt`,
restores each harness's corpus cache via `restore-keys` prefix match
(falling back to `main`'s most recent cache — this job never writes one
back), replays it, and generates `htmlcov-fuzz/` + `coverage-fuzz.xml`.
Copied into `_site/fuzz-coverage/` in the existing "Assemble Pages site"
step, so it deploys at release time exactly like the pytest report does —
no new Pages-deploy machinery, no `pages:write`/`id-token:write` needed in
`lint-and-test`, since that job only ever *assembles* `_site/`; the actual
deploy stays in the existing tag-gated `publish` job.

**Badge (revised 2026-07-23):** the first version used a `genbadge`
percentage badge labeled "fuzz coverage", same as the pytest one — but a
low percentage next to the 100%-required pytest Coverage badge reads as a
failing metric, when it's actually a different-in-kind number that's only
ever going to be a small fraction of the target files' lines (see above).
Replaced with a harness-count badge instead ("fuzz harnesses: N") — `curl`
against shields.io's static-badge endpoint with the count of `fuzz/fuzz_*.py`
files, no `genbadge` involved. Auto-updates whenever a harness is added or
removed, same as the percentage version did, just measuring something that
reads as unambiguously informational rather than a score.

**First-release gap:** the very first release built after Phase 2's three
new harnesses landed only showed 2 of the 4 target files in the coverage
report (`reservations/routes.py`, `documents/routes.py` — the Phase 1
targets). Not a bug: `fuzzing.yml` only grows a harness's corpus on
push-to-main/weekly schedule, and that release's Pages-assembly build ran
before `fuzzing.yml` had run even once for the brand-new harnesses — so
their corpus caches didn't exist yet for `ci.yml` to restore.
`fuzz_coverage_report.py` skips a harness with no corpus dir entirely
(rather than reporting 0%), so the file doesn't appear in the report at
all until that gap closes on its own at the next release. Documented in
`docs/development.md`'s "Fuzzing" section so this doesn't look broken next
time a harness is freshly added.

Linked from the README (`Fuzz harnesses` badge, next to `Coverage`) and
documented in `docs/development.md`'s "Fuzzing" section.
