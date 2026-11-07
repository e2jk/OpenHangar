**Fuel**
- The aircraft page shows the fuel on board: what was left after the latest flight (per tank when known) plus fuel added since, with a warning when later flights recorded no fuel remaining.

**GPS files**
- GPS files attached in the flight form are now kept unchanged too, like those from the GPS imports.
- Download a flight's original GPS file from its page, or all of an aircraft's (GPS import history) or all of your own (My Flight Tracks) as one zip.
- The configuration page shows how many original GPS files are kept and their total size.
- One flight can now have several GPS logs — e.g. when the logger lost power in flight and started a new file, or a flight logged as one entry landed and departed again: the tracks are combined into one, and each file can be downloaded.
- Linking a GPS track to a flight already in the logbook no longer changes its recorded times or date — the logbook stays authoritative; only flights created from GPS take their times from it.
- A GPS segment a few minutes off the logbook's times (which are only to the 0.1 h) is now recognised as the same flight when it's the only or clearly best candidate, instead of being flagged as uncertain.
- New **Accept all best matches** button on the GPS import review page links every matching segment at once.
- GPS imports no longer attach a quick turnaround's next leg to the previous flight, nor glue a ground-only recording from another day onto a flight.
- Uploading a flight's GPS files one at a time now works like uploading them together: a later file is offered as another part of the flight that already has a track (within its logged times), instead of as a new flight. The review page says whether a segment adds to a flight's track or replaces it.
- The pilot GPS import, too, treats a single close candidate as the match.

**Logbook imports**
- Review page: rows whose values differ are now readable in dark mode, and identical numbers (e.g. counters) are no longer flagged as different.

**Dashboard**
- The flights and hours card now covers the past month (rolling), instead of starting over on the 1st of each month.

**Aircraft logbook**
- After saving or deleting a flight, or cancelling its edit, you're back on the page of the aircraft's logbook you came from, with the same page size — not the first page. The flight page's back button now returns there too (it went to the aircraft page).

**Maintenance**
- Backups no longer include the temporary copies of files from imports in progress, and those older than a day are deleted daily (an abandoned import used to leave its file behind for good).
- CI runs on Ubuntu 26.04, named explicitly instead of the floating `ubuntu-latest` (which GitHub moved to 26.04).
- Fixed the code-scanning notes reported on v3.34.0 by changing the code (no suppressions).
- `scripts/ship.sh` lists the commits made on GitHub since the last release (e.g. dependency updates) that ship along, and asks before pushing when one that isn't a dependency update may be missing from this summary; `--upstream` just lists them.
