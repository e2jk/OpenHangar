# Overnight autopilot instructions

> **⚠️ Exceptional instructions — do not self-apply.**
> This file grants a standing exception to several default safety rules
> (most importantly: committing without asking first). It must **only**
> be followed when the user's message *this session* explicitly says so —
> e.g. "following the instructions in `.claude/overnight-autopilot.md`,
> develop all IMG-xx items from the backlog" or "use overnight-autopilot.md
> and work through the next phase of the implementation plan." Independently
> reading this file (during normal exploration of the repo, or because a
> task looks similar) is **not** an invocation. If in any doubt whether the
> user actually invoked this mode, ask before acting on it.
>
> Outside of an explicit invocation, the default stands: never run `git
> commit` yourself — propose a commit message and let the human commit.

## What this grants

When explicitly invoked for a specific scope (e.g. "all IMG-xx items",
"the next 5 backlog items", "Phase 12 of the implementation plan"):

- Work through the specified items **without stopping to ask for
  direction** on implementation approach, ordering within the batch, or
  judgment calls. Use your best judgement; if a backlog/plan entry
  documents a specific order or dependency between items (e.g. "do X
  before Y", "requires IMG-04 first"), follow it.
- **Commit in the user's name** after each item, once — see "Required
  gate before every commit" below. One item = one commit, conventional
  commits format, no AI-tool attribution trailer (matches this repo's
  standing convention).
- **Never `git push`.** All work stays local; the user reviews and
  pushes personally when they're back.
- Never force-push, `git reset --hard`, delete branches/tags, or touch
  anything outside this repository's working tree.
- Remove each completed item from its source doc **in the same commit**
  that implements it:
  - `docs/backlog.md` — delete the entry entirely (it's a live "still to
    do" list, not a history).
  - `docs/implementation_plan.md` — tick `- [x]` and add ✅ to the phase
    heading when the whole phase is done; don't delete the phase text.
- Re-read the **current** version of whatever doc you're working from at
  the start of the run and again before each item — these files are
  live and may have been edited since you last saw them; never work from
  a paraphrase carried over in conversation context.

## Required gate before every commit

- Python changes: `ruff check`, `ruff format --check`, `mypy app/`, then
  `bash scripts/run-tests-with-coverage.sh` — **100% line coverage
  required**, same as the standing project rule. Run lint/type checks
  *before* the test run, so the coverage run always validates the final,
  formatted code.
- Any `app/models.py` change needs an accompanying Alembic migration
  (`app/migrations/versions/`) with a genuinely random revision ID
  (`python3 -c "import secrets; print(secrets.token_hex(6))"`, never
  sequential) — validate with `scripts/check_migrations.py`.
- Any new/changed UI-visible string needs translations in every locale
  in `SUPPORTED_LOCALES` — validate with `scripts/check_translations.py`.
- Workflow YAML changes (`.github/workflows/*.yml`, `.github/dependabot.yml`):
  validate with `zizmor -q --persona=pedantic --offline .github/` and
  `actionlint` if available locally (`.githooks/pre-push --update`
  installs/syncs both plus every pip-based dev tool to what this repo
  pins). If a finding is a deliberate accepted risk, suppress it with a
  written reason (`# zizmor: ignore[rule] reason`), same policy as
  `# nosec`.
- Pure documentation / shell-script changes that touch no Python: the
  Python gate doesn't apply — just validate whatever's actually relevant
  (`bash -n` for shell, YAML syntax check, etc.). Don't run the full test
  suite for a change that couldn't possibly affect it.
- **Never commit red.** If an item can't be made to pass its gate with
  reasonable effort, leave it in the source doc with a short note on
  what's blocking it, and move on to the next item rather than guessing
  or committing broken work.

## Continuity across quota resets

Session usage quota may run out mid-batch. Set this up once at the start
of the run:

- `CronCreate` with `cron: "15 * * * *"` (15 minutes past every hour —
  clear of the top-of-hour thundering herd and far enough past typical
  quota-reset boundaries to actually find a fresh quota). The `prompt`
  should be self-contained: re-check `git log` and the current state of
  the source doc to see what's already committed vs. still remaining,
  then continue from there — never redo completed work, never assume the
  conversation's own context is still fresh.
- **Caveat**: `CronCreate` jobs are session-only (in-memory, gone if the
  process actually exits — not written to disk). This covers "the quota
  blocked further replies but the process is still idle, waiting" — it
  does not survive the terminal/session actually closing. It's still the
  right tool here since that's the failure mode a quota exhaustion
  produces.
- Cancel the cron job (`CronDelete`) once the requested batch is fully
  done, or if the user returns mid-run and says to stop.
- Use `TaskCreate`/`TaskUpdate` — one task per item in the batch, marked
  `in_progress` on start and `completed` on commit — so both you (on the
  next cron wake-up) and the user (checking in later) can see progress
  at a glance without re-deriving it from git log.

## Still stop and ask, even in this mode

This grant covers implementing, testing, and locally committing code.
It does **not** cover:

- Anything that touches the remote or shared state: pushing, opening or
  merging PRs, changing GitHub repository settings, dismissing security
  alerts, posting comments/issues.
- Anything genuinely destructive or irreversible outside git's own
  history (dropping data, rotating/regenerating secrets, deleting
  resources).
- A backlog/plan entry that itself says it needs human approval for a
  reason other than "just needs a commit" (e.g. a "human task" entry
  that's actually a GitHub UI settings toggle, not code) — skip it,
  leave it in place with a note, don't attempt it.
- `docker/docker-compose.yml` and `.env.example` (production deployment
  config — AGENTS.md's own "do not touch without human approval" list).
  CI pipeline and `Dockerfile` changes *are* in scope for this grant
  (they still go through the full gate and land as a normal local commit
  for later review) — it's specifically the production compose/env-example
  files that stay off-limits.
- Real ambiguity with actual behavioral or business consequences — not
  "which of several reasonable implementations", but "I genuinely don't
  know what's wanted here." Stop that one item, note why, move on.

## End of run

When the batch is complete (or you've gone as far as you can), leave a
clear summary: what was completed, what was skipped and why, and a
reminder that everything is local-only pending the user's own `git push`.
