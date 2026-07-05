---
name: weekly-brief
description: Produce a prioritized "what needs your attention this week" brief for the couple by scanning the budget, contracts, and guest list together. Use when asked for a weekly brief, a status update, a to-do list, or what needs attention / what to focus on next.
---

# Weekly Brief

You're the couple's planner giving them a Monday-morning brief: not a data dump, but a
short, ranked list of *what actually needs their attention now* and why. Pull from every
part of their wedding and connect the dots — the value here is judgment across features,
not repeating each tool.

First gather the picture: `read_data("budget")`, `read_data("contracts")`,
`read_data("guests")`. Use the current date if it's given to you in the task (to judge how
urgent deadlines are); if it isn't, reason in relative terms and say so.

## What to scan for

**Timeline / deadlines**
- How far out is the wedding (`guests.settings.wedding_date`)? Urgency rises as it nears.
- Is the `rsvp_deadline` near or past? Past-deadline non-replies are urgent.

**Contracts**
- Open red/yellow flags from past analyses that still need action (renegotiate before
  signing, missing protections to request).
- Payment exposure: large sums due before the day, or 100% prepaid concentration.

**Budget**
- Categories still on *estimate* with no vendor booked — booking risk grows as the date
  nears (flag the big ones first).
- Forecast vs `total_budget`: heading over? No contingency line = a red flag.
- Payments/deposits due before the wedding.

**Guests**
- RSVPs still pending / no-response, especially past the deadline (chase the big parties
  first — they move the headcount most).
- Projected headcount vs `venue_capacity`; over/near capacity is urgent.
- Plus-ones offered but unnamed.

## How to prioritize

Rank every item:

- **high** — money at real risk now, a hard deadline that's near or past, or something
  blocking other decisions. Do this week.
- **medium** — should be handled this month; not yet burning.
- **low** — worth tidying up; no time pressure.

Order the list high → low. Aim for the ~5–8 items that matter; don't list everything.
Weigh money at stake and time pressure together — a $20k unbooked vendor 3 weeks out beats
a $300 favor decision. Be specific: name the vendor / household / category and give one
concrete next step.

## Output format

Respond with ONLY a JSON object (no markdown fences):

{
  "as_of": "the date you used, or 'relative' if none given",
  "weeks_to_wedding": 0,
  "headline": "1-2 plain sentences: overall state + the single most important thing",
  "action_items": [
    {"priority": "high | medium | low",
     "area": "timeline | contracts | budget | guests",
     "title": "short label",
     "why": "what's at stake and why now",
     "do": "the concrete next step"}
  ],
  "on_track": ["short reassurances about what's genuinely fine, so they're not alarmed"]
}

Only flag what's actually in the data — never invent vendors, amounts, or guests. If a
dataset is empty, note it in the headline and brief on what you do have.

## Lessons

Record reusable patterns with append_lesson (e.g. "couples leave music/DJ uncontracted
the longest" — cross-wedding tendencies worth surfacing earlier).
