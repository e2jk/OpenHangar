# Faster GPS import review: an upload is analysed once

**GPS import**
- The review page analyses an upload once: confirming or skipping a segment reloads it in a fraction of a second instead of reading and matching every file again (39 Garmin logs: 5.7 s → 0.2 s), for the aircraft and the pilot GPS import.

**Maintenance**
- A GPS review test checks the page it was leaving unused (CodeQL note #638).
