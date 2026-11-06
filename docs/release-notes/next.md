**Logbook imports**
- Aircraft logbook import brings in fuel: litres added (before or after the flight), and fuel left — as a fraction of the tanks or in litres per tank (e.g. `9/13`). Rows already in the log get their missing fuel filled in.
- A flight cancelled before takeoff is matched across the pilot and aircraft logbooks instead of being logged twice.
- Review page: decide all remaining rows at once, the best match is pre-selected, no more jump back to the top after each row, and about twice as fast.
- `[empty]` cells are read as blank in the pilot logbook import too; unreadable cells are now reported by the aircraft logbook import; "Incidents/Obs" columns map to notes automatically; the mapping preview shows dates, times and durations as in the spreadsheet.

**Fuel and costs**
- New fuel log per aircraft: every refuel with its cost, and fuel added, fuel cost, average price per litre, litres per engine hour and fuel cost per engine hour / flight hour / flight over a chosen period.
- Fuel remaining can be recorded per tank on aircraft with several tanks.
- An expense can also note the amount paid in another currency (for reference only, e.g. fuel paid in GBP abroad).

**Other**
- The pilot logbook's back button returns to the page you were on.
- The navigation bar stays readable on narrow windows (e.g. a half-screen tiled browser).
