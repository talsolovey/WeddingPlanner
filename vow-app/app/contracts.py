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
                   parse_agent_json, rate_limit, run_job)

contracts_bp = Blueprint("contracts", __name__)


def load_contracts() -> list:
    return storage.load("contracts", [])


def save_contracts(contracts: list):
    storage.save("contracts", contracts)


def _extract_pdf_text(raw: bytes):
    reader = PdfReader(BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# The exact shape the UI renders (mirrors the contract-analyzer skill's
# "Output format" section) — used by the repair pass when a long real PDF
# pushes the model into prose despite the instructions.
_ANALYSIS_SCHEMA = """{
  "vendor_type": "photographer | caterer | venue | ...",
  "summary": "2-3 plain sentences: overall, is this contract fair?",
  "red_flags": [
    {"clause": "quoted or paraphrased clause", "issue": "why it hurts the couple",
     "severity": "red | yellow | green", "recommendation": "what to ask/change"}
  ],
  "missing_protections": ["protections a couple should ask to add"],
  "payment_summary": "deposit, schedule, and total exposure before the wedding day",
  "questions_for_vendor": ["specific questions before signing"]
}"""


def _is_unparsed(analysis: dict) -> bool:
    return "agent returned non-JSON" in str(analysis.get("note", ""))


def _repair_to_json(raw_answer: str, on_event=lambda e: None):
    """One cheap tool-free call that converts a prose analysis into the schema.
    Loses nothing the model already found; costs ~$0.002."""
    from .chat import llm_complete  # local import to avoid a cycle
    on_event("tidying the report format")
    system = (
        "You reformat a wedding-contract analysis into JSON. Preserve every "
        "finding, severity and recommendation from the analysis — do not add, "
        "drop, or soften anything. Respond with ONLY this JSON object, no "
        f"markdown fences, no commentary:\n{_ANALYSIS_SCHEMA}")
    return llm_complete(system, [{"role": "user", "content": raw_answer[:12000]}],
                        max_tokens=1500)


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
        analysis = parse_agent_json(answer)
        if _is_unparsed(analysis):
            # Long real PDFs still push some runs into prose; one repair call
            # converts the findings to the schema instead of showing raw text.
            try:
                analysis = parse_agent_json(_repair_to_json(answer, on_event))
            except Exception:
                pass  # keep the prose fallback rather than fail the job
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
