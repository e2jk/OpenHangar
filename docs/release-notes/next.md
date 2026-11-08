# GPS files on saved flights, and taxi runs further from a flight

**GPS import**
- The review page analyses an upload once: confirming or skipping a segment reloads it in a fraction of a second instead of reading and matching every file again (39 Garmin logs: 5.7 s → 0.2 s), for the aircraft and the pilot GPS import.
- A ground-only recording more than 45 minutes from any flight, but at the same airfield within 3 hours that day (a taxi back to the hangar after a long wait at the fuel station), now offers those flights to add it to as a taxi part, the closest preselected — instead of only importing it as a ground entry of its own or skipping it.
- Uploading a GPS file on a flight that's already saved adds it to the flight's track (as a taxi part when it's only ground movement) instead of replacing the track, and no longer changes the flight's own data: date, route and times stay as entered. A note shows the GPS times, to correct the flight by hand if needed.
- **Accept all best matches** is also at the bottom of the review page, after the segments, so there's no need to scroll back up after checking them.
- A Garmin data log fragment without its header (a minute or two the logger writes while powering up or down, never a flight) is reported as such instead of "Unsupported GPS file format".

**Maintenance**
- After removing a GPS file from a flight, the page to go back to (the pilot logbook entry or the aircraft's flight page) is decided by the server instead of taken from a URL sent with the form — CodeQL alerts #635 and #636 (open redirect). A test imports its module one way only (#637).
