# Release notes

`next.md` is the hand-written summary of the release being prepared: a few
plain-language lines on what changed for users, kept up to date while the
work happens. On shipping, it's put on top of the ship PR's description and
of the GitHub release notes, above the commit list grouped by kind
(`scripts/release_notes.py`).

It's only used when it **changed** in the batch being shipped (PR) /
since the previous version tag (release notes) — a summary left over from an
earlier release is never reused. At the start of the next round of work,
copy it to `vX.Y.Z.md` (the version it went out in) and empty `next.md`.
