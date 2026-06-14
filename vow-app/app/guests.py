"""Guest list + RSVP: the agent projects the headcount and reconciles it
against venue capacity and the catering budget."""

import json
import uuid

from flask import Blueprint, jsonify, request, send_from_directory

from agent.harness import AgentHarness
from .core import GUESTS_PATH, PUBLIC_DIR, parse_agent_json, run_job
from .samples import load_sample

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
            "them who to follow up with. Read the guest data."
        )
        return {"analysis": parse_agent_json(answer),
                "cost_usd": round(harness.last_run_cost, 4)}

    return jsonify({"job_id": run_job(task)})


@guests_bp.post("/api/guests/load-sample")
def load_sample_guests():
    guests = load_guests()
    if guests["households"]:
        return jsonify({"error": "Guest list already has households."}), 400
    sample = load_sample("guests")
    if sample is None:
        return jsonify({"error": "No sample data available."}), 404
    guests["settings"] = {**DEFAULT_GUEST_SETTINGS, **sample.get("settings", {})}
    for h in sample.get("households", []):
        guests["households"].append({
            "id": uuid.uuid4().hex[:8], "plus_one_allowed": False, "plus_one_name": "",
            "meals": {}, "dietary": [], "notes": "", **h})
    save_guests(guests)
    return jsonify(guests)
