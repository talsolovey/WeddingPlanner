"""Contract analyzer: upload a vendor PDF, the agent flags risks."""

import uuid
from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, request, send_from_directory
from pypdf import PdfReader

import storage
from agent.harness import AgentHarness
from agent.guard import wrap_untrusted
from .core import (MAX_PDF_MB, MAX_TEXT_CHARS, PUBLIC_DIR,
                   ensure_agent_json, rate_limit, run_job)

contracts_bp = Blueprint("contracts", __name__)


def load_contracts() -> list:
    return storage.load("contracts", [])


def save_contracts(contracts: list):
    storage.save("contracts", contracts)


def _extract_pdf_text(raw: bytes):
    reader = PdfReader(BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _analyze_contract_task(vendor: str, filename: str, text: str, truncated: bool,
                           replace_vendor: str = None):
    """Runs inside a job thread; set replace_vendor to overwrite a previous record
    (used by the sample button so repeat clicks don't pile up duplicates)."""
    def task(on_event):
        harness = AgentHarness(verbose=False, on_event=on_event)
        # The contract text is untrusted: a poisoned PDF could try to give the
        # agent instructions. wrap_untrusted fences it as data and flags any
        # injection-looking content for the model. The JSON instruction comes
        # AFTER the (long) contract text — instructions placed before 40k chars
        # of content get forgotten; recency wins.
        prompt = (
            f"A couple uploaded a contract from their vendor '{vendor}' and wants it "
            f"reviewed before signing.{' (Text truncated due to length.)' if truncated else ''}\n"
            f"{wrap_untrusted(text, source=f'{vendor} contract PDF')}\n\n"
            f"Now produce the review. Respond with ONLY the JSON object defined in "
            f"the contract-analyzer skill's Output format — no prose before or "
            f"after it, no markdown fences, no headings. Start your reply with '{{'."
        )
        answer = harness.run(prompt)
        analysis = ensure_agent_json(answer, skill="contract-analyzer",
                                     on_event=on_event)
        record = {
            "id": uuid.uuid4().hex[:8],
            "vendor": vendor,
            "filename": filename,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            "analysis": analysis,                         # metadata + verdict only,
            "cost_usd": round(harness.last_run_cost, 4),  # NOT the full contract text
        }
        contracts = load_contracts()
        if replace_vendor:
            contracts = [c for c in contracts if c["vendor"] != replace_vendor]
        contracts.append(record)
        save_contracts(contracts)
        return record
    return task


@contracts_bp.get("/contracts")
def contracts_page():
    return send_from_directory(str(PUBLIC_DIR), "contracts.html")


@contracts_bp.get("/api/contracts")
def list_contracts():
    return jsonify(load_contracts())


@contracts_bp.post("/api/contracts/analyze")
@rate_limit()
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


@contracts_bp.delete("/api/contracts/<contract_id>")
def delete_contract(contract_id):
    contracts = load_contracts()
    before = len(contracts)
    contracts = [c for c in contracts if c["id"] != contract_id]
    save_contracts(contracts)
    return jsonify({"ok": True, "removed": before - len(contracts)})
