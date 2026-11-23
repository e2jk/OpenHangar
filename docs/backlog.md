# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

## Airfield passports: deferred from Phase 41

Optional or stretch items of the airfield-passport phase, deliberately left out
of its core scope (decided 2026-11-21).

### GPS stop candidates

Propose candidate intermediate stops from GPS position, speed and dwell evidence
and show that evidence for confirmation. Proximity alone or a low-speed point
must never award a stamp. Test touch-and-go, taxi, GPS gaps/noise and nearby
parallel fields. Manual intermediate stops with the flight as supporting
evidence already exist.

### Runway suitability comparison in "Where next?"

User-entered runway length/surface/weight requirements compared against
licensed runway/restriction data, with source and date shown. Mark missing
values "unknown"; never imply operational suitability from aircraft weight or
runway length alone. The map must remain usable without OpenAIP access.

### Passport-only instance (publisher-operated)

Optional extension after the core phase, not a prerequisite for marking its
agreed core scope complete. An operator could run OpenHangar solely as a passport
completion service, without setting up aircraft, maintenance, reservations or a
pilot logbook. Example: propose a publisher-operated instance to LandeGut, where
Landegutscheinheft customers maintain a map of their own visits across editions.
This is a possible offering, not an existing publisher partnership.

- Provide an explicit passport-only instance setup/presentation using the same
  passport/account/series implementation. Onboarding and the home page lead straight
  to editions and personal progress; hide unrelated navigation and enforce the
  corresponding server-side access limits. No aircraft or full pilot profile is
  required to register visits. Decide the configuration mechanism before building;
  adding a new operating model requires operator agreement under AGENTS.md.
- Operator manages the catalogue, linked editions and customer enrolment.
  Reuse passport-scoped invitations initially; any purchase verification, publisher
  customer-account integration or open registration is separately specified before
  implementation. No payment/e-commerce system required for the stretch goal.
- Customer experience: select an enrolled edition, record visits on a phone,
  see the personal map fill, and browse their previous/combined editions. Retain
  export, account security and independent private completion records.
- Optional customer community: pilots can choose to share visits and airfield
  comments/tips with authenticated members of the defined publisher community.
  Personal tracking works without opting in. Distinguish viewing community content
  from consenting to share one's own activity; do not automatically turn every
  customer into a publicly ranked participant or share private history. Define
  publisher-community versus smaller friends-group audiences explicitly, with
  withdrawal and moderation controls. No internet-public publication by default.
- Validate fresh passport-only setup, registration/enrolment, manual stamping,
  edition history, community opt-in/withdrawal, cross-customer privacy, moderation,
  export/backup/restore and disabled-feature access. Document deployment and add
  representative demo/screenshots. Optional later pilot activation must follow
  the instance policy rather than exposing fleet features automatically.

---

## Follow-up: let a second crew member claim their own slot on an existing flight

**Depends on** the single-`Flight`-table refactor (shipped 2026-07-24,
unifying `FlightEntry`/`FlightCrew`/`PilotLogbookEntry` into one `Flight`
table) — this is explicitly a *later* pass, not part of it.

