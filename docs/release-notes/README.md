# Release notes

`next.md` is the hand-written summary of the release being prepared: a few
plain-language lines on what changed for users, kept up to date while the
work happens. On shipping, it's put on top of the ship PR's description and
of the GitHub release notes, above the commit list grouped by kind
(`scripts/release_notes.py`).

It's only used when it **changed** in the batch being shipped (PR) /
since the previous version tag (release notes) — a summary left over from an
earlier release is never reused. Once a release is out, empty `next.md` to
start the next one — the published GitHub release keeps the text.
