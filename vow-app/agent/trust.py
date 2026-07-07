"""Trust tiers: graduated autonomy the agent EARNS, the couple controls.

Every agent-initiated action has a tier:

  tier 0  auto               code does it silently (e.g. checklist auto-checks)
  tier 1  execute-and-report Vow acts on its own, then tells the couple
  tier 2  propose-and-wait   Vow leaves a proposal notice; nothing happens
                             until the couple clicks Approve

Tiers move with history: approving the same kind of proposal PROMOTE_AFTER
times in a row promotes it to tier 1 (Vow says so in a notice); one rejection
resets the streak; the couple can demote any action at any time via the API.
The decision arithmetic is code — no model decides its own autonomy.
"""

import os
from datetime import datetime

import storage

TRUST_DOC = "trust"
PROMOTE_AFTER = int(os.environ.get("VOW_TRUST_PROMOTE_AFTER", 10))
HISTORY_KEEP = 30

# The registry of agent-initiated actions and their starting tiers.
ACTIONS = {
    "send_nudge": {
        "tier": 2,
        "label": "Send WhatsApp reminders to households that haven't replied",
    },
    "refresh_brief": {
        "tier": 1,  # already bounded by the daily wake cap in agent.triggers
        "label": "Re-check everything and refresh the brief when data changes",
    },
    "auto_seat": {
        "tier": 1,  # code validation gates hard-invalid plans (Step 15b)
        "label": "Apply a code-validated seating arrangement",
    },
}


def get_trust() -> dict:
    """The trust doc merged over registry defaults (unknown keys dropped)."""
    saved = storage.load(TRUST_DOC, default={}) or {}
    saved_actions = saved.get("actions", {})
    out = {}
    for name, spec in ACTIONS.items():
        s = saved_actions.get(name, {})
        out[name] = {
            "label": spec["label"],
            "tier": int(s.get("tier", spec["tier"])),
            "streak": int(s.get("streak", 0)),
            "earned": bool(s.get("earned", False)),
            "history": s.get("history", []),
        }
    return out


def _save(actions: dict):
    storage.save(TRUST_DOC, {"actions": actions})


def tier(action: str) -> int:
    spec = get_trust().get(action)
    return spec["tier"] if spec else 2  # unknown actions never auto-execute


def set_tier(action: str, new_tier: int) -> dict:
    """Couple override. Only 1 (act-and-report) and 2 (always ask) are
    settable — tier 0 is reserved for actions code makes safe by design."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'.")
    if new_tier not in (1, 2):
        raise ValueError("Tier must be 1 (act and tell me) or 2 (always ask).")
    actions = get_trust()
    actions[action]["tier"] = new_tier
    if new_tier == 2:
        actions[action]["streak"] = 0
        actions[action]["earned"] = False
    _save(actions)
    return actions[action]


def record_decision(action: str, approved: bool, source: str = "notice") -> dict:
    """Called when the couple approves or rejects a proposal. Approval streaks
    earn autonomy; a single rejection resets the streak (and takes back an
    earned — not a couple-chosen — promotion)."""
    if action not in ACTIONS:
        return {}
    actions = get_trust()
    spec = actions[action]
    spec["history"] = (spec["history"] + [{
        "at": datetime.now().isoformat(timespec="seconds"),
        "approved": bool(approved), "source": source,
    }])[-HISTORY_KEEP:]

    promoted = False
    if approved:
        spec["streak"] += 1
        if spec["tier"] == 2 and spec["streak"] >= PROMOTE_AFTER:
            spec["tier"], spec["earned"], promoted = 1, True, True
    else:
        spec["streak"] = 0
        if spec["tier"] == 1 and spec["earned"]:
            spec["tier"], spec["earned"] = 2, False  # trust is revocable
    _save(actions)

    if promoted:
        from agent.triggers import add_notice  # local import: avoid a cycle
        add_notice("promoted",
                   f"You've approved my last {PROMOTE_AFTER} “{spec['label'].lower()}” "
                   f"suggestions, so I'll do that myself from now on — and always "
                   f"tell you here. One click takes it back.",
                   source="trust", action=action)
    return spec


# ---------------------------------------------------------------------------
# executing an approved (or tier-1 auto) proposal
# ---------------------------------------------------------------------------

def execute(action: str, payload: dict, base_url: str) -> dict:
    """Run an agent-initiated action. Every branch is plain code calling the
    same seams the UI buttons use — the agent gets no new powers here."""
    if action == "send_nudge":
        from app.whatsapp import nudge_household_by_id

        sent, failed = [], []
        for hid in (payload or {}).get("household_ids", [])[:20]:
            ok, reason = nudge_household_by_id(hid, base_url)
            (sent if ok else failed).append({"id": hid, "reason": reason})
        return {"action": action, "sent": len(sent), "failed": len(failed),
                "details": {"sent": sent, "failed": failed}}
    raise ValueError(f"Unknown action '{action}'.")
