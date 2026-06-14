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
GUESTS_PATH = BASE / "data" / "guests.json"

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


# ---------- guests ----------

DEFAULT_GUEST_SETTINGS = {
    "currency": "USD", "venue_capacity": 0, "catering_per_head": 0,
    "rsvp_deadline": "", "wedding_date": "",
}
RSVP_STATES = {"confirmed", "declined", "pending", "no_response"}


def load_guests():
    if GUESTS_PATH.exists():
        data = json.loads(GUESTS_PATH.read_text())
        data.setdefault("settings", dict(DEFAULT_GUEST_SETTINGS))
        data.setdefault("households", [])
        return data
    return {"settings": dict(DEFAULT_GUEST_SETTINGS), "households": []}


def save_guests(guests):
    GUESTS_PATH.parent.mkdir(exist_ok=True)
    GUESTS_PATH.write_text(json.dumps(guests, indent=2))


@app.get("/guests")
def guests_page():
    return send_from_directory(app.static_folder, "guests.html")


@app.get("/api/guests")
def get_guests():
    return jsonify(load_guests())


@app.put("/api/guests/settings")
def update_guest_settings():
    data = request.get_json(force=True)
    guests = load_guests()
    s = guests["settings"]

    def num(key):  # guardrail: non-negative numbers only
        try:
            return max(0, int(float(data.get(key, s.get(key, 0)) or 0)))
        except (TypeError, ValueError):
            return s.get(key, 0)

    s["venue_capacity"] = num("venue_capacity")
    s["catering_per_head"] = num("catering_per_head")
    if "currency" in data:
        s["currency"] = str(data.get("currency") or "USD")[:3].upper()
    if "rsvp_deadline" in data:
        s["rsvp_deadline"] = str(data.get("rsvp_deadline") or "")[:20]
    if "wedding_date" in data:
        s["wedding_date"] = str(data.get("wedding_date") or "")[:20]
    save_guests(guests)
    return jsonify(guests)


@app.post("/api/guests/households")
def add_household():
    data = request.get_json(force=True)
    name = str(data.get("household", "")).strip()[:100]
    if not name:
        return jsonify({"error": "Household name is required."}), 400

    try:
        party_size = max(1, int(float(data.get("party_size") or 1)))
    except (TypeError, ValueError):
        party_size = 1
    rsvp = str(data.get("rsvp", "pending")).strip()
    if rsvp not in RSVP_STATES:
        rsvp = "pending"
    try:
        attending = max(0, int(float(data.get("attending_count") or 0)))
    except (TypeError, ValueError):
        attending = 0
    # Only confirmed households have a real attending count.
    if rsvp != "confirmed":
        attending = 0
    attending = min(attending, party_size)

    side = str(data.get("side", "")).strip()
    if side not in {"partner_a", "partner_b"}:
        side = "partner_a"
    dietary = data.get("dietary", [])
    if isinstance(dietary, str):
        dietary = [d.strip() for d in dietary.split(",") if d.strip()]

    household = {
        "id": uuid.uuid4().hex[:8],
        "household": name,
        "side": side,
        "party_size": party_size,
        "rsvp": rsvp,
        "attending_count": attending,
        "plus_one_allowed": bool(data.get("plus_one_allowed", False)),
        "plus_one_name": str(data.get("plus_one_name", "")).strip()[:100],
        "meals": data.get("meals") if isinstance(data.get("meals"), dict) else {},
        "dietary": [str(d)[:60] for d in dietary][:10],
        "notes": str(data.get("notes", "")).strip()[:300],
    }
    guests = load_guests()
    guests["households"].append(household)
    save_guests(guests)
    return jsonify(household)


@app.delete("/api/guests/households/<household_id>")
def delete_household(household_id):
    guests = load_guests()
    before = len(guests["households"])
    guests["households"] = [h for h in guests["households"] if h["id"] != household_id]
    save_guests(guests)
    return jsonify({"ok": True, "removed": before - len(guests["households"])})


