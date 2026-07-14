# Phase 38 — Offline Logbook Editing: Implementation Spec

Companion to the Phase 38 section in
[`implementation_plan.md`](implementation_plan.md). The plan says *what*;
this document fixes the *how* — data flow, endpoints, storage schema,
conflict rules, and delivery order — so implementation can proceed without
re-opening design questions. Where this spec makes a choice, the choice has
been made deliberately; deviate only with a documented reason.

**Read first:** `AGENTS.md` (working rules — especially the JS/HTMX
architecture section), the Phase 35 section of the implementation plan
(PWA + offline queue, shipped and extended here), and
`app/static/js/pwa.js` + `app/static/js/sw.js` (the existing offline
machinery this phase builds on).

---

## Motivating use-case

A user is offline for many hours (e.g. a long-haul flight) and wants to
review and correct the airframe logbook of their aircraft — departure and
arrival times, flight/engine counter start and end values, and the other
scalar fields of existing `FlightEntry` rows — and, where they log their
own flights, the matching `PilotLogbookEntry` fields (night/instrument
time, landings, function, remarks). They may also want to correct older,
**standalone** pilot-logbook entries that have no linked `FlightEntry` at
all (manual entries, FSTD/simulator sessions, flights in aircraft outside
this fleet). When connectivity returns, all corrections upload
automatically, with explicit per-field conflict resolution if the server
copy changed in the meantime.

## Architecture in one paragraph

**Offline-first outbox with an auto-refreshed snapshot.** Whenever the user
browses an aircraft logbook while online, a JSON snapshot of its entries is
silently refreshed into IndexedDB — there is *no* explicit "take offline"
action. Edits made in a new per-aircraft **workbench** page are written to
a local outbox: when online, the outbox flushes immediately (the edit is
effectively a direct save); when offline, edits accumulate until
connectivity returns. Every outbox record carries the **base value** each
field had at snapshot time, so the server can detect, per field, whether
the live value changed while the user was offline. A new **Offline changes**
page lists everything pending and is where sync progress, errors, and
per-field conflict resolution (keep my offline value / take the server
value) are handled. The pilot logbook reuses this same outbox/base-value/
conflict machinery in two places: the current user's own entry linked to a
`FlightEntry` is edited alongside it in the aircraft workbench (mirroring
the online form, which always saves both together — see §38h), while the
current user's standalone entries get their own pilot-scoped snapshot and
workbench (§38i), since they are not tied to any aircraft.

## Hard constraints (do not violate)

- **No schema change, no migration.** Conflict detection is by per-field
  base-value comparison (§38b), not by a version column.
- **No reliance on the Background Sync API.** Firefox does not support it
  and Brave disables it by default — and those are exactly the two target
  browsers (Firefox in a plain tab on Ubuntu; Brave with the installed PWA
  on GrapheneOS/Android). All sync triggers are: page load while online,
  the `window` `online` event, and the existing SW `OH_SYNC_REQUESTED`
  message (kept as a bonus for browsers that have it).
- **Works in a plain browser tab.** PWA installation must not be required
  for any part of this phase (Firefox desktop cannot install PWAs).
- **CSRF tokens expire after 1 h** (Flask-WTF default `WTF_CSRF_TIME_LIMIT`;
  the app does not override it). Any queued request replayed after a long
  offline period MUST first obtain a fresh token from `GET /api/offline/csrf`
  (§38a). Never rely on a token captured before going offline.
- **All the AGENTS.md JS rules apply**: no inline `<script nonce>` in child
  templates; new JS files are IIFEs in `app/static/js/` loaded
  unconditionally from `base.html` with `DOMContentLoaded` +
  `htmx:afterSettle` init and `dataset.ohInited` guards; Jinja→JS data via
  `data-*` attributes, `<script type="application/json">` bridges, and
  `<template>` elements for translated row markup.
- **Scope: `FlightEntry` (+ its two `FlightCrew` slots), plus the current
  user's own `PilotLogbookEntry` rows** — both linked to a `FlightEntry`
  (§38h, edited alongside it) and standalone (§38h–§38i, own workbench).
  Photos, GPS tracks, and other crew members' data remain read-only context
  — the sync path never touches them. Creating or deleting entries offline
  is out of scope for both logbooks (creation is already covered by the
  Phase 35 queue for new flights, fixed in §38f). The workbenches must
  state these limitations in a help note.

---

## Delivery order

Twelve sub-phases, each independently committable, each green on the full
gate (tests at 100 % coverage, ruff, mypy, translations fr+nl, migration
chain) before moving on. 38a–38e are the critical path for the motivating
use-case; 38f and 38g close the airframe-logbook loop. 38h–38l are the
pilot-logbook extension — additive on top of 38a/38b, sequenced after the
critical path (see "Sequencing" above).

| Step | Contents | Depends on |
|---|---|---|
| 38a | Server read side: canonical serialization, snapshot API, CSRF refresh API | — |
| 38b | Server write side: per-field conflict detection + sync API | 38a |
| 38c | Client offline data layer: IndexedDB v2, auto-snapshot, SW route caching | 38a |
| 38d | Workbench page: editable logbook table + continuity checks | 38b, 38c |
| 38e | Offline-changes page: pending list, sync progress, conflict resolution | 38b, 38c |
| 38f | Phase 35 queue fixes: replay to correct URL, fresh CSRF, surface failures | 38a |
| 38g | Docs, screenshots, Playwright offline e2e tests (airframe logbook) | 38d, 38e |
| 38h | Pilot logbook server API: linked-entry pilot fields (extends 38a/38b) + standalone-entry snapshot/sync | 38b |
| 38i | Pilot logbook client + UI: "My logbook" section in the aircraft workbench + new standalone workbench | 38c, 38d, 38h |
| 38j | Offline-changes page extended to both pilot-logbook flavours | 38e, 38h |
| 38k | Cross-cutting offline-submit guard: friendly message on forms outside the offline-aware set | — |
| 38l | Docs, screenshots, Playwright e2e for the pilot-logbook additions and the 38k guard | 38i, 38j, 38k |

