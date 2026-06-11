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
    """The skill says JSON-only, but models sometimes add fences — strip and parse."""
    cleaned = re.sub(r"^```(json)?|```$", "", (answer or "").strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"summary": answer, "red_flags": [], "missing_protections": [],
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
