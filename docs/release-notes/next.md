# Places to fly to, shared with friends, and Where next?

**Security:** uploaded photos are now cleaned of their metadata. A photo
taken with a phone carries the GPS position where it was taken, and aircraft
photos were stored with it — and shown on public showcase links. Aircraft,
flight counter and fuel photos, and photos shared from a phone, are now
re-encoded without any metadata, like passport and place photos already were.
New photos must be JPEG, PNG or WebP (HEIC and GIF can't be cleaned; iPhones
convert to JPEG when uploading). **Administrators:** clean the photos stored
before this version once with `docker compose exec web flask clean-photos
--apply` (without `--apply` it only lists them).

**Places** is a new map of destinations worth a flight — a town, a museum, a
restaurant, a viewpoint or a place to stay — in the new **Explore** menu.
Every account has it, including friends with a Personal account.

**Collecting places**

- Add a place by clicking the map, pasting coordinates or a map link, or
  searching a name or address. Add a description, a photo, an address, a
  website and tags (a starter set is offered).
- In a hurry? Save just a link, with or without a name, and add the location
  later. The **Location still to add** filter finds these ideas; only located
  places appear on the map.
- Filter and sort the list and map together: by collection, tag, visited or
  not, archived, location or airfield.

**Sharing with friends**

- Keep places in your own **My places**, or create collections to share, each
  with its own members. Members are editors or readers; invite friends by
  their exact email address, and they accept from their own account.
- Copy a place to another collection, or move it there when everyone who
  visited it is a member. Deleted places stay in the bin for 30 days, and
  leaving a collection keeps its places and visits.

**Getting there from the airfield**

- Link a place to the airfields you fly to: OpenHangar suggests open airfields
  within 15 km, nearest first, or you search by name or code, or add a field
  missing from the catalogue. Note how to get there (walk, taxi, bus…) and mark
  your preferred airfield; the map draws the connections.

**Visits**

- On a place's page, **I was here** records your visit — today, or another
  date with a short note — and the page shows who visited when ("Last visited
  3 May 2026 by Anna and you"). You can correct or remove your own visits.
- After a flight that landed at an airfield linked to one of your places, your
  logbook offers "You landed at EBXX, 4 km from Pairi Daiza — record a visit?":
  one click, or **No, thanks**. The second pilot of the flight gets the same
  offer, and nothing is ever recorded by itself.

**Where next?**

- Where next? now has its own page in the **Explore** menu. It searches all
  your active airfield passports — or the ones you tick — together with the
  airfields near your places, within a distance, flight time or theoretical
  range of your starting airfield.
- Each airfield appears once with all its reasons: "Not yet stamped in
  *Belgian passport 2026*", "4.0 km from *Pairi Daiza* (taxi)". Filter places by
  tag, or keep only airfields you have never landed at; closed airfields never
  appear. The map shows the places and the airfields that serve them.
- Opened from a passport it searches that passport, and from a place that
  place. Older Where next? links and bookmarks keep working.

**Downloads and your data**

- Download the places of the list, as filtered, as GPX or KML waypoints, and
  Where next? results as before.
- Your personal data export now also holds the places you own, with their
  photos, tags and airfield links, and your own visits.
- The demo has a shared **Our places** collection to try it all.

**Sharing to OpenHangar from your phone**

- With OpenHangar installed as an app on Android, share a **link** — an
  Instagram reel, a video, a web page — and it opens a new place with the link
  and a suggested name, ready to save now and locate later.
- Share a **file** and it now goes all the way: an expense receipt, a flight
  photo or a maintenance paper opens that form with the file attached and is
  saved with it (before, you had to attach it again by hand). The share page
  only offers what your role can use, and Cancel drops the file. (iPhone
  browsers can't receive shares: copy the link or upload the file instead.)

**Accounts**

- Passport-only accounts are now **Personal accounts**: the same friends'
  accounts, now with Places and Where next? too, and still no access to the
  aircraft. The Viewer role is shown as **Viewer (read-only)**, and the user
  list explains which to choose for someone who doesn't fly.

**Flight logging**

- Attach a counter photo and OpenHangar suggests when it was taken (in UTC)
  and the airfield it was taken at, as the arrival — or, for a pre-flight
  photo, the departure: **Use as arrival time**, **Use as arrival airfield**.
  Nothing is filled in by itself, and the photo is still stored without its
  metadata.

**Maintenance**

- Address search runs through the OpenHangar server, using OpenStreetMap's
  Nominatim by default — or another compatible server set with the new
  `OPENHANGAR_GEOCODER_URL`, empty to turn it off — at most one request per
  second for the instance, with a cache, and never logging what was searched.
- Places and passports now share their image cleaning, file cleanup, sharing
  tokens, waypoint files and distance calculations. The personal data export
  gains a versioned list of its sections, keeping the existing passport files.
- A shared aircraft document is stored by the same code as an upload from the
  aircraft's documents page.
- The backup round-trip check restores a shared Places collection with a
  photo, a tag, visits, an airfield link and a link-only place.
- The user guide illustrates Places, airfield links, visits and Where next?;
  the screenshot tool finds places by name.
- Static analysis recognises the Places routes and the expected validation
  fallbacks.
- Shipping: `scripts/ship.sh --later` run in a parallel `git worktree` pushes
  to that worktree's own lane and pull request, so two lines of work can land
  side by side. It also runs the browser tests before every push (2 workers,
  about 7 minutes, a failing test retried once): every pull request that failed at
  first in the past week had failed in them.
