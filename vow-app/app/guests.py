"""Guest list + RSVP: the agent projects the headcount and reconciles it
against venue capacity and the catering budget."""

import json
import uuid

from flask import Blueprint, jsonify, request, send_from_directory

from agent.harness import AgentHarness
from .core import GUESTS_PATH, PUBLIC_DIR, parse_agent_json, run_job

guests_bp = Blueprint("guests", __name__)

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


@guests_bp.get("/guests")
def guests_page():
    return send_from_directory(str(PUBLIC_DIR), "guests.html")


@guests_bp.get("/api/guests")
def get_guests():
    return jsonify(load_guests())


@guests_bp.put("/api/guests/settings")
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


@guests_bp.post("/api/guests/households")
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
    household = {
        "id": uuid.uuid4().hex[:8],
        "household": name,
        "side": side,
        "group": str(data.get("group", "")).strip()[:60],
        "phone": str(data.get("phone", "")).strip()[:25],
        "party_size": party_size,
        "rsvp": rsvp,
        "attending_count": attending,
        "plus_one_allowed": bool(data.get("plus_one_allowed", False)),
        "plus_one_name": str(data.get("plus_one_name", "")).strip()[:100],
        "notes": str(data.get("notes", "")).strip()[:300],
    }
    guests = load_guests()
    guests["households"].append(household)
    save_guests(guests)
    return jsonify(household)


@guests_bp.put("/api/guests/households/<household_id>")
def update_household(household_id):
    """Partial update from the couple's UI — same validation rules as adding.
    Meals stay guest-owned (only the magic link writes them)."""
    data = request.get_json(force=True)
    guests = load_guests()
    h = next((x for x in guests["households"] if x["id"] == household_id), None)
    if h is None:
        return jsonify({"error": "Unknown household."}), 404

    def num(key, lo=0):
        try:
            return max(lo, int(float(data.get(key))))
        except (TypeError, ValueError):
            return None

    if str(data.get("household") or "").strip():
        h["household"] = str(data["household"]).strip()[:100]
    if "group" in data:
        h["group"] = str(data.get("group") or "").strip()[:60]
    if "phone" in data:
        h["phone"] = str(data.get("phone") or "").strip()[:25]
    if "invite_sent" in data:
        h["invite_sent"] = bool(data.get("invite_sent"))
    if "notes" in data:
        h["notes"] = str(data.get("notes") or "").strip()[:300]
    if "side" in data and str(data["side"]) in {"partner_a", "partner_b"}:
        h["side"] = str(data["side"])
    if "party_size" in data and num("party_size", 1) is not None:
        h["party_size"] = num("party_size", 1)
    if "rsvp" in data and str(data["rsvp"]) in RSVP_STATES:
        h["rsvp"] = str(data["rsvp"])
    if "plus_one_allowed" in data:
        h["plus_one_allowed"] = bool(data.get("plus_one_allowed"))
    if "plus_one_name" in data:
        h["plus_one_name"] = str(data.get("plus_one_name") or "").strip()[:100]
    if "attending_count" in data and num("attending_count") is not None:
        h["attending_count"] = num("attending_count")

    # Keep the row consistent (same rules as add + the RSVP form):
    if h.get("rsvp") != "confirmed":
        h["attending_count"] = 0
    max_party = h["party_size"] + (
        1 if h.get("plus_one_allowed") and h.get("plus_one_name") else 0)
    h["attending_count"] = min(h.get("attending_count", 0), max_party)

    save_guests(guests)
    return jsonify(h)


@guests_bp.delete("/api/guests/households/<household_id>")
def delete_household(household_id):
    guests = load_guests()
    before = len(guests["households"])
    guests["households"] = [h for h in guests["households"] if h["id"] != household_id]
    save_guests(guests)
    return jsonify({"ok": True, "removed": before - len(guests["households"])})


@guests_bp.post("/api/guests/analyze")
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
            "them who to follow up with. Read the guest data. "
            "Respond with ONLY the JSON object defined in the guest-list-manager skill "
            "— no prose, no markdown, no headings."
        )
        return {"analysis": parse_agent_json(answer),
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})
