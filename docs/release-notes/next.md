# Parallel shipping lanes and groundwork for address search

**Maintenance**

- Shipping from a parallel worktree: `scripts/ship.sh --later` run in another
  agent's `git worktree` now pushes to that worktree's own
  `ship-later--<branch>` lane with its own pull request, so two lines of work
  can land side by side. Releases straight away (and security fixes) still go
  through the main worktree.

- Groundwork for searching addresses and place names (coming with Places): a
  server-side search through OpenStreetMap's Nominatim — or another
  compatible server set with the new `OPENHANGAR_GEOCODER_URL`, empty to turn
  it off — at most one request per second for the instance, with a cache, and
  without ever logging what was searched.
