"""Weekly brief: cross-feature triage over budget + contracts + guests.

The latest brief is cached to disk (data/brief.json, code-owned — not one of
the agent's writable datasets) so the home page can show the to-do list
instantly without spending an agent run on every visit."""

import json
from datetime import date, datetime

from flask import Blueprint, jsonify, send_from_directory

from agent.orchestrator import WeeklyBriefOrchestrator
from .core import DATA_DIR, PUBLIC_DIR, rate_limit, run_job

weekly_brief_bp = Blueprint("weekly_brief", __name__)

BRIEF_PATH = DATA_DIR / "brief.json"


def _save_latest(result: dict):
    BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRIEF_PATH.write_text(json.dumps(dict(
        result, generated_at=datetime.now().isoformat(timespec="seconds")), indent=2))


@weekly_brief_bp.get("/weekly-brief")
def weekly_brief_page():
    return send_from_directory(str(PUBLIC_DIR), "weekly-brief.html")


@weekly_brief_bp.get("/api/weekly-brief/latest")
def latest_brief():
    if not BRIEF_PATH.exists():
        return jsonify({"exists": False})
    try:
        cached = json.loads(BRIEF_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return jsonify({"exists": False})
    return jsonify(dict(cached, exists=True))


@weekly_brief_bp.post("/api/weekly-brief/analyze")
@rate_limit()
def analyze_weekly_brief():
    today = date.today().isoformat()

    def task(on_event):
        # Orchestrated run (WS5): three parallel specialist sub-agents in isolated
        # contexts + a verifier pass each + one merge call. The orchestrator reads
        # the data itself; response keeps the old {analysis, cost_usd} shape and
        # adds a per-agent breakdown. Seating conflicts are computed in code and
        # handed to the merge as trusted facts.
        from .seating import seating_conflicts
        orch = WeeklyBriefOrchestrator(on_event=on_event)
        result = orch.run(today, extra_facts={
            "seating_conflicts": seating_conflicts(),
            "note": "seating_conflicts is computed by code from the live seating "
                    "chart — include unresolved ones as action items (area: guests).",
        })
        _save_latest(result)  # home page reads this back instantly
        return result

    return jsonify({"job_id": run_job(task)})
