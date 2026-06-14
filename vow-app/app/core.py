"""Shared building blocks used by every feature: file paths, the background-job
runner, the live-progress job endpoint, and the tolerant agent-JSON parser.

Nothing in here imports the feature blueprints, so there are no import cycles."""

import json
import os
import re
import threading
import uuid
from pathlib import Path

from flask import Blueprint, jsonify

# vow-app/  (this file lives in vow-app/app/, so go up two levels)
BASE = Path(__file__).resolve().parent.parent
# Data dir is overridable via VOW_DATA_DIR so a host (e.g. a Render persistent
# disk) can keep the couple's data outside the deployed code. Defaults to data/.
DATA_DIR = Path(os.environ.get("VOW_DATA_DIR", BASE / "data"))
SKILLS_DIR = BASE / "skills"
PUBLIC_DIR = BASE / "public"

CONTRACTS_PATH = DATA_DIR / "contracts.json"
BUDGET_PATH = DATA_DIR / "budget.json"
GUESTS_PATH = DATA_DIR / "guests.json"

MAX_PDF_MB = 10          # guardrail: upload size
MAX_TEXT_CHARS = 40_000  # guardrail: token burn


def parse_agent_json(answer: str):
    """The skill says JSON-only, but models add prose and fences anyway —
    try direct parse, then a fenced block, then the outermost brace span."""
    text = (answer or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1).strip())
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return {"summary": text, "red_flags": [], "missing_protections": [],
            "payment_summary": "", "questions_for_vendor": [],
            "note": "agent returned non-JSON; raw answer shown in summary"}


# ---------- background jobs (live agent progress) ----------
# NOTE: in-memory + threads — fine for a single local/long-running server,
# revisit for serverless deployment.
JOBS = {}


def run_job(task_fn) -> str:
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"events": [], "done": False, "result": None, "error": None}

    def work():
        try:
            JOBS[job_id]["result"] = task_fn(lambda e: JOBS[job_id]["events"].append(e))
        except Exception as e:  # surfaced to the UI, never a hung page
            JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return job_id


core_bp = Blueprint("core", __name__)


@core_bp.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404
    return jsonify(job)
