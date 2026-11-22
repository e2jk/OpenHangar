# Photos suggest where a place is and which airfield you visited

- Choose a photo for a place and, when it was taken with a phone, OpenHangar
  offers where it was taken as the place's pin: **Use as the place's
  location**. The photo is still stored without its metadata.
- On a phone, **Record a visit** on a passport can find the airfield from a
  photo taken there: it suggests the passport airfield and the date, and the
  photo is only read, not saved.

**Maintenance**

- The slow-connection browser check now waits for a known cached page and
  controls when its network response arrives, avoiding a cache-warming race.
- Passport browser tests wait for htmx to finish swapping in a reviewed
  passport before pressing Save, instead of sometimes clicking the page being
  replaced.

- The photo cleaning and photo suggestion tests import each module one way,
  as static analysis asked.