New code lives in a new blueprint package `app/offline/` (`routes.py` +
`__init__.py`, blueprint name `offline`), registered in `app/init.py` like
the other blueprints. Shared validation helpers extracted in 38b live in
`app/flights/` (they belong to the flights domain); the pilot-logbook
equivalent extracted in 38h lives in `app/pilots/`.

**Sequencing.** 38a–38g are unchanged from the original, airframe-only
design and remain the critical path (the motivating deadline is a specific
upcoming flight) — nothing in them depends on the pilot-logbook work below.
38h–38l are additive: they extend 38a/38b's endpoints with an optional
`pilot` field and add new endpoints/pages alongside them. Ship 38a–38g
first; 38h–38l can follow without putting the deadline at risk.

---

## 38a — Server read side

### Canonical serialization — the cornerstone

Conflict detection compares strings. Every value therefore has exactly one
canonical string form, produced by one function used *everywhere* (snapshot,
sync comparison, sync response). Implement as `canonical_entry(fe, crew)`
in `app/offline/serialize.py`:

| Field(s) | Canonical form |
|---|---|
| `date` | `"YYYY-MM-DD"` |
| `departure_time`, `arrival_time` | `"HH:MM"`; `None` → `""` |
| `flight_time`, `flight_time_counter_start/_end`, `engine_time_counter_start/_end` | `"%.1f"`; `None` → `""` |
| `fuel_added_qty`, `fuel_remaining_qty`, `oil_added_l` | `"%.2f"`; `None` → `""` |
| `passenger_count`, `landing_count` | `str(int)`; `None` → `""` |
| `departure_icao`, `arrival_icao` | stripped, upper-cased |
| `nature_of_flight`, `notes`, `fuel_added_unit`, `fuel_event` | stripped; `None` → `""` |
| `crew_name_0`, `crew_role_0`, `crew_name_1`, `crew_role_1` | from the first two `FlightCrew` rows ordered by `sort_order`; absent slot → `""` |

This exact field set is the **editable field set** for the whole phase.
A matching JS helper `ohCanon(field, rawInputValue)` (in
`app/static/js/offline_db.js`, §38c) must produce identical strings from
form-input values (e.g. `"1424.50"` → `"1424.5"`, `"lfpg"` → `"LFPG"`) —
write unit-style e2e assertions for the tricky cases (trailing zeros,
empty, whitespace).

### `GET /api/offline/aircraft/<int:aircraft_id>/logbook`

`@login_required` + the same access rule as `flights.list_flights`
(aircraft must be in `accessible_aircraft(tenant_id, include_archived=True)`;
404 otherwise — mirror `_get_aircraft_or_404`). Returns:

```json
{
  "aircraft": {"id": 3, "registration": "F-ABCD",
               "has_flight_counter": true, "flight_counter_offset": "0.3"},
  "snapshot_taken_at": "2026-07-14T12:34:56+00:00",
  "entries": [
    {"id": 812,
     "fields": { /* the canonical editable field set above */ },
     "meta": {"has_flight_counter_photo": false, "has_engine_counter_photo": true,
              "has_fuel_photo": false, "has_gps_track": true,
              "source": "gps_import", "created_at": "..."}}
  ]
}
```

Entries sorted ascending by `(date, id)`. No pagination — a full logbook of
a few thousand rows is well under a megabyte of JSON.

### `GET /api/offline/csrf`

`@login_required`; returns `{"csrf_token": generate_csrf()}`
(`from flask_wtf.csrf import generate_csrf`). Used by every sync/replay
path immediately before POSTing.

### JSON-aware auth for the API

`@login_required` redirects to the login page (HTML) when the session is
gone — useless to a fetch client. Add a tiny decorator
`@api_login_required` in `app/offline/routes.py` that returns
`401 {"status": "auth"}` instead of redirecting, and use it on all
`/api/offline/*` endpoints. (Keep `@login_required` semantics for
everything else: check the same session keys it checks.)

### Tests (38a)

- Snapshot: field-by-field canonical values for a fully-populated entry and
  for an all-nulls entry; crew slots; sort order; archived aircraft included.
- Access: other tenant's aircraft → 404; anonymous → 401 JSON (not a redirect).
- CSRF endpoint returns a token that passes `validate_csrf`.
- Canonical serializer unit tests: `Decimal("1424.50")` → `"1424.5"`, etc.

---

## 38b — Server write side

### Validation helper extraction (refactor, no behaviour change)

`_handle_log_flight_post` in `app/flights/routes.py` currently parses and
validates ~30 form fields inline. Extract two pure helpers into a new
`app/flights/form_parsing.py`:

- `parse_flight_fields(f: Mapping[str, str], ac: Aircraft | None) ->
  tuple[dict, list[str]]` — the existing parse + validation logic for the
  editable field set (date required/ISO; ICAO required, ≤ 4 chars, upper;
  times `HH:MM`; counters non-negative, end ≥ start per pair; flight_time
  non-negative, derived from counter deltas when blank — including the
  `has_flight_counter=False` engine-minus-offset rule; passenger/landing
  counts non-negative ints; fuel event in `("before","after")` or none;
  fuel/oil quantities non-negative; crew-1 name required when `ac` is set).
  Error strings stay `_()`-wrapped and identical to today's.
