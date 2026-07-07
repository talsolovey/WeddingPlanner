"""Outcome tracking: did Vow's past actions actually work? (act -> OBSERVE)

Deterministic, code-computed facts about the consequences of the agent's own
actions, handed to the weekly-brief merge as trusted `computed_facts`:

  - nudge outcomes  — which nudged households replied afterwards, which stayed
                      silent (so the brief can escalate: "try a phone call")
  - follow-through  — action items that keep reappearing brief after brief,
                      i.e. advice the couple hasn't acted on (so the brief can
                      say so plainly instead of repeating itself)

Nothing in this module calls a model: outcomes are arithmetic over timestamps
the app already records. The model only gets to *react* to these facts.
"""

from datetime import date, datetime

import storage

HISTORY_DOC = "brief_history"
KEEP_RUNS = 12          # how many past briefs to remember
SILENT_AFTER_DAYS = 2   # a nudge unanswered this long counts as ignored
MAX_LISTED = 8          # cap name lists fed into the merge prompt

_STOPWORDS = {
    "the", "a", "an", "to", "of", "for", "and", "or", "on", "in", "with",
    "your", "their", "is", "are", "be", "this", "that", "it", "you",
    "still", "now", "again", "yet",
}


def _stem(word: str) -> str:
    """Just enough stemming to match reworded advice: book/booking/booked."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _parse_dt(value):
    """Tolerant ISO parse; date-only strings become midnight. None on junk.
    Timestamps mix styles in the app (nudges are UTC+'Z', RSVPs local naive),
    so normalize everything to naive — day-scale arithmetic doesn't care."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# nudge -> reply
# ---------------------------------------------------------------------------

def nudge_outcomes(guests: dict, now: datetime = None):
    """Compute what happened after each WhatsApp nudge. Returns None when no
    household has ever been nudged (no signal -> no noise in the prompt)."""
    now = now or datetime.now()
    nudged, replied, silent = 0, 0, []
    for h in (guests or {}).get("households", []):
        nudged_at = _parse_dt(h.get("last_nudged_at"))
        if nudged_at is None:
            continue
        nudged += 1
        responded_at = _parse_dt(h.get("responded_at"))
        if responded_at is not None and responded_at >= nudged_at:
            replied += 1
        elif h.get("rsvp") in ("pending", "no_response"):
            days = (now - nudged_at).days
            if days >= SILENT_AFTER_DAYS:
                silent.append({"household": h.get("household", "?"),
                               "days_since_nudge": days})
    if nudged == 0:
        return None
    silent.sort(key=lambda s: -s["days_since_nudge"])
    return {
        "households_nudged": nudged,
        "replied_after_nudge": replied,
        "reply_rate": round(replied / nudged, 2),
        "still_silent": silent[:MAX_LISTED],
    }


# ---------------------------------------------------------------------------
# brief follow-through
# ---------------------------------------------------------------------------

def _tokens(title: str) -> set:
    return {_stem(w) for w in "".join(
        c.lower() if c.isalnum() else " " for c in str(title or "")
    ).split() if w not in _STOPWORDS}


def _similar(a: str, b: str) -> bool:
    """Same advice, reworded: token Jaccard >= 0.5 (models rephrase titles)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


def record_brief_run(result: dict, today: str = None):
    """Remember what this brief suggested, so next week we can tell repeats
    from news. Called after every orchestrator run; failures never block."""
    try:
        items = [
            {"title": str(i.get("title", ""))[:120],
             "area": i.get("area", ""), "priority": i.get("priority", "")}
            for i in ((result or {}).get("analysis") or {}).get("action_items", [])
            if isinstance(i, dict) and i.get("title")
        ]
        history = storage.load(HISTORY_DOC, default={"runs": []})
        history["runs"] = (history.get("runs", []) + [
            {"date": today or date.today().isoformat(), "items": items}
        ])[-KEEP_RUNS:]
        storage.save(HISTORY_DOC, history)
    except Exception:
        pass  # observability must never sink the brief itself


def repeated_items(history: dict, min_times: int = 2):
    """Items suggested in >= min_times distinct past briefs (similarity-matched
    within the same area). Returns [{title, area, times_suggested, first_seen}]."""
    runs = (history or {}).get("runs", [])
    clusters = []  # each: {"title", "area", "dates": set}
    for run in runs:
        for item in run.get("items", []):
            title, area = item.get("title", ""), item.get("area", "")
            for c in clusters:
                if c["area"] == area and _similar(c["title"], title):
                    c["title"] = title  # keep the freshest wording
                    c["dates"].add(run.get("date", ""))
                    break
            else:
                clusters.append({"title": title, "area": area,
                                 "dates": {run.get("date", "")}})
    out = [
        {"title": c["title"], "area": c["area"],
         "times_suggested": len(c["dates"]), "first_seen": min(c["dates"])}
        for c in clusters if len(c["dates"]) >= min_times
    ]
    out.sort(key=lambda r: -r["times_suggested"])
    return out[:MAX_LISTED]


# ---------------------------------------------------------------------------
# the facts bundle both brief entry points use
# ---------------------------------------------------------------------------

def weekly_extra_facts() -> dict:
    """Everything code knows that the merge model should treat as ground truth.
    Each part degrades independently: a failure just omits that fact."""
    facts, notes = {}, []
    try:
        from app.seating import seating_conflicts  # local import: avoids a cycle
        facts["seating_conflicts"] = seating_conflicts()
        notes.append("seating_conflicts is computed by code from the live seating "
                     "chart — include unresolved ones as action items (area: guests).")
    except Exception:
        pass
    try:
        outcomes = nudge_outcomes(storage.load("guests", default={}))
        if outcomes:
            facts["nudge_outcomes"] = outcomes
            notes.append("nudge_outcomes is computed by code from RSVP timestamps. "
                         "Households still silent days after a WhatsApp nudge need "
                         "a stronger next step (a phone call, not another nudge).")
    except Exception:
        pass
    try:
        repeats = repeated_items(storage.load(HISTORY_DOC, default={"runs": []}))
        if repeats:
            facts["repeatedly_suggested"] = repeats
            notes.append("repeatedly_suggested lists advice given in past briefs "
                         "more than once. If an item is still true, escalate it and "
                         "say plainly it has been waiting — don't repeat it neutrally.")
    except Exception:
        pass
    if notes:
        facts["note"] = " ".join(notes)
    return facts
