#!/usr/bin/env bash
# setup.sh — one command to set up Vow's autonomous run (WS4 pattern). Read it; it's short.
#
#   1. Makes the scripts + hooks executable
#   2. Ensures vow-app's venv exists and installs dependencies (incl. mcp)
#   3. Registers the Vow MCP server with Claude Code
#   4. Creates autonomous/.env from the example if missing (fill in your bot creds)
#
# It does NOT run the smoke test — run that yourself (./smoke-test.sh), so the
# test also confirms your environment is healthy before the first run.
#
# Usage:  ./autonomous/setup.sh        Re-running is safe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VOW_APP="$REPO_DIR/vow-app"

echo "▶ 1/4  Making scripts + hooks executable..."
chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/guardrails/hooks/*.sh

echo "▶ 2/4  Ensuring vow-app venv + dependencies..."
[ -d "$VOW_APP/venv" ] || python3 -m venv "$VOW_APP/venv"
VENV_PY="$VOW_APP/venv/bin/python3"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$VOW_APP/requirements.txt"

echo "▶ 3/4  Registering the Vow MCP server with Claude Code..."
if command -v claude >/dev/null 2>&1; then
  # --scope user (global): run-agent.sh launches the headless agent from
  # autonomous/, not the repo root — a project-scoped server registered here
  # would not be visible from that cwd. User scope works from anywhere.
  # Venv python by ABSOLUTE path so it works regardless of activation.
  claude mcp remove vow >/dev/null 2>&1 || true
  claude mcp remove --scope user vow >/dev/null 2>&1 || true
  claude mcp add --scope user --transport stdio vow -- "$VENV_PY" "$VOW_APP/mcp_server.py"
  echo "   ✓ registered as 'vow' (user scope) — tools: mcp__vow__*"
else
  echo "   ⚠  'claude' not found on PATH — install Claude Code + 'claude auth login',"
  echo "      then re-run ./autonomous/setup.sh"
fi

echo "▶ 4/4  Telegram creds file..."
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "   ✓ autonomous/.env exists"
else
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  echo "   ✓ created autonomous/.env from the example — fill in TELEGRAM_BOT_TOKEN"
  echo "     + TELEGRAM_CHAT_ID to get the brief pushed to your phone (optional)."
fi

echo ""
echo "✅ Environment ready. Prove it:  ./autonomous/smoke-test.sh"
echo "   Then one live run:            ./autonomous/run-agent.sh"
