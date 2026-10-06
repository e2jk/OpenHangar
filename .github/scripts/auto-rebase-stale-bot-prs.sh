#!/usr/bin/env bash
# .github/scripts/auto-rebase-stale-bot-prs.sh
#
# Finds open Renovate PRs against main that have fallen behind, hit a
# conflict, or are just sitting on a stale CI run from before the base
# advanced, and asks Renovate to rebase, via its own API-friendly path
# (see .github/workflows/auto-rebase-stale-bot-prs.yml for the full "why"
# — the ruleset's strict required-status-checks policy means any PR merge
# makes every other open PR stale at once).
#
# Every PR this script looks at is logged — number, author login, and
# mergeStateStatus, plus whether it matched the Renovate bot login and
# whether it counted as stale — even the ones skipped. A previous version
# filtered non-matching/non-stale PRs out of a jq pipeline before anything
# was ever logged, so when gh CLI 2.98 started reporting the bot author as
# "app/renovate" instead of the classic "renovate[bot]", the workflow
# silently matched nothing on every run — no error, no output, just quiet
# inaction. This script exists so that failure mode is visible instead of
# silent.
#
# Treats mergeStateStatus=UNKNOWN and BLOCKED the same as BEHIND/DIRTY
# (worth a nudge), not "not stale":
#
# - UNKNOWN isn't a real merge state -- it's GitHub's "still computing, ask
#   again later" placeholder, returned while it recomputes mergeability
#   after a base-branch push. This workflow's own push:main trigger fires
#   at exactly that moment for every *other* open PR, so under a merge
#   cascade (several bot PRs landing within minutes of each other) it can
#   read UNKNOWN for everyone, every time, and skip the whole batch.
# - BLOCKED is what UNKNOWN settles into once GitHub finishes that
#   computation, for a PR whose merge-preview is already clean against the
#   current base but whose *last CI run* predates that base and never got
#   re-triggered (no new commit landed on the PR branch itself, so neither
#   Renovate's own rebaseWhen:conflicted nor this script's BEHIND/DIRTY
#   check ever fires for it). Confirmed for real on PR #302 after
#   PR #313/#315 landed: its merge-commit already carried the fixed pins,
#   but its CI run still showed the pre-fix failure from before main
#   advanced.
#
# Either way the only backstop left, without this, is the 6h cron, which
# needs to catch a lull where GitHub's computation has actually settled
# *and* nothing else is making other PRs newly stale in the meantime. A
# false-positive nudge here (asking Renovate to rebase a PR that turns out
# not to need it, or that's genuinely still failing for an unrelated
# reason) is a bounded cost, not an unbounded one -- same MAX_ATTEMPTS
# tolerance this script already relies on elsewhere: a truly broken PR
# just burns its 3 attempts and then gets left for a human, same as today.
#
# Requires GH_TOKEN and GH_REPO in the environment, same as any other gh
# CLI invocation — safe to run locally with a personal token to debug.
#
# A PR labelled `no-auto-rebase` is skipped entirely, regardless of
# mergeStateStatus or attempt count. Use this for a bot PR that's stale for
# a known, non-staleness reason -- e.g. a dependency bump that conflicts
# with another package's pin until *that* package releases a compatible
# version. Rebasing such a PR every 6h just burns its 3 attempts without
# ever fixing the actual problem, and then it goes silent (see
# MAX_ATTEMPTS below) instead of surfacing that it's blocked on something
# external. Add the label by hand once the reason is understood; remove it
# once the blocking condition is resolved so normal auto-rebase resumes.
#
# Env vars (all optional, defaults match the workflow):
#   MAX_ATTEMPTS  max rebase nudges per PR before leaving it for a human (default 3)
#   MARKER        HTML comment used to count this script's own past comments

set -uo pipefail

MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
MARKER="${MARKER:-<!-- openhangar-auto-rebase -->}"

