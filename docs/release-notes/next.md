# Tracks played back in flight order, and a faster GPS import review

**Logbooks**
- An aircraft's flight log and your pilot logbook can be filtered: with or without a GPS track, by date range, and by crew (your own flights on an aircraft's log; as PIC or as second crew in your logbook) — e.g. the flights since you bought the aircraft that have no GPS track yet. Simulator sessions never count as missing a track. Filters stay applied across pages and when coming back from a flight.

**Maps**
- The tracks animation, the tracks GIF and the maps of all flights put same-day flights in the order they were flown (by GPS start time, else logged departure time): a return flight was sometimes drawn before the outbound one.

**GPS import**
- Uploading many files at once in your own logbook's GPS import (dozens of camera tracks) no longer ends in "Session expired — please upload your GPS files again": the import's file list outgrew the browser's session cookie, which the browser then dropped. It's now kept on the server, as the aircraft GPS import already did.
- The review page analyses an upload once: confirming or skipping a segment reloads it in a fraction of a second instead of reading and matching every file again (39 Garmin logs: 5.7 s → 0.2 s), for the aircraft and the pilot GPS import.

**Maintenance**
- A GPS review test checks the page it was leaving unused (CodeQL note #638).
