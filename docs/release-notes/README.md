# Release notes

`next.md` is the hand-written summary of the release being prepared: a few
plain-language lines on everything it contains — what changed for users
first, then internal changes under a short **Maintenance** heading — kept up
to date with each commit. On shipping, it's put on top of the ship PR's description and
of the GitHub release notes, above the commit list grouped by kind
(`scripts/release_notes.py`).

It's only used when it **changed** in the batch being shipped (PR) /
since the previous version tag (release notes) — a summary left over from an
earlier release is never reused. Once a release is out, empty `next.md` to
start the next one — the published GitHub release keeps the text.

## Shipping without an immediate release

`scripts/ship.sh --later` merges the local work without publishing a release
(see `docs/development.md`); the next daily release publishes it. Write its
lines in `next.md` as usual: the daily release takes `next.md` as its summary
when it changed since the latest release, exactly like a ship's release does,
and the `ship-later` PR's description shows it too.

## Commits made on GitHub

Changes can also land on `main` straight from GitHub — mostly dependency
updates merged there — between two releases. They're part of the next
release along with the local work: its commit list includes them on its own
(routine dependency updates folded into one line), but `next.md` only says
what was written into it. So, before finalising `next.md`, run
`scripts/ship.sh --upstream` (fetches, prints, pushes nothing) and add a line
for any that's worth mentioning. `scripts/ship.sh` itself lists them again
before pushing and, if any isn't a dependency update, asks to confirm
they're covered.
