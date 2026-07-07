"""Event-driven wake-ups: Vow notices changes instead of waiting for a click.

The flow (all per couple):

  data write -> record_change() -> debounce timer -> triage()
      triage: deterministic RULES over the live data decide severity
        high    -> wake the weekly-brief orchestrator (hard daily cap),
                   cache the fresh brief, leave a "Vow noticed" notice
        medium  -> leave a notice on the home dashboard
        nothing -> stay quiet

Design rules:
  - The DECISION is deterministic code (auditable, testable, no model flake).
    A model may optionally *phrase* the notice (VOW_TRIAGE_LLM=1), never decide.
  - Hard caps beat cleverness: at most VOW_MAX_WAKES_PER_DAY orchestrator
    wakes; a capped-out high signal degrades to a notice.
  - Re-entrancy guarded: a triage-triggered brief writing data must not
    re-trigger triage.
  - Everything here degrades to silence on error — a broken trigger must
    never break a save.
"""

import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta

import storage

WATCHED = {"guests", "budget", "seating", "contracts"}
NOTICES_DOC = "notices"
STATE_DOC = "agent_state"
MAX_NOTICES = 20

DEBOUNCE_S = float(os.environ.get("VOW_TRIGGER_DEBOUNCE_S", 90))
MAX_WAKES_PER_DAY = int(os.environ.get("VOW_MAX_WAKES_PER_DAY", 2))
DECLINE_SPIKE = int(os.environ.get("VOW_DECLINE_SPIKE", 3))
DEADLINE_SOON_DAYS = 7

_LOCK = threading.Lock()
_PENDING = {}   # couple_key -> set of changed dataset names
_TIMERS = {}    # couple_key -> threading.Timer
_TRIAGING = set()  # couples with a triage in flight (re-entrancy guard)


def _key(couple):
    return couple or "__legacy__"


# ---------------------------------------------------------------------------
# the hook storage.save() calls
# ---------------------------------------------------------------------------

def record_change(couple, name: str):
    """Called on every document save. Cheap: notes the change and (re)arms a
    debounce timer so a burst of edits triggers one look, not twenty."""
    if name not in WATCHED:
        return
    key = _key(couple)
    with _LOCK:
        if key in _TRIAGING:
            return  # our own wake caused this write
        _PENDING.setdefault(key, set()).add(name)
        timer = _TIMERS.get(key)
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(DEBOUNCE_S, _triage_thread, args=(couple,))
        timer.daemon = True
        _TIMERS[key] = timer
        timer.start()


def _triage_thread(couple):
    try:
        storage.set_couple(couple)
        triage(couple)
    except Exception:
        pass  # noticing is best-effort, never load-bearing


# ---------------------------------------------------------------------------
# deterministic rules: what in the changed data deserves attention?
# ---------------------------------------------------------------------------

def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def signals(guests: dict = None, budget: dict = None, now: datetime = None) -> list:
    """Pure function over the live data -> [{kind, severity, detail}].
    Every rule is arithmetic; nothing here guesses."""
    now = now or datetime.now()
    guests = guests if guests is not None else storage.load("guests", {}) or {}
    budget = budget if budget is not None else storage.load("budget", {}) or {}
    households = guests.get("households", [])
    settings = guests.get("settings", {})
    found = []

    # 1. Decline spike: several "not coming" answers within 24h.
    recent_declines = [
        h for h in households
        if h.get("rsvp") == "declined"
        and (_parse_dt(h.get("responded_at")) or datetime.min) > now - timedelta(hours=24)
    ]
    if len(recent_declines) >= DECLINE_SPIKE:
        found.append({
            "kind": "decline_spike", "severity": "high",
            "detail": f"{len(recent_declines)} households declined in the last 24h "
                      f"({', '.join(h.get('household', '?') for h in recent_declines[:4])}…)",
        })

    # 2. Capacity breach: confirmed heads now exceed the venue.
    capacity = settings.get("venue_capacity") or 0
    confirmed = sum(h.get("attending_count", 0) for h in households
                    if h.get("rsvp") == "confirmed")
    if capacity and confirmed > capacity:
        found.append({
            "kind": "capacity_breach", "severity": "high",
            "detail": f"{confirmed} confirmed guests vs venue capacity {capacity}",
        })

    # 3. RSVP deadline crunch: deadline within a week, many still silent.
    deadline = _parse_dt(settings.get("rsvp_deadline"))
    if deadline is not None:
        days_left = (deadline - now).days
        pending = [h for h in households
                   if h.get("rsvp") in ("pending", "no_response")]
        if 0 <= days_left <= DEADLINE_SOON_DAYS and len(pending) >= 3:
            found.append({
                "kind": "rsvp_deadline_crunch", "severity": "medium",
                "detail": f"RSVP deadline in {days_left} day(s) with "
                          f"{len(pending)} households still unanswered",
            })

    # 4. Budget overrun: committed money now above the couple's ceiling.
    total_budget = budget.get("total_budget") or 0
    committed = sum(
        max(i.get("contracted") or 0, i.get("estimated") or 0, i.get("paid") or 0)
        for i in budget.get("items", [])
    )
    if total_budget and committed > total_budget:
        found.append({
            "kind": "budget_overrun", "severity": "medium",
            "detail": f"committed ${committed:,.0f} vs budget ${total_budget:,.0f}",
        })

    return found


# ---------------------------------------------------------------------------
# notices + wake bookkeeping
# ---------------------------------------------------------------------------

def add_notice(kind: str, message: str, source: str = "triage", **extra) -> dict:
    """kinds: noticed | brief_refreshed | proposal | acted | promoted.
    Proposals carry `action` + `payload` so Approve can execute them later."""
    notice = {
        "id": uuid.uuid4().hex[:8],
        "at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind, "message": message, "source": source, "read": False,
        **extra,
    }
    doc = storage.load(NOTICES_DOC, default={"items": []})
    doc["items"] = (doc.get("items", []) + [notice])[-MAX_NOTICES:]
    storage.save(NOTICES_DOC, doc)
    return notice


