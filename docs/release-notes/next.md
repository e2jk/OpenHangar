# Simulator sessions keep their data and stay out of flight totals

**Pilot logbook**
- Marking a logbook entry as an FSTD/simulator session no longer wipes its aircraft, route and start/end times: they are kept as the simulator device (make and model, plus an optional identifier such as the school's "registration"), the simulated route and the session times. Only landings and single/multi-pilot flight time are dropped, as they can't apply to a simulator. Switching a session back to a flight keeps its times too.
- An FSTD session left without a duration gets it from its start and end times. The device qualification (FFS, FNPT…) can be left empty for a simulator that isn't qualified.
- Simulator sessions now count only towards the Sim total: their PIC, co-pilot, dual, instructor, night and instrument hours are no longer added to those logbook totals. They also no longer count as your last flight (or last instructor flight) for personal minimums' recency reminders.
