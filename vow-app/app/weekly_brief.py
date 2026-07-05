"""Weekly brief: cross-feature triage over budget + contracts + guests."""

from datetime import date

from flask import Blueprint, jsonify, send_from_directory

from agent.orchestrator import WeeklyBriefOrchestrator
from .core import PUBLIC_DIR, rate_limit, run_job

weekly_brief_bp = Blueprint("weekly_brief", __name__)


@weekly_brief_bp.get("/weekly-brief")
def weekly_brief_page():
    return send_from_directory(str(PUBLIC_DIR), "weekly-brief.html")


@weekly_brief_bp.post("/api/weekly-brief/analyze")
@rate_limit()
def analyze_weekly_brief():
    today = date.today().isoformat()

    def task(on_event):
        # Orchestrated run (WS5): three parallel specialist sub-agents in isolated
        # contexts + a verifier pass each + one merge call. The orchestrator reads
        # the data itself; response keeps the old {analysis, cost_usd} shape and
        # adds a per-agent breakdown.
        orch = WeeklyBriefOrchestrator(on_event=on_event)
        return orch.run(today)

    return jsonify({"job_id": run_job(task)})
