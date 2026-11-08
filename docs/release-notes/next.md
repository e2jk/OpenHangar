# SkyDemon exports, and release notes without repeats

**GPS import**
- SkyDemon's GPX and KML exports import as they come — its `.gpx.bin` files no longer need renaming: a file's GPX or KML content is recognised whatever its extension. (SkyDemon's own `.flightlog.bin` can't be read; the import says so and suggests exporting GPX or KML.)
- KML tracks get the same treatment as GPX files without speed: speed worked out over a few seconds and bad fixes left out — a SkyDemon KML used to show a dozen landings and a split flight.
- A recording that starts during the takeoff roll (as SkyDemon's often do) or stops right after the landing roll now names the airfield: it only counts as recorded from the air when it also starts high above the field.

**Maintenance**
- Release notes list only what's new since the previous release (patch or minor): no more PRs of the previous release, nor everything since the start of the minor version.
- A release without a title line in its summary is named after its only PR, "Dependency updates" when that's all it has, or its first meaningful PR "(+N more)" — the nightly releases included.
- A release that includes a security fix says so in its name: `[SECURITY]` at the end.
