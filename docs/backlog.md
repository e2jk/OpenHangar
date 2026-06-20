# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

## GPS mass import — per-segment review and unified form integration

The current GPS batch upload flow (confirm-all POST) works correctly but was
originally planned to be reworked as part of Phase 31b. That rework was deferred
because the existing flow delivers correct results and the added complexity wasn't
justified. Items if this is ever revisited:

- Update the segment-review page to show per-segment duplicate detection results
  (currently duplicate detection only applies to the single-flight unified form).
- Replace the single "Confirm all" POST with per-segment actions: "Edit & confirm"
  (opens `/flights/new` pre-populated with that segment's parsed data) and "Confirm
  as-is" (quick confirm for clean segments without opening the form).
- Remove the standalone `/aircraft/<id>/gps-import/confirm` POST endpoint; all
  confirmation goes through the unified form.
- Remove the legacy `/pilot/logbook/new` route (superseded by `/flights/new`);
  update any remaining template links.

### Duplicate detection and validation screen (priority use case)

The primary motivation for this rework is the scenario where a pilot uploads
their existing logbook first and then wants to attach GPS tracks to already-
registered flights. Without duplicate detection the mass import would create
ghost flights for every track.

**Required behaviour — both mass and single-track upload:**

1. For each uploaded track, attempt to match it against existing logbook entries
   (matching criteria: date, aircraft registration, approximate departure/arrival
   times, rough duration).
2. Present a **validation screen** before any records are created:
   - One row per track file.
   - Each row shows: filename, detected date/aircraft/duration, and one of:
     - **"Match found"** → link to the matched logbook entry; the track will be
       attached to it, no new flight created.
     - **"New flight"** → no match; will create a new logbook entry.
   - A checkbox per row: **"Skip"** — exclude this track from batch processing
     so the user can manually attach it to a specific entry later (e.g. when
     automatic detection failed or matched the wrong entry).
3. Only after the user confirms does any write happen.

### GPS track linking: airframe and pilot logbook

When a GPS track is matched to an existing logbook entry (or a new one is
created), it must be linked to **both** the airframe logbook entry (if the
aircraft is managed in OpenHangar) **and** the pilot logbook entry (if the
pilot has a logbook in the system). A single `GpsTrack` record should be
shared between both links — do not duplicate the file or the database row.

**Renter / third-party pilot scenario** (likely a separate sub-task):
If a renter logged a flight (producing an airplane entry and a pilot entry for
the renter) and the owner later uploads a GPS track, the track must be linked
to the airframe entry **and** to the renter's pilot logbook entry — not to an
owner pilot entry that does not exist for that flight. The upload UI must
therefore identify who flew the aircraft on the matched flight and link
accordingly.

### Overwriting an existing GPS track

If a track is uploaded but the matched logbook entry already has a GPS track
attached, the options are:

- **Replace**: unlink the old track, link the new one. The old `GpsTrack` row
  becomes an orphan (no logbook entry references it). Simplest to implement but
  loses the old track silently.
- **Primary track** concept: keep both records, mark one as primary (the one
  the map is drawn from), allow the user to switch which is primary. Adds
  schema complexity (`is_primary` flag or separate FK).

**Recommended approach**: do not support multiple GPS tracks per logbook entry.
If a user needs to combine two partial recordings into one, direct them to an
external tool such as [gpx.studio](https://gpx.studio/app) to concatenate the
files first, then upload the merged result. Prompt the user with a warning and
a choice: replace the existing track or cancel the upload.

---

## GPS track upload: link to renter's pilot logbook when owner uploads

When a renter (not the aircraft owner) logs a flight, it produces:
- an airframe logbook entry linked to the aircraft,
- a pilot logbook entry linked to the renter's user account.

If the aircraft owner later uploads a GPS track for that flight, the system
must link the track to the **airframe** entry and to the **renter's pilot**
entry — not to an owner pilot entry (which does not exist for that flight).

Implementation notes:
- When matching a GPS track to an existing logbook entry, look up which pilot
  is recorded on that entry (`pilot_id` or equivalent) and use that to resolve
  the pilot logbook link, regardless of who is performing the upload.
- The upload UI should show the detected pilot name in the validation screen so
  the owner can confirm before committing.
- If the renter has no OpenHangar account (external pilot), link only to the
  airframe entry; no pilot logbook link is created.

Prerequisite: the duplicate-detection validation screen described in the
"GPS mass import" entry above.

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

## Pilot logbook: FSTD / simulator sessions

EASA AMC1 FCL.050 includes a dedicated column 10 for synthetic training device
(FSTD / simulator) sessions. These sessions are currently logged in the Remarks
field only.

Future enhancement: add a dedicated FSTD section to `PilotLogbookEntry` with
fields for device type, session duration, and the exercises performed. Simulator
time should be excluded from flight-time totals but accumulated separately in
the running totals row.

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

## Pilot logbook import
- **Total-only logbooks**: `total_flight_time` is currently a computed `@property`
  (SE + ME + multi_pilot), so there is no stored column to map to. Pilots whose
  logbook only records a total (no SE/ME breakdown) cannot import that value. Fix
  requires converting `total_flight_time` to a real stored column with a computed
  fallback for manually-entered entries where only the components are known.
- Cross-country is not an official EASA logbook column (it is an FAA concept). Add
  it to the database and display it while leaving it out of official EASA exports —
  or giving the user an opt-in. Requires tagging each logbook column as
  EASA-official, FAA-official, or custom/optional.

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

## Config page: show number of releases behind when an update is available

Currently the update badge shows only the latest version number. It would be
more informative to say "3 releases behind" so the admin knows roughly how much
they have missed.

Implementation sketch: during the version check, fetch `/repos/e2jk/OpenHangar/releases`
(full list, paginated) instead of `/releases/latest`. Count how many published
releases have a version strictly greater than `current_version` and store that
count in an `AppSetting` (`versions_behind`). Display it alongside the existing
update badge on the config page.

Why deferred: requires pagination handling and a second AppSetting; the extra
signal is nice but doesn't change the admin's action (still "run the upgrade
script"). Implement once the release cadence is high enough that the count is
meaningful.

---

## Bug: `refresh.sh` breaks the `.env` symlink on servers using symlinked config

On servers where `~/docker/.env` and `~/docker/docker-compose.yml` are symlinks
into a git tracked and/or Syncthing-synced tree, GNU
`sed -i` silently destroys the symlink. `sed -i` writes its output to a temp
file then calls `rename()` into place — `rename()` replaces the directory entry,
so the symlink is unlinked and a new regular file is created at the same path.
The canonical file in `DockerConfig/` is left untouched but is no longer
reachable from `~/docker/`.

**Fix** — in `demo/refresh.sh`, immediately after:
```bash
ENV_FILE="${COMPOSE_DIR}/.env"
```
add:
```bash
ENV_FILE="$(readlink -f "${ENV_FILE}")"
```
`readlink -f` resolves the full symlink chain and returns the canonical path,
so subsequent `sed -i` calls operate on the real file in `DockerConfig/` and
the symlink in `~/docker/` is never touched.

---

## Config page: optional Gatus uptime badge

[Gatus](https://github.com/TwiN/gatus) exposes an SVG health badge per monitored
endpoint at:

```
GET /api/v1/endpoints/{key}/health/badge.svg
```

where `{key}` is `{group}_{endpoint-name}` lowercased with spaces replaced by
hyphens (e.g. `openhangar_openhangar-production`).

**Proposed feature:** add an optional Gatus instance URL to OpenHangar's
configuration. When set, display the relevant badge(s) inline in the System
section of the config page, giving operators an at-a-glance health indicator
without leaving the app.

Implementation notes:
- The Gatus base URL and endpoint key(s) would be stored as env vars
  (`OPENHANGAR_GATUS_URL`, `OPENHANGAR_GATUS_ENDPOINT_KEY`).
- The badge itself is a plain `<img src="…/health/badge.svg">` — no JS required.
- **Auth complication:** if the Gatus dashboard is protected by HTTP Basic Auth,
  the badge endpoint is protected too. Two options:
  - Configure Gatus to expose badge endpoints publicly (Gatus's `security` block
    may support path-scoped exceptions — needs investigation).
  - Store Basic Auth credentials in OpenHangar config and proxy the request
    server-side, forwarding the `Authorization` header so credentials never
    reach the browser.
- Degrade gracefully: if the URL is not configured, show nothing; if the fetch
  fails (Gatus down, auth issue), show nothing or a neutral placeholder and a
  message in the logging.

---

## Flight form: default date to today

When opening the "Register a new flight" form, the date field should be
pre-filled with today's date. Most logbook entries are made on the day of
the flight; forcing the pilot to fill in the date every time is unnecessary
friction.

Implementation: set the `value` attribute of the date `<input>` to
`datetime.date.today().isoformat()` in the route that renders the form.

---

## Flight form: remove total landings field; derive it from day + night

The unified flight form exposes three landing fields: **Total**, **Day**, and
**Night**. The EASA Part-FCL logbook (AMC1 FCL.050) records a single total
landing count; the day/night split is an OpenHangar extension.

Proposed change:
- Remove the **Total landings** input from the form (reduces clutter and
  eliminates the inconsistency risk of total ≠ day + night).
- Keep the `total_landings` database column — it is populated on save as
  `day_landings + night_landings`.
- Continue displaying total landings in the logbook table, the EASA PDF
  export, and any summary views (calculated on the fly or from the stored
  column).

Why deferred: small migration needed to backfill `total_landings` for existing
rows where it was entered manually and may differ from day + night; requires
a data-quality audit before the column becomes purely derived.

---

## Aircraft documents: auto-fill insurance expiry from uploaded document

When a user uploads an aircraft document and selects **Insurance** as the
document type and enters an expiry date, that date should automatically be
saved as the aircraft's insurance expiry date. A link to the document should
then appear in the Insurance section of the aircraft detail page.

Implementation sketch:
- In the document-upload POST handler, check if `document_type == "insurance"`
  and `expiry_date` is set.
- Write `expiry_date` to `Aircraft.insurance_expiry` (or equivalent field).
- On the aircraft detail page, when `insurance_expiry` is set, query for the
  most recent insurance document and render a "View document" link alongside
  the expiry date.

Edge cases: multiple insurance documents uploaded over time — only the most
recent (by upload date or by latest expiry) should be linked; earlier ones
remain accessible in the full documents list.

---

## Mobile navigation: bottom tab bar

On narrow viewports the current sidebar/navbar collapses to a hamburger menu,
which is not thumb-friendly. A bottom tab bar is the dominant native-feeling
pattern on iOS and Android and is well-established in mobile-first web apps
(PWA, Bootstrap-based or otherwise).

**Proposed design pattern — fixed bottom tab bar:**
- A `position: fixed; bottom: 0` bar containing 4–5 icon + label tabs for the
  most-used destinations: **Fleet**, **Flights**, **Documents**, **Profile**,
  and optionally **More** (overflow drawer for less-used items).
- Each tab is a large touch target (≥ 48 × 48 px), with an active-state
  highlight (filled icon or accent underline).
- Shown only on `max-width: 768px`; the existing navbar is kept for desktop.
- The main content area gets `padding-bottom` equal to the bar height so
  nothing is obscured.

**Reference patterns to research before implementing:**
- Bootstrap 5 does not ship a bottom nav component natively; options are a
  custom `fixed-bottom` flexbox bar or a third-party library such as
  [Bootstrap Bottom Nav](https://github.com/Johann-S/bs-bottom-nav).
- The [Material Design bottom navigation](https://m3.material.io/components/navigation-bar/overview)
  spec gives good guidance on item count (3–5), label truncation, and badge
  placement.
- Safari on iOS adds its own bottom chrome; the bar needs
  `padding-bottom: env(safe-area-inset-bottom)` to avoid overlap.

Why deferred: requires a mobile UX audit to choose the right top-level tabs
and decide how the "More" overflow drawer behaves.

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