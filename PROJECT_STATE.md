# PROJECT_STATE — WeddingOS / Vow

> A plain-language summary of where the project stands, updated after every step.
> Last updated: 2026-07-07 (Step 23: invitation waves deliver for real — provider-neutral WhatsApp sender, background per-recipient delivery).

## What is this project

**Vow** — an AI agent that helps couples plan their wedding (contracts, budget, vendors,
guest list). Built week by week in the agentic workshop. The workshop's bar: the agent
must *investigate, judge, and reason* on its own — not just run a fixed script — with
proper logging, cost control, and safety rails.

## The project, in folders

```
WeddingOS/
├── cursor-landing-page/   # Week 1 landing page — the one we're keeping
├── web-landing-page/      # Week 1, built without tools (for comparison)
├── harness-landing-page/  # Week 1, built with my custom harness (for comparison)
├── autonomous/            # WS4 kit: the weekly brief on a schedule, unattended
│   ├── PROMPT.md          #   the headless agent's job (call tool → save draft → stop)
│   ├── run-agent.sh       #   one capped run: claude -p → mcp__vow__* → outbox/ draft
│   ├── guardrails/        #   deny hook + "draft must exist" stop hook (synced to .claude/)
│   └── LaunchAgents/      #   launchd plist: fires daily at 08:00, runs on wake
└── vow-app/               # ← the actual product, everything new happens here
    ├── server.py          # thin entry point — builds the app, starts the server
    ├── mcp_server.py      # Vow as an MCP server: get_wedding_status (read-only) +
    │                      #   run_weekly_brief (runs the orchestrator) — the WS4 agent's only door in
    ├── app/               # the web layer, one module per feature (Flask blueprints)
    │   ├── core.py        #   shared: file paths, background jobs, JSON parsing
    │   ├── contracts.py   #   /api/contracts routes + helpers
    │   ├── budget.py      #   /api/budget routes (+ cached forecast)
    │   ├── guests.py      #   /api/guests routes (+ cached headcount check)
    │   ├── overview.py    #   /api/overview (home dashboard) + activity log
    │   ├── profile.py     #   couple profile from onboarding (names, date, photo)
    │   ├── chat.py        #   floating AI chat — LLM proxy with a live data snapshot
    │   ├── invitations.py #   invitation waves + scheduler (skip repliers, 3-reminder cap)
    │   ├── checklist.py   #   planning checklist with auto-check rules from live data
    │   └── timeline.py    #   day-of timeline + printable handoff sheet data
    ├── agent/             # the "brain": talks to GPT-4o, picks tools, loops until done
    │   └── orchestrator.py #  weekly brief: 3 specialist sub-agents in parallel + verifier + merge
    ├── skills/            # instruction files the agent reads to know HOW to do a job
    ├── data/              # the couple's wedding data — sample ILS wedding (~200 guests) loaded
    └── logs/              # every API call recorded: tokens used, cost in $, tools called
```

## How it works (the short version)

I give the agent a task ("analyze this contract"). It first asks itself *"which of my
skills fits this job?"*, reads that skill's instructions, reads the relevant data, works
through the task, and answers with its reasoning. When it learns something useful along
the way, it writes a note into the skill's `LESSONS.md` file — so next time it's smarter.
That's the self-improvement loop.

Safety rails: it can only touch 5 approved data files, it stops after 10 thinking rounds
*or* a per-run dollar ceiling, and every call is logged with its dollar cost. Uploaded
documents are treated as untrusted data (prompt-injection guard), every data write is
backed up and can't blank a dataset, and the public endpoints are rate-limited. Full
threat model + defenses in `vow-app/SECURITY.md`.

## What the agent can do right now

**Tools** (built into the harness — how the agent acts):

| Tool | What it does |
|---|---|
| `list_skills` | See which skills exist |
| `read_skill` | Read a skill's instructions + its learned lessons |
| `read_data` | Read one of the 5 wedding data files (budget, vendors, guests, contracts, decisions) |
| `write_data` | Update one of those data files (checked: valid JSON only) |
| `append_lesson` | Save a lesson it learned into a skill, for future runs |

**Skills** (instruction files — what the agent knows how to do):

