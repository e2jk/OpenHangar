**Maintenance**
- A GPS import test now checks what the review page shows instead of discarding it (CodeQL note on the previous release).
- `scripts/ship.sh --later` merges local changes without a release of their own: the next daily release (23:20 UTC) publishes them, like routine dependency updates.
- `scripts/ship.sh` no longer lists our own just-merged commits as "made on GitHub" (it now compares changes by content, as a rebase merge gives them new commit IDs).
