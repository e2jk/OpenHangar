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
# With --later it pushes to `ship-later` instead: same PR, same CI, same
# auto-merge, but merging it doesn't publish a release — the changes go out
# with the next daily release (.github/workflows/daily-release.yml, 23:20
# UTC), the way routine Renovate updates do, or with the next plain ship
# before that. For small changes that needn't be released straight away.
#
# This is for the repo owner's own solo commits only — external
# contributors should keep using the normal GitHub fork-and-PR flow
# straight into main (see CONTRIBUTING.md), which this script has no part
# in and doesn't affect.
#
# Usage: scripts/ship.sh [--later] [--no-verify | --upstream]
#   --later       merge without a release of its own (see above).
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

BRANCH=ship
OTHER_BRANCH=ship-later
NO_VERIFY=()
UPSTREAM_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --later) BRANCH=ship-later; OTHER_BRANCH=ship ;;
    --no-verify) NO_VERIFY=(--no-verify); echo "Skipping pre-push checks (--no-verify)." ;;
    --upstream) UPSTREAM_ONLY=1 ;;
    *) echo "Unknown argument: $arg (only --later, --no-verify and --upstream are supported)" >&2; exit 1 ;;
  esac
done
PUSH_ARGS=(origin "HEAD:${BRANCH}" --force-with-lease "${NO_VERIFY[@]}")

# Commits on origin/main that we don't have and no release contains yet —
# made on GitHub since the last release (Renovate dependency merges, a
# web-UI edit). Compared by patch, not by commit: a rebase merge gives main
# new SHAs for our own pushed commits, and release tags sit on the PR's own
# commits rather than on main — so "ours" and "released" both mean "the
# same change is in that history" (git log --cherry-pick).
upstream_unreleased() {
  local latest_tag not_released
  latest_tag="$(git tag --list 'v[0-9]*' --sort=-version:refname | head -n 1)"
  not_released=""
  if [ -n "${latest_tag}" ]; then
    not_released="$(git log --no-merges --cherry-pick --right-only --format=%H "${latest_tag}...origin/main")"
  fi
  # Not ours (oldest first), and — when there's a release — not in it.
  git log --no-merges --cherry-pick --right-only --reverse --format='%H %h %s' HEAD...origin/main |
    while read -r sha short subject; do
      if [ -z "${latest_tag}" ] || grep -qx "${sha}" <<<"${not_released}"; then
        printf '%s %s\n' "${short}" "${subject}"
      fi
    done
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

# The push target (ship or ship-later) is checked first: if it already
# exists and already points at our current HEAD, we already pushed this
# exact commit — most likely a previous push is still sitting in an open,
# not-yet-merged PR, and rerunning the script right now has nothing new to
# add. Checked before rebasing (and against pre-rebase HEAD) since nothing
# about that conclusion depends on rebasing onto origin/main first.
SHIP_SHA="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo "")"
if [ -n "${SHIP_SHA}" ] && [ "${SHIP_SHA}" = "$(git rev-parse HEAD)" ]; then
  echo "Nothing to ship — origin/${BRANCH} already has this exact commit (still pending merge?). Not pushing."
  exit 0
fi

# The PR's title (and the release's name) comes from next.md's `# <title>`
# first line when there is one (scripts/release_notes.py): too long a title
# is cut off in GitHub's lists, so stop here rather than after pushing.
# Only when next.md changed in this batch — otherwise the PR doesn't use it.
SUMMARY_FILE=docs/release-notes/next.md
if [ -f "${SUMMARY_FILE}" ] && ! git diff --quiet origin/main HEAD -- "${SUMMARY_FILE}"; then
  if ! HEADING="$(python3 scripts/release_notes.py heading "${SUMMARY_FILE}" --check)"; then
    echo "Not pushing — shorten the title line, commit, and run this again."
    exit 1
  fi
  if [ -n "${HEADING}" ]; then
    echo "Title (from ${SUMMARY_FILE}): ${HEADING}"
  fi
fi

# One PR in flight at a time: both branches carry the same local history,
# so two open PRs would overlap. A pending ship PR (released on merge)
# takes precedence — --later would only hold back what it's releasing
# anyway. A pending ship-later PR is superseded by a plain ship of the same
# commits: it's closed (its branch deleted) once ship has them all.
OTHER_SHA="$(git rev-parse "origin/${OTHER_BRANCH}" 2>/dev/null || echo "")"
if [ -n "${OTHER_SHA}" ] && [ "${BRANCH}" = "ship-later" ]; then
  echo "A ship PR (released when it merges) is still open on origin/ship."
  echo "Run scripts/ship.sh without --later to add these commits to it, or wait until it lands. Not pushing."
  exit 1
fi

# Commits made on GitHub since the last release ship with ours: the
# release's commit list includes them, but the hand-written summary
# (docs/release-notes/next.md) only says what someone wrote into it.
# Routine dependency updates are listed for information; anything else
# needs a line there, so stop and ask (when someone's there to answer).
if [ -n "${UPSTREAM}" ]; then
  echo "Also going out with ours, made on GitHub since the last release:"
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

if [ -n "${OTHER_SHA}" ]; then
  # BRANCH is ship here (see above). Close the superseded ship-later PR,
  # but only if every one of its commits is in what we just pushed.
  if git cherry HEAD "origin/${OTHER_BRANCH}" | grep -q '^+'; then
    echo "::warning:: origin/${OTHER_BRANCH} has commits that aren't in this push — leaving its PR open."
  else
    git push origin --delete "${OTHER_BRANCH}"
    echo "Closed the ship-later PR: its commits are all in this ship."
  fi
fi

if [ "${BRANCH}" = "ship-later" ]; then
  echo "Pushed to ship-later — merges once CI is green, released with the next daily release (23:20 UTC). Watch it land with: gh pr status"
else
  echo "Pushed to ship — watch it land with: gh pr status"
fi