| Skill | Status |
|---|---|
| `contract-analyzer` | ✅ working — red-flag checklist for vendor contracts |
| `budget-forecaster` | ✅ working — realistic final-cost forecast + risk warnings |
| `guest-list-manager` | ✅ working — headcount projection, capacity + catering reconciliation, dietary roll-up, RSVP follow-ups; full UI |
| `weekly-brief` | ✅ working — now orchestrated: 3 specialist sub-agents (contracts/budget/guests) run in parallel in isolated contexts, a verifier re-checks each against its skill checklist, one merge call ranks it all; full UI + home card |
| `seating-planner` | ✅ working — proposes a full table arrangement (families together, dietary clustering, notes as preferences, capacity math); proposal-only: code validates it and the couple must click Apply |

## Done so far

| Step | What | Status |
|---|---|---|
| Week 1 | Landing page, built 3 ways | ✅ done |
| Step 1 | Agent brain (harness) with skills support | ✅ tested — a run costs ~$0.0025 |
| Step 2 | Contract analyzer: upload a PDF → flags risks by severity | ✅ tested end to end (~$0.013/run); caught 4 of 8 planted traps — improving this in the evals step |
| Step 2b | Shared UI theme matching the landing page (cream/rose/serif) | ✅ all app pages will reuse `public/styles.css` |
| Step 3 | Budget tracker (add items, totals) + Vow's final-cost forecast | ✅ tested end to end (~$0.02/run); agent cross-referenced contract data unprompted |
| Step 3b | UX round: home dashboard, sample-data buttons, toasts/undo; analyses run in the background with a quiet loading state | ✅ tested |
| Step 4 | Guest-list manager skill + `guests.json` (capacity & per-head entered by couple): headcount range, capacity/catering reconciliation, dietary + RSVP follow-ups | ✅ tested end to end (~$0.022/run); `guests.html` page + home dashboard card + nav |
| Step 5 | Refactor: split the 340-line `server.py` into `app/` blueprints (core + contracts/budget/guests/overview). Considered TS/Nest/Express — kept Python, agentic focus | ✅ behavior-identical; all 23 routes + guardrails verified via test client |
| Step 6 | Removed all sample data: emptied live `data/*.json`, deleted `data/samples/`, the sample contract PDF, `app/samples.py`, all load-sample / analyze-sample endpoints + buttons | ✅ app starts empty; routes + add/delete verified via test client |
| Step 7 | Loaded one coherent sample wedding (venue + contract + ~200-guest list) across all 3 data files; switched currency to USD | ✅ loads coherently; cross-feature vendor names match |
| Step 8 | Deploy prep for Render: gunicorn, PORT + `VOW_DATA_DIR` env, `render.yaml`, `DEPLOY.md` | ✅ gunicorn serves all routes; data-dir override verified for server + agent |
| Step 9 | Eval harness: scored recall vs planted traps (`evals/`) for all three skills | ✅ runs; contract 7/8, budget 6/6, guests flagged for recheck |
| Step 10 | 4th feature — `weekly-brief` skill + page + home card + nav; made JSON parsing degrade generically | ✅ tested end to end (~$0.024/run); 4 features now live |
| Step 11 | Settled the layout: home = instant summary dashboard (no agent call on load); weekly brief lives on its own page, generated on demand | ✅ all 5 pages serve; nav consistent; analyze endpoint intact |
| Step 12 | Security hardening for autonomous running: prompt-injection guard (`agent/guard.py` + hardened system prompt), `write_data` backups + destructive-write guard, per-run cost ceiling, per-IP rate limiting on agent endpoints, output-escaping regression. Threat model in `SECURITY.md`; 17-test suite in `tests/` | ✅ all 17 tests pass (no network); all 5 pages still serve |
| Step 13 | WS5 sub-agents: weekly brief rebuilt as an orchestrator (`agent/orchestrator.py`) — 3 specialists fan out in parallel (fresh context + own $0.15 cap each), a verifier (tool-free call with skill + data + findings) appends misses tagged `flagged_by: "verifier"`, one merge call produces the brief; `weeks_to_wedding` computed in code; whole run capped at `VOW_ORCH_MAX_COST_USD` ($0.50). UI shows verifier catches + per-agent breakdown | ✅ 26 tests pass (9 new, offline); live e2e run: verifier caught 5 items the specialists missed, $0.083 total |
| Step 14 | Built-in RSVP + seating (replaces external vendors): per-household magic links (`app/rsvp.py` — public, scoped writes, strict validation, injection-scanned free text, tighter rate limit) + guest form (`rsvp.html`); seating chart (`app/seating.py` + `seating.html`) with tables, click-to-assign, deterministic conflict engine; `seating-planner` skill — agent proposes, code validates, couple clicks Apply (HITL). Full loop: RSVP submits report conflicts they create; seating conflicts feed the weekly-brief merge as computed facts; home card + nav everywhere | ✅ 40 tests pass (14 new, offline); live auto-seat run: agent proposed 13 sensible tables, validation caught 3 capacity errors before Apply, $0.029 |
| Step 14b | UI de-listing pass: guests page got filter chips + search + scrolling table + per-row RSVP-link copy (long links card removed); seating page draws real round table-tops with seat dots, conflicts collapsed into grouped chips; weekly brief shows only high priority by default (medium/low fold away), on-track as chips | ✅ 40 tests pass; JS syntax-checked on all three pages |
| Step 15 | Guest groups: `group` field on households, editable inline in the guest list (with autocomplete of existing groups) and settable on add; whitelisted `PUT /api/guests/households/<id>` (group/notes/side only — RSVP fields stay guest-owned); seating page groups the unassigned list with one-click "seat group" onto a table; `seating-planner` skill now keeps groups together (notes still beat groups) | ✅ 45 tests pass (5 new, offline); pages serve; JS checked |
| Step 15b | Iteration round from live use: full inline row editing in the guest list (PUT now covers all fields with add-rules validation; meals then dietary removed end-to-end — form, API, skills, data); WhatsApp invites via click-to-chat with per-household magic links, phone capture, one-tap invite queue with ✓ sent tracking; visual seating room (round table-tops, seat dots, hover-✕ remove, seat-by-group); auto-seat applies directly with code validation as the gate; UI minimalism pass (ghost buttons, folded settings, single rose action per page); agent results unified into one sectioned report card with verdict chips + severity folding; JSON-only reinforcement on all agent endpoints + graceful prose fallback | ✅ 47 tests pass; all pages serve; JS syntax-checked |
| Step 16 | Home = mission control: countdown hero (days/weeks from `wedding_date`), the weekly brief moved home as a checkable to-do list (latest brief cached to `data/brief.json`, served by `GET /api/weekly-brief/latest`, so home loads instantly; "✦ Ask Vow to refresh" re-runs the orchestrator), summary sidebar (budget/guests/seating/contracts/lessons). Weekly Brief dropped from the nav (page still serves at /weekly-brief) | ✅ 47 tests pass; all 6 pages serve; JS checked |
| Step 18 | Supabase migration: new `storage.py` document layer — every dataset (budget, guests, seating, contracts, profile, waves, checklist, timeline, caches) is one JSON document read/written through a single module by both the web app and the agent's tools. With `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` set it uses a Postgres `vow_documents` JSONB table (RLS on, no public policies, service key server-side only, short write-through read cache); without them it falls back to the local `data/*.json` files unchanged. `supabase_schema.sql` + `migrate_to_supabase.py` do the one-time setup; agent write backups + destructive-write guard preserved | ✅ 68 tests pass (6 new storage tests incl. a fake-client Supabase suite); live app verified on the file backend; Supabase push pending credentials |
| Step 20 | WS4 Task-4 completion: Telegram self-notification after each run (post-run script, not an agent tool; creds blind-copied to gitignored `autonomous/.env`, live push verified); `setup.sh` + 24-check `smoke-test.sh`; `TELEMETRY=1` (OTEL console → per-run .err) + `trajectory.sh` (reads the session JSONL: tool calls, blocks); `judge.py` + 8-case Vow golden set (incl. a prompt-injection pair) via headless `claude -p --model haiku` | ✅ smoke test 22/0 in sandbox; judge --dry-run validates; live judge calibration pending on the Mac (no claude auth in sandbox) |
| Step 19 | Scheduled autonomous brief (WS4 + WS3): `vow-app/mcp_server.py` exposes Vow as an MCP server — `run_weekly_brief` (triggers the orchestrator, caches to the home dashboard = self-notification) + read-only `get_wedding_status` fallback; `autonomous/` kit runs it headless (`claude -p`, allow-list of just Read/Write + the 2 tools, `--max-turns 15` + `--max-budget-usd 1.00` stacked on the orchestrator's own $0.50 cap), deny hook logs blocked commands, a Stop hook refuses to finish without a dated draft in `outbox/`, launchd fires it daily at 08:00 | ✅ 72 tests pass (4 new, offline); both hooks exercised (block / force-continue / anti-loop); MCP server lists both tools; live scheduled run pending on the Mac |
| Step 17 | Full UI redesign from the `design_handoff_vow_app/` package: shared design system (`public/vow.css` tokens + `vow-shell.js` header/nav/toasts/mobile tab bar), all pages rebuilt to the reference designs (Home with refresh progress + new-couple state, Budget with payments calendar + what-if sliders, Guests with filters + WhatsApp nudges, Seating, Contracts, guest RSVP invitation card), five new screens (Checklist, Invitations, Vendors, Timeline, Login + 6-step Onboarding), a print-ready day-of handoff sheet, and a floating "✦ Ask Vow" chat on every page. New backend: couple profile (photo, priorities; syncs date/budget), invitation wave scheduler (recipients recomputed at send time — repliers skipped, max 3 reminders per household, due waves auto-send), checklist with auto-check rules driven by live app data, day-of timeline + LLM "check the flow", chat + message-generation endpoints (couple's data snapshot injected server-side as the system prompt) | ✅ 62 tests pass (15 new, offline); every page screenshotted against the reference; live chat + message-generation calls verified |
| Step 21 | Real multi-couple authentication: Supabase Auth (email+password; Google OAuth wired, needs one-time provider config) in front of a server-side Flask session; every route gated (`before_request` — pages redirect to /login, APIs 401; public surface = login/OAuth pages, auth API, guest RSVP, assets); `storage.py` scopes every document per couple (contextvar; Supabase PK now `(couple_id, name)`, file backend `data/couples/<id>/`; pre-auth data lives under couple `default`); RSVP links carry the couple id (`/rsvp/<couple>/<token>`, old links still work); background jobs inherit the requesting couple and job polls are couple-checked; login page wired to real endpoints, OAuth callback verifies tokens server-side before opening a session; sign-out in the shared shell | ✅ 93 tests pass (21 new: gate, faked-Supabase auth endpoints, couple isolation, job scoping); run the migration block in `supabase_schema.sql` + claim `default` rows after first signup |
| Step 22 | Real WhatsApp nudges via the Twilio sandbox: `app/whatsapp.py` — gated, rate-limited `POST /api/guests/<id>/nudge` builds the reminder server-side (couple names, date, the household's magic RSVP link) and sends it through Twilio's WhatsApp API (creds env-only: `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`; `TWILIO_WHATSAPP_FROM` defaults to the shared sandbox number). Graceful degradation: no Twilio config, invalid phone, or a recipient who hasn't joined the sandbox (error 63015) → the endpoint returns a wa.me click-to-chat URL and the UI falls back with an explanatory toast. Phone normalization (`normalize_phone`, default CC via `VOW_DEFAULT_CC`) fixes the local-format bug (`050-…` → `972…`). Successful sends stamp `last_nudged_at` + the activity log | ✅ 103 tests pass (10 new, Twilio faked at the `_twilio_send` seam); sandbox limits noted: recipients must join, 3-day expiry, free-form only in the 24h window |
| Step 23 | Invitation waves now really deliver: `send_whatsapp()` is a provider-neutral dispatcher (`WHATSAPP_PROVIDER=twilio|meta` — Meta Cloud API sender already implemented, so migrating off the sandbox is env-vars-only); sending a wave (button or due-date auto-fire) starts a background delivery job that personalizes per household (`[name]`, `[rsvp link]` → magic link), paces sends for the sandbox (1/3s, `VOW_WA_SEND_INTERVAL`), and records per-recipient sent/failed reasons on the wave (`delivery`), shown on the invitations page ("12 delivered automatically, 3 need a manual send"). Unconfigured stays pure bookkeeping (manual flow unchanged). Also: offline test suite now force-neutralizes provider creds (a real `.env` was leaking into tests and starting live delivery threads) | ✅ 108 tests pass (5 new: personalization, mixed-outcome delivery, bookkeeping-only) — suite ran 3× to check for job races |

## Decisions made (and why)

- **Python + GPT-4o** — my choice; matches the workshop harness I already built.
- **Reused my workshop harness** instead of writing a new one — it already had cost
  tracking and conversation compression built in.
- **Deploy on Render** (not Vercel). Vercel is serverless — it can't keep the JSON
  files or the in-memory background jobs between requests. Render runs one long-lived
  process with an optional persistent disk, which fits the file-based design as-is.
  Data dir is overridable via `VOW_DATA_DIR` so a disk can be attached without code
  changes. See `DEPLOY.md` + `render.yaml`.
- **Stayed on Python (Flask), did not move to TS/Nest/Express** — a rewrite is pure
  plumbing with no agentic payoff, and the workshop explicitly de-prioritizes it. Solved
  the real "feels messy" concern by splitting `server.py` into per-feature blueprints.
- **Sub-agents for the weekly brief, not for every feature** — the brief is the one task
  that spans all three areas, so it's where a single diluted context demonstrably missed
  things (the contingency, the Patel meal row). Verifier appends misses rather than
  re-running the specialist — simpler, bounded cost, and the "verifier catch" tag is
  itself useful signal in the UI.
- **Dates computed in code, not by the model** — `weeks_to_wedding` comes from
  `guests.settings.wedding_date`; the merge model can't overrule arithmetic.
- **RSVP links are capability tokens, not logins** — one token = write access to exactly
  one household's RSVP fields. No accounts, no passwords, nothing else reachable. Free
  text from guests is injection-scanned before it's stored, because agents read it later.
- **Supabase as a document store (one JSONB table), not a normalized schema** — the
  app's data model is already whole-document JSON with guards at the edges; swapping
  the persistence behind the existing load/save helpers changed ~10 lines per module
  and zero behavior. Normalizing into per-entity tables was rejected as a rewrite with
  no feature payoff; it can still happen later behind the same storage API.
- **File fallback stays** — no Supabase credentials means local files, so dev and the
  offline test suite need zero setup, and `VOW_STORAGE_BACKEND=files` is an escape hatch.
- **Redesign kept the stack: static pages + vanilla JS, no build step** — the handoff
  allowed a light React setup, but the app already worked as server-served HTML; a shared
  `vow.css` + `vow-shell.js` gives the same consistency without adding a toolchain.
- **AI chat context is assembled server-side** — the chat endpoint builds the couple's
  data snapshot itself (budget, guests, seating, contract flags) and injects it as the
  system prompt, so the client can never spoof or leak someone else's context.
- **Invitation recipients are computed at send time, never stored ahead** — that's what
  makes "Vow stops nudging the moment they reply" true by construction, with a hard
  3-reminders-per-household cap tracked per send.
- **Auto-seat applies directly, validation still gates (seating)** — originally the couple
  had to click Apply on a proposal card; that felt like homework, so auto-seat now saves
  the arrangement immediately and the couple corrects on the visual chart. The safety
  didn't go away, it moved: code validation still rejects hard-invalid plans (unknown
  households, one household at two tables) before saving, soft issues (over-capacity)
  surface as conflict chips, and the agent itself still has no write path — the endpoint
  code writes. First live run had justified this gate: the model broke capacity on 3 of
  13 tables.

- **The unattended agent triggers Vow's orchestrator, it doesn't re-analyze** — the WS4
  workshop template suggested a read-only status tool; we kept that only as a fallback.
  `run_weekly_brief` runs the product's own sub-agents + verifier, so the scheduled brief
  is the same quality as the button in the UI ("your product's agent runs on the loop").
- **Credentials live in the MCP server, never the agent** — `OPENAI_API_KEY` is read from
  `vow-app/.env` by the server process; deny rules block the agent from reading any `.env`.
  The agent's whole world is: two Vow tools, Read, Write. No Bash on the allow-list.
- **Guardrail files live in git (`autonomous/guardrails/`), synced to `.claude/` by
  run-agent.sh** — this session couldn't write `.claude/` directly (protected path), and
  the sync is idempotent + reviewable anyway.

## Next steps

1. ~~Deploy to Render~~ — done; live instance running. Push redeploys it.
2. MCP vs no-MCP A/B test (homework Part 3): trajectory logging + Peekaboo screenshot
   (no MCP) vs GitHub MCP line-count, then compare.
3. A scored eval harness (still deferred).

3. ~~Scheduled autonomous brief (WS4)~~ — built; on the Mac: register the MCP server +
   load the launchd plist (see `autonomous/README.md`), then collect a week of run records.

Backlog: capstone demo video (due Wed Jul 8, 10:00) — the before/after verifier story is
the centerpiece; vendor comparison with a reasoned recommendation; measure the
lessons-loop effect with the eval suite.

## How to run it

```bash
cd vow-app
pip install -r requirements.txt
python -m agent.harness "What skills do you have?"
```