- `apply_flight_fields(fe: FlightEntry, values: dict) -> None` — assigns
  the parsed values onto the entry and replaces its two `FlightCrew` slots
  exactly the way the form handler does today.

Rewire `_handle_log_flight_post` to call these; the full existing flights
test suite must pass unchanged. This is the step that guarantees offline
sync and the online form can never diverge in validation.

### `POST /api/offline/flights/<int:flight_id>/sync`

`@api_login_required`; CSRF validated from the `X-CSRFToken` header
(Flask-WTF `CSRFProtect` accepts it natively); entry looked up with the
same tenant/access guard as `flights.edit_flight` (404 otherwise).

Request body:

```json
{
  "fields": { /* COMPLETE canonical editable field set — the user's merged local state */ },
  "base":   { /* COMPLETE canonical field set as of snapshot time */ },
  "force_duplicate": false
}
```

Processing order:

1. **Reject malformed** requests (missing keys, unknown field names) → 400
   `{"status": "invalid", "errors": [...]}`.
2. **Conflict scan** — let `current` = `canonical_entry(fe, crew)`. For each
   field `k` where `fields[k] != base[k]` (a field the user actually
   changed): it is **in conflict** iff `current[k] != base[k]` and
   `current[k] != fields[k]`. If any field conflicts, apply **nothing** and
   return `409 {"status": "conflict", "conflicts": [{"field": k, "base": …,
   "local": …, "server": …}, …], "entry": current}`.
   Fields the user did *not* change never conflict — the server value
   simply stands (the effective save is `current` overlaid with the user's
   changed fields).
3. **Validate** the effective field set via `parse_flight_fields` → on
   errors, 400 `{"status": "invalid", "errors": [translated strings]}`.
4. **Duplicate guard** — if `date`/`departure_icao`/`arrival_icao` changed,
   run `_find_duplicate_flight(..., exclude_flight_id=fe.id, …)` exactly
   like the edit form; on a hit and `force_duplicate` false →
   `409 {"status": "duplicate"}` (client resubmits with
   `force_duplicate: true` after user confirmation).
5. **Apply** via `apply_flight_fields`, commit, call
   `_check_flight_hour_milestone(fe)`, and return
   `200 {"status": "ok", "entry": canonical_entry(fe, crew)}` so the client
   can update its snapshot without a refetch.

### Tests (38b)

- Conflict matrix, per field type: (server unchanged → applied), (server
  changed to same value the user chose → no conflict, applied), (server
  changed differently → conflict listing base/local/server), (user-unchanged
  field drifted server-side → no conflict, server value preserved).
- Multi-field: one conflicting + one clean change → nothing applied.
- Validation errors surface translated; counters end < start rejected.
- Duplicate guard fires on date/ICAO change, bypassed with `force_duplicate`.
- Crew slot replacement; milestone hook called (mock).
- CSRF missing/stale → 400; wrong tenant → 404; no session → 401 JSON.
- Form handler refactor: existing flights tests green, zero behaviour diff.

---

## 38c — Client offline data layer

### IndexedDB v2

Bump `_DB_VERSION` to 2 in the shared DB `openhangar-offline`; the
`onupgradeneeded` handler adds (keeping the Phase 35 `queue` store):

- `snapshots` — keyPath `aircraft_id`; value = the full snapshot JSON from
  38a plus `fetched_at`.
- `outbox` — keyPath `id` autoIncrement; value =
  `{flight_id, aircraft_id, queued_at, fields: {…}, base: {…}}` — `fields`
  and `base` are complete canonical sets. **One outbox record per flight**:
  editing an already-queued flight merges into the existing record
  (updating `fields`, never `base` — the base must stay the last-synced
  server state).

Move the IDB plumbing out of `pwa.js` into a new `app/static/js/offline_db.js`
(loaded before `pwa.js` in `base.html`) exposing a single global
`window.OhOffline` with promise-returning helpers (`getSnapshot`,
`putSnapshot`, `getOutbox`, `upsertOutboxForFlight`, `deleteOutbox`,
`outboxCount`, `ohCanon`, `flush` — flush added in 38e). `pwa.js` keeps the
legacy `queue` store logic but reads counts through `OhOffline` so the
navbar badge can show one combined number.

### Automatic snapshot refresh — the "no explicit action" requirement

In `offline_db.js`: on init of any page whose root carries
`data-oh-aircraft-id` (add this attribute to the aircraft logbook list
template `flights/list.html` and to the workbench template), and when
`navigator.onLine`:

- **Skip refresh** if the outbox contains any record for this aircraft
  (never clobber the bases of pending edits); instead flag the snapshot
  "frozen while changes are pending" (consumed by the workbench UI).
- Otherwise `fetch` the snapshot API in the background and `putSnapshot`.
- On the first successful store, call `navigator.storage.persist()`
  (best-effort) — Firefox evicts unpersisted origin data under pressure.

Snapshots also refresh after every fully-successful outbox flush (38e).

### Service-worker page caching

In `sw.js`, replace the exact-match `SWR_ROUTES` check with a matcher that
accepts the current two pathnames **plus** these patterns:

```
^/aircraft/\d+/flights$          (aircraft logbook list)
^/aircraft/\d+/logbook/offline$  (workbench, 38d)
^/offline/changes$               (offline-changes page, 38e)
```

Additionally, page JS sends `{type: 'OH_PRECACHE', urls: […]}` to the SW
when online from the aircraft logbook page (workbench URL + `/offline/changes`),
and the SW fetches-and-caches them — so having *visited an aircraft's
logbook once while online* is sufficient to work offline; no page has to be
manually opened first. Add the corresponding `message` listener in `sw.js`.

### Tests (38c)

