# Correct maintenance and billing, faster pages, easier Places

**Maintenance and logbook**

- Editing a maintenance item due at a date *or* an hour count (an AMP
  "100FH / 12MO", whichever comes first) no longer drops its second limit.
- An hours limit is no longer left unchecked on an aircraft without a
  flight counter: it uses the engine counter instead, so the item comes due
  early rather than never.
- Month and year intervals never run past the calendar deadline ("1MO"
  from 31 January is due 28 February). Due dates computed from a last
  service are corrected (only ever earlier), with a note on each moved item.
- The AMP import no longer sets due dates as if every task had just been
  done: imported items read **Needs review** until their last service is
  recorded or a due date/hours is entered, which now clears the flag.
- Logbook totals now include the Dual, Co-pilot and Instructor hours of
  your own entries, imported instructor rows and the opening balance.

- A logbook import no longer overwrites a flight another pilot is linked to,
  and joining someone's flight from a GPS import keeps their hours.
- An opening balance of 1000 h or more can be imported.
- Editing a confirmed booking can no longer move it onto another confirmed
  booking or a maintenance downtime, and a booking can't be checked out
  before its day.
- The dashboard's passenger and night currency badges say they count any
  aircraft (the per-class recency below them is per class, as FCL.060).

**Co-owner billing**

- A fixed cost is split once, among the owners of the day: a share sold
  later no longer re-bills earlier costs to the new owner. Costs that
  earlier versions billed above 100% after an ownership change are corrected
  with visible entries.
- Each flight keeps the hourly rate it was billed at when the rate changes.
- A recurring expense's first occurrence is now billed, as the cost
  dashboard already counted it.

**Places and Where next?**

- Adding a place starts with one search box (addresses, coordinates or map
  links) and the name, with optional details below the map; editing the
  latitude or longitude moves the pin, and the map opens less zoomed in.
- Tags go on one comma-separated line, with your collections' tags
  suggested; collection settings show how many places use each tag and ask
  before removing one in use.
- Link an airfield while adding a place, move several places to another
  collection at once, and add another place straight from a place page.
- Place cards show linked airfield codes and filter by tag when you click
  one; large screens show two cards per row. Place pages have a
  "Collections > collection" breadcrumb.
- Where next? shows suggestions as soon as it opens, from a home airfield
  or the latest arrival, and defaults to flight time at the aircraft's
  cruise speed (or 100 kt). Its count of places without an airfield links
  to that filtered list.
- Fixes: the new-place form no longer refills the previous place's name,
  address matches stay visible, and spacing and the page title match the
  rest of the app.

**Security and privacy**

- Fuel costs (on the fuel log and the aircraft page) are shown to owners and
  admins only, like the other expenses.
- Each pilot's or aircraft's GPS file archive is limited to 1 GB.
- Document title suggestions only cover aircraft and documents you may see,
  and a maintenance page without a maintenance read permission shows nothing.
- A pilot can no longer delete another pilot's flight or change their mass &
  balance calculations (owners and admins still can); the GPS import only
  lists the aircraft you may fly.
- An aircraft link resolves to your own hangar's aircraft when another hangar
  uses the same registration.
- Admin pages no longer show raw backup or e-mail server errors (they go to
  the server log); airworthiness document links must be web links; flight
  places in map tooltips are shown as text; download file names are quoted.

**Faster pages**

- The maintenance pages, the aircraft page, the track maps and the dashboard
  load maintenance records and GPS tracks in one go instead of one query per
  item, and several often-filtered links are indexed.
- "Download all documents" no longer holds the ZIP in memory, and gives each
  document its own entry even when two share a name.
- "Show all" on the logbooks shows at most 2,000 rows (the export has them
  all) and is not remembered for the next visit.

**Airfield passports**

- Passport cards show interactive maps and visit percentages; each series
  has a named card linking its other editions.

**Maintenance**

- Two browser tests that read a page before it had settled now wait for it.
