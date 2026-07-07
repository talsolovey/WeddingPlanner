"""Vow's notices + trust settings: agent initiative, couple control.

Notices are written by agent/triggers.py (event-driven wake-ups) and
agent/trust.py (promotions). Proposals (kind="proposal") carry an action +
payload; approving one executes it through agent/trust.py and feeds the
approval streak that can earn the agent tier-1 autonomy. Dismissing a
proposal counts as a rejection and resets the streak. All documents here
are code-owned — never writable via the agent's write_data tool."""

from flask import Blueprint, jsonify, request

import storage
from agent import trust
from agent.triggers import NOTICES_DOC

notices_bp = Blueprint("notices", __name__)


def _find(doc, nid):
    return next((n for n in doc.get("items", []) if n.get("id") == nid), None)


@notices_bp.get("/api/notices")
def list_notices():
    doc = storage.load(NOTICES_DOC, default={"items": []})
    return jsonify({"items": [n for n in doc.get("items", []) if not n.get("read")]})


@notices_bp.post("/api/notices/<nid>/dismiss")
def dismiss_notice(nid):
    doc = storage.load(NOTICES_DOC, default={"items": []})
    n = _find(doc, nid)
    if n is None:
        return jsonify({"error": "Unknown notice."}), 404
    n["read"] = True
    storage.save(NOTICES_DOC, doc)
    if n.get("kind") == "proposal" and n.get("action"):
        trust.record_decision(n["action"], approved=False)  # a "no" resets trust
    return jsonify({"ok": True})


@notices_bp.post("/api/notices/<nid>/approve")
def approve_notice(nid):
    doc = storage.load(NOTICES_DOC, default={"items": []})
    n = _find(doc, nid)
    if n is None:
        return jsonify({"error": "Unknown notice."}), 404
    if n.get("kind") != "proposal" or not n.get("action"):
        return jsonify({"error": "This notice isn't approvable."}), 400
    if n.get("read"):
        return jsonify({"error": "Already handled."}), 409

    try:
        result = trust.execute(n["action"], n.get("payload") or {},
                               base_url=request.host_url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    n["read"] = True
    storage.save(NOTICES_DOC, doc)
    spec = trust.record_decision(n["action"], approved=True)
    return jsonify({"ok": True, "result": result,
                    "trust": {"tier": spec.get("tier"),
                              "streak": spec.get("streak")}})


# ---------- trust settings ----------

@notices_bp.get("/api/trust")
def get_trust():
    return jsonify({"actions": trust.get_trust(),
                    "promote_after": trust.PROMOTE_AFTER})


@notices_bp.put("/api/trust/<action>")
def put_trust(action):
    data = request.get_json(force=True, silent=True) or {}
    try:
        spec = trust.set_tier(action, int(data.get("tier", -1)))
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "action": action, "tier": spec["tier"]})
