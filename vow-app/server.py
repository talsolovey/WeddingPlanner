import json
import re
import threading
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from pypdf import PdfReader

from agent.harness import AgentHarness

BASE = Path(__file__).resolve().parent
CONTRACTS_PATH = BASE / "data" / "contracts.json"
BUDGET_PATH = BASE / "data" / "budget.json"

MAX_PDF_MB = 10          # guardrail: upload size
MAX_TEXT_CHARS = 40_000  # guardrail: token burn

app = Flask(__name__, static_folder="public", static_url_path="")


def load_contracts() -> list:
    if CONTRACTS_PATH.exists():
        return json.loads(CONTRACTS_PATH.read_text())
    return []


def save_contracts(contracts: list):
    CONTRACTS_PATH.parent.mkdir(exist_ok=True)
    CONTRACTS_PATH.write_text(json.dumps(contracts, indent=2))


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
# revisit for serverless deployment (Step 8).
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


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Unknown job."}), 404
    return jsonify(job)


# ---------- pages ----------

@app.get("/")
def home_page():
    return send_from_directory(app.static_folder, "home.html")


@app.get("/contracts")
def contracts_page():
    return send_from_directory(app.static_folder, "contracts.html")


# ---------- contracts ----------

@app.get("/api/contracts")
def list_contracts():
    return jsonify(load_contracts())


def _analyze_contract_task(vendor: str, filename: str, text: str, truncated: bool,
                           replace_vendor: str = None):
    """Runs inside a job thread; set replace_vendor to overwrite a previous record
    (used by the sample button so repeat clicks don't pile up duplicates)."""
    def task(on_event):
        harness = AgentHarness(verbose=False, on_event=on_event)
        prompt = (
            f"A couple uploaded a contract from their vendor '{vendor}' and wants it "
            f"reviewed before signing.{' (Text truncated due to length.)' if truncated else ''}\n"
            f"Contract text:\n---\n{text}\n---"
        )
        answer = harness.run(prompt)
        record = {
            "id": uuid.uuid4().hex[:8],
            "vendor": vendor,
            "filename": filename,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            "analysis": parse_agent_json(answer),         # metadata + verdict only,
            "cost_usd": round(harness.last_run_cost, 4),  # NOT the full contract text
        }
        contracts = load_contracts()
        if replace_vendor:
            contracts = [c for c in contracts if c["vendor"] != replace_vendor]
        contracts.append(record)
        save_contracts(contracts)
        return record
    return task


def _extract_pdf_text(raw: bytes):
    reader = PdfReader(BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@app.post("/api/contracts/analyze")
def analyze_contract():
    file = request.files.get("file")
    vendor = (request.form.get("vendor") or "Unknown vendor").strip()[:100]

    # --- guardrails: file type + size ---
    if file is None or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file."}), 400
    raw = file.read()
    if len(raw) > MAX_PDF_MB * 1024 * 1024:
        return jsonify({"error": f"PDF too large (max {MAX_PDF_MB} MB)."}), 400

    # --- deterministic plumbing: extract text ---
    try:
        text = _extract_pdf_text(raw)
    except Exception:
        return jsonify({"error": "Could not read this PDF."}), 400
    if len(text.strip()) < 100:
        return jsonify({"error": "No readable text in this PDF (is it scanned?)."}), 400
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]

    job_id = run_job(_analyze_contract_task(vendor, file.filename, text, truncated))
    return jsonify({"job_id": job_id})


@app.post("/api/contracts/analyze-sample")
def analyze_sample_contract():
    fixture = BASE / "evals" / "fixtures" / "golden_hour_photography.pdf"
    if not fixture.exists():
        return jsonify({"error": "Sample contract not found."}), 404
    text = _extract_pdf_text(fixture.read_bytes())
    job_id = run_job(_analyze_contract_task(
        "Golden Hour Studios (sample)", fixture.name, text, False,
        replace_vendor="Golden Hour Studios (sample)"))
    return jsonify({"job_id": job_id})


@app.delete("/api/contracts/<contract_id>")
def delete_contract(contract_id):
    contracts = load_contracts()
    before = len(contracts)
    contracts = [c for c in contracts if c["id"] != contract_id]
    save_contracts(contracts)
    return jsonify({"ok": True, "removed": before - len(contracts)})


# ---------- budget ----------

def load_budget():
    if BUDGET_PATH.exists():
        return json.loads(BUDGET_PATH.read_text())
    return {"currency": "USD", "total_budget": 0, "items": []}


def save_budget(budget):
    BUDGET_PATH.parent.mkdir(exist_ok=True)
    BUDGET_PATH.write_text(json.dumps(budget, indent=2))


@app.get("/budget")
def budget_page():
    return send_from_directory(app.static_folder, "budget.html")


@app.get("/api/budget")
def get_budget():
    return jsonify(load_budget())


