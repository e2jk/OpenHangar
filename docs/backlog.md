# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

## Onboarding: usage profile selection

At first setup, offer the user a choice of usage profile that tailors the UI to
their needs:

| Profile | Description | UI simplifications |
|---------|-------------|-------------------|
| **Single aircraft owner** | One pilot, one plane | Dashboard goes directly to that aircraft; no "add aircraft" prompts; no fleet views |
| **Fleet / flying club / school** | Multiple aircraft, multiple pilots | Current full UI |
| **Pilot only** | No aircraft managed in OpenHangar; pilot logbook use only | Aircraft management, maintenance, and expense modules hidden; entry point is the pilot logbook |

Key constraints:
- Profile is always changeable from the settings page without any data loss —
  switching from "single aircraft" to "fleet" just re-enables the hidden UI
  elements; switching back hides them again.
- A user who starts as "pilot only" and later acquires a plane should be able
  to upgrade to "single aircraft owner" and have their existing logbook entries
  automatically linked to the newly added aircraft.
- The profile is a UI preference, not a data constraint — all models remain
  identical regardless of profile.

Why deferred: requires an onboarding wizard and a per-tenant UI-profile setting;
the multi-user phase (Phase 15) should land first so the tenant model is stable
before adding profile-level customisation on top of it.

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

## Flight journey animation

Animate a pilot's or aircraft's complete flight history as a progressive
map drawing: routes appear in chronological order, the map zooms out as
total distances grow. Deliverable: live web view on the pilot/aircraft
detail page, and optionally an exportable GIF.

Why deferred: requires the unified GPS track storage (GpsTrack model) to
be in place and enough tracks to be linked before the feature is meaningful.

---

## Loose bits and pieces

### Pilot logbook import
- **Total-only logbooks**: `total_flight_time` is currently a computed `@property`
  (SE + ME + multi_pilot), so there is no stored column to map to. Pilots whose
  logbook only records a total (no SE/ME breakdown) cannot import that value. Fix
  requires converting `total_flight_time` to a real stored column with a computed
  fallback for manually-entered entries where only the components are known.
- Cross-country is not an official EASA logbook column (it is an FAA concept). Add
  it to the database and display it while leaving it out of official EASA exports —
  or giving the user an opt-in. Requires tagging each logbook column as
  EASA-official, FAA-official, or custom/optional.

### Pilot logbook
- Based on the data in the pilot log, check if currency/recency is still up to date
  (e.g. number of [night] landings in a specific type to take passengers). Requires
  a concept of "aircraft type family" so that PA28-161 TDI, PA28-161 and PA28-161 IFR
  are all treated as the same type. This is also a prerequisite for the multi-pilot
  currency matrix.

### Aircraft type: type-family mapping

`app/data/aircraft_types.csv` is now bundled and `aircraft_type_icao` is stored on
each `PilotLogbookEntry`. The remaining work is the type-family mapping:

- PA28-161 (freetext) → P28A (ICAO designator) already works via exact/normalised
  lookup; the next step is grouping variants under a canonical family designator
  (e.g. PA28-161, PA28-161 TDI, PA28-161 IFR all → P28A) so that the
  currency/recency check can treat them as the same type.
- Requires a `type_family` column or a separate mapping table that links each
  ICAO designator to a canonical family key, then the currency check queries by
  family rather than by exact designator.

### Aircraft creation: pre-populate components from ICAO type data

`aircraft_types.csv` includes `engine_count` and `engine_type` for every
designator.  When a user selects an ICAO type via the autocomplete on the
"Add aircraft" form, use that data to offer pre-creating the right number
of components:

- One engine component per `engine_count` (e.g. 2 engines for a twin).
- One propeller component per engine, but only when `engine_type` is
  `Piston` (turbojets and turbofans don't have separately-tracked
  propellers in typical maintenance programmes).
- Present this as an opt-in prompt after the aircraft is saved ("We
  noticed this is a single-engine piston — create an Engine and a
  Propeller component now?"), not as a mandatory step, so that users who
  manage components differently are not forced into a specific structure.

Why deferred: requires the aircraft-type autocomplete to be wired up on
`aircraft_form.html` (currently it only appears on the pilot logbook entry
form) and a post-save component-creation flow that doesn't yet exist.

### Flight entry: autosuggest for aircraft type (other-aircraft mode)

When logging a flight for an aircraft not managed in this instance, the "Make /
Model" field is a plain text input. It should reuse the ICAO type autosuggest
endpoint already wired up on the "Add aircraft" form so that pilots get
consistent, normalised type values in their logbook.

The same improvement applies to the GPS import upload page when "other aircraft"
mode is toggled on.

### Flight entry: registration-to-type auto-fill

The aircraft type and registration fields in other-aircraft mode are independent.
A better UX would put registration first and attempt to auto-fill the type field
when a known registration is entered.

Two data sources to consider:
- **Per-user history**: the pilot's own previously logged registrations + types
  (zero privacy concern; straightforward to implement).
- **Shared pool**: registrations logged by any user in this instance (registration
  and type are not sensitive, but requires a design decision on multi-tenant
  visibility).
- **External lookup**: query a public registry (e.g. OpenSky, local CAA open
  data) — useful but introduces an external dependency; should be opt-in via a
  config flag.

Start with per-user history; the other sources can be layered on later.

### Auto-upload selected GPS track

When a GPS track file is selected, immediately upload it and run the
parsing code — no Parse GPS button click required. If the user then
selects a different file, re-run parsing and overwrite the pre-filled
fields with the new result.

### Detect airplane based on GPS track device ID

Avionics suites embed a device identifier in exported GPS files (e.g.
`system_id` on the first line of a Garmin export). Store a mapping of
device IDs to aircraft registrations learned from previous uploads, and
use that mapping to auto-select the aircraft when a known device ID is
encountered.

### Handle multiple landings / touch-and-go's from a GPS file upload

If a GPS file contains multiple landings, count them and populate the
landing count field accordingly. Only the final destination airfield is
recorded in the arrival field, but intermediate touch-and-go's and their
associated airfield should be noted automatically in the comments field
(e.g. "2 touch & go at EBNM").

### Detect go-arounds / missed approaches

Distinguish between a touch-and-go that results in a ground contact and
a go-around or missed approach where the aircraft does not touch down.
Do not count go-arounds as landings in the day/night landing fields;
instead add a note to the remarks (e.g. "1 missed approach"). If IFR
logging is ever introduced, consider a dedicated field for instrument
approaches — deferred to that phase.

### Pilot profile: GPS tracks page

Add a page under the pilot profile that lists all GPS tracks associated
with that pilot's flights. Mirrors the per-aircraft GPS track list so
pilots can browse their own track history in one place.

### GPS track list: click-through to flight log entry

On both the per-aircraft and per-pilot GPS track list pages, make each
track row clickable. Clicking from the aircraft tracks page opens the
corresponding aircraft flight log entry; clicking from the pilot tracks
page opens the associated pilot logbook entry.

### Linked flight / pilot log entry: cross-reference link

When a pilot logbook entry is linked to an aircraft flight entry (or
vice versa), show a clearly labelled link on each entry's detail page
that navigates directly to the other associated entry.

### Auto-detect night landings from GPS track upload

When a GPS file contains a landing, determine whether it occurred during
aeronautical night at the landing location and classify it accordingly in
the day/night landing fields. The exact civil-twilight calculation varies
by date and coordinates; an approximation based on solar elevation at the
arrival fix and time is acceptable as a first pass.
