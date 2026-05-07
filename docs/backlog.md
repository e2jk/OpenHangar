# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

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

## Pilot logbook: flights on external aircraft

A pilot may fly aircraft that are not managed in OpenHangar (another club's
plane, a rental, a friend's aircraft). The pilot logbook must support manually
entered standalone entries for these flights alongside auto-populated entries
derived from FlightEntry records on managed aircraft.

Design constraint for Phase 17 (Pilot Logbook):
- `PilotLogbookEntry` should have a nullable `flight_id` FK to `FlightEntry`.
  A null value means a standalone (external) entry.
- External entries carry free-text aircraft registration and make/model rather
  than a FK into the `Aircraft` table.
- Totals (flight hours, engine hours, currency calculations) must aggregate
  across both linked and standalone entries transparently.
