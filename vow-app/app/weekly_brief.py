"""Weekly brief: cross-feature triage over budget + contracts + guests."""

from datetime import date

from flask import Blueprint, jsonify, send_from_directory

from agent.harness import AgentHarness
from .core import PUBLIC_DIR, parse_agent_json, rate_limit, run_job

weekly_brief_bp = Blueprint("weekly_brief", __name__)


@weekly_brief_bp.get("/weekly-brief")
def weekly_brief_page():
    return send_from_directory(str(PUBLIC_DIR), "weekly-brief.html")


@weekly_brief_bp.post("/api/weekly-brief/analyze")
@rate_limit()
def analyze_weekly_brief():
    today = date.today().isoformat()

    def task(on_event):
        # No data in the prompt: the agent reads budget, contracts, and guests itself.
        harness = AgentHarness(verbose=False, on_event=on_event)
        answer = harness.run(
            f"Today is {today}. Give the couple their weekly brief: what needs their "
            f"attention right now? Read the budget, contracts, and guest data. "
            f"Respond with ONLY the JSON object defined in the weekly-brief skill — "
            f"no prose, no markdown, no headings."
        )
        return {"analysis": parse_agent_json(answer),
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})
