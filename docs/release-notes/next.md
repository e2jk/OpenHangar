# Places worth visiting, with collections to share

Places is a map of destinations beyond the airfield: a museum, restaurant,
viewpoint or overnight stop worth a trip. Drop a pin, paste coordinates or a
map link, add a photo and tags, and keep the map and list together as you filter
or sort. Your default collection is preselected for new places. Search for a
place name or address and choose a result to set its pin; manual coordinates
and the map stay available when address search is disabled or unavailable.

Keep collections to yourself or share each one with its own friends, choosing
who can edit. Friends invited by exact email accept from their own account.
Copy a place with an independent photo, or move it while keeping every visitor
in the destination collection. The bin gives editors 30 days to restore a
place, and leaving a group preserves its places and visit history.

Sharing a file to the installed app on Android now goes all the way: an
expense receipt, a flight photo or a maintenance file opens that form with the
file attached, and it is saved with the form (before, OpenHangar only asked
you to attach it again by hand). The share page lists only what your role can
use, and Cancel drops the shared file.

Passport-only accounts are now **Personal accounts**: the same friends'
accounts, now with Places too, and still no access to the aircraft. The user
list explains when to choose a Personal account or a **Viewer (read-only)**
for someone who doesn't fly.

**Maintenance**

- A shared aircraft document is now stored by the same code as a document
  uploaded from the aircraft's documents page, instead of a copy of it.

- Groundwork for sharing a link from a phone to create a place: finding the
  link in what apps such as Instagram share, removing its tracking
  parameters, and suggesting a title from the shared text.

- Shipping from a parallel worktree: `scripts/ship.sh --later` run in another
  agent's `git worktree` now pushes to that worktree's own
  `ship-later--<branch>` lane with its own pull request, so two lines of work
  can land side by side. Releases straight away (and security fixes) still go
  through the main worktree.

- Address and place-name queries go through the server to OpenStreetMap's
  Nominatim — or another
  compatible server set with the new `OPENHANGAR_GEOCODER_URL`, empty to turn
  it off — at most one request per second for the instance, with a cache, and
  without ever logging what was searched.

- Places and passports share image cleaning, committed-file cleanup, capability
  tokens, waypoint documents and destination calculations. Account ZIP exports
  add a versioned feature manifest while retaining the existing passport JSON
  and photo paths.

- Static analysis now recognises the Places route-registration and pytest
  exports, explicit denied-request returns and expected validation fallbacks.
  The coordinate API returns a fixed translated validation message.
