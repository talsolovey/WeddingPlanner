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

## 2026-06-11 — UI round (live progress + dashboard)

- **Surprise (good):** after a lesson landed in contract-analyzer/LESSONS.md (from a
  real run), the same sample contract went from 4 flags to 6 on re-analysis. Can't
  fully attribute it to the lesson (model variance exists), but the self-improvement
  loop is visibly feeding back into results — worth measuring properly in the evals
  step.
