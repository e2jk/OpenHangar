# Maintenance commands no longer start background jobs

- One-off `flask` commands (`backup-now`, `restore-backup`, `shell`, …) no longer start the web server's background jobs (backup schedule, update check, notifications, sync watcher). A restore no longer runs next to them; it previously did even inside the web container. The web server itself, including `flask run` in development, is unchanged.

**Maintenance**
- Role-matrix test: a helper user that failed to sign in now fails at setup with the reason, and a wrong status shows where it redirected, so the rare unexplained failure seen once can be diagnosed if it recurs.
- Fuzzing: removed the two harnesses for the offline sync code deleted with offline editing (they failed the nightly fuzz run), and the test run now checks that every harness loads and that the fuzz workflow runs each one.
- Signed-in security scan: its attack phase is now time-limited so it always produces a report (the first run found hundreds of signed-in pages and hit the 2-hour job limit), and it now also scans the pilot profile page, which a too-broad exclusion had hidden.
