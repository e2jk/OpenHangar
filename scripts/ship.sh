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
set -euo pipefail

# --prune: without it, a plain fetch never removes local remote-tracking
# refs for branches deleted on the remote (e.g. `ship` itself, deleted by
# GitHub's delete_branch_on_merge after each round lands) — leaving stale
# knowledge of origin/ship behind, which then makes --force-with-lease
# below refuse the next push as a "stale info" mismatch even though the
# remote branch genuinely doesn't exist to conflict with.
git fetch origin --prune
git rebase origin/main
git push origin HEAD:ship --force-with-lease

echo "Pushed to ship — watch it land with: gh pr status"
