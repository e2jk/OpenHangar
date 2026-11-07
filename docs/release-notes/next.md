# GPX tracks from cameras and phones, and sturdier GPS matching

**GPS import**
- GPX files from cameras and phones import properly — e.g. a GoPro's GPS exported with ExifTool: GPX 1.0 or no namespace at all (all points used to be ignored), no speed values (speed is now worked out from the positions; all files used to read as "no movement"), and files missing their closing tag (rejected before).
- Bad GPS fixes recorded while the GPS starts up are left out: no fix at all (0°, 0°, with a wrong date), or a stale last-known position the track then jumps away from. They used to show up as short phantom flights.
- A noisy GPS no longer invents flights or landings: flying means at least 30 seconds above 30 kt, and a landing at least 30 seconds below it after flying.
- A recording that starts or ends in flight (the camera switched on or off in the air) no longer names the airfield it happened to be over; a recording covering only part of a flight is matched by its logged times; and pieces of one flight split by a recording stop are all linked to it.
- A dual flight and the solo flown straight after it, recorded back to back (even overlapping by a few minutes), stay two flights, each with its own track.
- Of several ground runs on one day, the longest becomes a logged ground entry's track (e.g. a cancelled flight's aborted takeoff run), the others taxi parts of it; ground runs recorded hours apart in one upload stay separate.
- Heliports are no longer taken for the airfield a flight used (a landing on Ostend's runway was named after the nearby Oostende Heliport).

**Maintenance**
- A GPS import test now checks what the review page shows instead of discarding it (CodeQL note on the previous release).
- `scripts/ship.sh --later` merges local changes without a release of their own: the next daily release (23:20 UTC) publishes them, like routine dependency updates.
- `scripts/ship.sh` no longer lists our own just-merged commits as "made on GitHub" (it now compares changes by content, as a rebase merge gives them new commit IDs).
- Ship PRs and releases are named after this summary's first line (`# <title>`, at most 72 characters) when there is one, instead of a count of commits by kind; the count moves into the PR's description.
- PR titles start with "[later]" for a `ship-later` push.
