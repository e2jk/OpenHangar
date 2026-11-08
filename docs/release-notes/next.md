# SkyDemon exports, and release notes without repeats

**GPS import**
- SkyDemon's GPX and KML exports import as they come — its `.gpx.bin` files no longer need renaming: a file's GPX or KML content is recognised whatever its extension. (SkyDemon's own `.flightlog.bin` can't be read; the import says so and suggests exporting GPX or KML.)
- KML tracks get the same treatment as GPX files without speed: speed worked out over a few seconds and bad fixes left out — a SkyDemon KML used to show a dozen landings and a split flight.
- A recording that starts during the takeoff roll (as SkyDemon's often do) or stops right after the landing roll now names the airfield: it only counts as recorded from the air when it also starts high above the field.

**Maintenance**
- Release notes list only what's new since the previous release (patch or minor): no more PRs of the previous release, nor everything since the start of the minor version.
- A release without a title line in its summary is named after its only PR, "Dependency updates" when that's all it has, or its most significant PR "(+N more)" (a hand-written title, then a feature, then a fix…) — the nightly releases included.
- A release that includes a security fix (a dependency update for a vulnerability) says so in its name: `[SECURITY]` at the end.
- All past releases were renamed and their change lists rewritten the same way (each lists only what's new; the early ones, which had no list, now list their commits).
- CI rides out brief outages of GitHub's downloads (actionlint, the fuzzing badge): retried instead of failing the job — one such outage had failed a ship PR's required checks.
- The site's versions.json, read by every instance's update check, is built from the published releases: a failed download of the current copy no longer risks republishing it as just the latest version, and versions earlier runs missed are listed again.
- Recognising a GPS file by its content no longer uses a regular expression that crafted input could make very slow (CodeQL alerts #619–621).
- Distinct French and Dutch wordings where two different English texts shared one translation (Weblate checks): the Data checks page's "Check" column and "Dismiss" button, and the dashboard's "Past month".
