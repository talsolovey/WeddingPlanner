"""WhatsApp nudges via the Twilio Sandbox — real server-side sending.

How it works:
- POST /api/guests/<id>/nudge (couple session required, rate-limited) builds
  the reminder server-side (couple names, date, the household's magic RSVP
  link) and sends it through Twilio's WhatsApp API.
- Sandbox reality: only phones that have joined the couple's Twilio sandbox
  (send "join <code>" to the sandbox number) can receive. If Twilio isn't
  configured, the phone is missing/unjoined, or the send fails, the endpoint
  returns a `fallback` payload with a wa.me click-to-chat URL so the UI can
  degrade to the old manual flow.
- Credentials live in env (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN /
  TWILIO_WHATSAPP_FROM), read server-side only — the browser never sees them.

Phone normalization: guests type numbers however they like ("050-123 4567");
wa.me and Twilio both need E.164 digits. `normalize_phone` strips formatting
and applies a default country code (VOW_DEFAULT_CC, default 972) to
local-format numbers.
"""

import os
from datetime import datetime

from flask import Blueprint, jsonify, request

import storage
from .core import rate_limit
from .guests import load_guests, save_guests

whatsapp_bp = Blueprint("whatsapp", __name__)

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886").strip()
DEFAULT_CC = os.environ.get("VOW_DEFAULT_CC", "972").strip()

# Twilio error code when the recipient hasn't joined the sandbox.
NOT_IN_SANDBOX = 63015


def _twilio_ready() -> bool:
    return bool(ACCOUNT_SID and AUTH_TOKEN)


def normalize_phone(raw: str, default_cc: str = None) -> str:
    """Best-effort E.164 digits (no '+'): '050-123 4567' -> '972501234567'.
    Returns '' if it can't make sense of the input."""
    cc = default_cc or DEFAULT_CC
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("00"):          # international dialing prefix
        digits = digits[2:]
    elif digits.startswith("0"):         # local format -> default country
        digits = cc + digits[1:]
    if not 8 <= len(digits) <= 15:
        return ""
    return digits


def _twilio_send(to_digits: str, body: str):
    """One call to Twilio's Messages API. Returns (http_status, response dict).
    Kept as a single seam so the offline tests can monkeypatch it."""
    import httpx
    resp = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json",
        auth=(ACCOUNT_SID, AUTH_TOKEN),
        data={"From": WHATSAPP_FROM, "To": f"whatsapp:+{to_digits}", "Body": body},
        timeout=20,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    return resp.status_code, payload


def _build_message(guests: dict, household: dict) -> str:
    profile = storage.load("profile", {}) or {}
    names = " & ".join(n for n in [profile.get("partner_a"), profile.get("partner_b")] if n) or "us"
    date_str = ""
    raw = guests.get("settings", {}).get("wedding_date", "")
    if raw:
        try:
            date_str = datetime.strptime(raw, "%Y-%m-%d").strftime("%B %-d")
        except ValueError:
            date_str = raw
    # The household's personal magic link (created on demand, like rsvp-links).
    from .rsvp import ensure_tokens  # local import to avoid a cycle
    if ensure_tokens(guests):
        save_guests(guests)
    link = (request.host_url.rstrip("/")
            + f"/rsvp/{storage.current_couple()}/{household['rsvp_token']}")
    return (f"Hi! Just a gentle reminder about {names}'s wedding"
            + (f" on {date_str}" if date_str else "")
            + f" — we'd love to know if you can make it. You can reply here: {link}")


def _log_nudge(household: dict, channel: str):
    """Same shape as POST /api/activity (a plain capped list)."""
    profile = storage.load("profile", {}) or {}
    entries = storage.load("activity", [])
    entries.append({"actor": profile.get("partner_a") or "You",
                    "text": f"nudged the {household['household']} on WhatsApp ({channel})",
                    "at": datetime.now().isoformat(timespec="seconds")})
    storage.save("activity", entries[-50:])


@whatsapp_bp.post("/api/guests/<hid>/nudge")
@rate_limit(max_calls=10, window=60)
def nudge(hid):
    guests = load_guests()
    household = next((h for h in guests["households"] if h.get("id") == hid), None)
    if household is None:
        return jsonify({"error": "Unknown household."}), 404

    message = _build_message(guests, household)
    phone = normalize_phone(household.get("phone", ""))

    def fallback(reason):
        from urllib.parse import quote
        wa = (f"https://wa.me/{phone}?text={quote(message)}" if phone
              else f"https://wa.me/?text={quote(message)}")
        return jsonify({"sent": False, "fallback": True, "reason": reason,
                        "wa_url": wa})

    if not phone:
        return fallback("no_valid_phone")
    if not _twilio_ready():
        return fallback("twilio_not_configured")

    status, payload = _twilio_send(phone, message)
    if status in (200, 201):
        household["last_nudged_at"] = datetime.utcnow().isoformat() + "Z"
        save_guests(guests)
        _log_nudge(household, "sent automatically")
        return jsonify({"sent": True, "sid": payload.get("sid", ""),
                        "status": payload.get("status", "queued")})

    code = payload.get("code")
    if code == NOT_IN_SANDBOX:
        return fallback("recipient_not_in_sandbox")
    return fallback(f"twilio_error_{code or status}")
