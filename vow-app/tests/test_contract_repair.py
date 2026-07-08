"""Agent-output repair for everything the UI renders (offline — LLM faked).

Real inputs sometimes push the model into prose despite JSON instructions.
core.ensure_agent_json() is the shared fix: parse; if prose, ONE repair call
reformats the model's own findings into the schema pulled live from the
skill's '## Output format' section.

What must hold:
- Prose triggers exactly one repair call; the stored analysis is the schema.
- Clean JSON never triggers repair.
- A failed repair keeps the prose fallback (jobs never die on formatting).
- The repair prompt carries the *current* skill schema (read at call time).
- Every agent-output endpoint (contracts, budget, guests, seating, timeline)
  routes through the helper.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ.setdefault("VOW_STORAGE_BACKEND", "files")  # tests never touch Supabase

import storage                                  # noqa: E402
import app.core as core                         # noqa: E402
import app.contracts as contracts_mod           # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))

GOOD_JSON = json.dumps({
    "vendor_type": "venue", "summary": "Mostly fair.",
    "red_flags": [{"clause": "180-day forfeiture", "issue": "harsh",
                   "severity": "red", "recommendation": "negotiate"}],
    "missing_protections": [], "payment_summary": "50% deposit",
    "questions_for_vendor": []})

PROSE = ("Here is my review of the contract. Overall it seems mostly fair, "
         "however the 180-day forfeiture clause is harsh and should be "
         "negotiated before signing. The deposit is 50%.")


class RepairSeam(unittest.TestCase):
    def setUp(self):
        self._orig = core._repair_call

    def tearDown(self):
        core._repair_call = self._orig


class TestEnsureAgentJson(RepairSeam):
    def test_prose_triggers_one_repair_with_current_skill_schema(self):
        calls = []
        def fake(system, user):
            calls.append(system)
            return GOOD_JSON
        core._repair_call = fake
        out = core.ensure_agent_json(PROSE, skill="contract-analyzer")
        self.assertEqual(len(calls), 1)
        self.assertIn("vendor_type", calls[0])       # schema pulled from SKILL.md
        self.assertIn("red_flags", calls[0])
        self.assertEqual(out["red_flags"][0]["severity"], "red")
        self.assertNotIn("note", out)

    def test_clean_json_skips_repair(self):
        calls = []
        core._repair_call = lambda s, u: calls.append(s)
        out = core.ensure_agent_json(GOOD_JSON, skill="contract-analyzer")
        self.assertEqual(calls, [])
        self.assertEqual(out["summary"], "Mostly fair.")

    def test_failed_repair_keeps_prose_fallback(self):
        def boom(s, u):
            raise RuntimeError("llm down")
        core._repair_call = boom
        out = core.ensure_agent_json(PROSE, skill="contract-analyzer")
        self.assertIn("non-JSON", out["note"])
        self.assertIn("forfeiture", out["summary"])   # nothing lost

    def test_repair_that_still_produces_prose_falls_back(self):
        core._repair_call = lambda s, u: "still just prose, sorry"
        out = core.ensure_agent_json(PROSE, skill="contract-analyzer")
        self.assertIn("non-JSON", out["note"])

    def test_explicit_schema_beats_skill_lookup(self):
        seen = []
        def fake(system, user):
            seen.append(system)
            return '{"flags": []}'
        core._repair_call = fake
        out = core.ensure_agent_json("prose about flags", schema='{"flags": [...]}')
        self.assertIn('{"flags": [...]}', seen[0])
        self.assertEqual(out, {"flags": []})

    def test_every_agent_endpoint_routes_through_the_helper(self):
        for module in ("contracts", "budget", "guests", "seating", "timeline"):
            src = (VOW_APP / "app" / f"{module}.py").read_text()
            self.assertIn("ensure_agent_json", src, module)
            self.assertNotIn("parse_agent_json(answer)", src, module)


class TestContractTaskIntegration(RepairSeam):
    COUPLE = "repair-test"

    class _FakeHarness:
        def __init__(self, answer):
            self._answer = answer
            self.last_run_cost = 0.01

        def run(self, prompt):
            self.prompt = prompt
            return self._answer

    def test_prose_pdf_analysis_ends_up_renderable(self):
        core._repair_call = lambda s, u: GOOD_JSON
        contracts_mod.AgentHarness = lambda **kw: self._FakeHarness(PROSE)
        try:
            storage.set_couple(self.COUPLE)
            storage.save("contracts", [])
            record = contracts_mod._analyze_contract_task(
                "Bella Vista", "contract.pdf", "x" * 200, False)(lambda e: None)
        finally:
            storage.set_couple(None)
            from agent.harness import AgentHarness
            contracts_mod.AgentHarness = AgentHarness
        self.assertEqual(record["analysis"]["vendor_type"], "venue")

    def test_json_instruction_sits_after_the_contract_text(self):
        fake = self._FakeHarness(GOOD_JSON)
        contracts_mod.AgentHarness = lambda **kw: fake
        try:
            storage.set_couple(self.COUPLE)
            contracts_mod._analyze_contract_task(
                "V", "f.pdf", "CONTRACT-TEXT-MARKER " * 20, False)(lambda e: None)
        finally:
            storage.set_couple(None)
            from agent.harness import AgentHarness
            contracts_mod.AgentHarness = AgentHarness
        self.assertGreater(fake.prompt.rfind("ONLY the JSON object"),
                           fake.prompt.rfind("CONTRACT-TEXT-MARKER"))


if __name__ == "__main__":
    unittest.main()
