"""Planning checklist, paced to the wedding date.

Items can carry an `auto_rule`: a named predicate evaluated against the live
app data on every read, so Vow checks things off as the couple actually does
them in the app (books the venue, sends save-the-dates, seats everyone...).
A manual toggle always wins over the rule — unchecking an auto item sticks."""

from datetime import date

from flask import Blueprint, jsonify, request, send_from_directory

import storage
from .core import PUBLIC_DIR
from .budget import load_budget
from .guests import load_guests

checklist_bp = Blueprint("checklist", __name__)


# ---------- auto-check rules (evaluated on read, all deterministic) ----------

def _signed(budget, *categories) -> bool:
    by_cat = {i["category"]: i for i in budget["items"]}
    return all((by_cat.get(c, {}).get("contracted") or 0) > 0 for c in categories)


def _rules():
    from .invitations import load_invitations
    from .seating import load_seating, seating_conflicts

    budget = load_budget()
    guests = load_guests()
    seating = load_seating()
    invitations = load_invitations(guests)
    waves = {w["id"]: w for w in invitations["waves"]}
    outstanding = [h for h in guests["households"]
                   if h.get("rsvp") in ("pending", "no_response")]
    seated = {hid for t in seating["tables"] for hid in t.get("households", [])}
    confirmed = [h for h in guests["households"] if h.get("rsvp") == "confirmed"]

    return {
        "date_and_budget_set": bool(guests["settings"].get("wedding_date"))
                               and (budget.get("total_budget") or 0) > 0,
        "venue_booked": _signed(budget, "venue"),
        "guest_list_drafted": len(guests["households"]) > 0,
        "photographer_booked": _signed(budget, "photography"),
        "save_the_dates_sent": waves.get("save_the_date", {}).get("status") == "sent",
        "invitations_sent": waves.get("invitation", {}).get("status") == "sent",
        "seating_started": len(seating["tables"]) > 0,
        "music_flowers_transport_signed": _signed(budget, "music/DJ", "flowers/decor",
                                                  "transport"),
        "rsvps_all_in": len(guests["households"]) > 0 and not outstanding,
        "contingency_set": any("contingency" in i["category"].lower()
                               for i in budget["items"]),
        "seating_finished": bool(confirmed)
                            and all(h["id"] in seated for h in confirmed)
                            and not seating_conflicts(guests, seating),
        "day_of_message_scheduled": waves.get("day_of", {}).get("status") == "sent",
    }


# ---------- phase pacing ----------

PHASE_MONTHS = [(12, "A year out"), (6, "6 months out"), (4, "4 months out"),
                (1, "1 month out"), (0, "The week of")]


def _current_phase_index(wedding_iso: str) -> int:
    """Which list the couple should be working on right now."""
    try:
        wedding = date.fromisoformat(wedding_iso)
    except (TypeError, ValueError):
        return 0
    days = (wedding - date.today()).days
    months = days / 30.44
    if months >= 8:
        return 0
    if months >= 5:
        return 1
    if months >= 1.5:
        return 2
    if days > 7:
        return 3
    return 4


def load_checklist() -> dict:
    return storage.load("checklist", {"phases": []})


def save_checklist(data: dict):
    storage.save("checklist", data)


def _view() -> dict:
    data = load_checklist()
    rules = _rules()
    guests = load_guests()
    current = _current_phase_index(guests["settings"].get("wedding_date", ""))

    phases = []
    done_count = total = 0
    for pi, phase in enumerate(data["phases"]):
        items = []
        for item in phase.get("items", []):
            auto_done = bool(rules.get(item.get("auto_rule"), False))
            manual = item.get("manual")  # true / false / None
            done = manual if manual is not None else auto_done
            total += 1
            done_count += 1 if done else 0
            items.append({
                "id": item["id"],
                "label": item["label"],
                "href": item.get("href", ""),
                "area": item.get("area", ""),
                "done": done,
                "auto": done and manual is None and auto_done,
            })
        d = sum(1 for i in items if i["done"])
        phases.append({
            "key": phase.get("key", f"p{pi}"),
            "title": phase.get("title", ""),
            "current": pi == current,
            "items": items,
            "done": d,
        })
    return {"phases": phases, "done_count": done_count, "total": total,
            "current_index": current,
            "wedding_date": guests["settings"].get("wedding_date", "")}


@checklist_bp.get("/checklist")
def checklist_page():
    return send_from_directory(str(PUBLIC_DIR), "checklist.html")


@checklist_bp.get("/api/checklist")
def get_checklist():
    return jsonify(_view())


@checklist_bp.put("/api/checklist/items/<item_id>")
def toggle_item(item_id):
    body = request.get_json(force=True, silent=True) or {}
    data = load_checklist()
    for phase in data["phases"]:
        for item in phase.get("items", []):
            if item["id"] == item_id:
                item["manual"] = bool(body.get("done"))
                save_checklist(data)
                return jsonify(_view())
    return jsonify({"error": "Unknown item."}), 404
