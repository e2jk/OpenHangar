# Security hardening, certificate renewals and landing recency by class

**Upgrade notes — please read before upgrading**
- **Sync offline changes first.** Ask everyone who uses OpenHangar offline to open **Offline changes** and sync any pending flights or logbook edits before you upgrade. Offline data is now stored per account; anything still pending from before the upgrade stays in that browser but is no longer shown or synced.
- **Everyone signs in again** after the upgrade: logins now have a server-side record, and older cookies are not accepted.
- **Direct installations (Raspberry Pi or a published port, no reverse proxy):** add `OPENHANGAR_PROXY_HOPS=0` to your compose file. Existing compose files keep the previous behaviour (one trusted proxy hop), which lets a client fake its address and protocol when nothing strips those headers.
- **Reverse-proxy installations:** nothing changes unless you adopt the updated `docker/docker-compose.yml`. It now sets `OPENHANGAR_TRUSTED_HOSTS` to `OPENHANGAR_HOSTNAME`, `localhost` and `127.0.0.1`; add any other hostname or IP you use, or those requests get HTTP 400. See `docs/configuration.md`.

**Documents**
- Pilot documents work like an aircraft's certificates: **Replace** on your medical certificate or licence uploads the next one, and the old one stays on file, marked superseded — still viewable and downloadable. Pilot documents also take a "Valid from" date.
- Your medical certificate now drives the **Medical expiry** on your profile (read-only once a medical document is uploaded), and with it the "medical expiring" reminder.
- The same rules for every certificate, aircraft or pilot: the one in force is the one that has started and is valid the longest, so uploading an older certificate for the record never displaces the current one. A renewal uploaded ahead of time stays "upcoming" — the current one remains in force until the renewal's start date, then is marked superseded on its own.
- No "expiring soon" warning or reminder while a renewal already on file picks up from the day after: the dashboard, the fleet status, the grounded check, the medical status and reminder and the documents' own badges now all count coverage through it (the aircraft page and insurance/ARC emails already did). A real gap is still reported.
- The aircraft documents list has a **Replace** button on the certificate in force, preselecting its type and category.
- Superseded links are now kept up to date (daily, and on every upload, edit and delete): an insurance certificate whose renewal was uploaded ahead of time is marked superseded once the renewal starts.

**Pilot logbook**
- The per-type landing recency on the pilot profile counts flights on your own aircraft again: a type such as "ROBIN DR-401 155CDI" (manufacturer in front, engine variant after the model) now resolves to its ICAO code, DR40. Simulator sessions are left out of the per-type recency.
- Landing recency now follows EASA FCL.060's "same type or class": piston aeroplanes count by class (SEP or MEP, land or sea), so landings on a DR40 also make you current on a C152 or a PA-28; other aircraft count by type. Each class lists the types it was flown on, and the pilot profile shows the aircraft flown in the last 6 months with their last flight and recent landings.

**Performance**
- The shared-ownership reserve-fund balance now uses one database query, rather than a query per historical contribution, while keeping the same cent amounts and reversal handling.
- The dashboard and pilot profile no longer load the whole logbook to work out landing recency — only the last 6 months, plus one summary query — and the dashboard's flight-tracks map loads its tracks in one query instead of one per flight (about a third faster on a 170-flight logbook).

**Security**
- Offline pages and saved changes are isolated by account. Signing out or changing accounts retires private page caches; pending changes remain with their original account and require an explicit review/resume after a new login. Private online navigation verifies access with the server before falling back to its cache.
- Authentication challenges now expire and can be used only once. Login and authenticator enrolment state stays on the server, including first-run setup, so saved browser cookies cannot replay a completed enrolment.
- Logins are now revocable: logout ends that browser session, and password recovery, password/authenticator changes, deactivation and membership removal invalidate older logins. Your acting browser stays signed in after changing its own credentials. Upgrading the stored password hash at login does not sign you out elsewhere.
- CSV downloads protect user-entered text from being interpreted as spreadsheet formulas, while keeping monetary amounts numeric.
- Share pages and aircraft photos recheck authorization on revisits, so revoked links and removed aircraft access are respected by browser HTTP caches. Previously saved copies remain outside the application’s control.
- Forwarded client addresses are trusted only as far as configured: `OPENHANGAR_PROXY_HOPS` sets the number of trusted proxies (`0` for direct access), and `OPENHANGAR_TRUSTED_HOSTS` limits the hostnames used for invite, password-reset and share links. The internal readiness check accepts only genuine local requests.

**Reservations and billing**
- Booking settings, dispatch counters and rental charges reject invalid, non-finite, negative (where prohibited) and out-of-range numbers without saving changes. Charge totals use decimal arithmetic with ties rounded to even cents; signed adjustments remain supported.
- Competing booking, downtime and dispatch changes now wait for each other per aircraft. Overlapping confirmations cannot both succeed, and repeated checkout/checkin requests return a useful message instead of a database error. Adjacent bookings and authorized overrides remain supported.

**Maintenance**
- The test wrapper accepts explicit `--targeted` selections without overwriting full-suite coverage reports; unrecognized full-run arguments now fail clearly instead of being ignored.
- Translation files regenerated with the i18n script: strings added by hand now carry their source references (their absence made the nightly Weblate scan's Code Scanning upload fail), and four strings no longer used are dropped.
- Contributor docs: new strings now always go through `scripts/update_i18n.sh`; the shortcut of adding a few `.po` entries by hand is retired.
- The nightly Weblate translation scan uses weblate-checks-action v1.1.1: a flagged string with no source location no longer makes GitHub reject the whole upload.
- Dutch: a past revision of the personal minimums is now labelled "Verouderd" instead of "Vervangen", the label of the documents' **Replace** button.
