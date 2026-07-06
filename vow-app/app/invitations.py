"""Invitation waves: save-the-date → invitation → reminders → day-of → thank-you.

Scheduler semantics (all deterministic code, no agent writes):
- Recipients are recomputed at read/send time, never stored ahead: reminder
  waves go only to households that still haven't replied, so anyone who
  answers first is skipped automatically.
- No household ever gets more than MAX_REMINDERS reminder sends (tracked in
  reminder_counts per household id).
- A scheduled wave whose date has arrived is auto-sent on the next visit
  (file-based app — the "clock" ticks whenever the data is read).

Message writing is delegated to the LLM via /api/invitations/generate with a
per-wave prompt (which wave, deadline, logistics)."""

import json
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request, send_from_directory

from .core import DATA_DIR, PUBLIC_DIR, rate_limit
from .guests import load_guests

invitations_bp = Blueprint("invitations", __name__)

INVITATIONS_PATH = DATA_DIR / "invitations.json"
MAX_REMINDERS = 3

WAVE_DEFS = [
    ("save_the_date", "Save the date", "broadcast"),
    ("invitation", "The invitation", "invite"),
    ("reminder_1", "Reminder 1", "reminder"),
    ("final_reminder", "Final reminder", "reminder"),
    ("day_of", "Day-of reminder & directions", "day_of"),
    ("thank_you", "The day after — thank you", "thank_you"),
]

DEFAULT_MESSAGES = {
    "reminder_1": "Hi [name]! 💍 Just a gentle nudge — we'd love to know if you can join us. "
                  "It takes half a minute: [rsvp link] 🤍",
    "final_reminder": "Hi [name], the caterer needs our final numbers this week — could you "
                      "let us know either way? [rsvp link] 🤍",
    "day_of": "Today's the day, [name]! 🤍 Directions & parking: [directions link]. "
              "See you there!",
    "thank_you": "Dear [name], thank you for celebrating with us yesterday — having you "
                 "there meant the world. We'll send photos soon. With all our love 🤍",
}


def _default_waves(guests) -> list:
    """First-run wave plan anchored to the couple's dates."""
    settings = guests["settings"]
    deadline = settings.get("rsvp_deadline", "")
    wedding = settings.get("wedding_date", "")
    today = date.today()

    def plus_days(iso, days):
        try:
            return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
        except (TypeError, ValueError):
            return ""

    return [
        {"id": "save_the_date", "title": "Save the date", "kind": "broadcast",
         "status": "scheduled", "send_on": today.isoformat(), "message": "", "sent_to": []},
        {"id": "invitation", "title": "The invitation", "kind": "invite",
         "status": "scheduled", "send_on": today.isoformat(), "message": "", "sent_to": []},
        {"id": "reminder_1", "title": "Reminder 1", "kind": "reminder",
         "status": "scheduled", "send_on": plus_days(deadline, -42) or today.isoformat(),
         "message": DEFAULT_MESSAGES["reminder_1"], "sent_to": []},
        {"id": "final_reminder", "title": "Final reminder", "kind": "reminder",
         # Anchored two weeks before the RSVP deadline.
         "status": "scheduled", "send_on": plus_days(deadline, -14),
         "message": DEFAULT_MESSAGES["final_reminder"], "sent_to": []},
        {"id": "day_of", "title": "Day-of reminder & directions", "kind": "day_of",
         "status": "scheduled", "send_on": wedding,
         "message": DEFAULT_MESSAGES["day_of"], "sent_to": []},
        {"id": "thank_you", "title": "The day after — thank you", "kind": "thank_you",
         "status": "scheduled", "send_on": plus_days(wedding, 1),
         "message": DEFAULT_MESSAGES["thank_you"], "sent_to": []},
    ]


def load_invitations(guests=None) -> dict:
    guests = guests or load_guests()
    if INVITATIONS_PATH.exists():
        try:
            data = json.loads(INVITATIONS_PATH.read_text())
            data.setdefault("waves", _default_waves(guests))
            data.setdefault("reminder_counts", {})
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"waves": _default_waves(guests), "reminder_counts": {}}


def save_invitations(data: dict):
    INVITATIONS_PATH.parent.mkdir(exist_ok=True)
    INVITATIONS_PATH.write_text(json.dumps(data, indent=2))


def _recipients(wave, guests, reminder_counts) -> list:
    """Who a wave would go to if it were sent right now."""
    households = guests["households"]
    kind = wave.get("kind")
    if kind == "reminder":
        return [h for h in households
                if h.get("rsvp") in ("pending", "no_response")
                and reminder_counts.get(h["id"], 0) < MAX_REMINDERS]
    if kind in ("day_of", "thank_you"):
        return [h for h in households if h.get("rsvp") == "confirmed"]
    return list(households)


def _send_wave(wave, guests, data):
    """Mark a wave sent now: freeze recipients, bump reminder counts."""
    recipients = _recipients(wave, guests, data["reminder_counts"])
    wave["status"] = "sent"
    wave["sent_on"] = date.today().isoformat()
    wave["sent_to"] = [h["id"] for h in recipients]
    if wave.get("kind") == "reminder":
        for h in recipients:
            data["reminder_counts"][h["id"]] = data["reminder_counts"].get(h["id"], 0) + 1
    return recipients


def _check_due(data, guests) -> bool:
    """The scheduler tick: send any scheduled wave whose date has arrived."""
    changed = False
    today = date.today().isoformat()
    for wave in data["waves"]:
        if wave.get("status") == "scheduled" and wave.get("send_on") and wave["send_on"] <= today:
            _send_wave(wave, guests, data)
            changed = True
    return changed


