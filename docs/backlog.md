# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

## UI: dark theme

Add a dark colour scheme switchable per user, with the system `prefers-color-scheme`
media query as the default.

Design notes:
- Bootstrap 5 ships built-in dark-mode support via `data-bs-theme="dark"` on
  `<html>`. No custom CSS framework needed — override tokens for the few
  non-Bootstrap colours (status badges, map tiles, chart colours).
- Store the user's preference (`"light"` / `"dark"` / `"system"`) in a new
  `theme` column on `User` (nullable, default `NULL` = follow system).
- Expose a toggle in the navbar (sun/moon icon) that POSTs to a small endpoint,
  same pattern as the language switcher.
- Map tiles (Leaflet) need a separate dark tile layer (e.g. CartoDB Dark Matter)
  swapped in when dark mode is active.

Why deferred: cosmetic feature — no user is blocked without it — but high
visibility and frequently requested.

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
