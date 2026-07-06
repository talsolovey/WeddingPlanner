"""Floating AI chat + invitation message generation.

Both endpoints are thin LLM proxies (single completion, no tools): the couple's
full data snapshot is composed server-side and injected as the system prompt,
so the model answers with real numbers. The client never sees the snapshot
assembly and can't override the system prompt.
"""

import os
from datetime import date
from pathlib import Path

import openai
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

from .core import rate_limit
from .budget import load_budget
from .contracts import load_contracts
from .guests import load_guests
from .profile import load_profile
from .seating import load_seating, seating_conflicts, _table_load

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

chat_bp = Blueprint("chat", __name__)

CHAT_MODEL = os.environ.get("VOW_CHAT_MODEL", "gpt-4o")
MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 2000

FAIL_COPY = "I couldn't reach my brain just now — try me again in a moment."


def _fmt_money(n) -> str:
    return f"${round(n):,}"


def _days_away(date_str: str):
    try:
        return (date.fromisoformat(date_str) - date.today()).days
    except (TypeError, ValueError):
        return None


def couple_snapshot() -> str:
    """The couple's whole wedding, compressed into a system-prompt data block
    (mirrors the CONTEXT string in the design's Vow Chat reference)."""
    profile = load_profile()
    budget = load_budget()
    guests = load_guests()
    seating = load_seating()
    contracts = load_contracts()

    names = " & ".join(n for n in (profile["partner_a"], profile["partner_b"]) if n) or "the couple"
    settings = guests["settings"]
    wedding_date = settings.get("wedding_date", "")
    days = _days_away(wedding_date)
    when = wedding_date or "not set yet"
    if days is not None:
        when = f"{wedding_date} ({days} days away)"
    venue = profile.get("venue") or "venue TBD"

    # Budget lines by state
    items = budget["items"]
    committed = sum((i.get("contracted") or i.get("estimated") or 0) for i in items)
    paid = sum(i.get("paid") or 0 for i in items)
    cap = budget.get("total_budget", 0)
    over = committed - cap

    def line(i):
        amt = i.get("contracted") or i.get("estimated") or 0
        who = f" {i['vendor']}" if i.get("vendor") else ""
        extra = f" ({_fmt_money(i['paid'])} paid)" if i.get("paid") else ""
        return f"{i['category']}{who} {_fmt_money(amt)}{extra}"

    signed = [line(i) for i in items if i.get("contracted")]
    quotes = [line(i) for i in items if not i.get("contracted") and i.get("vendor") and i.get("estimated")]
    estimates = [line(i) for i in items if not i.get("contracted") and not i.get("vendor") and i.get("estimated")]

    # Guests
    households = guests["households"]
    invited = sum(h.get("party_size", 0) for h in households)
    confirmed = sum(h.get("attending_count", 0) for h in households if h.get("rsvp") == "confirmed")
    pending = [h for h in households if h.get("rsvp") == "pending"]
    no_reply = [h for h in households if h.get("rsvp") == "no_response"]
    declined = [h["household"] for h in households if h.get("rsvp") == "declined"]
    outstanding_party = sum(h.get("party_size", 0) for h in pending + no_reply)
    likely = confirmed + round(outstanding_party * 0.75)
    hh = lambda h: f"{h['household']} ({h.get('party_size', 0)})"

    # Seating
    by_id = {h["id"]: h for h in households}
    tables = seating["tables"]
    seats = sum(t.get("capacity", 0) for t in tables)
    seated_ids = {hid for t in tables for hid in t.get("households", [])}
    seated_people = sum(_table_load(t, by_id) for t in tables)
    unseated = [hh(h) for h in households
                if h.get("rsvp") == "confirmed" and h["id"] not in seated_ids]
    conflicts = seating_conflicts(guests, seating)

    # Contract flags
    flag_lines = []
    for c in contracts:
        analysis = c.get("analysis") or {}
        flags = [f.get("issue") or f.get("clause", "") for f in analysis.get("red_flags", [])]
        if flags:
            flag_lines.append(f"{c['vendor']}: " + "; ".join(flags[:4]))

    has_contingency = any("contingency" in i["category"].lower() for i in items)
    parts = [
        f"You are Vow, a warm, concise AI wedding planner talking with the couple {names}. "
        f"Their wedding: {when} at {venue}.",
        "Data you know:",
        f"- Budget: {_fmt_money(cap)} cap; {_fmt_money(committed)} committed "
        f"({_fmt_money(abs(over))} {'over' if over > 0 else 'under'}); {_fmt_money(paid)} paid. "
        + (f"Signed: {', '.join(signed)}. " if signed else "")
        + (f"Unsigned quotes: {', '.join(quotes)}. " if quotes else "")
        + (f"Estimates: {', '.join(estimates)}. " if estimates else "")
        + ("" if has_contingency else "No contingency budgeted."),
        f"- Guests: {invited} invited across {len(households)} households; {confirmed} confirmed; "
        + (f"pending: {', '.join(hh(h) for h in pending)}; " if pending else "")
        + (f"no reply past deadline: {', '.join(hh(h) for h in no_reply)}; " if no_reply else "")
        + (f"Declined: {', '.join(declined)}. " if declined else "")
        + f"Venue holds {settings.get('venue_capacity', 0)}; catering "
          f"{_fmt_money(settings.get('catering_per_head', 0))}/head; likely headcount ~{likely}.",
        f"- Seating: {len(tables)} tables, {seats} seats, {seated_people} seated."
        + (f" Unseated confirmed: {', '.join(unseated)}." if unseated else "")
        + (f" Conflicts: {' '.join(conflicts[:4])}" if conflicts else ""),
    ]
    if flag_lines:
        parts.append("- Contract flags: " + " | ".join(flag_lines))
    parts.append(
        "Style: answer in 2-5 short sentences, specific numbers, one clear recommendation. "
        "Be a partner in debate — give a view, but respect their preferences. "
        "No markdown headers or bullet spam.")
    return "\n".join(p for p in parts if p)


def llm_complete(system: str, messages: list, max_tokens: int = 400) -> str:
    """One plain completion (shared by chat + invitation writing)."""
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


@chat_bp.post("/api/chat")
@rate_limit(max_calls=10, window=60)
def chat():
    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("messages")
    if not isinstance(raw, list) or not raw:
        return jsonify({"error": "messages is required."}), 400

    messages = []
    for m in raw[-MAX_MESSAGES:]:
        role = str((m or {}).get("role", ""))
        content = str((m or {}).get("content", "")).strip()[:MAX_MESSAGE_CHARS]
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "The last message must be from you."}), 400

    try:
        reply = llm_complete(couple_snapshot(), messages)
    except Exception:
        return jsonify({"error": FAIL_COPY}), 502
    return jsonify({"reply": reply or FAIL_COPY})