def _wake_allowed(state: dict, today: str) -> bool:
    wakes = [w for w in state.get("wakes", []) if w.get("date") == today]
    return len(wakes) < MAX_WAKES_PER_DAY


def _record_wake(state: dict, today: str, reason: str):
    state.setdefault("wakes", []).append(
        {"date": today, "at": datetime.now().isoformat(timespec="seconds"),
         "reason": reason})
    state["wakes"] = state["wakes"][-30:]
    storage.save(STATE_DOC, state)


def _phrase(found: list) -> str:
    """The notice text. Deterministic by default; VOW_TRIAGE_LLM=1 lets a small
    model word it more warmly (phrasing only — never the decision)."""
    plain = "; ".join(s["detail"] for s in found)
    if os.environ.get("VOW_TRIAGE_LLM") != "1":
        return plain
    try:
        import openai

        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        r = client.chat.completions.create(
            model=os.environ.get("VOW_TRIAGE_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content":
                    "Rewrite this wedding-planning alert as ONE warm, plain "
                    "sentence for the couple. No advice, just what changed."},
                {"role": "user", "content": plain},
            ],
        )
        return (r.choices[0].message.content or plain).strip()[:300]
    except Exception:
        return plain


# ---------------------------------------------------------------------------
# the triage itself
# ---------------------------------------------------------------------------

def triage(couple, run_brief=None) -> dict:
    """Look at what changed and decide: wake the orchestrator, leave a notice,
    or stay quiet. Returns a summary dict (also handy for tests).
    `run_brief` is injectable for tests; defaults to the real orchestrator."""
    key = _key(couple)
    with _LOCK:
        changed = _PENDING.pop(key, set())
        _TIMERS.pop(key, None)
        if key in _TRIAGING:
            return {"action": "skipped", "reason": "already triaging"}
        _TRIAGING.add(key)
    try:
        found = signals()
        if not found:
            return {"action": "quiet", "signals": []}

        today = date.today().isoformat()
        state = storage.load(STATE_DOC, default={})
        high = [s for s in found if s["severity"] == "high"]
        message = _phrase(found)

        # Independent of severity: silent-guest signals may spawn a nudge
        # action, gated by the couple's trust tier for "send_nudge".
        proposal = _maybe_nudge_action(found)

        if high and _wake_allowed(state, today):
            _record_wake(state, today, ", ".join(s["kind"] for s in high))
            (run_brief or _run_brief)(today)
            add_notice("brief_refreshed",
                       f"Something changed, so I re-checked everything — {message}. "
                       f"Your brief is fresh.")
            return {"action": "ran_brief", "signals": found,
                    "changed": sorted(changed), "nudge": proposal}

        add_notice("noticed", message)
        return {"action": "notice", "signals": found,
                "changed": sorted(changed), "nudge": proposal}
    finally:
        with _LOCK:
            _TRIAGING.discard(key)


def _maybe_nudge_action(found: list):
    """RSVP deadline crunch -> a concrete nudge action for the silent-but-
    phoneable households. What happens depends on the earned trust tier:
      tier 2 (default): a proposal notice — nothing sent until Approve
      tier 1 (earned or chosen): send now, then report in a notice
    Never duplicates an open proposal."""
    if not any(s["kind"] == "rsvp_deadline_crunch" for s in found):
        return None
    try:
        from agent import trust

        guests = storage.load("guests", {}) or {}
        targets = [h for h in guests.get("households", [])
                   if h.get("rsvp") in ("pending", "no_response") and h.get("phone")]
        if not targets:
            return None
        ids = [h["id"] for h in targets][:20]
        names = ", ".join(h.get("household", "?") for h in targets[:4])
        more = f" (+{len(targets) - 4} more)" if len(targets) > 4 else ""

        doc = storage.load(NOTICES_DOC, default={"items": []})
        if any(n.get("kind") == "proposal" and n.get("action") == "send_nudge"
               and not n.get("read") for n in doc.get("items", [])):
            return {"action": "already_proposed"}

        if trust.tier("send_nudge") == 1:
            base_url = os.environ.get("VOW_BASE_URL", "http://localhost:5050/")
            result = trust.execute("send_nudge", {"household_ids": ids}, base_url)
            if result["sent"]:
                add_notice("acted",
                           f"The RSVP deadline is close, so I sent WhatsApp reminders "
                           f"to {result['sent']} household(s): {names}{more}. "
                           f"I'll tell you who replies.", action="send_nudge")
                return {"action": "executed", **{k: result[k] for k in ("sent", "failed")}}
            # nothing went through (e.g. WhatsApp unconfigured) — fall back to asking

        add_notice("proposal",
                   f"{len(targets)} household(s) still haven't replied and the "
                   f"deadline is close: {names}{more}. Want me to send them a "
                   f"WhatsApp reminder?", action="send_nudge",
                   payload={"household_ids": ids})
        return {"action": "proposed", "count": len(targets)}
    except Exception:
        return None  # a broken proposal must not break triage


def _run_brief(today: str):
    """Wake the product's own agent (same path as the button and the scheduled
    run): orchestrator + outcome facts, cached to the home dashboard."""
    from agent.orchestrator import WeeklyBriefOrchestrator
    from agent.outcomes import record_brief_run, weekly_extra_facts

    result = WeeklyBriefOrchestrator().run(today, extra_facts=weekly_extra_facts())
    record_brief_run(result, today)
    storage.save("brief", dict(
        result, generated_at=datetime.now().isoformat(timespec="seconds")))
