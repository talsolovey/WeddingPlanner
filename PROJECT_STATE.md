# PROJECT_STATE — WeddingOS / Vow

> A plain-language summary of where the project stands, updated after every step.
> Last updated: 2026-06-11 (Step 3: budget tracker + forecast working end to end).

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
    ├── agent/             # the "brain": talks to GPT-4o, picks tools, loops until done
    ├── skills/            # instruction files the agent reads to know HOW to do a job
    ├── data/              # the couple's wedding data (budget, vendors...) as JSON files
    └── logs/              # every API call recorded: tokens used, cost in $, tools called
```

## How it works (the short version)

I give the agent a task ("analyze this contract"). It first asks itself *"which of my
skills fits this job?"*, reads that skill's instructions, reads the relevant data, works
through the task, and answers with its reasoning. When it learns something useful along
the way, it writes a note into the skill's `LESSONS.md` file — so next time it's smarter.
That's the self-improvement loop.

Safety rails: it can only touch 5 approved data files, it stops after 10 thinking rounds,
and every call is logged with its dollar cost.

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

## Done so far

| Step | What | Status |
|---|---|---|
| Week 1 | Landing page, built 3 ways | ✅ done |
| Step 1 | Agent brain (harness) with skills support | ✅ tested — a run costs ~$0.0025 |
| Step 2 | Contract analyzer: upload a PDF → flags risks by severity | ✅ tested end to end (~$0.013/run); caught 4 of 8 planted traps — improving this in the evals step |
| Step 2b | Shared UI theme matching the landing page (cream/rose/serif) | ✅ all app pages will reuse `public/styles.css` |
| Step 3 | Budget tracker (add items, totals) + Vow's final-cost forecast | ✅ tested end to end (~$0.02/run); agent cross-referenced contract data unprompted |
| Step 3b | UX round: home dashboard, sample-data buttons, toasts/undo; analyses run in the background with a quiet loading state | ✅ tested |

## Decisions made (and why)

- **Python + GPT-4o** — my choice; matches the workshop harness I already built.
- **Reused my workshop harness** instead of writing a new one — it already had cost
  tracking and conversation compression built in.
- **Deploy on Vercel** later. Open question: where data lives once deployed (Vercel's
  servers don't keep files between requests).

## Next steps

1. Vendor comparison with a reasoned recommendation
2. Weekly brief — "here's what needs your attention this week"

Then: tests that prove the agent catches known problems (evals), deployment, and a list
of moments the agent struggled or surprised me (for class).

## How to run it

```bash
cd vow-app
pip install -r requirements.txt
python -m agent.harness "What skills do you have?"
```
