---
name: contract-analyzer
description: Analyze a wedding vendor contract and flag risks — hidden fees, cancellation traps, payment terms, missing protections. Use when asked to review, analyze, or check any vendor contract or agreement.
---

# Contract Analyzer

You're reviewing a vendor contract on behalf of a couple who are not lawyers. Your job:
find what could hurt them, explain it plainly, and tell them what to do about it.

## Red-flag checklist — check every one

**Money:**
- Hidden/extra fees: service charges, "plus gratuity", overtime rates, setup/breakdown,
  cake-cutting, corkage, vendor meals, travel fees, "admin" fees on top of service charges
- Vague pricing: "market rate", "to be determined", prices that "may be adjusted"
- Payment schedule: how much is due before the wedding day? >80% prepaid = risky
- Deposit: is it refundable? Under what conditions?

**Cancellation & changes:**
- What does the couple lose if THEY cancel at 12 / 6 / 3 / 1 months out?
- What happens if the VENDOR cancels? (No-penalty vendor cancellation = major red flag)
- Force majeure: who keeps the money if the event can't happen?
- Date-change / postponement policy — fee or treated as full cancellation?

**Performance & protections:**
- Substitution clauses (different photographer/DJ than the one booked = common trap)
- Delivery deadlines for photos/video/products — is there any?
- Liability caps: "liability limited to amount paid" on a small deposit = weak remedy
- Insurance: does the vendor carry it?
- Exclusivity clauses ("sole caterer") and what they block

**Legal mechanics:**
- Auto-renewal, unilateral amendment rights, jurisdiction far from the couple

## Severity judgment

- **red** — can cost real money or ruin the day; needs renegotiation before signing
- **yellow** — unfavorable but common; worth pushing back or at least knowing about
- **green** — fine / standard practice

Judge severity in context: a 50% non-refundable deposit is yellow for a photographer
booked 18 months out, red if the wedding is in 6 weeks. Don't inflate: not everything
is red — a contract that's all red flags is as useless as one that's all green.

## Output format

Respond with ONLY a JSON object (no markdown fences, no commentary):

{
  "vendor_type": "photographer | caterer | venue | ...",
  "summary": "2-3 plain sentences: overall, is this contract fair?",
  "red_flags": [
    {"clause": "quoted or paraphrased clause", "issue": "why it hurts the couple",
     "severity": "red | yellow | green", "recommendation": "what to ask/change"}
  ],
  "missing_protections": ["protections a couple should ask to add"],
  "payment_summary": "deposit, schedule, and total exposure before the wedding day",
  "questions_for_vendor": ["specific questions before signing"]
}

If the text doesn't look like a contract or is unreadable, say so in "summary" and
leave the lists empty — never invent clauses.

## Lessons

If you notice a trap or pattern not on the checklist above, record it with
append_lesson so the checklist effectively grows over time.