def _view(data, guests) -> dict:
    households = guests["households"]
    replied = sum(1 for h in households if h.get("rsvp") in ("confirmed", "declined"))
    quiet = len(households) - replied
    by_id = {h["id"]: h for h in households}

    waves = []
    next_send = None
    for wave in data["waves"]:
        recipients = _recipients(wave, guests, data["reminder_counts"])
        w = dict(wave, recipients=[{"id": h["id"], "household": h["household"]}
                                   for h in recipients])
        if wave["status"] == "sent":
            w["sent_count"] = len(wave.get("sent_to", []))
            w["replied_of_sent"] = sum(
                1 for hid in wave.get("sent_to", [])
                if by_id.get(hid, {}).get("rsvp") in ("confirmed", "declined"))
        elif next_send is None and wave.get("send_on") and recipients:
            next_send = {"title": wave["title"], "date": wave["send_on"],
                         "count": len(recipients)}
        waves.append(w)

    return {
        "waves": waves,
        "stats": {
            "households": len(households),
            "replied": replied,
            "quiet": quiet,
            "next_send": next_send,
        },
        "settings": {
            "rsvp_deadline": guests["settings"].get("rsvp_deadline", ""),
            "wedding_date": guests["settings"].get("wedding_date", ""),
        },
    }


@invitations_bp.get("/invitations")
def invitations_page():
    return send_from_directory(str(PUBLIC_DIR), "invitations.html")


@invitations_bp.get("/api/invitations")
def get_invitations():
    guests = load_guests()
    data = load_invitations(guests)
    if _check_due(data, guests):
        save_invitations(data)
    if not INVITATIONS_PATH.exists():
        save_invitations(data)
    return jsonify(_view(data, guests))


@invitations_bp.put("/api/invitations/waves/<wave_id>")
def update_wave(wave_id):
    body = request.get_json(force=True, silent=True) or {}
    guests = load_guests()
    data = load_invitations(guests)
    wave = next((w for w in data["waves"] if w["id"] == wave_id), None)
    if wave is None:
        return jsonify({"error": "Unknown wave."}), 404
    if wave["status"] == "sent":
        return jsonify({"error": "This wave was already sent."}), 400
    if "message" in body:
        wave["message"] = str(body.get("message") or "")[:1000]
    if "send_on" in body:
        send_on = str(body.get("send_on") or "")[:10]
        try:
            date.fromisoformat(send_on)
            wave["send_on"] = send_on
        except ValueError:
            return jsonify({"error": "send_on must be a date (YYYY-MM-DD)."}), 400
    save_invitations(data)
    return jsonify(_view(data, guests))


@invitations_bp.post("/api/invitations/waves/<wave_id>/send")
def send_wave_now(wave_id):
    guests = load_guests()
    data = load_invitations(guests)
    wave = next((w for w in data["waves"] if w["id"] == wave_id), None)
    if wave is None:
        return jsonify({"error": "Unknown wave."}), 404
    if wave["status"] == "sent":
        return jsonify({"error": "This wave was already sent."}), 400
    recipients = _send_wave(wave, guests, data)
    save_invitations(data)
    return jsonify(dict(_view(data, guests),
                        sent={"wave": wave["title"], "count": len(recipients)}))


@invitations_bp.post("/api/invitations/generate")
@rate_limit(max_calls=10, window=60)
def generate_message():
    from .chat import llm_complete  # local import: keeps module load light
    from .profile import load_profile

    body = request.get_json(force=True, silent=True) or {}
    wave_id = str(body.get("wave_id", ""))
    guests = load_guests()
    data = load_invitations(guests)
    wave = next((w for w in data["waves"] if w["id"] == wave_id), None)
    if wave is None:
        return jsonify({"error": "Unknown wave."}), 404

    profile = load_profile()
    names = " & ".join(n for n in (profile["partner_a"], profile["partner_b"]) if n) or "the couple"
    settings = guests["settings"]
    wedding = settings.get("wedding_date", "")
    venue = profile.get("venue") or "the venue"
    deadline = settings.get("rsvp_deadline", "")

    def pretty(iso):
        try:
            return datetime.fromisoformat(iso).strftime("%A %B %-d, %Y")
        except (TypeError, ValueError):
            return iso

    kind_prompt = {
        "broadcast": "a save-the-date announcement to all invited households. No links needed.",
        "invite": "the wedding invitation, sent with each household's private RSVP link. "
                  "Include [rsvp link].",
        "reminder_1": "a first gentle RSVP reminder to a household that hasn't replied. "
                      "Include [rsvp link].",
        "final_reminder": f"a final RSVP reminder (two weeks before the "
                          f"{pretty(deadline) or 'RSVP'} deadline; the caterer needs final "
                          f"numbers). Include [rsvp link].",
        "day_of": "a morning-of-the-wedding message to confirmed guests with the day's "
                  "logistics. Include [directions link] instead of [rsvp link].",
        "thank_you": "a thank-you message sent the day after the wedding to guests who "
                     "attended. No links needed.",
    }
    kind = kind_prompt.get(wave_id) or kind_prompt.get(wave.get("kind"), "a wedding message.")

    system = (
        f"You write short WhatsApp messages for {names}, a couple getting married "
        f"{pretty(wedding)} at {venue}. Write warmly and personally, 1-3 sentences, at "
        f"most one emoji, no hashtags. Use the literal placeholder [name] for the "
        f"household name exactly once. Only include the link placeholders the request "
        f"asks for ([rsvp link] or [directions link]). Reply with the message text only.")
    try:
        text = llm_complete(system, [{"role": "user", "content": f"Write {kind}"}],
                            max_tokens=200)
    except Exception:
        return jsonify({"error": "Vow couldn't write just now — try again in a moment."}), 502
    return jsonify({"message": text.strip().strip('"')[:1000]})
