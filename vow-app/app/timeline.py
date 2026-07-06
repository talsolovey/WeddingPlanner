"""Day-of timeline: the schedule of the day itself, plus the printable
handoff sheet's supporting data (vendor contacts, key people, room notes).

"Check the flow" is a single LLM call over the timeline + wedding context that
returns flags (buffers, sunset, vendor arrivals); flags are stored back into
the timeline file so the page shows them instantly on the next visit.
Hollow dots (unbooked vendor slots) are computed in code from the budget."""

import copy
import json
import uuid

from flask import Blueprint, jsonify, request, send_from_directory

from .core import DATA_DIR, PUBLIC_DIR, parse_agent_json, rate_limit
from .budget import load_budget
from .guests import load_guests

timeline_bp = Blueprint("timeline", __name__)

TIMELINE_PATH = DATA_DIR / "timeline.json"

DEFAULT_TIMELINE = {
    "sunset": "",
    "flags": [],
    "events": [],
    "contacts": [],
    "key_people": [],
    "room_notes": "",
    "sideways": "",
}


def load_timeline() -> dict:
    if TIMELINE_PATH.exists():
        try:
            data = json.loads(TIMELINE_PATH.read_text())
            merged = copy.deepcopy(DEFAULT_TIMELINE)
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(DEFAULT_TIMELINE)


def save_timeline(data: dict):
    TIMELINE_PATH.parent.mkdir(exist_ok=True)
    TIMELINE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _signed_categories() -> set:
    """Budget categories with a signed contract. A timeline slot whose vendor
    category isn't in here gets a hollow dot (unbooked)."""
    budget = load_budget()
    return {i["category"] for i in budget["items"] if (i.get("contracted") or 0) > 0}


def _time_key(e) -> str:
    """Sort key that keeps the small hours after midnight at the end of the
    day (01:00 send-off comes after the 23:30 cake, not before breakfast)."""
    t = e.get("time", "")
    return f"~{t}" if t < "05:00" else t


def _view(data) -> dict:
    signed = _signed_categories()
    events = [dict(e, vendor_unbooked=bool(e.get("vendor_category"))
                   and e["vendor_category"] not in signed)
              for e in sorted(data["events"], key=_time_key)]
    return dict(data, events=events)


@timeline_bp.get("/timeline")
def timeline_page():
    return send_from_directory(str(PUBLIC_DIR), "timeline.html")


@timeline_bp.get("/day-of")
def day_of_page():
    return send_from_directory(str(PUBLIC_DIR), "day-of.html")


@timeline_bp.get("/api/timeline")
def get_timeline():
    return jsonify(_view(load_timeline()))


@timeline_bp.post("/api/timeline/events")
def add_event():
    body = request.get_json(force=True, silent=True) or {}
    time = str(body.get("time", "")).strip()[:5]
    title = str(body.get("title", "")).strip()[:120]
    if not time or not title:
        return jsonify({"error": "A moment needs a time and a title."}), 400
    data = load_timeline()
    data["events"].append({
        "id": uuid.uuid4().hex[:8],
        "time": time,
        "title": title,
        "detail": str(body.get("detail", "")).strip()[:200],
        "flag": "",
        "vendor_category": str(body.get("vendor_category", "")).strip()[:60],
    })
    save_timeline(data)
    return jsonify(_view(data))


@timeline_bp.delete("/api/timeline/events/<event_id>")
def delete_event(event_id):
    data = load_timeline()
    data["events"] = [e for e in data["events"] if e["id"] != event_id]
    save_timeline(data)
    return jsonify(_view(data))


@timeline_bp.post("/api/timeline/check")
@rate_limit()
def check_flow():
    from .chat import llm_complete  # local import: keeps module load light
    from .profile import load_profile

    data = load_timeline()
    if not data["events"]:
        return jsonify({"error": "Add some moments to the day first."}), 400

    guests = load_guests()
    profile = load_profile()
    signed = _signed_categories()
    unbooked = sorted({e.get("vendor_category") for e in data["events"]}
                      - signed - {"", None})

    system = (
        "You are Vow, an AI wedding planner reviewing a wedding-day timeline for flow "
        "problems: missing buffers, photo light vs sunset, vendor arrival conflicts, "
        "guests left waiting, and slots that depend on unbooked vendors. Respond with "
        "ONLY a JSON object: {\"flags\": [{\"event_id\": \"id or null\", \"text\": "
        "\"one short actionable sentence\"}]} — at most 2 flags with event_id null "
        "(day-level) and at most 3 attached to events. Flag text starts with the "
        "problem, ends with the fix. No markdown.")
    payload = {
        "wedding_date": guests["settings"].get("wedding_date", ""),
        "venue": profile.get("venue", ""),
        "sunset": data.get("sunset", ""),
        "events": [{k: e.get(k, "") for k in ("id", "time", "title", "detail")}
                   for e in sorted(data["events"], key=_time_key)],
        "unbooked_vendor_slots": unbooked,
        "expected_guests": sum(h.get("attending_count", 0) for h in guests["households"]
                               if h.get("rsvp") == "confirmed"),
    }
    try:
        answer = llm_complete(system, [{"role": "user", "content": json.dumps(payload)}],
                              max_tokens=500)
    except Exception:
        return jsonify({"error": "Vow couldn't check the flow just now — try again."}), 502

    parsed = parse_agent_json(answer)
    flags = parsed.get("flags") if isinstance(parsed, dict) else None
    if not isinstance(flags, list):
        return jsonify({"error": "Vow answered unclearly — try once more."}), 502

    by_id = {e["id"]: e for e in data["events"]}
    for e in data["events"]:
        e["flag"] = ""
    day_flags = []
    for f in flags[:5]:
        text = str((f or {}).get("text", "")).strip()[:200]
        if not text:
            continue
        event = by_id.get(str((f or {}).get("event_id") or ""))
        if event is not None:
            event["flag"] = text
        else:
            day_flags.append(text)
    data["flags"] = day_flags[:2]
    save_timeline(data)
    return jsonify(_view(data))
