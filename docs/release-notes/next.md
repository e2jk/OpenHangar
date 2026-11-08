# Safer return page after removing a GPS file

**GPS import**
- A Garmin data log fragment without its header (a minute or two the logger writes while powering up or down, never a flight) is reported as such instead of "Unsupported GPS file format".

**Maintenance**
- After removing a GPS file from a flight, the page to go back to (the pilot logbook entry or the aircraft's flight page) is decided by the server instead of taken from a URL sent with the form — CodeQL alerts #635 and #636 (open redirect). A test imports its module one way only (#637).
