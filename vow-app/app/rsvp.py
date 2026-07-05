"""Guest-facing RSVP via per-household magic links.

Security model (this is a PUBLIC write surface on a deployed app):
- A token identifies exactly one household; every read and write is scoped to
  that household only. No token enumeration: tokens are 20 hex chars.
- The public GET returns the minimum a guest needs (their own household's
  fields) — never other households, never internal settings.
- Validation is strict: bounded counts, capped string lengths. Free-text
  fields are scanned with the prompt-injection guard, since the agent later
  reads this data; suspicious notes are rejected outright.
- Tighter rate limit than the couple-facing endpoints.
"""

import secrets

from flask import Blueprint, jsonify, request, send_from_directory

from agent import guard
from .core import PUBLIC_DIR, rate_limit
from .guests import load_guests, save_guests

rsvp_bp = Blueprint("rsvp", __name__)


def ensure_tokens(guests) -> bool:
    """Give every household a magic-link token; returns True if any were added."""
    changed = False
    for h in guests["households"]:
        if not h.get("rsvp_token"):
            h["rsvp_token"] = secrets.token_hex(10)
            changed = True
    return changed


def _find_by_token(guests, token: str):
    if not token or len(token) > 40:
        return None
    for h in guests["households"]:
        if h.get("rsvp_token") and secrets.compare_digest(h["rsvp_token"], token):
            return h
    return None


# ---------- couple-facing: generate / list the links ----------

@rsvp_bp.post("/api/guests/rsvp-links")
def rsvp_links():
    guests = load_guests()
    if ensure_tokens(guests):
        save_guests(guests)
    return jsonify({"links": [
        {"id": h["id"], "household": h["household"], "rsvp": h.get("rsvp", "pending"),
         "url": f"/rsvp/{h['rsvp_token']}"}
        for h in guests["households"]
    ]})


# ---------- guest-facing: the form ----------

@rsvp_bp.get("/rsvp/<token>")
def rsvp_page(token):
    return send_from_directory(str(PUBLIC_DIR), "rsvp.html")


@rsvp_bp.get("/api/rsvp/<token>")
@rate_limit(max_calls=10, window=60)
def rsvp_get(token):
    guests = load_guests()
    h = _find_by_token(guests, token)
    if h is None:
        return jsonify({"error": "This RSVP link isn't valid."}), 404
    # Only this household's own fields — nothing about anyone else.
    return jsonify({
        "household": h["household"],
        "party_size": h["party_size"],
        "rsvp": h.get("rsvp", "pending"),
        "attending_count": h.get("attending_count", 0),
        "plus_one_allowed": bool(h.get("plus_one_allowed")),
        "plus_one_name": h.get("plus_one_name", ""),
        "notes": h.get("notes", ""),
        "wedding_date": guests["settings"].get("wedding_date", ""),
        "rsvp_deadline": guests["settings"].get("rsvp_deadline", ""),
    })


@rsvp_bp.post("/api/rsvp/<token>")
@rate_limit(max_calls=5, window=60)
def rsvp_submit(token):
    guests = load_guests()
    h = _find_by_token(guests, token)
    if h is None:
        return jsonify({"error": "This RSVP link isn't valid."}), 404
    data = request.get_json(force=True, silent=True) or {}

    rsvp = str(data.get("rsvp", "")).strip()
    if rsvp not in {"confirmed", "declined"}:
        return jsonify({"error": "Please choose attending or not attending."}), 400

    plus_one_name = str(data.get("plus_one_name", "")).strip()[:100]
    max_party = h["party_size"] + (1 if h.get("plus_one_allowed") and plus_one_name else 0)

    try:
        attending = int(float(data.get("attending_count", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "Attending count must be a number."}), 400
    if rsvp == "declined":
        attending = 0
    elif not 1 <= attending <= max_party:
        return jsonify({"error": f"Attending count must be between 1 and {max_party}."}), 400

    notes = str(data.get("notes", "")).strip()[:300]

    # The agent reads these fields later — reject anything that scans as an
    # instruction-injection attempt rather than storing it.
    for text in [notes, plus_one_name]:
        if text and guard.scan_for_injection(text):
            return jsonify({"error": "That text can't be saved. Please rephrase."}), 400

    h["rsvp"] = rsvp
    h["attending_count"] = attending
    h["notes"] = notes
    if h.get("plus_one_allowed"):
        h["plus_one_name"] = plus_one_name
    save_guests(guests)

    # Full loop: tell the couple immediately if this response created a conflict.
    from .seating import seating_conflicts  # local import to avoid a cycle
    return jsonify({"ok": True, "rsvp": rsvp, "attending_count": attending,
                    "conflicts_created": seating_conflicts(guests=guests)})
