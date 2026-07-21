#!/usr/bin/env bash
# scripts/ship.sh — land your local work on main.
#
# Rebases the current branch onto the latest origin/main — any commits from
# a previous round that already landed there are dropped automatically (git
# recognizes their patch is already applied and skips them; this is the same
# mechanism `git pull --rebase` relies on) — then pushes to the `ship`
# branch. That push triggers .github/workflows/auto-pr-merge.yml, which
# opens (or reuses) a PR to main and enables auto-merge: it lands on its own
# once CI is green, no waiting around and no separate manual sync needed
# before your next round of commits — just run this script again.
# Usage: scripts/ship.sh [--no-verify]
#   --no-verify   skip the pre-push hook (ruff/mypy/bandit/zizmor/
#                 actionlint/pip-audit/migrations/translations) — same
#                 flag, same meaning as `git push --no-verify`, just
#                 passed straight through.
set -euo pipefail

PUSH_ARGS=(origin HEAD:ship --force-with-lease)
for arg in "$@"; do
  case "$arg" in
    --no-verify) PUSH_ARGS+=(--no-verify); echo "Skipping pre-push checks (--no-verify)." ;;
    *) echo "Unknown argument: $arg (only --no-verify is supported)" >&2; exit 1 ;;
  esac
done

# --prune: without it, a plain fetch never removes local remote-tracking
# refs for branches deleted on the remote (e.g. `ship` itself, deleted by
# GitHub's delete_branch_on_merge after each round lands) — leaving stale
# knowledge of origin/ship behind, which then makes --force-with-lease
# below refuse the next push as a "stale info" mismatch even though the
# remote branch genuinely doesn't exist to conflict with.
git fetch origin --prune
git rebase origin/main

# After the rebase, no local commits ahead of origin/main means there's
# nothing of yours left to land — every commit you had was already merged
# upstream (that's what the rebase above just dropped). Pushing anyway
# would force-push a `ship` ref identical to main, which
# auto-pr-merge.yml's PR creation then rejects with "No commits between
# main and ship" — a confusing failure for a no-op.
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "Nothing to ship — HEAD is already even with origin/main. Not pushing."
  exit 0
fi

git push "${PUSH_ARGS[@]}"

echo "Pushed to ship — watch it land with: gh pr status"
