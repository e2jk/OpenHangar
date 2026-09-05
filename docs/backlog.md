# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

## Follow-up: let a second crew member claim their own slot on an existing flight

**Depends on** the single-`Flight`-table refactor (shipped 2026-07-24,
unifying `FlightEntry`/`FlightCrew`/`PilotLogbookEntry` into one `Flight`
table) — this is explicitly a *later* pass, not part of it.

Today there is still no UI path for a second crew member to attach their
own logbook figures to a flight someone else already logged —
`_find_duplicate_flight` only ever offers "create a duplicate" or "just
attach the GPS track" when it spots a near-match. Without this, the
second-crew slot on a `Flight` row
stays name-only (same limitation `FlightCrew` has today) and the whole
point of giving it independently-trackable EASA figures goes unused
unless the *same person* who created the row happens to occupy both
identity fields.

Needed: a "this flight is already logged — claim your slot" flow,
findable from the pilot's own logbook (e.g. an action on a near-match
result, or a dedicated search-by-date/route/aircraft lookup), that lets
a second pilot attach their own `user_id` into whichever slot
(`pic_user_id`/`second_crew_user_id`) matches their real role on that
flight, without needing edit rights over the fields the original logger
already filled in. Needs the same care already established for the
per-user boundary on shared flights (`edit_flight` today never touches
another pilot's linked entry) — claiming a slot should not let a second
pilot silently overwrite the header fields the first pilot logged.

---

## Shared ownership: deferred scope from Phase 39

Phase 39 (Shared Ownership) deliberately excluded the following; none of it
is scheduled, but each is a plausible follow-up if a real need surfaces:

- Voting weights, meeting/quorum features.
- Automated enforcement of overdue balances (emails, blocks) — the overdue
  flag is visual only today; would need a new notification type.
- Departing-owner settlement automation (manual payments/adjustments cover
  it today).
- Charging the second-crew slot, instruction splits, per-owner rate
  overrides.
- Pro-rating fixed expenses for billing (currently a reporting-only concept
  from Phase 36).
- PDF statements (no PDF-generation pipeline exists in the app today; CSV
  export covers this instead).
- Multi-currency (the shared billing ledger core is single-currency by
  design).

---

## Offline editing: consolidate on the workbenches, add "new row"

**Stale since the 2026-07-24 logbook-unification refactor** (`FlightEntry`/
`FlightCrew`/`PilotLogbookEntry` → one `Flight` table) — re-verify the
technical details below before starting. In particular: there's no more
nested `pilot` sub-diff/`PILOT_FIELDS` in `offline_workbench.js` (EASA
figures are now flat fields alongside everything else), no more
`apply_linked_pilot_entry` or `create_pilot` toggle in `flights/routes.py`
(replaced by `apply_pilot_identity` + `pilot_role`), and no more separate
`PilotLogbookEntry` to create alongside a `FlightEntry` — a "linked" pilot
entry is just the same `Flight` row with `pic_user_id`/`second_crew_user_id`
set. The core problem this item describes (two offline-editing paths, one
of them add-only via a blind resubmission queue) still exists; the specific
mechanics of "case 1" below need re-deriving against the current single-row
model, not implemented as written.

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

**Likely related to** the "data integrity" audit-view idea below (from a
friend's single-aircraft records site) — that item's "conflicting hour-meter
readings" case is essentially this same check, generalized into a
first-class audit page rather than a narrower per-aircraft view. Worth
designing together rather than building this one first and a separate,
overlapping page later.

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
       subscription_info={
           "endpoint": sub.endpoint,
           "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
       },
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
  creates e2e-only extras inline (two future-dated deletable `Flight` rows
  `fe_del1`/`fe_del2`, the admin's most recent flight claimed in place as
  their own pilot-log row, a standalone FSTD `Flight` row, and
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

---

## Human task: add a WEBLATE_API_TOKEN secret for the Weblate quality-check scan

`.github/workflows/weblate-i18n-scan.yml` runs
[e2jk/weblate-checks-action](https://github.com/e2jk/weblate-checks-action)
(originally developed in this repo, split out 2026-07-25) daily, uploading
Weblate's quality-check flags to Code Scanning under category
`weblate-i18n`. One thing only a human/maintainer with repo-settings access
can do:

- **Add a `WEBLATE_API_TOKEN` repository secret** (Settings → Secrets and
  variables → Actions), from a token created at
  `https://hosted.weblate.org/accounts/profile/#api`. Without it the
  workflow still runs, just against the 100 requests/day anonymous quota
  instead of 5000/hour. This workflow's own API usage is tiny (one request
  per language, not per flagged string — see the action's README), but
  GitHub-hosted runners share a rotating pool of egress IPs across unrelated
  CI jobs worldwide, and that quota is keyed by IP — so it can already be
  spent by someone else's workflow before this one runs.

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

## Fix a mis-entered PIC/second-crew identity on an existing flight

Today there's no supported way to correct the *identity* fields
(`pic_user_id`/`pic_name`, `second_crew_user_id`/`second_crew_name`/
`second_crew_role`) on a flight if the wrong person was recorded — as
opposed to the aircraft/registration, which is now fixable via the
aircraft selector on the edit form (see the commit adding that). Likely a
fringe case for a solo pilot (why would you log a flight in your own name
and later realize it wasn't you?), but plausible in a shared-ownership or
flight-club setting where an admin enters flights on behalf of the
partnership/members and mixes up who was PIC vs second crew, or attaches
the wrong member's account to a slot entirely. Needs the same care as the
aircraft-reassignment fix: reassigning a crew slot away from a pilot
changes *their* logbook total hours, currency tracking, and any per-pilot
billing that reads `pic_user_id`/`second_crew_user_id` — should be
reflected automatically the same way (those are live queries against
`Flight`, not a cached/denormalized total), but worth double-checking
before shipping this, especially around co-owner billing
(`services/co_owner_billing.py`).

---

## Pre-flight photos, alongside the existing post-flight ones

The flight form's Photos section (flight/engine counter + fuel) is
implicitly post-flight — encouraging a photo taken right after shutdown,
as proof/backup for the readings entered above. Some pilots may also want
to snap a photo *before* the flight (e.g. the counters at block-off, or a
walk-around/damage photo), which isn't facilitated today. Would need: a
second set of photo fields (or a single field pair reused with a
before/after toggle — needs a design decision), updated labels/help text
distinguishing the two, and a decision on whether pre-flight photos feed
any validation (e.g. cross-checking the pre-flight counter reading against
the previous flight's post-flight one, similar to the existing counter
continuity warning).

---

## Training dashboard: instructors/aircraft variety + user-defined training phases

A dashboard summarizing training progress for a pilot: how many different
instructors they've flown with, how many different aircraft, hours per
phase, etc. — useful for a student working through initial PPL, then later
an instrument rating, then further ratings, each a distinct "phase" of
their flying career with different relevant stats.

Needs a way for the pilot (or their instructor/school) to define training
phases — each a date range (start, optional end for the current/ongoing
one) with a label (e.g. "PPL", "Instrument rating", "Night rating") — used
to group existing `Flight` rows by date into the matching phase for
reporting. Open questions: new dedicated model (e.g. `TrainingPhase`:
`pilot_user_id`, `label`, `start_date`, `end_date`) vs. reusing something
existing; how phases interact with the personal-minimums/currency
tracking already in `pilots/personal_minimums.py`; whether this belongs in
`flight_school` operating-model scope specifically or is generally useful
across models.

---

## Ideas gathered from a friend's single-aircraft records site

A friend built a static, owner-maintained "records site" for one aircraft
(private, password-protected — not linked here). It's not a fleet-management
app, but several of its ideas are worth considering for OpenHangar's own
airworthiness/maintenance/document features. None of this is scoped or
prioritised; recorded here as raw inspiration.

- **Show the *basis* for a due date, not just the countdown.** Every status
  tile on their dashboard pairs the due-date/hours-remaining with a one-line
  "basis": last-done date + interval + the regulation or house rule it comes
  from (e.g. "Last change 2026-03-02 at tach 4821.3 · Lycoming SB 480F").
  OpenHangar's airworthiness tracker and maintenance triggers currently show
  the computed due value; consider always surfacing *how* it was computed
  (source entry + interval) inline, not just on drill-down.

- **AD/SB compliance board tied to serial numbers, with an exportable annual
  checklist.** A per-aircraft board of Airworthiness Directives/Service
  Bulletins, each tagged recurring/conditional/verify-part-number/closed/N-A,
  with a disclaimer that it's an owner's working summary, not the
  authoritative FAA record. Below it, a checklist of "applicability items to
  confirm with the IA at the annual" (checkboxes persisted client-side, plus
  a "copy as plain text" button to paste into a work order/email). OpenHangar
  has no AD/SB tracking today — this could be a genuinely new module under
  `airworthiness`, distinct from the generic document/trigger model, with a
  print/export view for the annual.

- **A "data integrity" page that discloses record-keeping gaps as a first-class
  feature** (see also "Logbook: counter continuity discrepancy detection"
  above — likely the same underlying check, generalized), instead of only
  tracking what's compliant. Their site has a
  dedicated page listing every place their own paper trail contradicts itself
  (e.g. three different computed "hours since overhaul" figures that don't
  agree), each with severity, consequence, and a suggested resolution — plus
  documented transcription conventions (`[illegible]`, `[sic]`, etc.) for
  hand-transcribed historical entries. For OpenHangar this maps to a new kind
  of audit view — separate from snags — that surfaces things like conflicting
  hour-meter readings or gaps in the logbook chain, useful for shared
  ownership handoffs, resale, or partner trust generally.

- **A computed "what's the next thing to do" agenda.** Rather than just
  listing every trigger sorted by date, their maintenance planner computes
  one sentence naming the single most urgent action across *all* trigger
  types (oil, annual, AD, ELT battery, etc.), then an ordered "then, in
  order" list for the rest. Worth considering as a small addition on top of
  the existing maintenance-trigger list: a one-line "next action" summary per
  aircraft.

- **A printable one-page "hangar sheet".** A `@media print`-only view
  combining open squawks + on-hand parts + parts due for reorder, meant to be
  printed and carried to the aircraft. OpenHangar's snag list and maintenance
  triggers could gain an equivalent print stylesheet/view for offline
  ramp/hangar use — cheap to add, no new data model needed.

- **Oil-analysis trend log with a benchmark reference line.** A dedicated
  engine-health page charting each wear-metal/viscosity parameter over every
  logged oil sample, with a "universal average" (manufacturer/fleet
  benchmark) line on each chart, and short auto-generated findings (e.g. "the
  January spike was chased with a shortened interval and closed by three
  clean follow-up samples"). OpenHangar has cost tracking and maintenance
  triggers but nothing engine-health-specific; an oil-sample log + trend
  chart (even without auto-narrative) would be a natural airworthiness/
  maintenance sub-feature for owners who do regular oil analysis.

- **IFR-specific currency items tracked alongside airworthiness**: VOR
  accuracy check (14 CFR 91.171, 30-day validity) and nav database cycle
  (28-day), each with the regulatory citation and tolerance shown inline.
  Currently out of scope for OpenHangar's airworthiness tracker (which is
  ARC/AD/insurance-oriented) but could be added as configurable/optional
  trigger types for IFR-equipped aircraft.

- **Owner "insight log" — a tag-filterable decision journal**, separate from
  the maintenance logbook and from snags: dated entries capturing *why* a
  decision was made ("the call" + "because"), not just what was done. Useful
  institutional memory that logbooks don't capture (why a repair was
  deferred, why a vendor was chosen). Could be a lightweight new note/insight
  feature tied to an aircraft, filterable by tag, linking out to the
  relevant document/trigger/squawk.

- **Parts reorder prediction from historical order-date cadence** (not a
  fixed schedule): for each consumable, compute the average interval between
  past orders and flag "Due"/"Soon"/"Not yet" based on time since the last
  one, plus a small "do not reorder blindly" flag list for part numbers known
  to be wrong/superseded. Distinct from OpenHangar's cost tracking; would
  need an actual parts/inventory model first.

- **Aggregate flight-statistics view**: total nautical miles, airports/states
  visited, "corners of the map" (farthest N/S/E/W, highest/lowest field),
  longest single leg and longest multi-leg day, all computed from existing
  flight-log data. For shared ownership/flight club, a combined-vs-per-pilot
  stats table that de-duplicates flights logged by multiple crew. OpenHangar
  already has flight logging + GPS import; this is a reporting view on top,
  no new data model needed beyond what `Flight` already has.

- **Avionics/equipment inventory with per-unit status and an upgrade wish
  list.** A page listing each installed avionics unit with role, status
  (serviceable/open-squawk/placarded-inoperative), certification history,
  and STC/AFMS approvals — plus a separate "wish list" of planned upgrades
  with rough cost and what installing them would require. Could extend
  OpenHangar's aircraft/document model with an equipment sub-list distinct
  from generic documents.

