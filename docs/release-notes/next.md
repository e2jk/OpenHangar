# Instant bottom-bar screens, tidier logbook, dark-theme fixes

- **All five bottom-bar screens are now instant** (Dashboard, Aircraft, Log
  a flight, Pilot, Airfield passports): they're saved on the device right
  after you sign in, before any other page, and so are each aircraft's page
  and the Places and Where next? pages. Blank forms to enter something new
  (a flight, a pilot logbook entry, an expense, a refuel, a snag) open
  instantly too, always with today's date; forms editing existing data still
  come from the server, so what they show is current.
- A confirmation message ("Flight saved") no longer shows up again when a
  page later opens from its saved copy, and a server error on one page no
  longer signs the app out of its saved pages ("Your account session
  changed").
- Pages fetched in advance no longer ask the server each time a screen
  changes when their saved copy is still fresh: less mobile data and server
  load.
