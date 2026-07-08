"""Seating chart: tables, manual assignment, and agent-proposed arrangements.

HITL by design: the agent's auto-seat run only *proposes* an arrangement (it
has no write path here). The couple reviews it in the UI and clicks Apply,
which validates the proposal in code — capacity, known households, no
duplicates — before anything is saved. Conflicts (over-full tables, unseated
confirmed households, seated decliners) are computed deterministically in code
and reused by the RSVP loop and the weekly brief.
"""

import uuid

from flask import Blueprint, jsonify, request, send_from_directory

import storage
from agent.harness import AgentHarness
from .core import PUBLIC_DIR, ensure_agent_json, rate_limit, run_job
from .guests import load_guests

seating_bp = Blueprint("seating", __name__)


def load_seating():
    data = storage.load("seating", {})
    data.setdefault("tables", [])
    return data


def save_seating(seating):
    storage.save("seating", seating)


def _table_load(table, by_id):
    """Seats used at a table = confirmed attending counts (+ named plus-ones)."""
    total = 0
    for hid in table.get("households", []):
        h = by_id.get(hid)
        if h and h.get("rsvp") == "confirmed":
            total += h.get("attending_count", 0)
            if h.get("plus_one_allowed") and h.get("plus_one_name"):
                total += 1
    return total


def seating_conflicts(guests=None, seating=None) -> list:
    """Deterministic conflict list; shared by GET /api/seating, the RSVP
    submit response, and the weekly-brief merge payload."""
    guests = guests or load_guests()
    seating = seating or load_seating()
    by_id = {h["id"]: h for h in guests["households"]}
    conflicts = []

    seated = set()
    for t in seating["tables"]:
        for hid in t.get("households", []):
            if hid in seated:
                conflicts.append(f"'{by_id.get(hid, {}).get('household', hid)}' is "
                                 f"assigned to more than one table.")
            seated.add(hid)
            h = by_id.get(hid)
            if h is None:
                conflicts.append(f"Table '{t['name']}' has an unknown household ({hid}).")
            elif h.get("rsvp") == "declined":
                conflicts.append(f"'{h['household']}' declined but is still seated "
                                 f"at '{t['name']}'.")
        load = _table_load(t, by_id)
        if load > t.get("capacity", 0):
            conflicts.append(f"Table '{t['name']}' is over capacity "
                             f"({load}/{t.get('capacity', 0)} seats).")

    for h in guests["households"]:
        if h.get("rsvp") == "confirmed" and h["id"] not in seated:
            conflicts.append(f"'{h['household']}' ({h.get('attending_count', 0)} "
                             f"confirmed) has no table yet.")
    return conflicts


def _view(guests=None, seating=None):
    guests = guests or load_guests()
    seating = seating or load_seating()
    by_id = {h["id"]: h for h in guests["households"]}
    seated = {hid for t in seating["tables"] for hid in t.get("households", [])}
    return {
        "tables": [dict(t, seats_used=_table_load(t, by_id)) for t in seating["tables"]],
        "unassigned": [
            {"id": h["id"], "household": h["household"], "rsvp": h.get("rsvp"),
             "attending_count": h.get("attending_count", 0), "side": h.get("side"),
             "group": h.get("group", "")}
            for h in guests["households"]
            if h["id"] not in seated and h.get("rsvp") != "declined"
        ],
        "conflicts": seating_conflicts(guests, seating),
    }


# ---------- pages + CRUD ----------

@seating_bp.get("/seating")
def seating_page():
    return send_from_directory(str(PUBLIC_DIR), "seating.html")


@seating_bp.get("/api/seating")
def get_seating():
    return jsonify(_view())


@seating_bp.post("/api/seating/tables")
def add_table():
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()[:60]
    if not name:
        return jsonify({"error": "Table name is required."}), 400
    try:
        capacity = max(1, min(100, int(float(data.get("capacity") or 8))))
    except (TypeError, ValueError):
        capacity = 8
    seating = load_seating()
    seating["tables"].append({"id": uuid.uuid4().hex[:8], "name": name,
                              "capacity": capacity, "households": []})
    save_seating(seating)
    return jsonify(_view(seating=seating))


@seating_bp.put("/api/seating/tables/<table_id>")
def update_table(table_id):
    data = request.get_json(force=True)
    seating = load_seating()
    table = next((t for t in seating["tables"] if t["id"] == table_id), None)
    if table is None:
        return jsonify({"error": "Unknown table."}), 404
    if "name" in data and str(data["name"]).strip():
        table["name"] = str(data["name"]).strip()[:60]
    if "capacity" in data:
        try:
            table["capacity"] = max(1, min(100, int(float(data["capacity"]))))
        except (TypeError, ValueError):
            pass
    save_seating(seating)
    return jsonify(_view(seating=seating))


