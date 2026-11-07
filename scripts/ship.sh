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
#
# This is for the repo owner's own solo commits only — external
# contributors should keep using the normal GitHub fork-and-PR flow
# straight into main (see CONTRIBUTING.md), which this script has no part
# in and doesn't affect.
#
# Usage: scripts/ship.sh [--no-verify | --upstream]
#   --no-verify   skip the pre-push hook (ruff/mypy/bandit/zizmor/
#                 actionlint/pip-audit/migrations/translations) — same
#                 flag, same meaning as `git push --no-verify`, just
#                 passed straight through.
#   --upstream    only list the commits made on GitHub (e.g. dependency
#                 updates merged there) that aren't in a release yet and
#                 not here either — they'll be in the next release with
#                 ours, so docs/release-notes/next.md should mention any
#                 worth it. Fetches, prints, pushes nothing.
set -euo pipefail

PUSH_ARGS=(origin HEAD:ship --force-with-lease)
UPSTREAM_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-verify) PUSH_ARGS+=(--no-verify); echo "Skipping pre-push checks (--no-verify)." ;;
    --upstream) UPSTREAM_ONLY=1 ;;
    *) echo "Unknown argument: $arg (only --no-verify and --upstream are supported)" >&2; exit 1 ;;
  esac
done

# Commits on origin/main that we don't have and no release contains yet —
# made on GitHub since the last release (Renovate dependency merges, a
# web-UI edit). Release tags sit on PR merge commits, not on main itself,
# so "not in a release" is "not reachable from the latest tag".
upstream_unreleased() {
  local latest_tag
  latest_tag="$(git tag --list 'v[0-9]*' --sort=-version:refname | head -n 1)"
  git log --reverse --format='%h %s' HEAD..origin/main ${latest_tag:+"^${latest_tag}"}
}

# The ones release_notes.py doesn't already fold into its "N dependency
# updates" line — those are the ones the hand-written summary must cover.
not_dependency_updates() {
  grep -Ev '^[0-9a-f]+ (chore|fix|build)\(deps\)' || true
}

# --prune: without it, a plain fetch never removes local remote-tracking
# refs for branches deleted on the remote (e.g. `ship` itself, deleted by
# GitHub's delete_branch_on_merge after each round lands) — leaving stale
# knowledge of origin/ship behind, which then makes --force-with-lease
# below refuse the next push as a "stale info" mismatch even though the
# remote branch genuinely doesn't exist to conflict with.
git fetch origin --prune

UPSTREAM="$(upstream_unreleased)"
if [ "${UPSTREAM_ONLY}" = 1 ]; then
  if [ -z "${UPSTREAM}" ]; then
    echo "No unreleased commits on origin/main that aren't here."
  else
    echo "Unreleased commits on origin/main, not here — they'll be in the next release:"
    printf '%s\n' "${UPSTREAM}" | sed 's/^/  /'
  fi
  exit 0
fi

# ship is the actual push target, so check it first: if it already exists
# and already points at our current HEAD, we already pushed this exact
# commit — most likely a previous ship push is still sitting in an open,
# not-yet-merged PR, and rerunning the script right now has nothing new to
# add. Checked before rebasing (and against pre-rebase HEAD) since nothing
# about that conclusion depends on rebasing onto origin/main first.
SHIP_SHA="$(git rev-parse origin/ship 2>/dev/null || echo "")"
if [ -n "${SHIP_SHA}" ] && [ "${SHIP_SHA}" = "$(git rev-parse HEAD)" ]; then
  echo "Nothing to ship — origin/ship already has this exact commit (still pending merge?). Not pushing."
  exit 0
fi

# Commits made on GitHub since the last release ship with ours: the
# release's commit list includes them, but the hand-written summary
# (docs/release-notes/next.md) only says what someone wrote into it.
# Routine dependency updates are listed for information; anything else
# needs a line there, so stop and ask (when someone's there to answer).
if [ -n "${UPSTREAM}" ]; then
  echo "Also in this release, made on GitHub since the last one:"
  printf '%s\n' "${UPSTREAM}" | sed 's/^/  /'
  OTHER="$(printf '%s\n' "${UPSTREAM}" | not_dependency_updates)"
  if [ -n "${OTHER}" ] && [ -t 0 ]; then
    read -r -p "Are the non-dependency ones above mentioned in docs/release-notes/next.md? Ship anyway? [y/N] " answer
    case "${answer}" in
      [yY]*) ;;
      *) echo "Not pushing — add them to docs/release-notes/next.md, commit, and run this again."; exit 1 ;;
    esac
  fi
fi

git rebase origin/main

# ship doesn't already have our content — but after rebasing, landing
# exactly on origin/main means there's nothing of ours left to land either:
# every commit we had was already merged upstream (that's what the rebase
# above just dropped). Pushing anyway would force-push a `ship` ref
# identical to main, which auto-pr-merge.yml's PR creation then rejects
# with "No commits between main and ship" — a confusing failure for a
# no-op.
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
  echo "Nothing to ship — HEAD is already even with origin/main. Not pushing."
  exit 0
fi

git push "${PUSH_ARGS[@]}"

echo "Pushed to ship — watch it land with: gh pr status"
