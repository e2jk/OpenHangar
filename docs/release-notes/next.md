**Fuel**
- The aircraft page shows the fuel on board: what was left after the latest flight (per tank when known) plus fuel added since, with a warning when later flights recorded no fuel remaining.

**Logbook imports**
- Review page: rows whose values differ are now readable in dark mode, and identical numbers (e.g. counters) are no longer flagged as different.

**Dashboard**
- The flights and hours card now covers the past month (rolling), instead of starting over on the 1st of each month.

**Maintenance**
- CI runs on Ubuntu 26.04, named explicitly instead of the floating `ubuntu-latest` (which GitHub moved to 26.04).
- Fixed the code-scanning notes reported on v3.34.0 by changing the code (no suppressions).