Python side: `tests/test_pwa.py`-style content assertions — SW source
contains the new patterns and the `OH_PRECACHE` handler; `base.html` loads
`offline_db.js` before `pwa.js`; logbook template carries
`data-oh-aircraft-id`. Behavioural coverage is Playwright (38g).

---

## 38d — Workbench page

**Implementation-time correction:** the workbench's "online = immediate
save" behaviour requires `OhOffline.flush()` to exist, but the original
draft assigned `flush()` to §38e. Built as part of 38d instead (§38e now
builds the changes-page UI on top of an already-working engine) — a real
dependency-ordering fix, not a scope change. Also added here:
`@require_pilot_access` on both the workbench route and the 38b sync
endpoint, matching the online edit form's guard (`flights.edit_flight`)
— an oversight in the original 38b write-up, which only carried the
tenant/aircraft-access check.

### Route + template

`GET /aircraft/<int:aircraft_id>/logbook/offline` in the `offline`
blueprint, `@login_required` + `@require_pilot_access` + same access guard,
rendering `templates/offline/workbench.html`. The page is a **shell**: header with the
aircraft registration, an empty `<tbody>`, a `<template id="oh-wb-row">`
with translated cell markup, a JSON i18n bridge, and
`data-oh-aircraft-id`. All rows are rendered client-side from the
IndexedDB snapshot by `app/static/js/offline_workbench.js` — this is what
makes the page fully functional offline. Link to it from the aircraft
logbook list page (normal hx-boosted link).

### Behaviour

- Table sorted ascending by `(date, id)`; columns: date, dep/arr ICAO,
  dep/arr times, flight time, flight-counter start/end, engine-counter
  start/end, landings, passengers; secondary fields (fuel, oil, nature,
  notes, crew) in an expandable detail row. Read-only meta shown as icons
  (photos, GPS track).
- Inline editing; on field commit (`change` event): canonicalize with
  `ohCanon`, client-side validate (mirror the 38b rules; show the message
  inline, don't queue invalid values), update the in-memory snapshot copy,
  `upsertOutboxForFlight`, then trigger `OhOffline.flush()` — **when online
  this makes every edit an immediate direct save**; offline it just queues.
- **Continuity highlighting** (the actual correction job): for each counter
  pair, flag any row whose `*_counter_start` differs from the previous
  row's `*_counter_end` (both non-empty). Non-blocking, just a warning
  style + tooltip. Recomputed live as edits land, using local (edited)
  values.
- **Row status chips**: pending (queued, offline), syncing, synced,
  conflict / error (chip links to `/offline/changes`).
- **Offline indication**: in addition to the existing global red navbar
  badge, the workbench shows a persistent banner while offline:
  “Working offline — changes are stored on this device and will upload
  automatically” with the pending count.

### Tests (38d)

Python: route auth/access; template wiring (shell elements, template tag,
i18n bridge, `data-oh-aircraft-id`, no inline `<script nonce>`). Behaviour:
Playwright (38g).

---

## 38e — Offline-changes page (pending list + conflict resolution)

### Route + template

`GET /offline/changes`, `@login_required`, template
`templates/offline/changes.html` — same shell approach (renders from
IndexedDB, works offline). The combined navbar queue badge becomes a link
to this page.

### Pending list

One card per outbox record (plus legacy Phase 35 `queue` records, labelled
“new flight entry”): aircraft registration, flight date/route, and a
field-by-field diff table — **base (online value before) → new (offline
value)**. Actions per card: *Discard change* (per record) and, per field,
revert. This satisfies “show the details of what is currently cached
offline to be uploaded once online”.

### Sync engine (`OhOffline.flush`)

Lives in `offline_db.js`, shared by every page. Serialized (never two
flushes concurrently — module-level flag), processes outbox records in
`queued_at` order:

1. If offline → stop silently.
2. `GET /api/offline/csrf` → token for the batch (fresh, so the 1-hour
   expiry never bites). A 401 here → **auth-required state**: stop, banner
   “Session expired — your changes are safe on this device; log in to
   sync” with a login link, on all pages via the badge and prominently on
   the changes page.
3. Per record: `POST .../sync` with `X-CSRFToken`.
   - `ok` → delete outbox record, patch the snapshot with the returned
     `entry` (and reset any stored base for that flight).
   - `conflict` → mark the record `conflict`, store the `conflicts` array
     and returned `entry` on it (IDB update). **Not deleted.**
   - `duplicate` → mark `duplicate`, keep.
   - `invalid` → mark `error` with messages, keep.
   - Network failure → stop the batch (still offline / flaky), keep all.
4. Fire a DOM event `oh-offline-sync` with a summary so open pages update
   badges/chips live.

Triggers: page load when online, `window` `online` event, the SW
`OH_SYNC_REQUESTED` message, and after each workbench edit. On the changes
page, show live progress (“Syncing 3 of 12…”) and an end summary
(“9 uploaded · 2 conflicts · 1 needs attention”), plus a manual “Sync now”
button (needed for the auth-required flow after re-login).

### Conflict resolution — per field, on this page

A card in `conflict` state renders each conflicting field as a
three-column row — *value before (base)*, *my offline value*, *current
online value* — with a radio choice between the last two (default:
offline value). Non-conflicting changed fields are listed as already
decided. On “Apply resolution”: rebuild the request with
`base` = the server `entry` returned with the conflict, `fields` = server
`entry` overlaid with the fields where the user chose their offline value,
and resubmit through the normal flush path. Choosing the server value for
every field simply results in a no-op-equivalent save. A `duplicate` card
gets a “save anyway” confirmation that resubmits with
`force_duplicate: true`.

### Tests (38e)

Python: route + template wiring, badge link in `base.html`. Full conflict
UX: Playwright (38g).

---

## 38f — Phase 35 queue fixes (pre-existing bugs this phase must close)

1. **Wrong replay target**: `pwa.js` intercepts any `#flight-form` submit —
   the *edit* form included (`flight_form.html` uses the same id) — but
   `_submitEntry` replays to a hardcoded `/flights/new`, so an offline edit
   would resurface as a duplicate new flight. Fix: store the form's
   resolved `action` URL in the queued record; replay to it. Records
   without a stored action (queued by an older version) default to
   `/flights/new`. Note: once 38d ships, the workbench is the recommended
   offline-edit path, but the plain edit form must still behave correctly.
2. **Stale CSRF on replay**: queued form data contains a `csrf_token`
   captured before going offline; after > 1 h it is expired and the replay
   400s forever, silently. Fix: before replaying, fetch
   `/api/offline/csrf` and overwrite the `csrf_token` field.
3. **Silent permanent failures**: a non-5xx failure currently leaves the
   record queued with no feedback. Fix: surface legacy-queue failures on
   the `/offline/changes` page (status + discard action).

### Tests (38f)

Python: none beyond template/SW content checks (logic is JS). Playwright:
offline edit via the plain form replays to the edit URL, not `/flights/new`.

---

## 38g — Docs, screenshots, e2e

- **Playwright e2e** (`tests/e2e/test_offline_logbook.py`, `--e2e` flag,
  Playwright `context.set_offline(True)`):
  1. Visit logbook online → go offline → open workbench by URL → edit a
     counter → continuity flag updates → reload page (still offline, still
     there) → go online → auto-sync → server value verified.
  2. Conflict: snapshot → server-side change via a second session → offline
     edit of the same field → reconnect → conflict card → keep offline
     value → server verified; repeat choosing server value.
  3. Offline-changes page lists a queued edit with base → new values;
     discard works.
  4. The 38f regression test.
- **User guide** (`docs/user-guide.md`): new “Working offline” section —
  what is cached automatically and when, the workbench, the offline-changes
  page, conflict resolution, browser notes (Firefox tab is fine; install
  optional on Android; log in before going offline since sessions last
  30 days by default but a logged-out browser cannot sync).
- **Screenshots**: add workbench + offline-changes entries to
  `docs/screenshots/manifest.yml` (check whether seeded data needs query
  params, per the manifest gotcha in AGENTS.md).
- **Backlog**: none of the remaining PWA backlog items are implemented by
  this phase; do not remove them. (The former “PWA: Background Sync
  (offline flight logging)” item was folded into this phase at planning
  time.)

---

## 38h — Pilot logbook server API

Covers two distinct kinds of `PilotLogbookEntry` row, both restricted to
`pilot_user_id == current_user.id`:

- **Linked** (`flight_id` set) — always edited *alongside* its `FlightEntry`,
  online and offline, matching today's behaviour (`edit_entry` in
  `app/pilots/routes.py` already redirects linked entries to
  `flights.edit_flight` rather than rendering its own form). These ride
  inside the existing 38a/38b endpoints as an optional `pilot` payload —
  no new endpoint, so the two logbooks can never desync mid-resolve. The
  payload carries **only the user-entered subset** defined below;
  everything else is derived from the flight and recomputed server-side,
  exactly as the online form does.
