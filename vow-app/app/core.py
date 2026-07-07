"""Shared building blocks used by every feature: file paths, the background-job
runner, the live-progress job endpoint, and the tolerant agent-JSON parser.

Nothing in here imports the feature blueprints, so there are no import cycles."""

import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, request

# vow-app/  (this file lives in vow-app/app/, so go up two levels)
BASE = Path(__file__).resolve().parent.parent
# Data dir is overridable via VOW_DATA_DIR so a host (e.g. a Render persistent
# disk) can keep the couple's data outside the deployed code. Defaults to data/.
DATA_DIR = Path(os.environ.get("VOW_DATA_DIR", BASE / "data"))
SKILLS_DIR = BASE / "skills"
PUBLIC_DIR = BASE / "public"

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
    # Generic fallback (not tied to any one skill's shape): every render guards
    # missing fields, and shows `summary`, so the raw answer still surfaces.
    return {"summary": text,
            "note": "agent returned non-JSON; raw answer shown in summary"}


# ---------- background jobs (live agent progress) ----------
# NOTE: in-memory + threads — fine for a single local/long-running server,
# revisit for serverless deployment.
JOBS = {}


def run_job(task_fn) -> str:
    import storage
    job_id = uuid.uuid4().hex[:8]
    # Capture the requesting couple: the worker thread has no request context,
    # and contextvars don't cross threads on their own.
    couple = storage.current_couple()
    JOBS[job_id] = {"events": [], "done": False, "result": None, "error": None,
                    "couple": couple}

    def work():
        storage.set_couple(couple)
        try:
            JOBS[job_id]["result"] = task_fn(lambda e: JOBS[job_id]["events"].append(e))
        except Exception as e:  # surfaced to the UI, never a hung page
            JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return job_id


# ---------- rate limiting (abuse / cost control on public endpoints) ----------
# The agent endpoints each spend real OpenAI money, and the app is publicly
# deployed, so an open analyze endpoint is a way to burn the API key. This is a
# small in-memory sliding-window limiter per client IP — matching the app's
# existing single-worker, in-memory design (see JOBS above). For multi-worker or
# multi-instance hosting, swap in a shared store (e.g. Redis).
#
# Defaults are overridable via env so the live instance can be tuned without a
# code change.
RATE_LIMIT_CALLS = int(os.environ.get("VOW_RATE_LIMIT_CALLS", 5))
RATE_LIMIT_WINDOW = int(os.environ.get("VOW_RATE_LIMIT_WINDOW", 60))  # seconds

_CALL_TIMES = defaultdict(list)  # ip -> [timestamps]
_RATE_LOCK = threading.Lock()


def _client_ip() -> str:
    # Honor the first hop in X-Forwarded-For (Render/most PaaS sit behind a proxy).
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


def rate_limit(max_calls: int = RATE_LIMIT_CALLS, window: int = RATE_LIMIT_WINDOW):
    """Decorator: allow `max_calls` per `window` seconds per client IP, else 429."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ip = _client_ip()
            now = time.time()
            with _RATE_LOCK:
                recent = [t for t in _CALL_TIMES[ip] if now - t < window]
                if len(recent) >= max_calls:
                    retry = int(window - (now - recent[0])) + 1
                    _CALL_TIMES[ip] = recent
                    resp = jsonify({
                        "error": f"Too many requests. Try again in ~{retry}s.",
                        "retry_after": retry,
                    })
                    resp.headers["Retry-After"] = str(retry)
                    return resp, 429
                recent.append(now)
                _CALL_TIMES[ip] = recent
            return fn(*args, **kwargs)
        return wrapper
    return decorator


core_bp = Blueprint("core", __name__)


@core_bp.get("/api/jobs/<job_id>")
def job_status(job_id):
    import storage
    job = JOBS.get(job_id)
    # A couple can only poll their own jobs (job ids are random, but cheap to enforce).
    if job is None or job.get("couple") not in (None, storage.current_couple()):
        return jsonify({"error": "Unknown job."}), 404
    return jsonify({k: v for k, v in job.items() if k != "couple"})
