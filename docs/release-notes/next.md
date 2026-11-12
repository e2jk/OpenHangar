# Certificates: replace, keep the old one, and no warning with a renewal

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
- The dashboard and pilot profile no longer load the whole logbook to work out landing recency — only the last 6 months, plus one summary query — and the dashboard's flight-tracks map loads its tracks in one query instead of one per flight (about a third faster on a 170-flight logbook).

**Maintenance**
- Translation files regenerated with the i18n script: strings added by hand now carry their source references (their absence made the nightly Weblate scan's Code Scanning upload fail), and four strings no longer used are dropped.
- Contributor docs: new strings now always go through `scripts/update_i18n.sh`; the shortcut of adding a few `.po` entries by hand is retired.
- The nightly Weblate translation scan uses weblate-checks-action v1.1.1: a flagged string with no source location no longer makes GitHub reject the whole upload.
