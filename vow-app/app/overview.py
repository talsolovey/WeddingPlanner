"""Home dashboard: a single summary across budget, contracts, guests, and skills,
plus a lightweight partner-activity log ("Omer nudged the Malka family · Tuesday")."""

import json
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory

from .core import DATA_DIR, PUBLIC_DIR, SKILLS_DIR
from .budget import load_budget
from .contracts import load_contracts
from .guests import load_guests
from .seating import load_seating, seating_conflicts, _table_load

overview_bp = Blueprint("overview", __name__)

ACTIVITY_PATH = DATA_DIR / "activity.json"


@overview_bp.get("/vendors")
def vendors_page():
    return send_from_directory(str(PUBLIC_DIR), "vendors.html")


def load_activity() -> list:
    if ACTIVITY_PATH.exists():
        try:
            return json.loads(ACTIVITY_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


@overview_bp.post("/api/activity")
def add_activity():
    data = request.get_json(force=True, silent=True) or {}
    actor = str(data.get("actor", "")).strip()[:40]
    text = str(data.get("text", "")).strip()[:200]
    if not actor or not text:
        return jsonify({"error": "actor and text are required."}), 400
    entries = load_activity()
    entries.append({"actor": actor, "text": text,
                    "at": datetime.now().isoformat(timespec="seconds")})
    ACTIVITY_PATH.parent.mkdir(exist_ok=True)
    ACTIVITY_PATH.write_text(json.dumps(entries[-50:], indent=2))
    return jsonify({"ok": True})


@overview_bp.get("/api/overview")
def overview():
    budget = load_budget()
    contracts = load_contracts()
    committed = sum((i.get("contracted") or i.get("estimated") or 0) for i in budget["items"])
    paid = sum((i.get("paid") or 0) for i in budget["items"])
    settled = sum(1 for i in budget["items"] if (i.get("contracted") or 0) > 0
                  or (i.get("paid") or 0) > 0)

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
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
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

    seating = load_seating()
    by_id = {h["id"]: h for h in households}
    seated = {hid for t in seating["tables"] for hid in t.get("households", [])}
    seating_card = {
        "table_count": len(seating["tables"]),
        "total_seats": sum(t.get("capacity", 0) for t in seating["tables"]),
        "seated_households": len(seated),
        "seated_people": sum(_table_load(t, by_id) for t in seating["tables"]),
        "conflicts": len(seating_conflicts(guests, seating)),
    }

    return jsonify({
        "wedding_date": guests["settings"].get("wedding_date", ""),
        "budget": {"total_budget": budget["total_budget"], "currency": budget.get("currency", "USD"),
                   "committed": committed, "paid": paid, "item_count": len(budget["items"]),
                   "settled_count": settled},
        "latest_contract": latest_card,
        "contract_count": len(contracts),
        "guests": guests_card,
        "seating": seating_card,
        "skills": skills,
        "activity": load_activity()[-5:][::-1],
    })
