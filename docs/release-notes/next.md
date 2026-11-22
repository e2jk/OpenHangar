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

**Maintenance**

- The slow-connection browser check now waits for a known cached page and
  controls when its network response arrives, avoiding a cache-warming race.
- Passport browser tests wait for htmx to finish swapping in a reviewed
  passport before pressing Save, instead of sometimes clicking the page being
  replaced.

- The photo cleaning and photo suggestion tests import each module one way,
  as static analysis asked.
- Browser tests sign in as the admin through one helper that, when another
  sign-in already used the current authenticator code, waits for the next
  one instead of failing.
- Browser tests now use the same disposable test data locally and in CI.
  Missing fixture IDs fail clearly, and deletion tests use dedicated synthetic
  flights.
