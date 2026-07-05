# PROJECT_STATE — WeddingOS / Vow

> A plain-language summary of where the project stands, updated after every step.
> Last updated: 2026-07-05 (Step 13: weekly brief rebuilt as parallel sub-agents + a verifier).

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
└── vow-app/               # ← the actual product, everything new happens here
    ├── server.py          # thin entry point — builds the app, starts the server
    ├── app/               # the web layer, one module per feature (Flask blueprints)
    │   ├── core.py        #   shared: file paths, background jobs, JSON parsing
    │   ├── contracts.py   #   /api/contracts routes + helpers
    │   ├── budget.py      #   /api/budget routes + helpers
    │   ├── guests.py      #   /api/guests routes + helpers
    │   └── overview.py    #   /api/overview (home dashboard)
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

## Next steps

1. ~~Deploy to Render~~ — done; live instance running. Push redeploys it.
2. MCP vs no-MCP A/B test (homework Part 3): trajectory logging + Peekaboo screenshot
   (no MCP) vs GitHub MCP line-count, then compare.
3. A scored eval harness (still deferred).

Backlog: capstone demo video (due Wed Jul 8, 10:00) — the before/after verifier story is
the centerpiece; vendor comparison with a reasoned recommendation; scheduled autonomous
brief (WS4); measure the lessons-loop effect with the eval suite.

## How to run it

```bash
cd vow-app
pip install -r requirements.txt
python -m agent.harness "What skills do you have?"
```
