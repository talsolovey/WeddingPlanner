#!/usr/bin/env bash
# smoke-test.sh — one command: prove the whole autonomous stack is wired (WS4 pattern).
#
# NO real side-effect, no model call, costs $0, takes ~15 seconds.
#
# WHAT IT CHECKS: claude + jq installed; both guardrail hooks behave (deny hook
#   blocks destructive commands, brief gate enforces only during a run); the
#   guardrails settings are valid + wired; every script parses; the Vow MCP
#   server imports and exposes both tools; the offline test suite passes; the
#   'vow' server is registered; secrets are gitignored; the plist is valid.
#
# USAGE:  ./autonomous/smoke-test.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VOW_APP="$REPO_DIR/vow-app"
PASS=0; FAIL=0; WARN=0
pass() { PASS=$((PASS + 1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }
warn() { WARN=$((WARN + 1)); echo "  ⚠️  $1"; }

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  Vow — Scheduled Autonomous Brief — Smoke Test            ║"
echo "╚═══════════════════════════════════════════════════════════╝"

# ── 1. Toolchain ──
echo "1. Toolchain"
command -v claude &>/dev/null && pass "claude found: $(claude --version 2>/dev/null || echo '?')" \
                              || fail "claude not found — install Claude Code first"
command -v jq &>/dev/null && pass "jq found: $(jq --version 2>/dev/null)" \
                          || fail "jq not found — brew install jq"

# ── 2. Hook: block-dangerous.sh (the deny hook doesn't trust the model) ──
echo "2. Hook: block-dangerous.sh"
HOOK="$SCRIPT_DIR/guardrails/hooks/block-dangerous.sh"
chmod +x "$HOOK" 2>/dev/null || true
TMP_PROJ="$(mktemp -d)"
if echo '{"tool_input":{"command":"rm -rf outbox"}}' | CLAUDE_PROJECT_DIR="$TMP_PROJ" "$HOOK" >/dev/null 2>&1; then
  fail "deny hook ALLOWED 'rm -rf' (expected exit 2)"
else
  [ $? -eq 2 ] && pass "deny hook blocks 'rm -rf' (exit 2)" || fail "deny hook wrong exit on 'rm -rf'"
fi
if echo '{"tool_input":{"command":"cat ../vow-app/.env"}}' | CLAUDE_PROJECT_DIR="$TMP_PROJ" "$HOOK" >/dev/null 2>&1; then
  fail "deny hook ALLOWED '.env' read (expected exit 2)"
else
  [ $? -eq 2 ] && pass "deny hook blocks '.env' access (exit 2)" || fail "deny hook wrong exit on '.env'"
fi
echo '{"tool_input":{"command":"ls -la"}}' | CLAUDE_PROJECT_DIR="$TMP_PROJ" "$HOOK" >/dev/null 2>&1 \
  && pass "deny hook allows 'ls -la' (exit 0)" || fail "deny hook blocked a benign command"
grep -q "rm -rf" "$TMP_PROJ/agent-runs/blocked.log" 2>/dev/null \
  && pass "blocked commands are audited to blocked.log" || fail "blocked.log audit trail missing"

# ── 3. Hook: require-brief.sh (the run can't end without its draft) ──
echo "3. Hook: require-brief.sh"
GATE="$SCRIPT_DIR/guardrails/hooks/require-brief.sh"
chmod +x "$GATE" 2>/dev/null || true
echo '{}' | CLAUDE_PROJECT_DIR="$TMP_PROJ" "$GATE" >/dev/null 2>&1 \
  && pass "brief gate stays quiet in interactive sessions" || fail "brief gate fired outside a run"
if echo '{}' | VOW_ENFORCE_BRIEF=1 CLAUDE_PROJECT_DIR="$TMP_PROJ" "$GATE" >/dev/null 2>&1; then
  fail "brief gate allowed stop with no draft (expected exit 2)"
else
  [ $? -eq 2 ] && pass "brief gate forces continue when the draft is missing (exit 2)" \
               || fail "brief gate wrong exit with no draft"
fi
echo '{"stop_hook_active":true}' | VOW_ENFORCE_BRIEF=1 CLAUDE_PROJECT_DIR="$TMP_PROJ" "$GATE" >/dev/null 2>&1 \
  && pass "brief gate allows stop when stop_hook_active=true (anti-loop)" \
  || fail "brief gate loops forever (stop_hook_active ignored!)"
mkdir -p "$TMP_PROJ/outbox" && echo draft > "$TMP_PROJ/outbox/wedding_actions_$(date +%Y-%m-%d).md"
echo '{}' | VOW_ENFORCE_BRIEF=1 CLAUDE_PROJECT_DIR="$TMP_PROJ" "$GATE" >/dev/null 2>&1 \
  && pass "brief gate allows stop once the draft exists" || fail "brief gate blocked a completed run"
rm -rf "$TMP_PROJ"

# ── 4. Guardrail settings ──
echo "4. Guardrail settings"
SETTINGS="$SCRIPT_DIR/guardrails/settings.json"
if jq . "$SETTINGS" >/dev/null 2>&1; then
  pass "guardrails/settings.json is valid JSON"
  jq -e '.hooks.PreToolUse' "$SETTINGS" >/dev/null && pass "PreToolUse hook wired" || fail "PreToolUse hook missing"
  jq -e '.hooks.Stop' "$SETTINGS" >/dev/null && pass "Stop hook wired" || fail "Stop hook missing"
  jq -e '.permissions.deny | length > 0' "$SETTINGS" >/dev/null \
    && pass "deny list present ($(jq '.permissions.deny | length' "$SETTINGS") rules)" || fail "deny list empty"
else
  fail "guardrails/settings.json is not valid JSON"
fi

# ── 5. Script + plist syntax ──
echo "5. Script + plist syntax"
sfail=0
for s in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/guardrails/hooks/*.sh; do
  bash -n "$s" 2>/dev/null || { fail "syntax error: $s"; sfail=1; }
done
[ "$sfail" -eq 0 ] && pass "all shell scripts pass bash -n"
if command -v plutil &>/dev/null; then
  plutil -lint -s "$SCRIPT_DIR/LaunchAgents/com.vow.weekly-brief.plist" >/dev/null 2>&1 \
    && pass "launchd plist is valid" || fail "launchd plist failed plutil -lint"
else
  warn "plutil not found (not macOS?) — skipping plist lint"
fi

# ── 6. The Vow MCP server ──
echo "6. Vow MCP server"
PYBIN="$VOW_APP/venv/bin/python3"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3 || true)"
if [ -n "$PYBIN" ]; then
  if TOOLS="$(cd "$VOW_APP" && "$PYBIN" -c "
import asyncio, mcp_server
assert mcp_server.mcp is not None, 'mcp package missing'
print(','.join(sorted(t.name for t in asyncio.run(mcp_server.mcp.list_tools()))))
" 2>/dev/null)"; then
    if [ "$TOOLS" = "get_wedding_status,run_weekly_brief" ]; then
      pass "mcp_server.py exposes exactly: $TOOLS"
    else
      fail "unexpected tool list: $TOOLS"
    fi
  else
    fail "mcp_server.py failed to import (run ./autonomous/setup.sh to install deps)"
  fi
else
  fail "no python3 found"
fi

# ── 7. Offline test suite (the strongest evidence: it must pass) ──
echo "7. Offline test suite"
if [ -n "$PYBIN" ] && (cd "$VOW_APP" && "$PYBIN" -m unittest discover tests >/dev/null 2>&1); then
  pass "vow-app test suite passes ($(cd "$VOW_APP" && "$PYBIN" -m unittest discover tests 2>&1 | grep -o 'Ran [0-9]* tests' || echo '?'))"
else
  fail "vow-app test suite FAILED — run: cd vow-app && python3 -m unittest discover tests"
fi

# ── 7b. Judge + golden set ──
echo "7b. Judge + golden set"
jq -e 'length > 0 and all(.[]; .human_label == "pass" or .human_label == "fail")' \
  "$SCRIPT_DIR/golden/golden_set.json" >/dev/null 2>&1 \
  && pass "golden_set.json parses ($(jq length "$SCRIPT_DIR/golden/golden_set.json") cases, labels valid)" \
  || fail "golden_set.json invalid"
if [ -n "$PYBIN" ] && "$PYBIN" "$SCRIPT_DIR/judge.py" --dry-run >/dev/null 2>&1; then
  pass "judge.py --dry-run works"
else
  fail "judge.py --dry-run failed"
fi

# ── 8. MCP registration (info) ──
echo "8. MCP registration"
if command -v claude &>/dev/null; then
  if claude mcp list 2>/dev/null | grep -q "^vow\b\|/vow "; then
    pass "'vow' server is registered with Claude Code"
  else
    warn "'vow' not in claude mcp list — run ./autonomous/setup.sh"
  fi
else
  warn "claude not available — skip MCP listing"
fi

# ── 9. Secrets hygiene ──
echo "9. Secrets"
[ -f "$SCRIPT_DIR/.env.example" ] && pass ".env.example present" || fail ".env.example missing"
for envf in "autonomous/.env" "vow-app/.env"; do
  if [ -f "$REPO_DIR/$envf" ]; then
    git -C "$REPO_DIR" check-ignore "$envf" >/dev/null 2>&1 \
      && pass "$envf is gitignored (keys won't be committed)" \
      || fail "$envf exists but is NOT gitignored — key could leak!"
  fi
done

# ── Summary ──
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Results: ✅ $PASS passed  ❌ $FAIL failed  ⚠️  $WARN warnings"
echo "════════════════════════════════════════════════════════════"
if [ "$FAIL" -gt 0 ]; then
  echo "  Fix the failures above before scheduling the agent."
  exit 1
fi
echo "  🎉 All checks passed. One live run:  ./autonomous/run-agent.sh"
exit 0
