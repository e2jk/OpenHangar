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

content="This session-start briefing was injected automatically by a SessionStart hook (not a CLAUDE.md instruction or memory) so it cannot be skipped. AGENTS.md and the memory index + all 'project_*.md' files (Project / In-progress work sections of MEMORY.md) are included in full below as already-read context. Do not re-read these specific files with the Read tool at session start; do read the 'Feedback & conventions' entries in the index if you need one in full, and re-Read anything here if you need to verify it's current."
content+=$'\n\n## AGENTS.md\n\n'
content+="$(cat "$REPO_ROOT/AGENTS.md" 2>/dev/null || echo "(AGENTS.md not found at $REPO_ROOT)")"
content+=$'\n\n## Memory index (MEMORY.md)\n\n'
content+="$(cat "$MEMORY_DIR/MEMORY.md" 2>/dev/null || echo "(No memory index yet at $MEMORY_DIR — auto-memory may not have run on this machine yet.)")"
content+=$'\n'

for f in "$MEMORY_DIR"/project_*.md; do
  [ -e "$f" ] || continue
  content+=$'\n## Memory: '"$(basename "$f")"$'\n\n'
  content+="$(cat "$f")"
  content+=$'\n'
done

content+=$'\n\n## Session-start acknowledgement (do this now)\n\n'
content+='Start your very first reply this session with this exact line, verbatim, so the user knows this briefing was actually delivered and read, not skipped:'
content+=$'\n\n> Squawk 7000 — pre-flight briefing complete, no snags on the board.\n\n'
content+='Then give 2-3 bullet points of what you currently understand the OpenHangar project and its state to be, so the user can correct anything stale, then ask what they would like to work on.'
content+=$'\n'

jq -n --arg ctx "$content" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