@seating_bp.delete("/api/seating/tables/<table_id>")
def delete_table(table_id):
    seating = load_seating()
    seating["tables"] = [t for t in seating["tables"] if t["id"] != table_id]
    save_seating(seating)
    return jsonify(_view(seating=seating))


@seating_bp.put("/api/seating/assign")
def assign_household():
    data = request.get_json(force=True)
    hid = str(data.get("household_id", ""))
    table_id = data.get("table_id")  # null/None = unassign

    guests = load_guests()
    if hid not in {h["id"] for h in guests["households"]}:
        return jsonify({"error": "Unknown household."}), 404

    seating = load_seating()
    for t in seating["tables"]:  # a household sits at exactly one table
        t["households"] = [x for x in t.get("households", []) if x != hid]
    if table_id is not None:
        table = next((t for t in seating["tables"] if t["id"] == table_id), None)
        if table is None:
            return jsonify({"error": "Unknown table."}), 404
        table["households"].append(hid)
    save_seating(seating)
    return jsonify(_view(guests=guests, seating=seating))


# ---------- agent auto-seat: agent proposes, code validates, then it's applied
# directly — the couple corrects on the visual chart afterwards. Hard-invalid
# plans (unknown households, a household at two tables) are never saved. ----------

def _hard_issues(issues) -> list:
    return [i for i in issues if "unknown household" in i.lower()
            or "more than one table" in i.lower()]


def _tables_from_proposal(data) -> dict:
    return {"tables": [
        {"id": uuid.uuid4().hex[:8], "name": str(t["name"]).strip()[:60],
         "capacity": max(1, min(100, int(t.get("capacity", 8)))),
         "households": [str(h) for h in t.get("households", [])]}
        for t in data["tables"]
    ]}


@seating_bp.post("/api/seating/auto")
@rate_limit()
def auto_seat():
    guests = load_guests()
    confirmed = [h for h in guests["households"] if h.get("rsvp") == "confirmed"]
    if not confirmed:
        return jsonify({"error": "No confirmed households to seat yet."}), 400

    def task(on_event):
        harness = AgentHarness(verbose=False, on_event=on_event)
        answer = harness.run(
            "Propose a seating arrangement for the wedding. Use the seating-planner "
            "skill. Read the guest data and the current seating data yourself. "
            "Respond with ONLY the JSON object the skill defines — no prose, no "
            "markdown fences."
        )
        proposal = ensure_agent_json(answer, skill="seating-planner",
                                     on_event=on_event)
        issues = validate_proposal(proposal, guests)
        hard = _hard_issues(issues)
        applied = False
        if not hard and isinstance(proposal, dict) and proposal.get("tables"):
            save_seating(_tables_from_proposal(proposal))
            applied = True
        return {"proposal": proposal, "validation": issues, "applied": applied,
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})


def validate_proposal(proposal, guests) -> list:
    """Code checks the agent's plan — the model doesn't get to break invariants."""
    issues = []
    tables = proposal.get("tables") if isinstance(proposal, dict) else None
    if not isinstance(tables, list) or not tables:
        return ["Proposal has no tables."]
    by_id = {h["id"]: h for h in guests["households"]}
    seen = set()
    for t in tables:
        if not isinstance(t, dict) or not str(t.get("name", "")).strip():
            issues.append("A table is missing its name.")
            continue
        try:
            capacity = int(t.get("capacity", 0))
        except (TypeError, ValueError):
            capacity = 0
        load = 0
        for hid in t.get("households", []):
            h = by_id.get(hid)
            if h is None:
                issues.append(f"Table '{t['name']}': unknown household id '{hid}'.")
                continue
            if hid in seen:
                issues.append(f"'{h['household']}' appears at more than one table.")
            seen.add(hid)
            if h.get("rsvp") == "confirmed":
                load += h.get("attending_count", 0)
                if h.get("plus_one_allowed") and h.get("plus_one_name"):
                    load += 1
        if load > capacity:
            issues.append(f"Table '{t['name']}' is over capacity ({load}/{capacity}).")
    for h in guests["households"]:
        if h.get("rsvp") == "confirmed" and h["id"] not in seen:
            issues.append(f"'{h['household']}' (confirmed) was left unseated.")
    return issues


@seating_bp.post("/api/seating/apply")
def apply_proposal():
    """Apply a full arrangement (used by the API/tests; the UI now applies
    auto-seat results directly). Hard-invalid plans are rejected."""
    data = request.get_json(force=True)
    guests = load_guests()
    issues = validate_proposal(data, guests)
    hard = _hard_issues(issues)
    if hard:
        return jsonify({"error": "Proposal is invalid.", "issues": hard}), 400
    seating = _tables_from_proposal(data)
    save_seating(seating)
    return jsonify(_view(guests=guests, seating=seating))
