# Safer return page after removing a GPS file

**GPS import**
- Uploading a GPS file on a flight that's already saved adds it to the flight's track (as a taxi part when it's only ground movement) instead of replacing the track, and no longer changes the flight's own data: date, route and times stay as entered. A note shows the GPS times, to correct the flight by hand if needed.
- **Accept all best matches** is also at the bottom of the review page, after the segments, so there's no need to scroll back up after checking them.
- A Garmin data log fragment without its header (a minute or two the logger writes while powering up or down, never a flight) is reported as such instead of "Unsupported GPS file format".

**Maintenance**
- After removing a GPS file from a flight, the page to go back to (the pilot logbook entry or the aircraft's flight page) is decided by the server instead of taken from a URL sent with the form — CodeQL alerts #635 and #636 (open redirect). A test imports its module one way only (#637).
