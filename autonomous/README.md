# Vow — scheduled autonomous weekly brief (WS4)

Vow's one core cross-cutting skill — the weekly brief — running **unattended,
reliably, safely, and provably**, on the WS4 pattern:

```
launchd (daily 08:00)
  → run-agent.sh                     caps: --max-turns 15, --max-budget-usd 1.00
    → claude -p PROMPT.md            allow-list ONLY: Read, Write, 2 Vow tools
      → mcp__vow__run_weekly_brief   Vow's own orchestrator (3 specialists ∥ + verifier + merge,
        |                            its own $0.50 cap) — the product's agent runs on the loop
        └→ brief cached → couple's home dashboard   (self-notification)
      → draft written → outbox/wedding_actions_<date>.md   (Stop-hook enforced)
    → run record → agent-runs/run_<stamp>.json   (cost, turns, ok)
    → notify-telegram.sh → the brief lands in YOUR Telegram chat  (self-notification)
```

## Layout

| File | Purpose |
|---|---|
| `PROMPT.md` | the agent's job: trigger the brief, write the draft, stop |
| `run-agent.sh` | one unattended run (installs guardrails, caps, run record) |
| `guardrails/` | `.claude` settings + hooks, kept in git; synced to `.claude/` each run |
| `guardrails/hooks/block-dangerous.sh` | PreToolUse deny hook (audit → `agent-runs/blocked.log`) |
| `guardrails/hooks/require-brief.sh` | Stop hook: no non-empty draft → force-continue (once) |
| `LaunchAgents/com.vow.weekly-brief.plist` | durable daily schedule (fires on wake) |
| `run-loop-fallback.sh` | tmux loop if MDM blocks launchd |
| `notify-telegram.sh` | post-run push of the draft to YOUR Telegram chat (creds in `.env`) |
| `.env.example` | template for `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (never committed) |
| `outbox/` | the drafts (gitignored; curate into the capstone submission) |
| `agent-runs/` | run records + blocked.log (gitignored; same) |

The MCP server itself lives with the product: [`../vow-app/mcp_server.py`](../vow-app/mcp_server.py).

## Setup (once)

```bash
cd /Users/talsolovey/Desktop/WeddingOS
vow-app/venv/bin/pip install mcp        # server dep (already in requirements.txt)

claude mcp add --scope user --transport stdio vow -- \
  "$(pwd)/vow-app/venv/bin/python3" "$(pwd)/vow-app/mcp_server.py"
claude mcp list                          # expect: vow ✓ (tools mcp__vow__*)
```

`OPENAI_API_KEY` (and optional `SUPABASE_*`, `VOW_DATA_DIR`) are read from
`vow-app/.env` by the **server**, never by the agent — the deny rules block the
agent from reading any `.env`, and its only path to the wedding data is the two
tools. Credential isolation by design.

## One test run

```bash
./autonomous/run-agent.sh
ls autonomous/outbox/                                    # the dated draft
jq '{cost:.total_cost_usd, turns:.num_turns}' autonomous/agent-runs/run_*.json | tail -1
```

## Schedule it

```bash
cp autonomous/LaunchAgents/com.vow.weekly-brief.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.vow.weekly-brief.plist
launchctl list | grep com.vow
```

**🛑 Kill switch:** `launchctl unload ~/Library/LaunchAgents/com.vow.weekly-brief.plist`
(tmux fallback: `tmux kill-session -t vow-agent`).

## Safety model (for the proposal, §5)

- **No high-harm actions possible:** the allow-list has no Bash, no messaging,
  no data-write tool. `run_weekly_brief` writes exactly one code-owned cache
  document (`brief`) — a reversible self-notification to the couple's dashboard.
- **Telegram push is code, not a capability:** notify-telegram.sh runs AFTER the
  agent exits, sends only to the couple's own pinned chat id (a self-notification),
  and reads its creds from `autonomous/.env` — which the agent is deny-listed
  from reading. The model never holds a messaging tool.
- **Layered spend caps:** `--max-budget-usd 1.00` on the run, `$0.50` orchestration
  cap and `$0.15`/specialist inside the tool. Two independent layers must fail
  before money runs away.
- **Guardrails don't trust the model:** deny hook blocks destructive Bash by
  pattern (logged to `blocked.log`); deny rules survive even bypass mode; the
  Stop hook proves a draft exists before the run may end.
- **Untrusted input:** tool output (guest notes, contract text) is data, not
  instructions — stated in PROMPT.md *and* enforced downstream by vow-app's
  injection guard on everything the orchestrator reads.

## Evidence this produces

- `agent-runs/run_*.json` — per-run cost/turns/ok from unattended fires
- `outbox/wedding_actions_*.md` — the dated drafts themselves
- `agent-runs/blocked.log` — anything the deny hook stopped
- `launchctl list | grep com.vow` — the live schedule
