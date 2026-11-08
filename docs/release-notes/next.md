**GPS import**
- SkyDemon's GPX and KML exports import as they come — its `.gpx.bin` files no longer need renaming: a file's GPX or KML content is recognised whatever its extension. (SkyDemon's own `.flightlog.bin` can't be read; the import says so and suggests exporting GPX or KML.)
- KML tracks get the same treatment as GPX files without speed: speed worked out over a few seconds and bad fixes left out — a SkyDemon KML used to show a dozen landings and a split flight.
- A recording that starts during the takeoff roll (as SkyDemon's often do) or stops right after the landing roll now names the airfield: it only counts as recorded from the air when it also starts high above the field.
