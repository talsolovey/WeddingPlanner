#!/usr/bin/env bash
# notify-telegram.sh — push today's draft to the couple's own Telegram chat.
#
# WHY A SCRIPT, NOT AN AGENT TOOL (safety, rubric §5):
#   This is DETERMINISTIC post-run code — the model never holds a messaging
#   capability. run-agent.sh calls this after the agent has finished; the chat
#   id is pinned to the couple's own chat in autonomous/.env, which the agent
#   is deny-listed from reading. A push to yourself is a self-notification —
#   explicitly a safe action; messaging *other* people would need HITL, and
#   there is deliberately no code path for that.
#
# Same Bot API wiring as WS4 task2-deploy/lib/telegram.js: plain `fetch`
# (curl here), PLAIN text on purpose — Telegram's Markdown parser 400s on
# unbalanced *_ entities; plain text never fails.
#
# SETUP: put in autonomous/.env (never committed; see .env.example):
#   TELEGRAM_BOT_TOKEN=123456789:AA...   (from @BotFather, reuse the WS4 bot)
#   TELEGRAM_CHAT_ID=...                 (your own chat — locks the push to you)
#
# Usage: ./notify-telegram.sh [path-to-draft]   (default: today's draft)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Creds live in autonomous/.env — the agent's deny rules block any .env read.
[ -f "$SCRIPT_DIR/.env" ] && set -a && . "$SCRIPT_DIR/.env" && set +a

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping push." >&2
  exit 0   # not an error: the run itself succeeded, the push is optional
fi

DRAFT="${1:-$SCRIPT_DIR/outbox/wedding_actions_$(date +%Y-%m-%d).md}"
if [ ! -s "$DRAFT" ]; then
  echo "[notify] no draft at $DRAFT — nothing to send." >&2
  exit 0
fi

# Telegram caps messages at 4096 chars; keep headroom for the header.
HEADER="✦ Vow — your weekly wedding brief ($(date +%Y-%m-%d))"
BODY="$(head -c 3800 "$DRAFT")"
[ "$(wc -c < "$DRAFT")" -gt 3800 ] && BODY="$BODY
… (truncated — full brief on your Vow dashboard)"

# --data-urlencode handles newlines/special chars; plain text, no parse_mode.
resp="$(curl -sS --max-time 20 \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${HEADER}

${BODY}")"

if printf '%s' "$resp" | grep -q '"ok":true'; then
  echo "[notify] draft pushed to Telegram."
else
  echo "[notify] Telegram push failed: $resp" >&2
  exit 1
fi