**Related but separate**: [PR #254](https://github.com/e2jk/OpenHangar/pull/254)
added type-ahead name suggestions for the free-text `crew_name_*`/`pic_name`
fields (`tenant_pilot_names()` in `utils.py`) — autocomplete only, picking a
suggestion just fills in the text, no `user_id` is ever attached. Useful
prior art (same tenant/pilot-role query this item would also need to find
candidate accounts) but doesn't implement any part of the "claim your slot"
flow below, which is still fully unbuilt.

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

**Status — phase 1 built (invite from the logger's side):** picking a tenant
pilot in the flight form's PIC / second-crew name field now records a pending
`FlightCrewInvite` (`flights/crew_invites.py`), e-mails that pilot
(`NotificationType.CREW_INVITE`), and lists it on their dashboard and pilot
logbook (plus a nav badge) with Confirm / Decline. Only confirming writes
their `user_id` into the slot. Deleting never removes another pilot's hours
(`flights/crew_removal.py`): a pilot-side delete or logbook import rollback
only unlinks the acting pilot while another account is linked, and an
aircraft-side delete (flight log, airframe/GPS import rollback, deleting the
aircraft) detaches linked flights into "other aircraft" flights instead of
deleting them.

**Status — phase 2 built (per-pilot edit boundary):** `Flight.created_by_user_id`
records the logger; while linked, only they (plus tenant owners/admins on a
managed aircraft) edit the shared fields, and every save path — flight form,
standalone entry form — restores the other linked pilot's
personal fields (name, role, function hours, new `pic_remarks` /
`second_crew_remarks`). The other pilot edits those on *My part of this
flight* (`flights.crew_entry`) and proposes changes to shared fields as a
`FlightCorrectionSuggestion` the logger accepts or rejects
(`flights/shared_flight.py`), with `crew_invite_answered`,
`shared_flight_changed` and `flight_correction` notifications.

**Status — phase 3 built (claim from the other side):** the duplicate-flight
warning offers *Ask to be added to this flight* when the matched flight was
logged by someone else and the slot for the pilot's role is free; it records
a `FlightCrewInvite` with `kind="claim"` that the logger approves or declines
(`crew_claim` notification). Remaining gap: `_find_duplicate_flight` only
matches *other* pilots' flights on a managed aircraft, so there is still no
claim path for a standalone ("other aircraft") flight someone else logged —
it would need a tenant-wide search by date/registration/route, which exposes
other pilots' private logbook entries and so needs a product decision first.

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

## Pilot logbook: timezone detection from ICAO airfield location

A counter photo's time is converted to UTC with the camera's own UTC offset
(`OffsetTimeOriginal`, written by current phones) when it has one, else with
the device's offset — marked "about" in the suggestion (Phase 46). Still
unreliable without a camera offset when the flight crosses a time zone or the
pilot fills the form somewhere else. Remaining: look up the UTC offset of the
photo's GPS position or of the departure/arrival airfield with a
timezone-by-coordinates database (e.g. `timezonefinder`, a new dependency to
weigh), and flag when departure and arrival time zones differ.

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

## Data checks: follow-ups

The Data checks page (Configuration → Data checks, `services/data_checks.py`)
crosses each logged flight with its GPS track — departure/arrival airfield
(by distance), date, GPS track outside the logged times — with "use the GPS
value" (noting the paper logbook's value on the flight) or "dismiss". Left
for later:

- **Landings:** compare the logged landing count with the landings the GPS
  track shows — needs the detected count stored on `GpsTrack` (or the points
  re-analysed), and touch-and-goes counted the same way as the logbook.
- **Airfields not in the airfield list** (private strips): nothing to measure
  the GPS end against today, so they're skipped. Could compare with the
  aircraft's previous/next flight instead, or let a tenant add its own fields.
- **Times that look like local time** (seen on a real import: a previous
  owner's flights logged 1–2 h off, matching the CET/CEST offset): a
  specific finding offering to shift the logged times, ties in with
  "Pilot logbook: timezone detection from ICAO airfield location" above.
- **Pilots' own view:** the page is for admins/owners and covers aircraft
  flights only; standalone pilot-logbook flights with a GPS track (another
  aircraft) aren't checked, and a pilot can't see findings on their flights.
- **More kinds of checks** on the same page (counter gaps are there now):
  overlapping flights of one aircraft, a flight's pilot-logbook figures
  disagreeing with the aircraft log. The aircraft detail page could also
  flag its own counter gaps.
- **Email digest** of new findings, for admins who rarely open the
  configuration page.

## GPS import: further ground runs in the pilot GPS import

The pilot GPS import now links a ground-only recording like the aircraft
import does — to a logged ground entry, or as a taxi part of the pilot's
flight departing/arriving within 45 minutes at that airfield. Left: the
aircraft import's choice for one further away (up to 3 hours, same day
and airfield: `nearby_flights(..., NEARBY_TAXI_WINDOW)` and the
`ground_candidates` picker on its review page).

## GPS import: one import instead of the aircraft and pilot ones

The aircraft GPS import (`aircraft/routes.py`, `gps_import_*`) and the pilot
one (`pilots/routes.py`, `pilot_gps_import_*`) share the parser, segment
detection, track linking (`services.gps_link`), file archive and state
helpers, but are two route sets and two review templates — fixes keep
having to be made twice (session-cookie overflow, crew overwritten on
confirm, "Accept all best matches", analysing the upload once). Make it one
import with a context — "for aircraft X" or "for my logbook" — whose
differences become options:

| | Aircraft context | Logbook context |
|---|---|---|
| Matches against | that aircraft's flights | the user's flights, any aircraft |
| Unmatched segment becomes | an aircraft-log flight (+ the user's entry by role) | the user's entry (other or managed aircraft) |
| Ground runs (taxi parts, nearby flights) | yes | not yet ("ground runs in the pilot GPS import" above) |
| Undo import | per batch | — |
| Files archived in | the aircraft's folder | the user's folder |
| Access | aircraft owner/pilot roles | pilot access |

The aircraft import's "other aircraft" mode is the logbook context done a
second time, and goes. Steps: one review template, then one confirm path,
then one upload page with the context chosen (current URLs kept as entry
points); check against a real import (Garmin + camera tracks) at each step.

## GPS files: follow-ups to removing them from flights

A GPS file can be taken off a flight from the flight's page (its part of the
track comes off, the file goes to the trash for 7 days, restorable from
"Removed GPS files" — `services/gps_removal.py`). Left for later:

- **Move a file to another flight** (it was linked to the wrong one): today,
  remove it and import it again. Better: removing it lists the file as an
  *unlinked recording*, and "re-import from the archive" sends it through
  the normal review page without uploading it again — the matching picks
  (or offers) the right flight.
- **Split one file over two flights:** a single log covering two logged
  flights (e.g. two legs without an engine stop) is linked to both, so both
  show the whole track. Cut the file's track at the landing in between and
  give each flight its own half — and removing it from one flight then only
  takes that half.
- **Undo one segment on the GPS import review page:** confirming links the
  track at once; picking the wrong candidate means removing the file from
  that flight (Remove file) and importing it again. A per-segment "undo"
  could take that part off the flight (services.gps_removal.remove_part,
  parts recording their batch — the pilot import would have to record one
  too) and offer the segment's choices again.

## Tracks GIF: extend the kept GIF instead of drawing it again

The tracks GIF of an aircraft or a pilot is now kept once drawn
(`services/tracks_gif_cache.py`, per owner, orientation and quality) and
served again until its tracks change — but any change still draws it from
scratch (87 tracks: ~4 s with the tile cache warm, ~25 s cold). Mostly a
new flight only adds a frame at the end, so the kept GIF could be extended
— but a few things tie every frame to the whole set today, to change first:
- the "k / N" counter on each frame (N changes with every new flight);
- frames ordered by date: a backfilled older flight goes in the middle,
  and its frames' views (the zoom-out) shift for everything after it;
- the final "All N tracks" frame, and the one palette taken from it
  (`quantize` on the last frame) shared by all frames.
With those changed (no total on frames, per-frame palettes, the final
frame kept apart), append frames for flights newer than the last one, and
draw it all again only when a track is removed, changed or inserted
earlier.

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

Phase 35 added a PWA with camera capture, which covers the main mobile
use-cases (quick flight entry, Hobbs photo) with no second codebase. Offline
editing (Phases 35 and 38) was removed in November 2026; logbooks stay
readable offline.

A native app (React Native or Flutter) would only add meaningful value if two
conditions are met:

1. **Background push notifications** — Phase 34 email notifications are the
   current channel; native push requires APNs/FCM integration and app store
   distribution, which is a significant ongoing maintenance burden.
2. **Offline editing** — removed from the PWA in November 2026 (only two
   users, and the sync/conflict machinery was a large maintenance and
   security surface); a native app would only be worth it if offline data
   entry turns out to be needed after all.

Prerequisite: Phase 35 (PWA). Re-evaluate
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

## Maintenance: use the due-date projection to tune `MAINTENANCE_DUE_SOON`

The due-date projection itself shipped (`maintenance/due_date_projection.py`:
rolling utilization rate per aircraft, minimum-data guard, projected date
shown on the per-aircraft trigger list, the fleet maintenance overview, and
the chronological view's sort). Still open: using it to make
`MAINTENANCE_DUE_SOON` notifications more actionable — today the hours
criterion fires at ≥ 90 % of the hours limit, which for a low-utilization
aircraft can mean months of lead time noise or, for a high-utilization one,
too little warning. A projected-date threshold ("due in ~3 weeks at current
usage") would match how shop appointments are actually booked, but needs its
own design pass on the notification side (warn_hours vs. a new
warn_days-from-projection setting) rather than reusing the display-only
projection as-is.

---

## Process: e2e suite de-flaking (fixture hardening)

The e2e suite has a history of intermittent failures (see commits `0ff5f20`,
`39422a0`, `bd780a2`, `8a86c87`, `c0460e9`). Tasks are ordered by value and
are independently committable — one task per commit, and after each task run
the full e2e suite three consecutive times locally
(`bash scripts/run-tests-with-coverage.sh --e2e`) and confirm CI's
`browser-tests-seeded-crawl` and `browser-tests-seeded-rest` jobs pass.

All work is in `tests/e2e/`, `app/dev_seed.py`, and `scripts/` — no app
behaviour changes, no migrations, no translations. Test files keep their
feature-based names. **Any `.github/workflows/ci.yml` edit (task 3)
must be explicitly approved by the maintainer first.**

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
it with event-based waits. `tests/e2e/conftest.py` already counts settles
on every context built through `_block_external_network`, and
`click_and_settle(page, locator)` clicks a boosted control and waits for
the next settle (used by the passport builder tests; not for pages with
JavaScript off, whose clicks are real navigations).
1. Conversion rules, applied one test file per commit (start with
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
2. Run the converted file 3× in a row before committing.

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

- **Show the *basis* for a due date on the airworthiness tracker too.**
  Maintenance triggers now show this (`MaintenanceTrigger.service_basis`:
  last-done date + reading + interval, rendered via
  `partials/_maintenance_basis.html` in the fleet overview's Last-service
  column and inline in the per-aircraft limited view — the full per-aircraft
  view already had separate Interval/Last-service columns). The AD/SB/house-
  rule "source" half of the original idea was already covered by the
  existing `reference`/`category`/`action` fields shown above the trigger
  name. Still open: the equivalent for the airworthiness tracker, which
  still only shows the computed due value.

- **AD/SB compliance board — tie items to component serial numbers.** The
  board itself shipped: `AdSbItem`/`AdSbStatus` (new `ad_sb_items` table,
  `airworthiness.adsb_board`/`add_ad_sb_item`/`edit_ad_sb_item`/
  `delete_ad_sb_item`/`adsb_print`), each item tagged recurring/conditional/
  verify_part_number/closed/not_applicable, with the "owner's working
  summary, not the authoritative record" disclaimer, a client-side-only
  (localStorage) checklist of open items with a "copy as plain text" button
  (`static/js/adsb_checklist.js`), and a print view for the annual. Still
  open: tying an item to a specific installed component's serial number
  (rather than just the aircraft as a whole) — today an item is aircraft-
  scoped only, same granularity as `InstalledSTC`.

- **A "data integrity" page that discloses record-keeping gaps as a first-class
  feature** (the Data checks page now lists counter gaps — likely the
  place to grow this, see "Data checks: follow-ups" above), instead of only
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

- **Aggregate flight-statistics view — shared-ownership/flight-club variant.**
  The per-pilot version shipped (`/pilot/stats`: total distance, airports/
  countries/regions visited, "corners of the map", highest/lowest field,
  longest single leg and longest day, computed in `reports/flight_stats.py`
  from `app/data/airports.csv` coordinates). Still open: for shared
  ownership/flight club, a combined-vs-per-pilot stats table that
  de-duplicates flights logged by multiple crew (the per-pilot view today
  would double-count a flight both pilots logged). OpenHangar already has
  flight logging + GPS import; this is a reporting view on top,
  no new data model needed beyond what `Flight` already has.


## Raspberry Pi: local-only HTTPS option (no internet exposure)

The [Raspberry Pi guide](raspberry-pi.md) serves OpenHangar over plain HTTP
(with `OPENHANGAR_SESSION_COOKIE_SECURE=false`). Its only HTTPS path points
to the Traefik + Let's Encrypt setup in the self-hosting guide, which uses
the TLS-ALPN challenge. That needs a domain and port 443 forwarded, so it
exposes the Pi to the internet. Some users will want HTTPS at home *without*
that, mainly to get PWA install and offline logbook reading, which browsers
only enable in a secure context. It also matters for clubs running the Pi on
shared Wi-Fi, where plain HTTP sends passwords unencrypted.

Candidate approaches (pick one as the primary path, maybe mention a second):

- **Caddy with its built-in local CA (`tls internal`)**: an optional `caddy`
  service in front of `web` in `docker/docker-compose.raspberry-pi.yml` (or a
  separate override file). It issues certificates for `openhangar.local` (and
  optionally the Pi's IP) with no domain and no internet access. The catch:
  each device must trust Caddy's root certificate once. That's fiddly on iOS
  (install the profile, then enable full trust under Settings → General →
  About → Certificate Trust Settings). Chrome on Android accepts
  user-installed certificates. Have the container export the root cert into
  `~/openhangar/` so it's easy to grab, and document the per-device steps.
- **Tailscale (`tailscale serve`)**: a publicly trusted certificate on a
  `*.ts.net` name, with no ports opened and nothing to trust per device. It
  also gives remote access (log flights from the airfield). Downside:
  depends on a third-party account.
- **Let's Encrypt DNS challenge with Traefik**: for users who own a domain.
  The name can resolve to a private IP (`192.168.x.x`) and the certificate is
  proven through DNS, so nothing is exposed. Needs a DNS-provider API token,
  so it's for the more technical crowd.

Explicitly **not** a self-signed certificate: full-page warnings on every
device, and Chrome still refuses to register the service worker on an
untrusted certificate, so it gives no PWA or offline benefit.

Done means: a tested compose variant, a "Local HTTPS" section in
`docs/raspberry-pi.md` (replacing or complementing the current pointer to
the self-hosting guide), and a reminder to drop
`OPENHANGAR_SESSION_COOKIE_SECURE=false` once on HTTPS.

---

## Flight Debriefs & Shared Experience

Capture lessons and recommendations after a flight, with explicit control over
what stays personal and what is shared with a group. An idea to expand later;
not yet an implementation-plan phase. Phase 41's airfield comments and visitor
lists remain independently planned there.

- Debrief attached to a flight, with optional airfield context and personal
  reflections; keep existing flight notes independent.
- Personal by default. Explicitly choose content and destination group before
  sharing an airfield lesson or recommendation, reusing the passport group's
  airfield discussion rather than introducing a duplicate comments system.
- Sharing an excerpt must not implicitly expose the underlying flight, private
  notes, crew details, route or attachments, or publish it on the internet.
- Expand the workflow and fields later, including editing, withdrawal, deletion,
  provenance, and what happens to shared excerpts when their source changes.

## Airfield catalogue: optional ULM / microlight fields

The bundled catalogue (`app/data/airports.csv`, from OurAirports) has no ULM
classification — the `ulm` flag is "unknown unless corrected" — and many
ULM-only fields (French *bases ULM*, Belgian/Dutch microlight strips) are not
listed at all. Find a source of ULM fields with a licence compatible with
redistribution (candidates to evaluate: OpenAIP, OpenStreetMap
`aeroway=aerodrome` + `aerodrome:type`, national ULM federation lists), and
import it as a separate dataset rather than mixing it into the main catalogue.

- Ship the dataset with the image, but **disabled by default**: an instance
  setting (or per-user preference) turns ULM fields on for passport building,
  Where next? and place airfield suggestions. Enabled, they're tagged `ulm` so
  lists and maps can filter them.
- Decide de-duplication against fields already in the catalogue (same field
  listed in both — never merge by proximity alone, per the catalogue rules).
- Measure the size impact on search lists and the map before choosing the
  default; keep the existing behaviour when disabled.

## Photos: more suggestions from a photo's metadata

Counter and fuel photos already suggest the flight's arrival/departure time
and airfield (Phase 46, `services/photo_hints.py`), and a place photo its
pin, read before the photo is cleaned, and a photo on the passport "Record a visit" form its airfield and
date. Left:

- capture time as context for counter-photo OCR (backlog "Logbook: OCR
  auto-fill from counter photos").
