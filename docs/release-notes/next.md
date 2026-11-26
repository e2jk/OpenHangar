# Link places to passport airfields and review data checks

**Places**

- See nearby airfields on a map when linking them to a place, with your
  passports marked in the results. Passport airfields also show linked
  places and nearby candidates, and both sections lead to one suggestions page.
  The airfield linking page also links back to the place and its collection.

- A click anywhere on a place card in Places opens that place, not just its
  name; its airfield and tag pills still filter the list.

**Data checks**

- Dismiss all open data checks for the current hangar in one action.
- Data checks use takeoff time and continuous counter readings to place flights
  with missing departure times, avoiding false counter-gap alerts.

**Fixes**

- A container run read-only, as the reference setups do, no longer logs
  "Fontconfig error: No writable cache directories" on every PDF: the
  font cache now lives in the writable /tmp.

**Maintenance**

- Updated the Ruff linter and formatter to 0.17.5.
- The offline-login browser tests no longer fail now and then with
  "database is locked": their test server answers a page again when two
  requests collide on its SQLite file while the service worker loads
  several pages at once.
