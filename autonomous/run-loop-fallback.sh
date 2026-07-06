#!/usr/bin/env bash
# run-loop-fallback.sh — tmux/while-loop fallback when MDM blocks launchd.
#
# USE THIS IF your Mac blocks LaunchAgent plists (MDM/SIP), or for a quick
# "run every N seconds" test loop. SESSION-BOUND: it dies with the terminal /
# tmux session — the durable scheduler is launchd (see LaunchAgents/).
#
# Usage:
#   tmux new-session -d -s vow-agent './run-loop-fallback.sh'
#   tmux attach -t vow-agent                  # watch it
#   tmux kill-session -t vow-agent            # KILL SWITCH
#
set -euo pipefail

INTERVAL="${INTERVAL:-86400}"  # seconds between runs (default: daily)
MAX_RUNS="${MAX_RUNS:-7}"      # safety: stop after this many runs (default: a week)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_SCRIPT="$SCRIPT_DIR/run-agent.sh"

echo "Vow agent loop (fallback) — every ${INTERVAL}s, max ${MAX_RUNS} runs."
echo "Kill: Ctrl-C, or 'tmux kill-session -t vow-agent'"

count=0
while [ "$count" -lt "$MAX_RUNS" ]; do
  count=$((count + 1))
  echo ""
  echo "──── Run $count / $MAX_RUNS ────"
  "$RUN_SCRIPT" || echo "[WARN] run-agent.sh exited with code $?"
  echo "[$(date)] Sleeping ${INTERVAL}s until next run..."
  sleep "$INTERVAL"
done

echo "[$(date)] Reached MAX_RUNS ($MAX_RUNS). Stopping."
