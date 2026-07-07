#!/usr/bin/env python3
"""Vow MCP server — the product's tools, exposed to a headless agent (WS4).

Two tools, stdio transport:

  • get_wedding_status  — read-only JSON snapshot of the couple's wedding
    (budget rollup, RSVP counts, contract red flags, seating conflicts,
    weeks to wedding). No LLM call, costs $0. The fallback data source.

  • run_weekly_brief    — triggers Vow's OWN orchestrator (three specialist
    sub-agents in parallel + a verifier pass + one merge call — exactly what
    the web endpoint /api/weekly-brief/analyze runs), caches the result to
    the `brief` document so the couple's home dashboard shows it instantly
    (a self-notification), and returns the brief JSON.

SECURITY — why the key lives here, not in the agent:
  OPENAI_API_KEY (and any Supabase creds) are read from THIS server's process
  environment / vow-app/.env. The headless agent never sees them — the repo's
  deny rules block it from reading .env, and its only path to the wedding data
  is through these two tools. Credential isolation by design: the protection
  doesn't depend on the agent behaving.

REGISTRATION (from the WeddingOS repo root):
  claude mcp add --scope user --transport stdio vow -- \
    "$(pwd)/vow-app/venv/bin/python3" "$(pwd)/vow-app/mcp_server.py"
  claude mcp list        # tools appear as mcp__vow__<tool>

REQUIRES: pip install mcp   (plus vow-app/requirements.txt)
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# Load vow-app/.env into this server's env (OPENAI_API_KEY, VOW_DATA_DIR,
# optional SUPABASE_*). Optional: real env vars win; missing .env is fine.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE / ".env")
except ImportError:
    pass

import storage  # noqa: E402  (needs BASE on sys.path)

# The `mcp` package is only needed to SERVE. The tool functions stay importable
# without it, so the offline test suite doesn't grow a dependency.
try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("vow")
    _tool = mcp.tool()
except ImportError:  # pragma: no cover - exercised only without `mcp` installed
    mcp = None
    _tool = lambda f: f  # noqa: E731

# Injectable for offline tests: swap in a fake orchestrator, no network.
_orchestrator_factory = None


def _make_orchestrator():
    if _orchestrator_factory is not None:
        return _orchestrator_factory()
    from agent.orchestrator import WeeklyBriefOrchestrator

    return WeeklyBriefOrchestrator()


def _days_to(date_str):
    try:
        return (date.fromisoformat(date_str) - date.today()).days
    except (TypeError, ValueError):
        return None


@_tool
def get_wedding_status() -> str:
    """Read-only snapshot of the couple's wedding as JSON: date & countdown,
    budget rollup, guest/RSVP counts, contract red-flag summary, seating
    conflicts. Deterministic — no model call, no writes."""
    profile = storage.load("profile", {}) or {}
    budget = storage.load("budget", {}) or {}
    guests = storage.load("guests", {}) or {}
    contracts = storage.load("contracts", []) or []
    brief = storage.load("brief", {}) or {}

    items = budget.get("items", [])
    committed = sum((i.get("contracted") or i.get("estimated") or 0) for i in items)
    paid = sum(i.get("paid") or 0 for i in items)

    households = guests.get("households", [])
    by_rsvp = {}
    for h in households:
        by_rsvp[h.get("rsvp", "unknown")] = by_rsvp.get(h.get("rsvp", "unknown"), 0) + 1
    confirmed_people = sum(
        h.get("attending_count", 0) for h in households if h.get("rsvp") == "confirmed"
    )

    # Deterministic conflict engine — same code path the UI and the weekly-brief
    # merge use. Import is lazy so a missing optional dep can't break the snapshot.
    try:
        from app.seating import seating_conflicts

        conflicts = seating_conflicts()
    except Exception as exc:  # pragma: no cover - defensive
        conflicts = [{"error": f"seating conflicts unavailable: {exc}"}]

    wedding_date = (guests.get("settings", {}) or {}).get("wedding_date") or profile.get(
        "wedding_date"
    )
    days = _days_to(wedding_date)

    return json.dumps(
        {
            "as_of": date.today().isoformat(),
            "couple": " & ".join(
                n for n in (profile.get("partner_a"), profile.get("partner_b")) if n
            ),
            "venue": profile.get("venue"),
            "wedding_date": wedding_date,
            "days_to_wedding": days,
            "weeks_to_wedding": max(0, days // 7) if days is not None else None,
            "budget": {
                "currency": budget.get("currency"),
                "total_budget": budget.get("total_budget"),
                "committed": committed,
                "paid": paid,
                "over_budget_by": max(0, committed - (budget.get("total_budget") or 0)),
                "line_items": len(items),
            },
            "guests": {
                "households": len(households),
                "invited_people": sum(h.get("party_size", 0) for h in households),
                "confirmed_people": confirmed_people,
                "households_by_rsvp": by_rsvp,
            },
            "contracts": [
                {
                    "vendor": c.get("vendor"),
                    "red_flags": len((c.get("analysis") or {}).get("red_flags", [])),
                }
                for c in contracts
            ],
            "seating_conflicts": conflicts,
            "latest_brief_generated_at": brief.get("generated_at"),
        }
    )


@_tool
def run_weekly_brief() -> str:
    """Run Vow's weekly-brief orchestration (3 specialist sub-agents in parallel
    + verifier + merge — the product's own agent, with its own cost caps) and
    return the brief JSON: {analysis, cost_usd, agents, generated_at}. Also
    caches it so the couple sees it on their home dashboard. On failure returns
    {"error": ...} — fall back to get_wedding_status."""
    try:
        try:
            from agent.outcomes import weekly_extra_facts

            extra = weekly_extra_facts()
        except Exception:  # pragma: no cover - defensive
            extra = None

        today = date.today().isoformat()
        result = _make_orchestrator().run(today, extra_facts=extra)
        try:
            from agent.outcomes import record_brief_run

            record_brief_run(result, today)  # follow-through memory
        except Exception:  # pragma: no cover - defensive
            pass
        result = dict(
            result, generated_at=datetime.now().isoformat(timespec="seconds")
        )
        storage.save("brief", result)  # home dashboard reads this back instantly
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


if __name__ == "__main__":
    if mcp is None:
        sys.exit("The `mcp` package is required to serve: pip install mcp")
    mcp.run(transport="stdio")
