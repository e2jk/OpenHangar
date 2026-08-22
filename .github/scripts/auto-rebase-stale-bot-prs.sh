#!/usr/bin/env bash
# .github/scripts/auto-rebase-stale-bot-prs.sh
#
# Finds open Dependabot/Renovate PRs against main that have fallen behind
# (or hit a conflict) and asks each bot to rebase, via its own
# API-friendly path (see .github/workflows/auto-rebase-stale-bot-prs.yml
# for the full "why" — the ruleset's strict required-status-checks policy
# means any PR merge makes every other open PR stale at once).
#
# Every PR this script looks at is logged — number, author login, and
# mergeStateStatus, plus whether it matched a known bot login and whether
# it counted as stale — even the ones skipped. A previous version filtered
# non-matching/non-stale PRs out of a jq pipeline before anything was ever
# logged, so when gh CLI 2.98 started reporting bot authors as
# "app/renovate"/"app/dependabot" instead of the classic
# "renovate[bot]"/"dependabot[bot]", the workflow silently matched nothing
# on every run — no error, no output, just quiet inaction. This script
# exists so that failure mode is visible instead of silent.
#
# Requires GH_TOKEN and GH_REPO in the environment, same as any other gh
# CLI invocation — safe to run locally with a personal token to debug.
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
is_dependabot() {
  [ "$1" = "dependabot[bot]" ] || [ "$1" = "app/dependabot" ]
}
is_renovate() {
  [ "$1" = "renovate[bot]" ] || [ "$1" = "app/renovate" ]
}

echo "Listing open PRs against main..."
prs_json=$(gh pr list --state open --base main --limit 100 --json number,author,mergeStateStatus)
pr_count=$(echo "$prs_json" | jq 'length')
echo "Found $pr_count open PR(s) targeting main."
echo

echo "$prs_json" | jq -c '.[]' | while read -r pr; do
  number=$(echo "$pr" | jq -r '.number')
  login=$(echo "$pr" | jq -r '.author.login')
  status=$(echo "$pr" | jq -r '.mergeStateStatus')

  if is_dependabot "$login"; then
    bot="dependabot"
  elif is_renovate "$login"; then
    bot="renovate"
  else
    echo "PR #$number: author '$login' does not match a known bot login — skipping."
    continue
  fi

  if [ "$status" != "BEHIND" ] && [ "$status" != "DIRTY" ]; then
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

  if [ "$bot" = "dependabot" ]; then
    body=$(printf '@dependabot rebase\n\n%s\n%s\n' "$note" "$MARKER")
    if ! gh pr comment "$number" --body "$body"; then
      echo "::warning::Failed to comment on PR #$number"
    fi
  else
    if ! gh pr edit "$number" --add-label rebase; then
      echo "::warning::Failed to label PR #$number"
      continue
    fi
    body=$(printf 'Added the rebase label to ask Renovate to rebase this PR.\n\n%s\n%s\n' "$note" "$MARKER")
    if ! gh pr comment "$number" --body "$body"; then
      echo "::warning::Failed to comment on PR #$number"
    fi
  fi
done

echo
echo "Done — checked $pr_count PR(s)."
