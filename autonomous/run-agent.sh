#!/usr/bin/env bash
# run-agent.sh — ONE unattended run of Vow's weekly-brief agent (WS4 pattern).
#
# What it does:
#   1. Syncs the guardrails (deny hook + brief gate) into autonomous/.claude/.
#   2. Keeps the Mac awake (caffeinate) for the duration.
#   3. Runs a headless Claude Code session (claude -p) that reads PROMPT.md,
#      calls the Vow MCP tools, and writes a draft to outbox/.
#   4. Saves the machine-readable run record (JSON) to agent-runs/.
#   5. Exits. The agent is stateless per run — the schedule lives outside it
#      (launchd plist in LaunchAgents/, or run-loop-fallback.sh).
#
# Flags (verified against the WS4 kit, June 2025 docs):
#   --permission-mode dontAsk → auto-denies anything not on the allow-list
#   --max-budget-usd          → hard dollar cap on API spend
#   --max-turns               → hard cap on agentic turns
#   --output-format json      → result in .result, cost in .total_cost_usd
#   --allowedTools            → space-separated quoted tool names
#
# PREREQ (once): register the Vow MCP server —
#   claude mcp add --scope user --transport stdio vow -- \
#     "<repo>/vow-app/venv/bin/python3" "<repo>/vow-app/mcp_server.py"
#
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
# The agent runs INSIDE autonomous/ — that's where the .claude/ guardrails
# live and where outbox/ + agent-runs/ go. The wedding data stays in vow-app/,
# reachable ONLY through the MCP tools (never as files).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"

RUN_DIR="${RUN_DIR:-$PROJECT_DIR/agent-runs}"

# The ONLY MCP tools this agent may call (mcp__<server>__<tool>).
BRIEF_TOOL="${BRIEF_TOOL:-mcp__vow__run_weekly_brief}"
STATUS_TOOL="${STATUS_TOOL:-mcp__vow__get_wedding_status}"

# Hard caps — cheap insurance against runaway loops. These stack on top of the
# orchestrator's OWN ceilings inside the tool ($0.15/specialist, $0.50/run):
# two independent layers have to fail before money runs away.
MAX_TURNS="${MAX_TURNS:-15}"
MAX_BUDGET="${MAX_BUDGET:-1.00}"

# ──────────────────────────────────────────────────────────────────────────────
# RUN
# ──────────────────────────────────────────────────────────────────────────────
mkdir -p "$RUN_DIR" "$PROJECT_DIR/outbox"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
cd "$PROJECT_DIR"

# Guardrails are kept in git under guardrails/ (this session couldn't write
# .claude/ directly) and installed here on every run — idempotent, reviewable.
mkdir -p "$PROJECT_DIR/.claude/hooks"
cp "$SCRIPT_DIR/guardrails/settings.json" "$PROJECT_DIR/.claude/settings.json"
cp "$SCRIPT_DIR"/guardrails/hooks/*.sh "$PROJECT_DIR/.claude/hooks/"
chmod +x "$PROJECT_DIR/.claude/hooks/"*.sh

# Pin the project dir so the hooks resolve deterministically and the brief
# gate checks the same outbox/ the agent writes to.
export CLAUDE_PROJECT_DIR="$PROJECT_DIR"

# Turn ON the completion gate for THIS run only. require-brief.sh enforces
# only when it sees this flag — unattended runs must produce their draft;
# interactive sessions are never nagged.
export VOW_ENFORCE_BRIEF=1

echo "[$(date)] Starting Vow weekly-brief run (stamp=$STAMP)..."
echo "  PROJECT_DIR=$PROJECT_DIR"
echo "  TOOLS=$BRIEF_TOOL $STATUS_TOOL"
echo "  MAX_TURNS=$MAX_TURNS  MAX_BUDGET=\$$MAX_BUDGET"

# caffeinate -s → keep the Mac awake for this process (AC power).
# --permission-mode dontAsk → DENY anything not on the allow-list. This is a
#   hard control: messaging guests/vendors or touching wedding data is simply
#   not on the list, so it's denied — no matter what the model decides.
# Note: no Bash on the allow-list. Read/Write only (for PROMPT.md + outbox/),
#   plus the two Vow tools.
caffeinate -s claude -p "$(cat "$SCRIPT_DIR/PROMPT.md")" \
  --permission-mode dontAsk \
  --allowedTools "Read" "Write" "$BRIEF_TOOL" "$STATUS_TOOL" \
  --max-turns "$MAX_TURNS" \
  --max-budget-usd "$MAX_BUDGET" \
  --output-format json \
  > "$RUN_DIR/run_$STAMP.json" 2> "$RUN_DIR/run_$STAMP.err"

echo "[$(date)] Run complete → $RUN_DIR/run_$STAMP.json"

# Quick status from the run record:
if command -v jq &>/dev/null; then
  jq '{cost: .total_cost_usd, turns: .num_turns, ok: (.is_error|not)}' \
    "$RUN_DIR/run_$STAMP.json" 2>/dev/null || true
fi

# ── Self-notification: push today's draft to the couple's own Telegram chat ──
# Deterministic post-run code, NOT an agent tool — the model never holds a
# messaging capability. Skips silently if autonomous/.env has no bot creds.
"$SCRIPT_DIR/notify-telegram.sh" || echo "[WARN] Telegram push failed (run itself succeeded)"
