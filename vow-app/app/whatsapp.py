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

# ---------- provider config ----------
# The sender is provider-neutral: WHATSAPP_PROVIDER picks the wire (twilio |
# meta), and everything above the seam only sees (ok, reason). Migrating to
# Meta's Cloud API later = fill the meta env vars and flip one variable.
PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "twilio").strip().lower()

ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886").strip()

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "").strip()

DEFAULT_CC = os.environ.get("VOW_DEFAULT_CC", "972").strip()
# Sandbox pacing: 1 message per 3 seconds. Tests set this to 0.
SEND_INTERVAL = float(os.environ.get("VOW_WA_SEND_INTERVAL", "3"))

# Twilio error code when the recipient hasn't joined the sandbox;
# Meta codes when the recipient is outside the 24h service window.
NOT_IN_SANDBOX = 63015
META_UNREACHABLE = {131047, 131026}


def _twilio_ready() -> bool:
    return bool(ACCOUNT_SID and AUTH_TOKEN)


def _meta_ready() -> bool:
    return bool(META_ACCESS_TOKEN and META_PHONE_NUMBER_ID)


def configured() -> bool:
    return _meta_ready() if PROVIDER == "meta" else _twilio_ready()


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


def _meta_send(to_digits: str, body: str):
    """One call to Meta's WhatsApp Cloud API. Same (status, payload) contract."""
    import httpx
    resp = httpx.post(
        f"https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages",
        headers={"Authorization": f"Bearer {META_ACCESS_TOKEN}"},
        json={"messaging_product": "whatsapp", "to": to_digits,
              "type": "text", "text": {"body": body}},
        timeout=20,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    return resp.status_code, payload


def send_whatsapp(to_digits: str, body: str):
    """Provider-neutral send. Returns (ok, reason, meta) — reason is '' on
    success, otherwise a stable string the UI/wave delivery can act on."""
    if not configured():
        return False, f"{PROVIDER}_not_configured", {}
    if PROVIDER == "meta":
        status, payload = _meta_send(to_digits, body)
        if status == 200:
            msg = (payload.get("messages") or [{}])[0]
            return True, "", {"sid": msg.get("id", ""), "status": "accepted"}
        code = (payload.get("error") or {}).get("code")
        if code in META_UNREACHABLE:
            return False, "recipient_unreachable", {}
        return False, f"meta_error_{code or status}", {}
    status, payload = _twilio_send(to_digits, body)
    if status in (200, 201):
        return True, "", {"sid": payload.get("sid", ""),
                          "status": payload.get("status", "queued")}
    code = payload.get("code")
    if code == NOT_IN_SANDBOX:
        return False, "recipient_not_in_sandbox", {}
    return False, f"twilio_error_{code or status}", {}


def _couple_bits(guests: dict):
    profile = storage.load("profile", {}) or {}
    names = " & ".join(n for n in [profile.get("partner_a"), profile.get("partner_b")] if n) or "us"
    date_str = ""
    raw = guests.get("settings", {}).get("wedding_date", "")
    if raw:
        try:
            date_str = datetime.strptime(raw, "%Y-%m-%d").strftime("%B %-d")
        except ValueError:
            date_str = raw
    return names, date_str


def _magic_link(base_url: str, guests: dict, household: dict) -> str:
    """The household's personal RSVP link (token created on demand)."""
    from .rsvp import ensure_tokens  # local import to avoid a cycle
    if ensure_tokens(guests):
        save_guests(guests)
    return (base_url.rstrip("/")
            + f"/rsvp/{storage.current_couple()}/{household['rsvp_token']}")


def _build_message(guests: dict, household: dict, base_url: str) -> str:
    names, date_str = _couple_bits(guests)
    link = _magic_link(base_url, guests, household)
    return (f"Hi! Just a gentle reminder about {names}'s wedding"
            + (f" on {date_str}" if date_str else "")
            + f" — we'd love to know if you can make it. You can reply here: {link}")


def personalize(template: str, guests: dict, household: dict, base_url: str) -> str:
    """Fill a wave message's placeholders for one household; falls back to the
    default reminder text when the couple didn't write a message."""
    if not (template or "").strip():
        return _build_message(guests, household, base_url)
    text = template.replace("[name]", household.get("household", ""))
    if "[rsvp link]" in text:
        text = text.replace("[rsvp link]", _magic_link(base_url, guests, household))
    return text


def _log_nudge(household: dict, channel: str):
    """Same shape as POST /api/activity (a plain capped list)."""
    profile = storage.load("profile", {}) or {}
    entries = storage.load("activity", [])
    entries.append({"actor": profile.get("partner_a") or "You",
                    "text": f"nudged the {household['household']} on WhatsApp ({channel})",
                    "at": datetime.now().isoformat(timespec="seconds")})
    storage.save("activity", entries[-50:])


def nudge_household_by_id(hid: str, base_url: str):
    """Server-side nudge with no request context — the seam agent-initiated
    sends (trust tier 1 / approved proposals) go through. Same behavior as
    the button endpoint: build, normalize, send, stamp, log.
    Returns (ok, reason)."""
    guests = load_guests()
    household = next((h for h in guests["households"] if h.get("id") == hid), None)
    if household is None:
        return False, "unknown_household"
    phone = normalize_phone(household.get("phone", ""))
    if not phone:
        return False, "no_valid_phone"
    message = _build_message(guests, household, base_url)
    ok, reason, _meta = send_whatsapp(phone, message)
    if ok:
        household["last_nudged_at"] = datetime.utcnow().isoformat() + "Z"
        save_guests(guests)
        _log_nudge(household, "sent by Vow")
    return ok, reason


@whatsapp_bp.post("/api/guests/<hid>/nudge")
@rate_limit(max_calls=10, window=60)
def nudge(hid):
    guests = load_guests()
    household = next((h for h in guests["households"] if h.get("id") == hid), None)
    if household is None:
        return jsonify({"error": "Unknown household."}), 404

    message = _build_message(guests, household, request.host_url)
    phone = normalize_phone(household.get("phone", ""))

    def fallback(reason):
        from urllib.parse import quote
        wa = (f"https://wa.me/{phone}?text={quote(message)}" if phone
              else f"https://wa.me/?text={quote(message)}")
        return jsonify({"sent": False, "fallback": True, "reason": reason,
                        "wa_url": wa})

    if not phone:
        return fallback("no_valid_phone")

    ok, reason, meta = send_whatsapp(phone, message)
    if ok:
        household["last_nudged_at"] = datetime.utcnow().isoformat() + "Z"
        save_guests(guests)
        _log_nudge(household, "sent automatically")
        return jsonify({"sent": True, "sid": meta.get("sid", ""),
                        "status": meta.get("status", "queued")})
    return fallback(reason)


# ---------- wave delivery (invitations page) ----------

def deliver_wave(base_url: str, wave_id: str, emit=lambda e: None):
    """Send a just-sent wave's message to every frozen recipient, paced for
    the sandbox. Runs inside a background job (couple context inherited);
    records per-recipient results on the wave document as it goes."""
    import time
    from .invitations import load_invitations, save_invitations

    guests = load_guests()
    data = load_invitations(guests)
    wave = next((w for w in data["waves"] if w["id"] == wave_id), None)
    if wave is None or wave.get("status") != "sent":
        return {"error": "wave not found or not sent"}

    by_id = {h["id"]: h for h in guests["households"]}
    delivery = {"sent": [], "failed": [], "finished": False}
    wave["delivery"] = delivery

    for i, hid in enumerate(wave.get("sent_to", [])):
        household = by_id.get(hid)
        if household is None:
            delivery["failed"].append({"id": hid, "reason": "household_removed"})
            continue
        phone = normalize_phone(household.get("phone", ""))
        if not phone:
            delivery["failed"].append({"id": hid, "reason": "no_valid_phone"})
            continue
        if i and SEND_INTERVAL:
            time.sleep(SEND_INTERVAL)  # sandbox: 1 message / 3s
        message = personalize(wave.get("message", ""), guests, household, base_url)
        ok, reason, _meta = send_whatsapp(phone, message)
        if ok:
            delivery["sent"].append(hid)
        else:
            delivery["failed"].append({"id": hid, "reason": reason})
        save_invitations(data)  # durable progress, one document write per send
        emit(f"{household['household']}: {'sent' if ok else reason}")

    delivery["finished"] = True
    delivery["finished_at"] = datetime.now().isoformat(timespec="seconds")
    save_invitations(data)
    return {"sent": len(delivery["sent"]), "failed": len(delivery["failed"])}
