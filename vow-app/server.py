import json
import re
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


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "contracts.html")


@app.get("/api/contracts")
def list_contracts():
    return jsonify(load_contracts())


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
        reader = PdfReader(BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return jsonify({"error": "Could not read this PDF."}), 400
    if len(text.strip()) < 100:
        return jsonify({"error": "No readable text in this PDF (is it scanned?)."}), 400
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]

    # --- the agent does the judging ---
    harness = AgentHarness(verbose=False)
    prompt = (
        f"A couple uploaded a contract from their vendor '{vendor}' and wants it "
        f"reviewed before signing.{' (Text truncated due to length.)' if truncated else ''}\n"
        f"Contract text:\n---\n{text}\n---"
    )
    answer = harness.run(prompt)
    analysis = parse_agent_json(answer)

    record = {
        "id": uuid.uuid4().hex[:8],
        "vendor": vendor,
        "filename": file.filename,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "analysis": analysis,                         # metadata + verdict only,
        "cost_usd": round(harness.last_run_cost, 4),  # NOT the full contract text
    }
    contracts = load_contracts()
    contracts.append(record)
    save_contracts(contracts)
    return jsonify(record)


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
    # No data in the prompt: the agent must fetch it itself via read_data.
    harness = AgentHarness(verbose=False)
    answer = harness.run(
        "The couple wants their wedding budget reviewed: forecast the realistic final "
        "cost and flag risks. Read the budget data and any analyzed contracts."
    )
    return jsonify({"analysis": parse_agent_json(answer),
                    "cost_usd": round(harness.last_run_cost, 4)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
