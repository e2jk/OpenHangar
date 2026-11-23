# GPS import shows logbook differences, and moving a GPS file

- Admins can turn instant screens off for the whole instance with the new
  `OPENHANGAR_INSTANT_PAGES=false` (pages then come from the server first),
  shown on the Settings page.
- The Settings page's "Active in this browser" badge for offline support
  now also shows when the page was opened from within the app.
- The aircraft GPS import's review page now says when a flight it's about
  to link differs from its GPS track ("Logbook: EDBK · GPS: EDRK"), with a
  box to correct the logbook at the same time — only when ticked, never by
  **Accept all best matches**.
- A GPS file linked to the wrong flight can be moved: remove it from that
  flight, then **Import again** on **Removed GPS files** sends it back
  through the GPS import's review — no need to upload it again — where you
  choose the right flight.
- Your own GPS import (My Flight Tracks): a ground-only recording further
  from one of your flights than a usual taxi (up to 3 hours, same day and
  airfield) is offered with those flights to choose from, as in the
  aircraft's import.
- **Data checks** also lists two flights of one aircraft logged at times
  that overlap — one of them has the wrong date or times.
- An aircraft's page says how many counter gaps its log has (for admins and
  owners), with a link to them on Data checks.

**Maintenance**

- A failing browser test now leaves its Playwright trace and a screenshot
  for the tests on the shared page too, not only those opening their own
  browser.