- **Standalone** (`flight_id` is `NULL`) — manual entries, imports, FSTD
  sessions, flights in aircraft outside this fleet. No aircraft to key a
  snapshot off, so these get their own endpoints below.

### Canonical serialization

`canonical_pilot_entry(pe)` in `app/offline/serialize.py`, alongside
`canonical_entry`. Editable field set — everything `_entry_from_form`
(`app/pilots/routes.py`) currently parses; `cross_country` is a model
column with no form field anywhere and is **not** included:

| Field(s) | Canonical form |
|---|---|
| `date` | `"YYYY-MM-DD"` |
| `departure_time`, `arrival_time` | `"HH:MM"`; `None` → `""` |
| `night_time`, `instrument_time`, `single_pilot_se`, `single_pilot_me`, `multi_pilot`, `function_pic`, `function_copilot`, `function_dual`, `function_instructor`, `fstd_duration` | `"%.1f"`; `None` → `""` |
| `landings_day`, `landings_night` | `str(int)`; `None` → `""` |
| `aircraft_type`, `aircraft_type_icao`, `aircraft_registration`, `departure_place`, `arrival_place`, `pic_name`, `remarks` | stripped; `None` → `""` (no case-forcing — these are free-text, not strict ICAO like `FlightEntry`) |
| `entry_type` | `"flight"` or `"fstd"` |
| `fstd_type` | as stored; `None` → `""` |

### Linked entries: user-entered subset vs. derived fields

The full set above is editable only for **standalone** entries. For a
**linked** entry, the online form recomputes most pilot fields from the
flight on every save (the pilot-entry block of `_handle_log_flight_post`):
`date`, `aircraft_type`, `aircraft_type_icao`, `aircraft_registration`,
`departure_place`, `arrival_place`, `single_pilot_se`/`_me`,
`function_pic`/`_dual` (from flight time and role), and `remarks`
(= flight `notes`) are **derived** — treating them as independently
editable offline would freeze stale mirrors and manufacture conflicts
after any online save. Therefore, for linked entries:

- The `pilot` payload (snapshot `fields`, outbox base, sync request)
  contains ONLY the **user-entered subset**: `night_time`,
  `instrument_time`, `landings_day`, `landings_night`, `multi_pilot`,
  `pic_name`, `departure_time`, `arrival_time`. A sync request naming any
  other pilot field for a linked entry → 400 `invalid`.
