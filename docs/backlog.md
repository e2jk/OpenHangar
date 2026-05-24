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