# Known bot-login spellings. gh CLI >=2.98 reports GitHub App PR authors as
# "app/<slug>" instead of the classic "<slug>[bot]" — match both so this
# keeps working regardless of which gh version the runner ships.
is_renovate() {
  [ "$1" = "renovate[bot]" ] || [ "$1" = "app/renovate" ]
}

echo "Listing open PRs against main..."
prs_json=$(gh pr list --state open --base main --limit 100 --json number,author,mergeStateStatus,labels)
pr_count=$(echo "$prs_json" | jq 'length')
echo "Found $pr_count open PR(s) targeting main."
echo

# Nudges at most one PR per run, not every stale one -- labelling several
# bot PRs with `rebase` in the same breath makes Renovate force-push all
# of them within the same minute or two, and they then queue up behind
# each other in ci.yml's single 'bot-pipeline' concurrency lane with their
# merge-ref locked in at synchronize time, not refreshed while queued --
# by the time one several slots back actually runs, main has usually
# moved again, wasting that attempt. Observed for real after #313/#315
# landed: all 7 then-stale PRs got rebased together and were already
# BEHIND again minutes later. The normal push:main trigger re-invokes this
# script as soon as *that* one PR's turn resolves (merge or 3 exhausted
# attempts), pacing nudges to the actual merge cadence.
#
# `shuf` randomizes which stale PR gets that one slot, rather than always
# the first in list order -- a PR that's stale for a real, unrelated
# reason (its own bump genuinely breaks something) would otherwise
# monopolize every run until it burns all 3 attempts, leaving any PR that
# *would* merge cleanly stuck behind it. Random choice means a broken PR
# competes for a slot like everyone else instead of blocking the line.
echo "$prs_json" | jq -c '.[]' | shuf | while read -r pr; do
  number=$(echo "$pr" | jq -r '.number')
  login=$(echo "$pr" | jq -r '.author.login')
  status=$(echo "$pr" | jq -r '.mergeStateStatus')
  has_skip_label=$(echo "$pr" | jq -r '[.labels[].name] | any(. == "no-auto-rebase")')

  if is_renovate "$login"; then
    bot="renovate"
  else
    echo "PR #$number: author '$login' does not match a known bot login — skipping."
    continue
  fi

  if [ "$has_skip_label" = "true" ]; then
    echo "PR #$number ($bot): labelled 'no-auto-rebase' — skipping."
    continue
  fi

  if [ "$status" != "BEHIND" ] && [ "$status" != "DIRTY" ] && [ "$status" != "UNKNOWN" ] && [ "$status" != "BLOCKED" ]; then
    echo "PR #$number ($bot, author '$login'): mergeStateStatus=$status — not stale, skipping."
    continue
  fi

  attempts=$(gh pr view "$number" --json comments \
    --jq "[.comments[] | select(.body | contains(\"$MARKER\"))] | length")

  if [ "$attempts" -ge "$MAX_ATTEMPTS" ]; then
    echo "PR #$number ($bot): mergeStateStatus=$status, already retried $attempts/$MAX_ATTEMPTS times — leaving for a human, not retrying again."
    continue
  fi

  next_attempt=$((attempts + 1))
  echo "PR #$number ($bot): mergeStateStatus=$status, triggering rebase, attempt $next_attempt/$MAX_ATTEMPTS"
  note="Auto-rebase attempt $next_attempt/$MAX_ATTEMPTS -- this PR fell behind main (or hit a conflict) after another PR merged."

  if ! gh pr edit "$number" --add-label rebase; then
    echo "::warning::Failed to label PR #$number"
    continue
  fi
  body=$(printf 'Added the rebase label to ask Renovate to rebase this PR.\n\n%s\n%s\n' "$note" "$MARKER")
  if ! gh pr comment "$number" --body "$body"; then
    echo "::warning::Failed to comment on PR #$number"
  fi

  # One nudge per run -- see the comment above this loop for why. Any
  # other stale PR is left for this script's next invocation (the very
  # next push to main, or the 6h cron at the latest).
  echo "Nudged PR #$number this run -- leaving any other stale PR for next time."
  break
done

echo
echo "Done — checked $pr_count PR(s)."
