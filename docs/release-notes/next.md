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

- Passport browser checks exercise personal ZIP downloads outside the signup flow,
  wait for photo uploads to finish and keep failure traces for diagnosis.

- Planning only, no code yet: the roadmap now has a Phase 42 “Explore” —
  shareable collections of places worth flying to (towns, attractions,
  restaurants) linked to nearby airfields, with **Where next?** becoming one
  search over places and passports, and *Personal* accounts. Later phases moved
  up by one number.

- Two ideas added to the backlog for later: an optional, off-by-default set of
  ULM / microlight fields, and cleaning every stored photo of its location data
  after reading what's useful from it — such as suggesting the departure or
  arrival airfield from where a counter photo was taken.

- A ship-later pull request's description now shows only the release-summary
  entries its own changes added, instead of the whole summary of the coming
  release, which presented earlier changes as its own. The descriptions of the
  56 affected pull requests since 7 November, three of their titles and the
  corresponding lines in the v3.38.0 and v3.44.0 release notes were corrected.

- Public links use shared storage and revocable, expiring tokens. Existing visits
  and photos remain private until their participants choose to publish them.

- The demo and development data now include a group passport shared by two
  pilots and a friend with a passport-only account, to try the group features.

- New automated checks for airfield passports: map marker sizes in the visit
  frequency view, separate arrivals on the same day, redirect targets in
  invitation links and friends who join a group's discussion later.

- The automatic backup check now also restores airfield passports — groups,
  friends' accounts, visits, vouchers, comments and stamp photos — and fails
  if any of them is missing after the restore.

- Documentation only: an audit of the airfield-passport roadmap ticked the
  edition, history, colour, reminder and invitation items that were already
  built and tested; the few still open now say what remains.

- Public-passport browser checks now declare their pytest markers for static
  analysis and wait for publication changes to finish before checking public
  access, avoiding a timing-dependent test failure.

- Planning only: three optional airfield-passport ideas — GPS-suggested
  intermediate stops, a runway suitability comparison in **Where next?** and a
  publisher-operated passport-only instance — moved from the roadmap to the
  backlog, so the passport phase can be completed without them.

- Prepare Places with reusable image cleaning, committed-file cleanup, capability
  tokens, waypoint documents and destination-search utilities. Passport behavior
  and existing links stay unchanged. Account ZIP exports add a versioned feature
  manifest while retaining the existing passport JSON and photo paths.

- Add the internal Places collection model and permission checks. Collection
  membership controls reading and editing independently of aircraft roles;
  destinations and visit history are retained when a member leaves. The Places
  screens are still being built.
