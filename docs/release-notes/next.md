**Fuel**
- The aircraft page shows the fuel on board: what was left after the latest flight (per tank when known) plus fuel added since, with a warning when later flights recorded no fuel remaining.

**GPS files**
- GPS files attached in the flight form are now kept unchanged too, like those from the GPS imports.
- Download a flight's original GPS file from its page, or all of an aircraft's (GPS import history) or all of your own (My Flight Tracks) as one zip.
- The configuration page shows how many original GPS files are kept and their total size.

**Logbook imports**
- Review page: rows whose values differ are now readable in dark mode, and identical numbers (e.g. counters) are no longer flagged as different.

**Dashboard**
- The flights and hours card now covers the past month (rolling), instead of starting over on the 1st of each month.

**Maintenance**
- Backups no longer include the temporary copies of files from imports in progress, and those older than a day are deleted daily (an abandoned import used to leave its file behind for good).
- CI runs on Ubuntu 26.04, named explicitly instead of the floating `ubuntu-latest` (which GitHub moved to 26.04).
- Fixed the code-scanning notes reported on v3.34.0 by changing the code (no suppressions).