- The two times keep the form's **mirror-unless-override** semantics
  (`flight_form.html` only pre-fills them when they differ from the
  flight's times): a linked entry's time canonicalizes to `""` whenever it
  equals the flight's corresponding time, and to `"HH:MM"` only when it is
  a genuine override. `""` in a payload means "mirror the (possibly
  updated) flight time".
- Derived fields are still shipped in the snapshot for display, under a
  separate `derived` key (see below) — never in `fields`, never in bases,
  never conflict-scanned, rejected on write.

### Validation helper extraction (refactor, no behaviour change)

`_entry_from_form` in `app/pilots/routes.py` currently builds a whole
`PilotLogbookEntry` inline. Extract into `app/pilots/form_parsing.py`:

- `parse_pilot_fields(f: Mapping[str, str]) -> tuple[dict, list[str]]` — the
  existing parse + validation logic (date required/ISO; times `HH:MM`;
  decimals/int non-negative; the FSTD toggle that nulls flight-only fields
  when `entry_type == "fstd"` and nulls `fstd_type`/`fstd_duration`
  otherwise). Error strings stay `_()`-wrapped and identical to today's.
- `apply_pilot_fields(entry: PilotLogbookEntry, values: dict) -> None` —
  assigns the parsed values onto the entry.

Rewire `new_entry` and `edit_entry` to call these; existing pilots tests
must pass unchanged. Preserve one pre-existing quirk exactly: `edit_entry`
copies *all* columns from the freshly-parsed entry, which nulls
`cross_country` (an import-only column with no form field) on every online
standalone edit — do not "fix" this in passing; the zero-behaviour-diff
rule wins.

### Linked-entry extension (38a/38b)

- `GET /api/offline/aircraft/<id>/logbook` (38a): each entry object gains
  an optional `"pilot": {"entry_id": 91, "fields": {…user-entered subset…},
  "derived": {…remaining canonical pilot fields, display-only…}}` key,
  present only when a `PilotLogbookEntry` exists with
  `flight_id = fe.id AND pilot_user_id = current_user.id`; omitted
  otherwise. Only `fields` participates in bases and conflict scans.
- `POST /api/offline/flights/<id>/sync` (38b): request body gains an
  optional `"pilot": {"fields": {…}, "base": {…}}` sibling to the top-level
  `fields`/`base` — **user-entered subset only**. When present: re-fetch
  the linked entry (if it no longer exists, or no longer belongs to the
  user, return `409 {"status": "pilot_missing"}` — the client drops the
  pilot section and retries with flight fields only); run the same
  conflict-scan → validate (`parse_pilot_fields`, subset) sequence against
  it, **in the same transaction and same all-or-nothing rule** as the
  flight fields (one row, one outbox record, one commit).
- **Derived-field recomputation**: extract the pilot-entry update block of
  `_handle_log_flight_post` (`pe.date = flight_date` … `pe.remarks = notes`)
  into a shared helper `apply_linked_pilot_entry(fe, pe, ac, values,
  pilot_role)`, where `values` is the user-entered subset (times `None` =
  mirror the flight times, exactly today's semantics). Both the online form
  handler and the sync endpoint call it after the flight fields are
  applied, so derived fields always follow the updated flight. `pilot_role`
  is not in any payload: the sync path recovers it from the existing entry
  (`function_pic` non-null → `"pic"`, `function_dual` non-null → `"dual"`,
  neither → apply the block without touching the function columns).
  Response gains `"pilot": {"fields": …, "derived": …}` when applicable.

### Standalone-entry endpoints

`GET /api/offline/pilot/logbook` — `@api_login_required`; current user's
`PilotLogbookEntry` rows with `flight_id IS NULL`, canonical-serialized,
sorted `(date, id)`.

`POST /api/offline/pilot/logbook/<int:entry_id>/sync` — `@api_login_required`;
entry must have `pilot_user_id == current_user.id` **and** `flight_id IS
NULL` (404 otherwise — a linked row must go through the flight sync path,
never this one, so the two logbooks can't drift apart). Same
conflict-scan → validate (`parse_pilot_fields`) → apply
(`apply_pilot_fields`) → commit → `200 {"status": "ok", "entry":
canonical_pilot_entry(entry)}` sequence as 38b, minus the duplicate guard
(no equivalent uniqueness check exists for pilot entries) and minus the
flight-counter milestone hook (not applicable).

### Tests (38h)

- Canonical serializer: full field-by-field, including FSTD-nulled fields.
- Linked snapshot: `pilot` key present/absent correctly; `fields` vs
  `derived` split correct; sync happy path; pilot-only conflict;
  flight-only conflict; both conflicting (nothing applied);
  `pilot_missing` when the linked entry was deleted server-side.
- Linked derivation: a flight-date or counter edit propagates to the
  linked entry's `date` and `function_pic`/`function_dual` hours;
  `remarks` follows flight `notes`; a payload naming a derived pilot
  field → 400 `invalid`; role recovery from the function columns.
- Mirror times: `""` in the payload tracks the updated flight time; an
  explicit `"HH:MM"` override survives a flight-time change; an override
  equal to the flight time canonicalizes back to `""`.
- Standalone: full conflict matrix mirroring 38b's; access checks (other
  user's entry → 404; a linked entry hit on the standalone sync endpoint →
  404); anonymous → 401 JSON.
- Form refactor: existing `test_pilot_logbook*` tests green, zero
  behaviour diff.

---

## 38i — Pilot logbook client + UI

### IndexedDB v3 (version bump required)

Object stores can only be created inside `onupgradeneeded`, which only
fires on a version change — and 38c ships v2 before these stores exist.
Bump `_DB_VERSION` to 3; the upgrade handler creates each store only if
absent (`db.objectStoreNames.contains(...)`), so v1→3 and v2→3 upgrades
both work and existing v2 data is untouched.

- `pilot_snapshot` — single record, fixed key `"me"`; value = the
  standalone-entries snapshot JSON (38h) plus `fetched_at`.
- `pilot_outbox` — keyPath `id` autoIncrement; one record per
  `entry_id`: `{entry_id, queued_at, fields, base}` — merges on repeated
  edits of the same entry, same rule as the flight `outbox`.
- The existing `outbox` record (one per flight, from 38c) gains an
  optional `pilot: {fields, base}` sibling, written by a new
  `upsertOutboxForFlight(flightId, {flight: {...}, pilot: {...}})` overload.

New `OhOffline` helpers: `getPilotSnapshot`, `putPilotSnapshot`,
`getPilotOutbox`, `upsertPilotOutboxForEntry`, `deletePilotOutbox`,
`pilotOutboxCount`. `flush()` (38e, extended in 38j) processes `outbox`
then `pilot_outbox` in the same serialized batch.

### Aircraft workbench — "My logbook" section

In `workbench.html`/`offline_workbench.js` (38d): each row's expandable
detail section gains a "My logbook" subsection when the snapshot entry
carries a `pilot` object — the user-entered subset (night/instrument
time, landings, multi-pilot, PIC name, the two time overrides) is
editable; the `derived` fields (date, places, function hours, remarks —
§38h) render read-only with a hint that they follow the flight fields.
Edits canonicalize via `ohCanonPilot(field, value)`
(mirrors `ohCanon`), update the in-memory copy, call
`upsertOutboxForFlight(flightId, {pilot: {...}})`, then `flush()` — same
immediate-save-when-online behaviour as the flight fields. Rows with no
linked entry show a disabled placeholder: "No personal logbook entry
linked to this flight — add one from the online form" (translated, links
to the online edit form; that form remains the only way to *create* the
link, per the "no creation offline" constraint).

