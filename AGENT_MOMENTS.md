# AGENT_MOMENTS — where the agent struggled or surprised us

Running list for the workshop ("bring a list of moments where the agent struggled or
surprised you").

## 2026-06-11 — Step 1 (harness)

- **Surprise (good):** with zero skills installed, the agent answered honestly that it
  had no skills instead of inventing capabilities — the system prompt's "be honest about
  uncertainty" line seems to work.

## 2026-06-11 — Step 2 (contract analyzer)

- **Struggle:** first eval contract had ~8 planted traps; the agent flagged only 4
  (caught: 22% service charge, vendor can cancel penalty-free, no delivery deadline,
  photographer substitution). Missed: client cancellation = total forfeiture of all
  payments, liability capped at retainer, far-away jurisdiction (Alaska), exclusivity
  clause. The skill checklist names all of these — the agent read the skill but didn't
  check every item. To address in the evals step.
- **Surprise (good):** without being told which skill to use, the agent went
  list_skills → read_skill(contract-analyzer) on its own, first try.
- **Infra struggle (not the agent's fault):** sandbox runs hung twice during testing;
  turned out to be an environment blip + a missing dependency, but it looked exactly
  like an agent hang. Lesson: log per-iteration so you can tell "thinking" from "stuck".

## 2026-06-11 — Step 3 (budget forecaster)

- **Struggle:** the skill says "respond with ONLY a JSON object, no fences" — the agent
  wrapped its JSON in prose + markdown fences anyway, breaking the strict parser. Fixed
  by making the parser tolerant (extract the JSON from wherever it is) instead of
  fighting the model. Lesson: instructions reduce, but don't eliminate, format drift —
  parse defensively.
- **Surprise (great):** the analyze prompt gave the agent NO data — it fetched the
  budget itself via read_data("budget"), and then, unprompted in the task, also pulled
  read_data("contracts") and folded the Golden Hour contract's 22% service charge into
  the payment-exposure warning. Cross-feature reasoning, exactly what we hoped skills
  would enable.
- It found all 8 deliberately missing categories, including the absent contingency.

## 2026-06-14 — Step 4 (guest-list-manager skill, CLI test)

- **Surprise (good):** strong quantitative reasoning. Unprompted, it built a headcount
  *range* — confirmed floor 24, likely 36 (it assumed 80% attendance on pending and even
  cited the Garcia "likely most will attend" note), worst-case ceiling exactly 40 — then
  noticed the ceiling maxes the 40-seat venue and rated capacity yellow rather than green.
  All catering math (24/36/40 × $145) was correct.
- **Struggle (partial recall, same as contract-analyzer):** the data-quality check is
  "for each confirmed household, meals should sum to attending_count." It caught Nguyen
  (0 of 2 meals) but missed Patel (4 of 5 meals submitted — 1 unaccounted). It read the
  rule but didn't apply it to every row.
- **Struggle (instruction not followed):** the skill says "treat a severe allergy as
  red." It listed the severe nut allergy in dietary_summary but never raised it as a red
  warning — under-weighted an explicit instruction.
- **Struggle (format drift, same as budget-forecaster):** "respond with ONLY a JSON
  object, no fences" — it wrapped output in ```json fences plus a trailing prose sentence.
  Confirms the standing lesson: parse defensively, don't expect the model to obey strict
  format rules. UI will reuse the tolerant JSON extractor from the budget feature.
- Run cost: $0.021 (4 iterations: list_skills → read_skill → read_data → answer).

## 2026-06-14 — Step 9 (weekly-brief skill, CLI test)

- **Surprise (great):** the clearest cross-feature reasoning yet. From a single data-free
  prompt it ran list_skills → read_skill → read_data(budget, contracts, guests) → answer,
  computed weeks-to-wedding (21) from the injected date, and produced one ranked brief that
  *combined* sources: the venue contract's cancellation/100%-prepaid risk (high), unbooked
  budget vendors DJ/flowers then attire/transport/cake (high/medium), the big pending RSVP
  families by name (medium), and the planted Klein plus-one + Ben-David meal gap (low).
- **Small miss:** the budget has no contingency line, which budget-forecaster flags, but
  the weekly brief didn't surface it — cross-skill knowledge doesn't always carry over.
  Candidate lesson for weekly-brief: always check for a missing contingency.
- Cost $0.024, 4 iterations.
- **Struggle (format drift, recurred):** wiring up the UI, the *same* prompt that gave
  clean JSON in the CLI test instead returned a prose/markdown briefing one run — so
  `parse_agent_json` fell back. Two things bit us: (1) the fallback was contract-shaped,
  which is wrong for non-contract skills, and (2) the brief is narrative-sounding, so the
  model is extra prone to prose. Fixes: made the fallback generic, reinforced "respond
  with ONLY the JSON object — no prose/markdown" in the endpoint prompt, and made the page
  show the raw text if drift recurs. After the prompt reinforcement it returned schema
  JSON reliably. Lesson reaffirmed: never trust strict output format; reinforce + parse
  defensively + degrade gracefully.

## 2026-06-14 — Step 8 (eval harness)

- Built scored evals (recall vs planted traps). First run: contract 7/8 (missed the
  distant-jurisdiction/Alaska clause), budget 6/6, guests 1/5 — the guest score looked
  off vs earlier behavior and was mid-investigation when work paused; worth re-checking
  whether it's a real regression or a scoring/keyword artifact.

## 2026-06-23 — Step 12 (security hardening)

- **Design call:** treated the agent as the thing to defend *and* a thing that can be
  weaponized. The contract-upload path was the obvious injection surface (untrusted PDF
  text → straight into the prompt), so the fix has two layers: wrap/flag untrusted input
  *and* make `write_data` reversible + refuse to blank a dataset — so even a successful
  injection can't destroy data.
- **Reassuring find:** the frontend already escaped all agent/vendor text via `esc()`,
  so the stored-analysis XSS surface was closed; added a regression test so it stays that
  way rather than rewriting anything.
- **Testing the cost ceiling without spending:** used a fake OpenAI client returning an
  expensive tool-calling response so the run *would* loop forever — proves the ceiling,
  not the model. Whole 17-test suite runs with no network.
- **Note for next week's autonomous run:** the deterministic guards (write backup +
  no-blanking, cost ceiling, rate limit) are what I'd actually trust unattended; the
  injection scanner is probabilistic and is there to reduce attempts, not guarantee.

## 2026-07-05 — Step 13 (sub-agents + verifier, live e2e)

- **Surprise (great):** the verifier pass earned its keep on the very first live run —
  it added 5 findings the specialists missed (4 in budget, incl. hidden service
  charges and a flowers/decor under-estimate; 1 in guests). Same model, same data —
  the only difference is one narrow job with a clean context. Isolation works.
- **Interesting:** contracts verifier added 0 — the contract specialist, doing exactly
  one contract against its checklist, was already complete. The dilution problem is
  real mainly where one agent juggles breadth.
- Parallel fan-out worked as advertised: all three specialists ran concurrently
  (log shows interleaved progress); whole orchestration $0.083 — ~3.5x a single
  weekly-brief run, buying 5 extra catches and per-area cost attribution.

## 2026-07-05 — Step 14 (seating-planner, live auto-seat)

- **Surprise (good):** first live run showed real seating judgment — clustered the
  kosher households at dedicated tables, kept every family whole, gave lone households
  same-side company, and flagged the no-meals household in its reasoning. It even used
  the current room's tables instead of inventing its own.
- **Struggle (arithmetic, again):** capacity math failed on 3 of 13 tables — it summed
  party sizes loosely instead of confirmed attending counts. Models drift on arithmetic
  under load; this is why `validate_proposal` recomputes every table's load in code and
  the UI shows the failures next to the Apply button. The HITL gate earned its keep on
  run one.
- Candidate lesson for the skill: "compute each table's load as the sum of
  attending_count (+1 per named plus-one) and re-check it before answering."
- Cost $0.029, 4 iterations.

## 2026-06-11 — UI round (live progress + dashboard)

- **Surprise (good):** after a lesson landed in contract-analyzer/LESSONS.md (from a
  real run), the same sample contract went from 4 flags to 6 on re-analysis. Can't
  fully attribute it to the lesson (model variance exists), but the self-improvement
  loop is visibly feeding back into results — worth measuring properly in the evals
  step.
