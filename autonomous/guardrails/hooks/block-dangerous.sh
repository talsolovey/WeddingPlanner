#!/usr/bin/env bash
# block-dangerous.sh — PreToolUse deny hook (fires BEFORE any Bash tool call).
#
# WHY THIS EXISTS (WS4 pattern):
#   The model may be tricked (prompt injection via tool output — e.g. a guest's
#   free-text RSVP note, a vendor contract) into running destructive commands.
#   This hook is the enforcement layer that does NOT depend on the model's
#   judgment. It reads the tool call as JSON on stdin, checks the command
#   against a deny pattern, and:
#     exit 0 → allow the call
#     exit 2 → BLOCK the call (stderr is fed back to the agent as an error)
#
# Deny rules in settings.json also survive --dangerously-skip-permissions.
# Belt + braces.
#
# NOTE: Bash is NOT on the --allowedTools list in run-agent.sh, so in the
# unattended run the agent can't invoke Bash at all. This hook is the backstop
# for interactive use inside autonomous/ or if someone adds Bash later.

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

# Nothing to check if there's no command (e.g. a non-Bash tool call).
[ -z "$cmd" ] && exit 0

# ── Deny patterns ──
#   rm -rf / --force / --recursive → destructive file removal
#   sudo   → privilege escalation
#   mkfs   → disk formatting
#   :()\{  → fork bomb
#   (curl|wget)…|…sh → download-and-execute
#   /etc/  → system config modification
#   \.env  → secrets file access (OPENAI_API_KEY lives in vow-app/.env)
if printf '%s' "$cmd" | grep -qiE 'rm +-rf|rm .*--(force|recursive)|rm +-[a-z]*f|rm +-[a-z]*r|(^|[ ;|&])sudo( |$)|mkfs|:\(\)\{|(curl|wget) .*\|.*sh|/etc/|\.env'; then
  printf 'GUARDRAIL BLOCKED: %s\n' "$cmd" >&2

  # Audit trail — review what was blocked after a week of unattended runs.
  LOG_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}/agent-runs"
  mkdir -p "$LOG_DIR"
  printf '%s | %s\n' "$(date)" "$cmd" >> "$LOG_DIR/blocked.log"

  exit 2   # ← BLOCK
fi

exit 0     # ← ALLOW
