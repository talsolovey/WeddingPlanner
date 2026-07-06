#!/usr/bin/env bash
# trajectory.sh — read what the agent ACTUALLY did (WS4 Task-4 "observability").
#
# Claude Code writes every session — headless runs included — as a JSONL file
# under ~/.claude/projects/<project-path-slug>/. This script finds the latest
# session for the autonomous project dir and summarizes the trajectory:
# how many turns, which tools fired (and how often), and what got blocked.
#
# Reading the real lines IS the skill — if the summary looks off, open the
# JSONL yourself; jq paths drift between Claude Code versions.
#
# Usage:
#   ./trajectory.sh                 # summarize the latest run
#   ./trajectory.sh <session.jsonl> # summarize a specific session file
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -ge 1 ]; then
  SESS="$1"
else
  # Claude Code slugs the project path by replacing '/' and '.' with '-'.
  SLUG="$(printf '%s' "$SCRIPT_DIR" | tr '/.' '--')"
  DIR="$HOME/.claude/projects/$SLUG"
  # Fallback: if the slug format drifted, take the newest session anywhere.
  [ -d "$DIR" ] || DIR="$HOME/.claude/projects"
  SESS="$(ls -t "$DIR"/*.jsonl "$DIR"/*/*.jsonl 2>/dev/null | head -1 || true)"
fi

if [ -z "${SESS:-}" ] || [ ! -f "$SESS" ]; then
  echo "No session JSONL found under ~/.claude/projects — run the agent first." >&2
  exit 1
fi

echo "Session: $SESS"
echo "Lines:   $(wc -l < "$SESS" | tr -d ' ')"
echo ""

echo "── Tool calls (count by tool) ──"
jq -r 'select(.message.content) | .message.content[]? | select(.type=="tool_use") | .name' "$SESS" 2>/dev/null \
  | sort | uniq -c | sort -rn || echo "(jq path drifted — open the JSONL and adjust)"

echo ""
echo "── MCP tool inputs (what the agent asked Vow for) ──"
jq -r 'select(.message.content) | .message.content[]? | select(.type=="tool_use") | select(.name|startswith("mcp__vow")) | "\(.name)"' "$SESS" 2>/dev/null || true

echo ""
echo "── Guardrail blocks this week ──"
BLOCKED="$SCRIPT_DIR/agent-runs/blocked.log"
if [ -s "$BLOCKED" ]; then cat "$BLOCKED"; else echo "(none — blocked.log is empty)"; fi