### New pilot logbook workbench

`GET /pilot/logbook/offline` in the `offline` blueprint, `@login_required`
+ same-user guard, template `templates/offline/pilot_workbench.html`, JS
`offline_pilot_workbench.js`. Same shell-page pattern as 38d: rendered
client-side from `pilot_snapshot`, works fully offline. Columns mirror
`entry_form.html`; rows where `entry_type == "fstd"` show FSTD-specific
columns (type, duration) and blank/disable the flight-route columns,
matching the online form's toggle. No continuity highlighting — this
logbook has no counters to check. Same row-status chips and "working
offline" banner as 38d. Linked from `pilots/logbook.html` (normal
hx-boosted link) and from the "My logbook" placeholder above.

### Auto-snapshot and SW caching

Add `data-oh-pilot-logbook` to `pilots/logbook.html` and to the new
workbench template; refresh `pilot_snapshot` the same way as aircraft
snapshots (skip while `pilot_outbox` has any record; freeze with the same
"frozen while changes are pending" flag). `sw.js` route matcher gains
`^/pilot/logbook/offline$`; `OH_PRECACHE` fires for it (and
`/offline/changes`) when visiting `pilots/logbook.html` online, same
mechanism as 38c.

### Tests (38i)

Python: route auth/access; template wiring (shell elements, `<template>`
tag, i18n bridge, `data-oh-pilot-logbook`, no inline `<script nonce>`);
"My logbook" section markup present in `workbench.html`. Behaviour:
Playwright (38l).

---

## 38j — Offline-changes page extended to the pilot logbook

`/offline/changes` (38e) gains a third card family sourced from
`pilot_outbox`: standalone pilot-logbook edits, same base→new diff table,
same discard action. Aircraft-logbook cards (from `outbox`) show the
`pilot` sub-diff inline when present, alongside the flight-field diff —
one card per flight, not two.

Conflict resolution (38e) extended: a card in `conflict` state for an
aircraft-logbook row may carry conflicts from the flight fields, the pilot
fields, or both — each resolved independently per field before "Apply
resolution" resubmits the merged request. A `pilot_missing` response (the
linked entry was deleted server-side while offline) renders as its own
notice — "Your personal logbook entry for this flight was removed online;
the aircraft-log changes above are unaffected" — with a "discard pilot
changes, keep flight changes" action that resubmits flight-only.

`flush()` progress ("Syncing 5 of 14…") and the end summary count across
all three sources (aircraft outbox, pilot outbox, legacy queue).

### Tests (38j)

Python: route/template wiring for the new card family. Full conflict UX
incl. `pilot_missing`: Playwright (38l).

---

## 38k — Cross-cutting offline-submit guard

A user already sitting on a page that is *not* offline-aware (a
maintenance form, the standalone pilot `entry_form.html` when linked-entry
creation is attempted, or any future page) when connectivity drops can
still fill it in and submit; the POST then fails with a raw network error
instead of a clear message — the trap this sub-phase closes.

New `app/static/js/offline_form_guard.js`, loaded unconditionally like
every other module (IIFE, guarded, `DOMContentLoaded` + `htmx:afterSettle`
init). It attaches one capturing `submit` listener at `document` level: if
`navigator.onLine` is false at submit time and the form lacks
`data-oh-offline-aware` (present on the workbench forms from 38d/38i
**and on `flight_form.html`**, whose offline submits are already queued by
the Phase 35 machinery in `pwa.js` — without the attribute the guard's
capturing listener would fire before that queue and show a contradictory
"can't be saved" message on a flow that works), `preventDefault()` and show an
inline, dismissible alert above the form: *"You're offline — this can't be
saved right now. Reconnect and try again."* No queuing, no retry — purely
a UX guard so nothing is lost silently or hangs on a dead fetch. It also
listens for `htmx:sendError` (already-online submit whose connection drops
mid-flight) and shows the same message.

