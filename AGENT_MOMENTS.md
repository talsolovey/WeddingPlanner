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
