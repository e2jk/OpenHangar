# Correct maintenance due dates, fair co-owner billing, easier Places

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

**Airfield passports**

- Passport cards show interactive maps and visit percentages; each series
  has a named card linking its other editions.

**Maintenance**

- Two browser tests that read a page before it had settled now wait for it.
