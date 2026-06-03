# Backlog — nice to have, not yet planned

Ideas that were considered but deferred. Not prioritised, not scheduled.

---

### Frontend: automated library updates and version tracking

Frontend libraries (Bootstrap, Leaflet, Bootstrap Icons, qrcodejs, canvas-confetti)
are pinned in `scripts/fetch_vendor_assets.py`. Updating a library is a manual
10-minute task: bump the version and URL, run the script to get the new hash from
the mismatch output, commit.

A more automated approach would use Renovate or Dependabot to open PRs when new
versions are published, auto-update the hashes in `fetch_vendor_assets.py`, and
merge automatically for patch releases. This is not worth the setup cost yet because:

- The libraries are stable and update infrequently.
- **Auto-merging minor or major updates requires a frontend test suite** (Playwright
  or Cypress) to catch regressions — Bootstrap 5→6 will have breaking changes, and
  without automated visual/interaction tests there is no safe way to merge
  automatically. The test suite is the real prerequisite.

When a frontend test suite is eventually added, revisit this item alongside the
`require-hashes` note below.

---

### Security: `require-hashes` for Node/NPM if a frontend build pipeline is introduced

OpenHangar currently has no Node.js build step. If a webpack/vite/esbuild pipeline
is ever added, the npm equivalent of pip's `--require-hashes` should be enforced:
use `npm ci` (which verifies `package-lock.json` integrity), and consider
`npm audit --omit=dev` in CI to catch CVEs in production dependencies.

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

## Syncthing integration for document sync

Allow OpenHangar's upload folder to be a Syncthing-managed directory, so
documents are automatically synchronised between the server, personal computers,
and any other peers — without manual upload through the web UI.

**This is entirely opt-in.** Users who upload documents only through the
OpenHangar web UI are unaffected. The structured path layout described below
is used by OpenHangar regardless of whether Syncthing is involved — it is simply
how files are stored on disk. Syncthing just makes that folder visible on other
devices.

### Ownership model

**Syncthing owns the folder structure.** Users organise files on their own
computers in the canonical path layout; OpenHangar reconciles what it finds.
OpenHangar does *not* rename or move files that arrived via Syncthing — it reads
the path to infer metadata and writes its own database record.

When a document is uploaded through the OpenHangar web UI, OpenHangar writes the
file to the canonical path automatically. This means Syncthing (if configured)
will pick it up and propagate it to other peers without any extra steps.

### Document category — a prerequisite

The `doc_type` path component requires a **document category** field on the
`Document` model (currently absent). This is needed regardless of Syncthing:
it enables filtering and organising documents in the UI. The category becomes
the on-disk folder name. Implementing document categories is a prerequisite for
this feature.

### Canonical path format

```
{tenant_slug}/{aircraft_reg}/{doc_type}/{YYYY-MM-DD} - {title}.{ext}
```

Example:
```
example-hangar/OO-TUF/maintenance/2024-03-15 - Annual inspection.pdf
example-hangar/OO-TUF/insurance/2025-01-01 - Hull insurance.pdf
```

Canonical `doc_type` folder names (maps to the document category field):
`maintenance`, `insurance`, `poh`, `airworthiness`, `logbook`, `invoice`, `other`

Documents uploaded without a category (during a transition period) go into an
`uncategorised/` subfolder until the user assigns a category.

### Docker setup

Mount the Syncthing-managed folder as the uploads volume:
```yaml
volumes:
  - /path/to/syncthing/OpenHangar:/data/uploads
```
No Syncthing API integration needed — Syncthing handles transport, OpenHangar
reads from disk.

Configure Syncthing with **"Receive Only"** mode disabled on the OpenHangar
peer (it can send and receive), but document in self-hosting guide that renaming
or moving files outside OpenHangar is unsupported and will create orphaned DB
records.

### Reconcile screen

A background job (or manual "Scan for new files" button) diffs the filesystem
against the `documents` table and surfaces untracked files. For each:

- Parse `tenant_slug` → look up tenant (prompt once if ambiguous, then cache)
- Parse `aircraft_reg` → look up aircraft (prompt once if ambiguous, then cache)
- Parse `doc_type` folder → map to document category (flag unknown names)
- Parse `YYYY-MM-DD` from filename → pre-fill document date
- Parse title from filename → pre-fill document title
- User confirms or edits, clicks "Import" → creates `Document` row

A "pending reconcile" table (`filename`, `detected_at`, `reconciled_at`,
`ignored`) stores the queue so the scan is idempotent.

### Deletions

- **Deleted via OpenHangar UI**: move file to a `_trash/{tenant}/{reg}/`
  subfolder rather than hard-deleting. Syncthing propagates the move. Both
  sides stay consistent and the file is recoverable.
- **Deleted on a peer (outside OpenHangar)**: Syncthing removes the file from
  disk; the `Document` DB row now points to a missing file. A nightly scan flags
  these as broken links on the document list (warning icon, no download link).

### Limitations to document

- Renaming a file on a peer = Syncthing sees delete + add = orphaned DB record +
  new reconcile entry. Workaround: always rename through the OpenHangar UI.
- Pilot logbook attachments and aircraft photos use a different storage path and
  are out of scope for the initial implementation.

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

