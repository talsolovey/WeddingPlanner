"""Weekly brief: cross-feature triage over budget + contracts + guests.

The latest brief is cached to disk (data/brief.json, code-owned — not one of
the agent's writable datasets) so the home page can show the to-do list
instantly without spending an agent run on every visit."""

from datetime import date, datetime

from flask import Blueprint, jsonify, send_from_directory

import storage
from agent.orchestrator import WeeklyBriefOrchestrator
from .core import PUBLIC_DIR, rate_limit, run_job

weekly_brief_bp = Blueprint("weekly_brief", __name__)


def _save_latest(result: dict):
    storage.save("brief", dict(
        result, generated_at=datetime.now().isoformat(timespec="seconds")))


@weekly_brief_bp.get("/weekly-brief")
def weekly_brief_page():
    return send_from_directory(str(PUBLIC_DIR), "weekly-brief.html")


@weekly_brief_bp.get("/api/weekly-brief/latest")
def latest_brief():
    cached = storage.load("brief")
    if cached is None:
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
        # adds a per-agent breakdown. computed_facts (seating conflicts, nudge
        # outcomes, repeatedly-ignored advice) are code-computed trusted facts.
        from agent.outcomes import record_brief_run, weekly_extra_facts
        orch = WeeklyBriefOrchestrator(on_event=on_event)
        result = orch.run(today, extra_facts=weekly_extra_facts())
        record_brief_run(result, today)  # remember what we advised (follow-through)
        _save_latest(result)  # home page reads this back instantly
        return result

    return jsonify({"job_id": run_job(task)})
