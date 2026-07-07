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
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory

import storage
from agent import guard
from .core import PUBLIC_DIR, rate_limit
from .guests import load_guests, save_guests

rsvp_bp = Blueprint("rsvp", __name__)


def _enter_couple(couple: str) -> bool:
    """Pin storage to the couple named in a public RSVP link. The token is
    still the capability — the couple id only picks whose guest list the
    token is checked against."""
    try:
        storage.set_couple(couple)
        return True
    except ValueError:
        return False


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
    couple = storage.current_couple()
    return jsonify({"links": [
        {"id": h["id"], "household": h["household"], "rsvp": h.get("rsvp", "pending"),
         "url": f"/rsvp/{couple}/{h['rsvp_token']}"}
        for h in guests["households"]
    ]})


# ---------- guest-facing: the form ----------
# Links carry the couple id (/rsvp/<couple>/<token>) so the token is checked
# against the right couple's guest list. Legacy single-segment links keep
# working against the pre-auth data.

@rsvp_bp.get("/rsvp/<couple>/<token>")
def rsvp_page(couple, token):
    return send_from_directory(str(PUBLIC_DIR), "rsvp.html")


@rsvp_bp.get("/rsvp/<token>")
def rsvp_page_legacy(token):
    return send_from_directory(str(PUBLIC_DIR), "rsvp.html")


def _rsvp_get(guests):
    """Only this household's own fields — nothing about anyone else."""
    profile = storage.load("profile", {}) or {}
    return {
        "wedding_date": guests["settings"].get("wedding_date", ""),
        "rsvp_deadline": guests["settings"].get("rsvp_deadline", ""),
        # The guest page shows whose wedding this is; guests can't call the
        # gated /api/profile, so the public payload carries the header bits.
        "partner_a": profile.get("partner_a", ""),
        "partner_b": profile.get("partner_b", ""),
        "venue": profile.get("venue", ""),
    }


def _rsvp_get_impl(couple, token):
    if not _enter_couple(couple):
        return jsonify({"error": "This RSVP link isn't valid."}), 404
    guests = load_guests()
    h = _find_by_token(guests, token)
    if h is None:
        return jsonify({"error": "This RSVP link isn't valid."}), 404
    payload = _rsvp_get(guests)
    payload.update({
        "household": h["household"],
        "party_size": h["party_size"],
        "rsvp": h.get("rsvp", "pending"),
        "attending_count": h.get("attending_count", 0),
        "plus_one_allowed": bool(h.get("plus_one_allowed")),
        "plus_one_name": h.get("plus_one_name", ""),
        "notes": h.get("notes", ""),
    })
    return jsonify(payload)


@rsvp_bp.get("/api/rsvp/<couple>/<token>")
@rate_limit(max_calls=10, window=60)
def rsvp_get(couple, token):
    return _rsvp_get_impl(couple, token)


@rsvp_bp.get("/api/rsvp/<token>")
@rate_limit(max_calls=10, window=60)
def rsvp_get_legacy(token):
    return _rsvp_get_impl(storage.LEGACY_COUPLE_ID, token)


def _rsvp_submit_impl(couple, token):
    if not _enter_couple(couple):
        return jsonify({"error": "This RSVP link isn't valid."}), 404
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
    # Outcome tracking (act -> observe): the agent's nudges are judged by
    # whether a reply followed them, so stamp when this household answered.
    h["responded_at"] = datetime.now().isoformat(timespec="seconds")
    if h.get("plus_one_allowed"):
        h["plus_one_name"] = plus_one_name
    save_guests(guests)

    # Full loop: tell the couple immediately if this response created a conflict.
    from .seating import seating_conflicts  # local import to avoid a cycle
    return jsonify({"ok": True, "rsvp": rsvp, "attending_count": attending,
                    "conflicts_created": seating_conflicts(guests=guests)})


@rsvp_bp.post("/api/rsvp/<couple>/<token>")
@rate_limit(max_calls=5, window=60)
def rsvp_submit(couple, token):
    return _rsvp_submit_impl(couple, token)


@rsvp_bp.post("/api/rsvp/<token>")
@rate_limit(max_calls=5, window=60)
def rsvp_submit_legacy(token):
    return _rsvp_submit_impl(storage.LEGACY_COUPLE_ID, token)
