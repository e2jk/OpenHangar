# Safer one-click upgrades, and Passports in the Explore menu

- The place editor shows more of the surrounding area when it centers on a pin.
- The new-place form asks the browser to leave the place name empty.
- Adding a place starts with search and name, with optional details below the map.
- Where next? shows suggestions on opening from a home airfield or the latest logged arrival.
- Where next? defaults to flight time using the selected aircraft’s cruise speed or 100 kt.
- Airfield passports now show interactive maps and visit percentages in cards.
  Linked editions share one card with direct links to the other editions.
  The account export is at the bottom of the page.
- Add another place in the same collection directly from a place page.
- Places now shows two cards per row on large screens.
- The Places collection buttons now have consistent spacing.
- The Where next? count of places without airfields now links to the filtered Places list.
- Where next? now uses the same title style as other pages.
- **Action needed for one-click upgrades:** the server's cron job must now
  run the new `openhangar-launcher.sh`, installed once next to
  `docker-compose.yml` (see "One-click upgrades" in the self-hosting
  guide). The launcher only runs the upgrade, restore and demo refresh
  scripts out of the signed image it has just verified, never a copy from a
  folder the container can write to, which could have let a compromised
  container take over the server. The container no longer publishes those
  scripts, and the Settings page reminds you until the new cron line runs.
  Restoring a backup is now `openhangar-launcher.sh restore <archive>`.
- The top bar's Explore menu now lists Where next?, Places and Airfield
  passports together, in that order.

**Maintenance**

- A browser test that read a page before htmx had swapped it in now waits
  for the expected text.