Deliberately generic: no per-page allow/deny list to maintain — any
current or future form gets the same protection automatically, and only
the pages that explicitly opt out (via `data-oh-offline-aware`) bypass it.

### Tests (38k)

Python: content assertion — file exists, listens for `submit` and
`htmx:sendError`, loaded from `base.html`; `flight_form.html` and both
workbench templates carry `data-oh-offline-aware`. Behaviour: Playwright
(38l), including a regression check that an offline new-flight submit
still queues via `pwa.js` with no guard message.

---

## 38l — Docs, screenshots, e2e (pilot logbook additions)

Extends 38g's suite:

- **Playwright e2e**: linked-entry pilot fields edited offline via the
  aircraft workbench, incl. pilot-only conflict, flight-only conflict,
  both-conflicting, and `pilot_missing`; a standalone pilot-logbook entry
  (incl. an FSTD entry) edited via `/pilot/logbook/offline`;
  `/offline/changes` shows all three card families; the 38k guard fires on
  a non-offline-aware form (e.g. a maintenance edit page) while offline
  and blocks the submit.
- **User guide**: extend the "Working offline" section (38g) with the
  pilot logbook workbench, the "My logbook" section on aircraft rows, and
  what still isn't offline-capable (creating entries, maintenance).
- **Screenshots**: pilot workbench + an FSTD row, added to
  `docs/screenshots/manifest.yml`.

---

## Cross-cutting requirements

- **i18n**: every new user-visible string wrapped in `_()` / Jinja `_()`,
  translated in `fr` (U+202F narrow no-break space before `: ; ! ?` etc.)
  and `nl`; JS strings via JSON bridges only. Plural forms (“%(n)s changes
  pending”) via `ngettext`.
- **Coverage**: 100 % on all new/changed Python lines; JS is exercised via
  the e2e suite.
- **No new env vars, no migrations** — if an implementation choice seems to
  require either, the choice is wrong; revisit.
- **Security**: all endpoints tenant-scoped through the same access helpers
  as the flights blueprint; CSRF enforced on the sync POST via header; no
  new bandit findings. IndexedDB content is per-origin and unencrypted —
  acceptable (same trust level as the browser session cookie), but the user
  guide should mention that a shared device keeps logbook data locally.
- **Pilot logbook endpoints/pages (38h–38l) are always scoped to
  `pilot_user_id == current_user.id`** — never another pilot's data, even
  for admins.
- **Conventional commits**, one per sub-phase (e.g.
  `feat(offline): snapshot + csrf APIs with canonical serialization (38a)`).

---

## Decisions log (why, in one line each)

- **No version column / no migration** — per-field base-value comparison
  gives finer-grained conflicts than a row version *and* keeps the phase
  schema-free; canonical strings make equality trivial.
- **Complete field set + base in every sync request** — mirrors form
  semantics, keeps the server stateless about client snapshots, and makes
  the conflict rule a pure function of (base, local, current).
- **Row-level all-or-nothing apply on conflict** — partial application
  would leave counters half-updated; the user resolves and resubmits the
  whole row.
- **Snapshot frozen while outbox non-empty** — refreshing bases under
  pending edits would silently redefine what “conflict” means mid-flight.
- **Workbench renders from IndexedDB, not server HTML** — one code path
  online and offline; server HTML would fork behaviour and break the live
  continuity checks.
- **Reuse `parse_flight_fields` for both form and API** — a single
  validation authority; divergence here is the biggest correctness risk of
  the phase.
- **Sync via `online` event + page load, not Background Sync API** — the
  target browsers (Firefox, Brave) don't provide it reliably; the user is
  present anyway when resolving conflicts.
- **Fresh CSRF fetched at flush time** — tokens expire after 1 h; any
  design that stores tokens across the offline window is dead on arrival.
- **`/offline/changes` is the single sync-status surface** — one place to
  see, resolve, retry, or discard everything pending, across the aircraft
  outbox, the pilot outbox, and the legacy Phase 35 queue.
- **Linked pilot fields ride inside the flight sync endpoint, not a
  parallel one** — mirrors the online form's "one save updates both"
  behaviour and keeps one outbox record per flight; a separate endpoint
  would let the two logbooks drift apart mid-resolve.
- **Standalone entries get their own snapshot/outbox/workbench, keyed by
  pilot rather than aircraft** — they have no aircraft at all (FSTD,
  manual entries, other-fleet flights), so the aircraft-scoped model
  doesn't fit; reusing the same base-value/outbox/conflict pattern avoids
  inventing a second architecture, just a second instance of the same one.
- **Pilot-logbook work (38h–38l) sequenced after 38a–38g** — the airframe
  workbench is the deadline-critical path; keeping 38a–38g's endpoints and
  shapes exactly as originally specced (38h only adds an optional field)
  means the larger scope can't put the deadline at risk.
- **No continuity highlighting for pilot entries** — `PilotLogbookEntry`
  has no counters; the feature has nothing to check.
- **Offline-submit guard (38k) is generic, not a per-section allow/deny
  list** — a list needs updating every time a new form is added; a single
  document-level listener keyed off `data-oh-offline-aware` degrades
  safely for anything new, including future features outside this phase.
- **Linked pilot payload = user-entered subset only; derived fields
  recomputed server-side** — online saves rewrite those fields from the
  flight (`_handle_log_flight_post`); letting them be edited independently
  offline would freeze stale mirrors and manufacture spurious conflicts.
- **`flight_form.html` opts out of the 38k guard** — its offline submits
  are already queued by `pwa.js`; guarding it would contradict a shipped
  flow with a duplicate, opposite banner.
- **IndexedDB bumps to v3 in 38i** — object stores can only be created
  during a version change, and 38c ships v2 before the pilot stores exist.