---

### Pilot logbook
- Based on the data in the pilot log, check if currency/recency is still up to date
  (e.g. number of [night] landings in a specific type to take passengers). Currency
  is grouped by ICAO type designator — PA28-161, PA28-161 TDI and PA28-161 IFR all
  share the P28A designator and are therefore already treated as the same type.
  This is also a prerequisite for the multi-pilot currency matrix.

  **Entries without an ICAO code:** at currency-check time, attempt on-the-fly
  resolution via `resolve_aircraft_type_icao` (handles hyphens/spaces, e.g.
  "C-172" → "C172"). Entries that still cannot be resolved are excluded from
  type-based currency but surfaced with a warning ("N entries have no ICAO type
  assigned — assign types to include them"). A future bulk-assignment screen
  (link from the warning) would let pilots fix imported logbooks in one pass.

  Cross-ICAO-code family grouping (e.g. treating P28A + P28B as the same family)
  is explicitly out of scope for the first implementation — it is an advanced use
  case driven by specific national authority rules and can be layered on later.

---

### Flight tracks animation: gradual fade of older tracks

During the animation, older tracks all fade simultaneously when it finishes.
A smoother UX would reduce each track's opacity incrementally as newer ones
are drawn, so the most recent track is always the brightest and earlier
ones progressively dim in real time rather than all at once at the end.

---

### GIF export: progressive zoom-out effect

The web animation progressively re-fits the map bounds as each track is
drawn, creating a zoom-out effect. The server-side GIF currently starts
at the final zoom level for all frames. A nicer GIF would replicate this
by re-computing the bounding box per frame and re-compositing tiles —
adds significant complexity (tile refetching or pre-fetching at multiple
zoom levels) so deferred.

---

### Security alerting on `[SECURITY]` log events (N-22)

Send a real-time notification when an escalated security event is logged, so
administrators are alerted without having to tail logs manually.

**Which events warrant an alert** (the rest are log-only):
- `auth.login.account_locked` / `auth.login.account_blocked` — active brute force
- `auth.totp.replay` — targeted session attack
- `users.role.changed`, `users.access.revoked` — post-auth privilege changes

**Implementation**: a `SecurityAlertHandler(logging.Handler)` attached to the
`openhangar` logger in `app/init.py`. It filters for WARNING+ records containing
`[SECURITY]` and only fires for the escalated event types above. It must handle
delivery failures gracefully (log the error, never raise — alerting must never
break the app). Include a short debounce (e.g. 60 s per event+email pair) to
avoid alert storms from a single lockout generating multiple log lines.

The handler reads delivery config from env vars; unset vars silently disable that
channel. Three channels, in increasing effort:

**ntfy.sh** (recommended first) — HTTP POST to a topic URL; works with the free
hosted service or a self-hosted instance. Instant push to mobile via the ntfy app.

```
NTFY_TOPIC_URL=https://ntfy.sh/your-private-topic
```

Self-hosting ntfy as a separate Docker service (not bundled inside OpenHangar —
if OpenHangar goes down you still want alerts):

```yaml
ntfy:
  image: binwiederhier/ntfy
  command: serve
  volumes:
    - ./ntfy/data:/var/lib/ntfy
  ports:
    - "8080:80"
```

Document the ntfy setup in `docs/self-hosting.md` with a ready-to-paste snippet.

**Email** — uses the existing `SMTP_*` env vars already stubbed in
`docker-compose.yml`. Send via `smtplib` with a plain-text body. No extra
dependencies.

```
ALERT_EMAIL_TO=admin@example.com
# SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD already in compose
```

**Generic webhook** — HTTP POST with a JSON body `{"event": "...", "detail": "..."}`.
Covers Slack/Discord incoming webhooks and any custom receiver.

```
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

All three channels can be active simultaneously. Each is enabled only when its
env var is set. Add `NTFY_TOPIC_URL`, `ALERT_EMAIL_TO`, and `ALERT_WEBHOOK_URL`
as commented-out stubs in `docker-compose.yml`.

---

### Operational activity logging (non-security audit trail)

Add structured log entries for significant fleet and operational changes so that
administrators can reconstruct "what happened and when" without querying the database.

Proposed events (using a `[ACTIVITY]` prefix distinct from `[SECURITY]`):

- Aircraft created / deleted / archived — `aircraft_id`, `registration`, `user_id`
- Component added / removed — `component_id`, `type`, `aircraft_id`
- Maintenance entry recorded / deleted — `service_id`, `aircraft_id`
- Flight logged / deleted — `flight_id`, `aircraft_id`, `pilot_user_id`
- Document uploaded / deleted — `document_id`, `aircraft_id` or `pilot_user_id`
- Snag opened / resolved — `snag_id`, `aircraft_id`
- User invited / invitation accepted — `invitation_id`, `email`

Implementation notes:
- Use a dedicated logger (`openhangar.activity`) so ops can route `[ACTIVITY]` to a
  separate sink (file, syslog, external SIEM) without mixing with security events.
- Each entry should include `ip=` and `user_id=` for traceability, sanitised via
  the same `_sl()` helper used in security logging.
- Consider a future database-backed audit table if export/search is needed; the
  structured log format makes migration straightforward.
