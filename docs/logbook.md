# OpenHangar — Aircraft Logbook Guide

This page explains the concepts behind the aircraft logbook fields and how to
configure OpenHangar to match your aircraft's instruments and regulatory regime.

---

## Regulatory framework

OpenHangar supports two logbook regimes, selectable per aircraft:

| Regime | Governing regulation | Typical use |
|--------|---------------------|-------------|
| **EASA** | Part-M / M.A.306, Commission Regulation (EU) No 1321/2014 | European-registered aircraft |
| **FAA** | 14 CFR §91.417, 14 CFR §43.9 | US-registered aircraft |

The regime controls which fields are displayed and which are mandatory. Both
regimes share a common core set of fields; the differences are noted where
relevant below.

---

## Flight time vs engine time

These two quantities are **different** and serve different purposes. OpenHangar
tracks both.

### Flight time

Flight time is the time the aircraft was actually in motion — typically measured
from when the aircraft lifts off the ground until it touches down again (or from
when it starts moving until it stops, depending on the instrument). It is the
value recorded in the aircraft's journey log (EASA "Carnet de route") column
**Duration of flight**, and in the pilot's personal logbook as flight hours.

### Engine time

Engine time measures how long the engine has been running, normalised to a
reference RPM (typically 2400 RPM for light aircraft). It accumulates during
engine start, run-up, taxi, flight, and cool-down. It runs slower than clock
time at low RPM (ground idle) and faster at high RPM (takeoff power). Engine
time is the basis for **maintenance scheduling** (50-hour, 100-hour checks, TBO
intervals) and is recorded in the pilot's logbook as engine hours.

Because of ground operations, engine time is always **greater than** flight time
for a given flight.

### Why the difference matters

| | Flight time | Engine time |
|---|---|---|
| Aircraft logbook (journey log) | ✓ column 8 | — |
| Pilot logbook | ✓ (flight hours) | ✓ (engine hours) |
| Maintenance scheduling | — | ✓ (50 h, 100 h, TBO) |
| Rental billing (common practice) | ✓ | sometimes used instead |

---

## Instruments and counters

### Tach timer (engine time counter)

A tach timer counts the total number of engine revolutions, expressed as hours
normalised to the engine's reference RPM. It activates as soon as the engine
starts and runs continuously until shutdown. Because it is RPM-based rather
than clock-based, one "tach hour" equals one clock hour only at the reference
RPM; ground idle and taxi accumulate tach time more slowly.

The tach timer reading at the end of a flight is stored in OpenHangar as
**engine time counter** (`engine_time_counter_end`).

### Hobbs meter — flight-detection variant (flight time counter)

A Hobbs meter is a generic elapsed-time meter. When fitted with a
**flight-detection switch** (airspeed vane or weight-on-wheels switch), it runs
only when the aircraft is airborne above a minimum airspeed (or has weight off
wheels). This variant therefore measures true airborne/flight time.

The flight-detection Hobbs reading at the end of a flight is stored in
OpenHangar as **flight time counter** (`flight_time_counter_end`).

> **Note:** Some Hobbs meters are instead activated by oil pressure (common in
> US rental fleets). That variant runs from engine start to shutdown and
> measures engine running time, not flight time. OpenHangar does not use the
> instrument name "Hobbs" in its fields precisely to avoid this ambiguity — the
> fields are always labelled by what they measure.

### Aircraft with only one counter (tach only)

Older and simpler aircraft often have only a tach timer and no separate flight
time counter. In that case, OpenHangar derives flight time by subtracting a
configurable fixed offset from the engine time reading for each flight.

This offset represents the total ground-operations time per flight
(engine start + run-up before departure + taxi-in + cool-down after landing).
A typical value is **0.3 h (18 minutes)** but it varies by aircraft type and
operation.

---

## Aircraft-level counter settings

These are configured on the aircraft detail page.

| Setting | Description | Default |
|---------|-------------|---------|
| **Regulatory regime** | EASA or FAA — controls which logbook fields are displayed and required | EASA |
| **Separate flight time counter** | Whether the aircraft has a flight-detection Hobbs meter in addition to the tach timer | Yes |
| **Flight time offset** | Fixed offset in tenths of an hour subtracted from engine time to derive flight time, used only when *Separate flight time counter* is disabled | 0.3 h |

---

## Per-flight entry fields

The table below maps the official Belgian EASA journey log columns
("Carnet de route / Reisdagboek") to OpenHangar fields. Fields marked
EASA-only or FAA-only are hidden for the other regime.