@app.post("/api/guests/analyze")
def analyze_guests():
    guests = load_guests()
    if not guests["households"]:
        return jsonify({"error": "Add some guests first."}), 400

    def task(on_event):
        # No data in the prompt: the agent fetches it itself via read_data("guests").
        harness = AgentHarness(verbose=False, on_event=on_event)
        answer = harness.run(
            "The couple wants their guest list reviewed: project the final headcount, "
            "check it against the venue capacity and catering per-head budget, and tell "
            "them who to follow up with. Read the guest data."
        )
        return {"analysis": parse_agent_json(answer),
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})


SAMPLE_GUEST_SETTINGS = {
    "currency": "USD", "venue_capacity": 40, "catering_per_head": 145,
    "rsvp_deadline": "2026-08-15", "wedding_date": "2026-09-26",
}
SAMPLE_HOUSEHOLDS = [
    {"household": "Okonkwo Family", "side": "partner_a", "party_size": 4, "rsvp": "confirmed", "attending_count": 4, "meals": {"chicken": 2, "fish": 1, "veg": 1}, "dietary": ["severe nut allergy"]},
    {"household": "Rossi Family", "side": "partner_a", "party_size": 3, "rsvp": "confirmed", "attending_count": 3, "meals": {"chicken": 1, "fish": 2}, "dietary": []},
    {"household": "Chen", "side": "partner_b", "party_size": 2, "rsvp": "confirmed", "attending_count": 2, "meals": {"veg": 2}, "dietary": ["vegan"]},
    {"household": "Nguyen", "side": "partner_b", "party_size": 2, "rsvp": "confirmed", "attending_count": 2, "meals": {}, "dietary": [], "notes": "confirmed verbally, meal choices not submitted"},
    {"household": "Patel Family", "side": "partner_a", "party_size": 5, "rsvp": "confirmed", "attending_count": 5, "meals": {"chicken": 3, "fish": 1}, "dietary": ["halal"]},
    {"household": "Smith", "side": "partner_a", "party_size": 1, "rsvp": "confirmed", "attending_count": 1, "plus_one_allowed": True, "meals": {"chicken": 1}, "notes": "plus-one offered; guest hasn't said if bringing anyone"},
    {"household": "Garcia Family", "side": "partner_b", "party_size": 6, "rsvp": "pending", "attending_count": 0, "notes": "large family, likely most will attend"},
    {"household": "Andersson", "side": "partner_b", "party_size": 4, "rsvp": "pending", "attending_count": 0},
    {"household": "Becker", "side": "partner_b", "party_size": 3, "rsvp": "no_response", "attending_count": 0, "notes": "past RSVP deadline, no contact"},
    {"household": "Thompson", "side": "partner_a", "party_size": 2, "rsvp": "declined", "attending_count": 0},
]


@app.post("/api/guests/load-sample")
def load_sample_guests():
    guests = load_guests()
    if guests["households"]:
        return jsonify({"error": "Guest list already has households."}), 400
    guests["settings"] = dict(SAMPLE_GUEST_SETTINGS)
    for h in SAMPLE_HOUSEHOLDS:
        guests["households"].append({
            "id": uuid.uuid4().hex[:8], "plus_one_allowed": False, "plus_one_name": "",
            "meals": {}, "dietary": [], "notes": "", **h})
    save_guests(guests)
    return jsonify(guests)


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

    guests = load_guests()
    households = guests["households"]
    confirmed_people = sum(h.get("attending_count", 0) for h in households
                           if h.get("rsvp") == "confirmed")
    outstanding = sum(1 for h in households if h.get("rsvp") in ("pending", "no_response"))
    guests_card = {
        "household_count": len(households),
        "invited_people": sum(h.get("party_size", 0) for h in households),
        "confirmed_people": confirmed_people,
        "outstanding_households": outstanding,
        "venue_capacity": guests["settings"].get("venue_capacity", 0),
    }

    return jsonify({
        "budget": {"total_budget": budget["total_budget"], "currency": budget.get("currency", "USD"),
                   "committed": committed, "paid": paid, "item_count": len(budget["items"])},
        "latest_contract": latest_card,
        "contract_count": len(contracts),
        "guests": guests_card,
        "skills": skills,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
