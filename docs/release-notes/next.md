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

Paste a link and save it for later, even without a name or location. The new-place
form accepts shared titles and links, and a “Location still to add” filter helps
you find ideas to finish together. Only located places appear on the map.
A missing name is shown as “Unnamed place”, separately from a missing location.

Found a place to fly to in an Instagram reel or a video? Share it to the
installed app on Android: OpenHangar opens a new place with the link and a
suggested name, ready to save now and locate later — Personal accounts too.

**Where next?** has its own page, in the new **Explore** menu next to Places:
search all your active airfield passports at once, or tick the ones you want,
and the airfields near your places. Each airfield appears once, with all its
reasons: the passports where it is still to stamp and the places it serves. Filter places by tag, or keep only
airfields you have never landed at; the map links each place to its airfields.
A passport's **Where next?** link and older bookmarks open it with that passport
selected.

Places can be downloaded as GPX or KML waypoints, and your personal data export
now holds the places you own, their photos, tags and airfield links, and your own visits. The
demo has a shared "Our places" collection to try it.

Places now helps with the part between landing and your destination. Choose a
nearby airfield, search by name or code, or add one missing from the catalogue.
Save how to get there, mark your preferred airfields, and see the connections on
the map. A filter finds places still needing an airfield.

Each place also shows the collection's visit history. Record a visit with one
click, choose another date or add a short note, and correct your own entries
later. Download the filtered Places list as GPX or KML, or open Where next? to
plan a trip.

Visits: on a place's page, **I was here** records your visit, and the page
shows who visited when. After a flight that landed at an airfield linked to one
of your places, your logbook offers to record the visit in one click.

**Maintenance**

- Two members choosing the same airfield at the same time now keep a single
  link, preserving its saved directions instead of failing with a database error.



- The backup round-trip check now also restores a shared Places collection,
  with a photo, a tag, visits and a link-only place.

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
