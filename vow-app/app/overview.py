"""Home dashboard: a single summary across budget, contracts, guests, and skills."""

from flask import Blueprint, jsonify

from .core import SKILLS_DIR
from .budget import load_budget
from .contracts import load_contracts
from .guests import load_guests

overview_bp = Blueprint("overview", __name__)


@overview_bp.get("/api/overview")
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

    return jsonify({
        "budget": {"total_budget": budget["total_budget"], "currency": budget.get("currency", "USD"),
                   "committed": committed, "paid": paid, "item_count": len(budget["items"])},
        "latest_contract": latest_card,
        "contract_count": len(contracts),
        "guests": guests_card,
        "skills": skills,
    })
