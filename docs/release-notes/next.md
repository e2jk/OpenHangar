# Tracks played back in flight order, and a faster GPS import review

**Maps**
- The tracks animation, the tracks GIF and the maps of all flights put same-day flights in the order they were flown (by GPS start time, else logged departure time): a return flight was sometimes drawn before the outbound one.

**GPS import**
- The review page analyses an upload once: confirming or skipping a segment reloads it in a fraction of a second instead of reading and matching every file again (39 Garmin logs: 5.7 s → 0.2 s), for the aircraft and the pilot GPS import.

**Maintenance**
- A GPS review test checks the page it was leaving unused (CodeQL note #638).
