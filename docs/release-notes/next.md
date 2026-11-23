# Photos suggest where a place is and which airfield you visited

- Choose a photo for a place and, when it was taken with a phone, OpenHangar
  offers where it was taken as the place's pin: **Use as the place's
  location**. The photo is still stored without its metadata.
- On a phone, **Record a visit** on a passport can find the airfield from a
  photo taken there: it suggests the passport airfield and the date, and the
  photo is only read, not saved.
- Deleting a flight — from the aircraft log, your pilot logbook, or with its
  aircraft — now also takes its GPS files off: they go to **Removed GPS
  files** for a week, where they can still be put back in the archive,
  instead of staying behind unused.
- The tracks animation of an aircraft or of your logbook downloads at once
  the second time: it's kept once drawn, and drawn again only when its
  flights change (or after a month, for map updates).
- An aircraft can say whether it's usually refuelled **before** or **after**
  the flight (aircraft settings): its flight form then lists that fuel line
  first, and the logbook import puts a fuel quantity with no before/after
  note on that side. Left as "It varies", nothing changes.
- Your own GPS import (My Flight Tracks) now handles a ground-only recording
  like the aircraft's import: the taxi to the fuel station before a flight,
  or back to the hangar after it, joins that flight's track without changing
  its times, and an engine run joins the ground entry you logged for it.
  One further away (up to 3 hours, same day and airfield) is offered with
  those flights to choose from.
- **Data checks** (Configuration) also lists counter gaps: a flight whose
  flight or engine counter doesn't start where the previous flight's ended
  — a typo, or flights missing from the log. Open the flight to correct it,
  or dismiss the gap when it's right (a counter replaced).
- **Data checks** also lists two flights of one aircraft logged at times
  that overlap — one of them has the wrong date or times.
- An aircraft's page says how many counter gaps its log has (for admins and
  owners), with a link to them on Data checks.
- The aircraft GPS import's review page now says when a flight it's about
  to link differs from its GPS track ("Logbook: EDBK · GPS: EDRK"), with a
  box to correct the logbook at the same time — only when ticked, never by
  **Accept all best matches**.
- A GPS file linked to the wrong flight can be moved: remove it from that
  flight, then **Import again** on **Removed GPS files** sends it back
  through the GPS import's review — no need to upload it again — where you
  choose the right flight.

**Maintenance**

- The slow-connection browser check now waits for a known cached page and
  controls when its network response arrives, avoiding a cache-warming race.
- Passport browser tests wait for htmx to finish swapping in a reviewed
  passport before pressing Save, instead of sometimes clicking the page being
  replaced.

- The photo cleaning and photo suggestion tests import each module one way,
  as static analysis asked.
- A new test catches locally three notes static analysis kept raising
  (mixed imports, a silent `except`, an `abort()` it reads as falling
  through); the 15 places it found are fixed.
- Browser tests sign in as the admin through one helper that, when another
  sign-in already used the current authenticator code, waits for the next
  one instead of failing.
- The browser tests' in-process servers are used as soon as they answer,
  instead of after a fixed pause.
- A failing browser test now leaves its Playwright trace and a screenshot
  for the tests on the shared page too, not only those opening their own
  browser.
- Browser tests now use the same disposable test data locally and in CI.
  Missing fixture IDs fail clearly, and deletion tests use dedicated synthetic
  flights.