@app.put("/api/budget/settings")
def update_budget_settings():
    data = request.get_json(force=True)
    budget = load_budget()
    try:
        budget["total_budget"] = max(0.0, float(data.get("total_budget", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "total_budget must be a number."}), 400
    budget["currency"] = str(data.get("currency", budget.get("currency", "USD")))[:3].upper()
    save_budget(budget)
    return jsonify(budget)


@app.post("/api/budget/items")
def add_budget_item():
    data = request.get_json(force=True)
    category = str(data.get("category", "")).strip()[:60]
    if not category:
        return jsonify({"error": "Category is required."}), 400

    def num(k):  # guardrail: numbers only, no negatives
        try:
            return max(0.0, float(data.get(k) or 0))
        except (TypeError, ValueError):
            return 0.0

    item = {
        "id": uuid.uuid4().hex[:8],
        "category": category,
        "vendor": str(data.get("vendor", "")).strip()[:100],
        "estimated": num("estimated"),
        "contracted": num("contracted"),
        "paid": num("paid"),
        "due_before_wedding": bool(data.get("due_before_wedding", True)),
        "notes": str(data.get("notes", "")).strip()[:300],
    }
    budget = load_budget()
    budget["items"].append(item)
    save_budget(budget)
    return jsonify(item)


@app.delete("/api/budget/items/<item_id>")
def delete_budget_item(item_id):
    budget = load_budget()
    before = len(budget["items"])
    budget["items"] = [i for i in budget["items"] if i["id"] != item_id]
    save_budget(budget)
    return jsonify({"ok": True, "removed": before - len(budget["items"])})


@app.post("/api/budget/analyze")
def analyze_budget():
    budget = load_budget()
    if not budget["items"]:
        return jsonify({"error": "Add some budget items first."}), 400

    def task(on_event):
        # No data in the prompt: the agent must fetch it itself via read_data.
        harness = AgentHarness(verbose=False, on_event=on_event)
        answer = harness.run(
            "The couple wants their wedding budget reviewed: forecast the realistic final "
            "cost and flag risks. Read the budget data and any analyzed contracts."
        )
        return {"analysis": parse_agent_json(answer),
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})


SAMPLE_BUDGET_ITEMS = [
    {"category": "venue", "vendor": "Rosewood Manor", "estimated": 0, "contracted": 14000, "paid": 7000, "notes": "includes catering"},
    {"category": "photography", "vendor": "Golden Hour Studios", "estimated": 0, "contracted": 6800, "paid": 3400, "notes": ""},
    {"category": "flowers", "vendor": "Petal & Stem", "estimated": 9000, "contracted": 0, "paid": 0, "notes": "initial quote"},
    {"category": "music/DJ", "vendor": "", "estimated": 2500, "contracted": 0, "paid": 0, "notes": ""},
    {"category": "attire", "vendor": "", "estimated": 3000, "contracted": 0, "paid": 1200, "notes": "dress deposit paid"},
    {"category": "stationery", "vendor": "", "estimated": 800, "contracted": 0, "paid": 0, "notes": ""},
    {"category": "cake", "vendor": "Sweet Layers", "estimated": 900, "contracted": 0, "paid": 0, "notes": "per-slice pricing"},
    {"category": "rings", "vendor": "", "estimated": 2200, "contracted": 0, "paid": 0, "notes": ""},
]


@app.post("/api/budget/load-sample")
def load_sample_budget():
    budget = load_budget()
    if budget["items"]:
        return jsonify({"error": "Budget already has items."}), 400
    budget["total_budget"] = budget["total_budget"] or 40000
    for item in SAMPLE_BUDGET_ITEMS:
        budget["items"].append({"id": uuid.uuid4().hex[:8], "due_before_wedding": True, **item})
    save_budget(budget)
    return jsonify(budget)


# ---------- overview (home dashboard) ----------

@app.get("/api/overview")
def overview():
    budget = load_budget()
    contracts = load_contracts()
    committed = sum((i.get("contracted") or i.get("estimated") or 0) for i in budget["items"])
    paid = sum((i.get("paid") or 0) for i in budget["items"])

    latest = contracts[-1] if contracts else None
    latest_card = None
    if latest:
        flags = (latest.get("analysis") or {}).get("red_flags") or []
        latest_card = {
            "vendor": latest["vendor"],
            "uploaded_at": latest["uploaded_at"],
            "summary": (latest.get("analysis") or {}).get("summary", ""),
            "flag_counts": {
                sev: sum(1 for f in flags if f.get("severity") == sev)
                for sev in ("red", "yellow", "green")
            },
        }

    skills = []
    for path in sorted((BASE / "skills").glob("*/SKILL.md")):
        lessons_file = path.parent / "LESSONS.md"
        lesson_count = 0
        if lessons_file.exists():
            lesson_count = sum(1 for line in lessons_file.read_text().splitlines()
                               if line.strip().startswith("-"))
        skills.append({"name": path.parent.name, "lessons": lesson_count})

    return jsonify({
        "budget": {"total_budget": budget["total_budget"], "currency": budget.get("currency", "USD"),
                   "committed": committed, "paid": paid, "item_count": len(budget["items"])},
        "latest_contract": latest_card,
        "contract_count": len(contracts),
        "skills": skills,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
