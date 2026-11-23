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
- The pilot logbook and an aircraft's flight log open straight on the
  flights: the filters fold away behind a **Filter** button (showing how
  many are active), and the page-size and compact-view options — and, on a
  phone, the page numbers — moved below the list.
- The **Export my passport data** button, and the GPX/KML download buttons
  on places, passports and Where next?, only appear when there is something
  to download.
- In the dark theme, the menu button at the top right is visible again, and
  a few other dark-on-dark icons and buttons (airworthiness N/A filter,
  reservation calendar weekday headers, setup-wizard icons) are easy to
  read.
