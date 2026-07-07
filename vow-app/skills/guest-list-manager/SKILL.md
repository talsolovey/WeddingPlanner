---
name: guest-list-manager
description: Review a wedding guest list and RSVP status — project the final headcount, reconcile it against venue capacity and the catering per-head budget, flag ambiguous plus-ones and overdue RSVPs. Use when asked to analyze, check, or reconcile the guest list, RSVPs, headcount, or seating capacity.
---

# Guest List Manager

You're helping a couple make sense of their guest list. RSVPs trickle in, plus-ones are
fuzzy, and the catering bill is driven by the final headcount. Your job is to turn a
half-answered list into a clear picture: how many people are *actually* coming, whether
that fits the room and the budget, and what the couple needs to chase before the deadline.

Read the data with `read_data("guests")`. It has a `settings` block
(`venue_capacity`, `catering_per_head`, `currency`, `rsvp_deadline`, `wedding_date`) and a
list of `households`, each with a `party_size`, an `rsvp` status
(`confirmed | declined | pending | no_response`), an `attending_count`, and plus-one fields.

## How to project the headcount

Don't just count the confirmed. Reason about the uncertain middle:

- **Confirmed** — use `attending_count`. This is your floor.
- **Plus-ones** — if `plus_one_allowed` is true but `plus_one_name` is empty, that's an
  *uncertain* head: include it in the optimistic projection, not the floor, and flag it.
- **Pending / no_response** — these are a range, not a zero. Historically ~75–85% of
  invited guests attend. Estimate likely attendance from `party_size` (note your
  assumed rate), and treat `no_response` past the `rsvp_deadline` as low-probability but
  not impossible.
- Report a **range**: a confirmed floor, a likely projection, and a worst-case ceiling
  (everyone outstanding says yes, every offered plus-one comes).

## Capacity reconciliation

Compare the projection to `venue_capacity`:

- Likely projection within capacity = green.
- Worst-case ceiling exceeds capacity = yellow (watch it; capacity is a hard limit).
- Likely projection already at/over capacity = red (act now — capacity can't flex).

Yellow and red capacity statuses ALWAYS belong in your reported findings — a hard
venue limit that the guest list can breach is never a footnote. State the ceiling,
the capacity, and the gap in people.

State how much headroom (or overflow) there is, in people.

## Budget reconciliation

Catering cost = headcount × `catering_per_head`. Compute it for the floor, the likely
projection, and the ceiling. If a budget exists, you may `read_data("budget")` to compare
this against the venue/catering line — but only if useful; don't force it.

## Data-quality checks (the unglamorous, high-value part)

- **Ambiguous plus-ones** — list guests offered a plus-one who haven't named one.
- **Overdue RSVPs** — anyone `pending` or `no_response` past `rsvp_deadline` needs a chase.
- **Impossible counts** — a household whose `attending_count` exceeds `party_size`
  (plus 1 if a plus-one is allowed) is a data error that inflates the headcount.
  Name the household and both numbers.
- **Confirmed-but-zero** — `rsvp: confirmed` with `attending_count: 0` is
  contradictory: either nobody is actually coming or the count was never filled
  in. Either way the floor is wrong; name the household.

Check these row-integrity rules against EVERY household, not just the ones that
look odd — the errors hide in rows that look routine.

## Follow-up list

Produce a **prioritized** list of who to chase and why — overdue large parties first
(they move the headcount most), then ambiguous plus-ones.
Specific and actionable: name the household and the one thing needed from them.

## Severity

- **red** — likely over capacity, or RSVPs so incomplete the headcount can't be
  trusted for catering
- **yellow** — worst-case risk, overdue replies, fuzzy plus-ones
- **green** — comfortably within capacity and budget, data is clean

## Output format

Respond with ONLY a JSON object (no markdown fences, no commentary):

{
  "headcount": {
    "confirmed_floor": 0,
    "likely_projection": 0,
    "worst_case_ceiling": 0,
    "assumptions": "the attendance rate you assumed for pending/no_response and why"
  },
  "capacity": {
    "venue_capacity": 0,
    "headroom_at_likely": 0,
    "status": "red | yellow | green",
    "note": "plain-language read on fitting the room"
  },
  "catering_cost": {
    "per_head": 0,
    "at_floor": 0,
    "at_likely": 0,
    "at_ceiling": 0,
    "note": "what this means for the budget"
  },
  "warnings": [
    {"issue": "what's wrong", "detail": "households/people involved",
     "severity": "red | yellow | green", "recommendation": "what to do"}
  ],
  "follow_ups": ["prioritized, specific: who to chase and for what"],
  "summary": "2-3 plain sentences: are we on track, and what's the single biggest risk"
}

Never invent households or counts that aren't in the data. If the list is empty, say so in
summary and return zeros.

## Lessons

Record reusable patterns with append_lesson (e.g. "large pending families swing the
headcount most — chase them first" — things you notice across guest lists).
