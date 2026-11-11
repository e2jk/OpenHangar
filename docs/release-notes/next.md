# Simulator sessions keep their data and stay out of flight totals

**Pilot logbook**
- Marking a logbook entry as an FSTD/simulator session no longer wipes its aircraft, route and start/end times: they are kept as the simulator device (make and model, plus an optional identifier such as the school's "registration"), the simulated route and the session times. Only landings and single/multi-pilot flight time are dropped, as they can't apply to a simulator. Switching a session back to a flight keeps its times too.
- An FSTD session left without a duration gets it from its start and end times. The device qualification (FFS, FNPT…) can be left empty for a simulator that isn't qualified.
- Simulator sessions now count only towards the Sim total: their PIC, co-pilot, dual, instructor, night and instrument hours are no longer added to those logbook totals. They also no longer count as your last flight (or last instructor flight) for personal minimums' recency reminders.

**Maintenance**
- `scripts/ship.sh` stops before pushing when `docs/release-notes/next.md` still holds notes that a recent release already published, so they aren't announced twice.
- CI rides out network outages on GitHub's runners: the browser-test jobs no longer fail when Ubuntu's package mirrors are unreachable (as long as Chromium launches with what the runner already has), jobs that fail only while setting up are re-run once automatically a few minutes later, and `pip-audit` waits longer for pypi.org — locally too, where the pre-push hook retries it once on a network error.
