#!/usr/bin/env bash
# SessionStart hook: forces the "read AGENTS.md + memory index in full" ritual
# formerly described in the repo's CLAUDE.md by injecting the actual file
# contents as additionalContext, instead of relying on the model to notice
# and follow a plain-text instruction. There is deliberately no repo-root
# CLAUDE.md anymore — AGENTS.md is the single agent-agnostic technical
# briefing, and this hook is the whole Claude-Code-specific delivery
# mechanism for it plus the memory index.
#
# Portable across machines: $CLAUDE_PROJECT_DIR is set by Claude Code to the
# repo root for every hook invocation, and the auto-memory directory is
# derived from it using Claude Code's own path-sanitization convention
# (absolute path with "/" replaced by "-"), so nothing here is hardcoded to
# a particular clone location or username. Machine-specific facts (dev URLs,
# local paths, etc.) belong in memory, not in this script or its output —
# see reference_dev_server / feedback_local_dev_permissions in the memory
# index.
#
# Setup on a new machine: nothing to do. This script is tracked in git and
# wired up via the "hooks" block in the tracked .claude/settings.json, so it
# runs automatically as soon as you open Claude Code in a clone of this repo
# (Claude Code needs to have already been running in this directory once, or
# you may need to run /hooks once, for its settings file watcher to pick up
# a .claude/ directory it hasn't seen before).
set -euo pipefail

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SANITIZED="${REPO_ROOT//\//-}"
MEMORY_DIR="$HOME/.claude/projects/$SANITIZED/memory"

# NOTE ON SIZE: the harness persists any hook additionalContext over
# ~20,000 chars to a file and inlines only the first ~2,000 chars as a
# preview — the rest is silently NOT delivered into the model's context
# unless it separately chooses to Read the persisted file. AGENTS.md alone
# is already ~23KB, so building the full AGENTS.md + memory dump and
# attaching it "as a bonus" is wasted work whenever it's going to blow the
# budget anyway: it costs a cat/concat/persist-to-disk cycle for content
# that never reaches the model. So check the prospective size FIRST
# (cheap: file byte counts, no need to build the dump to measure it). Only
# build and inline the dump when it will actually fit under the guaranteed
# preview budget; otherwise skip straight to instructing the model to Read
# the files itself as a follow-up tool call — a clean, single read instead
# of one wasted shell-side read plus one real Read-tool read.
INLINE_BUDGET=17000  # headroom under the ~20,000-char persistence threshold for the instruction block + JSON overhead

agents_size=$(wc -c < "$REPO_ROOT/AGENTS.md" 2>/dev/null || echo 0)
memory_size=$(wc -c < "$MEMORY_DIR/MEMORY.md" 2>/dev/null || echo 0)
project_size=0
project_files=()
for f in "$MEMORY_DIR"/project_*.md; do
  [ -e "$f" ] || continue
  project_files+=("$f")
  project_size=$(( project_size + $(wc -c < "$f") ))
done
prospective_size=$(( agents_size + memory_size + project_size ))

content="This session-start briefing was injected automatically by a SessionStart hook (not a CLAUDE.md instruction or memory) so it cannot be skipped."
content+=$'\n\n## Session-start acknowledgement (do this first, before anything else)\n\n'
content+='Start your very first reply this session with this exact line, verbatim, so the user knows this briefing was actually delivered and read, not skipped:'
content+=$'\n\n> Squawk 7000 — pre-flight briefing complete, no snags on the board.\n\n'

if [ "$prospective_size" -lt "$INLINE_BUDGET" ]; then
  content+="AGENTS.md and the memory index + all project_*.md files are included in full below as already-read context — do not re-read them with the Read tool at session start unless something looks stale."
  content+=$'\n\n## AGENTS.md\n\n'
  content+="$(cat "$REPO_ROOT/AGENTS.md" 2>/dev/null || echo "(AGENTS.md not found at $REPO_ROOT)")"
  content+=$'\n\n## Memory index (MEMORY.md)\n\n'
  content+="$(cat "$MEMORY_DIR/MEMORY.md" 2>/dev/null || echo "(No memory index yet at $MEMORY_DIR — auto-memory may not have run on this machine yet.)")"
  content+=$'\n'
  for f in "${project_files[@]}"; do
    content+=$'\n## Memory: '"$(basename "$f")"$'\n\n'
    content+="$(cat "$f")"
    content+=$'\n'
  done
  sysmsg="Squawk 7000 — session-start hook ran, briefing inlined in full (~${prospective_size}B). Claude will open its first reply with an acknowledgement once you send a message."
else
  content+="This briefing (AGENTS.md + memory index + $(( ${#project_files[@]} )) project memory file(s), ~${prospective_size} bytes) is too large to inline reliably (the harness truncates hook output over ~20,000 chars to a short preview, silently dropping the rest). So it is NOT inlined below — as your very next action after the acknowledgement line, issue two separate Read tool calls, in order:"
  content+=$'\n\n'
  content+="1. Read($REPO_ROOT/AGENTS.md)"$'\n'
  content+="2. Read($MEMORY_DIR/MEMORY.md)"
  content+=$'\n\nMANDATORY, both calls, no exceptions: you have a separate built-in memory-recall system that may independently surface related snippets ("recalled N memories") — that is a different mechanism, is relevance/keyword-triggered so it is not guaranteed to fire on every query, and does NOT satisfy this requirement. Recall is a bonus on top, never a substitute for these two Read calls. If you reach your first reply without a visible Read tool call on both exact paths above, you have not complied, regardless of whether the reply text happens to be correct.'
  content+=$'\n\nThen, only if relevant to what the user actually asks about, Read any project_*.md file in '"$MEMORY_DIR"$' that the '"'"'In-progress work'"'"' section of MEMORY.md points to (read the rest on demand, not all up front).'
  content+=$'\n\nDo all of this before your first substantive reply — do not proceed on the acknowledgement text alone, and do not proceed on memory-recall snippets alone, neither is a substitute for actually reading the files.'
  sysmsg="Squawk 7000 — session-start hook ran, briefing too large to inline (~${prospective_size}B) so Claude will Read AGENTS.md/MEMORY.md as its first action once you send a message."
fi

content+=$'\n\nOnce you have this context (inline above or via Read), give 2-3 bullet points of what you currently understand the OpenHangar project and its state to be, so the user can correct anything stale, then ask what they would like to work on.\n'

# systemMessage is displayed directly by the harness the moment this hook
# runs — no model turn required, so (unlike additionalContext, which only
# reaches the model on its next reply) it's visible before you type
# anything. It only proves the SCRIPT ran; it can't prove Claude actually
# read/acted on the briefing the way the acknowledgement line in Claude's
# first reply does — those are two different guarantees, keep both.
jq -n --arg ctx "$content" --arg sysmsg "$sysmsg" \
  '{systemMessage: $sysmsg, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
