#!/usr/bin/env bash
# require-brief.sh — Stop hook (completion gate), adapted from WS4.
#
# WHY THIS EXISTS:
#   The "task-transition stall" failure mode: the agent finishes its tool calls,
#   announces "done!", but never actually wrote the action-list draft. Without
#   this hook the run exits successfully with no output. This gate forces the
#   agent to keep working until a non-empty draft exists.
#
#   exit 0 → allow the agent to stop (draft exists, or we already forced once)
#   exit 2 → FORCE the agent to keep working (stderr tells it what to do)
#
# SCOPE — only the unattended run, never an interactive session:
#   run-agent.sh sets VOW_ENFORCE_BRIEF=1 for the headless run; the hook
#   inherits it. In a plain interactive session the variable is unset → exit 0.
#
# ANTI-INFINITE-LOOP:
#   stdin JSON includes "stop_hook_active": true when the agent has already
#   been forced to continue once. If we see that, allow the stop — otherwise
#   a broken draft path would loop forever.

input="$(cat)"

# Only enforce during the unattended agent run (run-agent.sh sets this).
[ "${VOW_ENFORCE_BRIEF:-}" = "1" ] || exit 0

# Guard against infinite loops: if we already forced a continue once, allow stop.
stop_active="$(printf '%s' "$input" | jq -r '.stop_hook_active // empty')"
[ "$stop_active" = "true" ] && exit 0

# Check for today's draft — the filename PROMPT.md tells the agent to write.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
draft="$PROJECT_DIR/outbox/wedding_actions_$(date +%Y-%m-%d).md"

if [ ! -s "$draft" ]; then
  echo "No non-empty draft at $draft yet. Write your weekly action list to that file, then stop." >&2
  exit 2   # ← FORCE CONTINUE
fi

exit 0     # ← ALLOW STOP (draft exists)