| Col | Official label | OpenHangar field | Notes |
|-----|---------------|-----------------|-------|
| 1 | Date | **Date** | Required |
| 2 | Name (flight crew) | **Crew member(s)** | See [Crew](#crew) below |
| 3 | Function (flight crew) | **Role** | See [Crew](#crew) below |
| 4 | Departure place | **Departure (ICAO)** | 4-letter ICAO code |
| 5 | Destination place | **Arrival (ICAO)** | 4-letter ICAO code |
| 6 | Departure time | **Departure time (UTC)** | See [Time entry](#time-entry) below |
| 7 | Arrival time | **Arrival time (UTC)** | See [Time entry](#time-entry) below |
| 8 | Duration of flight | **Flight time** | Derived from counter difference or from arrival − departure; see [Time entry](#time-entry) |
| 9 | Nature of flight | **Nature of flight** | See [Nature of flight](#nature-of-flight) below |
| 10 | Number of passengers / landings | **Passengers** / **Landings** | Two separate fields; see note |
| 11 | Incidents and observations | **Engine time counter (end)**, **Flight time counter (end)**, **Notes** | Three separate fields; see [Counter values](#counter-values) below |
| 12 | Commander signature | Implicit (logged-in user) | The user who saves the entry is the signing commander |
| 13 | Visa | Out of scope for flight log | Maintenance releases are handled in the maintenance module |

---

## Crew

Each flight entry supports one or two crew members. For each crew member:

| Field | Description |
|-------|-------------|
| **Name** | Free text, or select from pilots already registered in the system. Selecting a registered user enables automatic population of that pilot's personal logbook. |
| **Role** | Dropdown: **PIC** (Pilot in Command), **IP** (Instructor Pilot), **SP** (Student Pilot), **Co-pilot**. Required under EASA; optional under FAA. |

For a dual flight (instructor + student), enter both as separate crew rows with
roles IP and SP respectively — rather than slash-separating names in a single
field as is common in paper logbooks.

> **Pilot logbook note:** flights on aircraft *not* managed in OpenHangar (a
> rental, a friend's plane, another club's aircraft) can be entered directly in
> the pilot logbook as standalone entries, without needing a corresponding
> aircraft logbook entry. See the pilot logbook documentation (future) for
> details.

---

## Nature of flight

The **Nature of flight** field (EASA AMC1 ORO.MLR.110 item 10) officially
distinguishes scheduled from non-scheduled commercial operations. For
non-commercial GA operations all flights are non-scheduled, so pilots use
this field in practice to record the type of flight activity.

OpenHangar uses a combobox that lets the pilot select a previously used value
or type anything new. The following values are pre-loaded as suggestions:

| Value | Typical use |
|-------|------------|
| Local | Flight remaining in the local aerodrome area |
| Navigation | Cross-country / VFR NAV flight |
| Training | Dual instruction or solo training exercise |
| IFR | Instrument flight rules flight |
| Night | Night VFR or night IFR flight |
| Ferry | Positioning / delivery flight |
| Other | Anything not covered above |

Any free-text value entered by the pilot is saved and offered as a suggestion
on future entries for the same aircraft.

---

## Time entry workflow

Times in the aircraft logbook are always recorded in **UTC**.

The recommended workflow after landing:

1. While still in the aircraft (before turning off the battery), photograph
   both counters — the camera timestamp serves as your arrival time reference.
2. Convert the photo timestamp to UTC and floor it to the nearest tenth of an
   hour (e.g. 15:38 UTC → **15:36 UTC**) — this is the **arrival time**.
3. Subtract the flight time counter difference (current end − previous end)
   from the arrival time to obtain the **departure time**.

OpenHangar pre-fills counter start values from the previous flight entry for
the same aircraft, so you only need to enter the end readings.

For the **pilot logbook** (future), engine start and end times are derived from
the flight times by splitting the difference between engine time and flight time
(approximately 2/3 before departure for run-up, 1/3 after arrival for cool-down).

---

## Counter values

The **engine time counter (end)** and **flight time counter (end)** fields store
the physical instrument readings at the end of the flight (in tenths of hours).
The start values for each flight are taken automatically from the previous
entry's end values for the same aircraft — they are not directly editable in the
UI. The difference between start and end gives the duration for that leg.

> **Continuity:** if a counter start value ever differs from the previous
> flight's end value (e.g. due to a counter replacement or a data correction),
> the logbook will flag this as a discrepancy for review. Do not manually adjust
> counter values without noting the reason in the **Notes** field.

Both fields support attaching a **photo** of the instrument panel for
verification — the recommended practice is to photograph both counters
simultaneously at the end of each flight before shutting down the electrical
system. The photo timestamp (EXIF) is used to derive the arrival time
automatically (see [Time entry workflow](#time-entry-workflow) above).

For aircraft with no separate flight time counter, the **flight time counter
(end)** field is hidden and flight time is computed automatically from the engine
time counter reading minus the configured offset.

---

## FAA-specific notes

Under FAA regulations (14 CFR §91.417), the aircraft logbook must record:

- Total time in service for airframe, each engine, and propeller
- Current status of life-limited parts
- Time since last overhaul (where applicable)
- Airworthiness Directive compliance status

The per-flight journey log is less formally prescribed than under EASA.
The **crew role** field (EASA col. 3) is primarily a pilot-logbook concept under
FAA and is therefore **optional** (not hidden) for FAA-regime aircraft — it
remains useful for accountability and incident investigation even when not
formally required.
