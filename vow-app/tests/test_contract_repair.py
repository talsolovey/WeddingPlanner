"""Contract analysis output repair (offline — harness and LLM faked).

Real PDFs sometimes push the model into prose despite JSON instructions.
What must hold:
- A prose answer triggers ONE repair call that converts it to the schema,
  and the stored analysis is the parsed JSON (renderable by the UI).
- A JSON answer never triggers the repair call.
- If the repair itself fails, the prose fallback is stored (job never dies).
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


class _FakeHarness:
    def __init__(self, answer):
        self._answer = answer
        self.last_run_cost = 0.01

    def run(self, prompt):
        self.prompt = prompt
        return self._answer


class TestRepair(unittest.TestCase):
    COUPLE = "repair-test"

    def setUp(self):
        storage.set_couple(self.COUPLE)
        storage.save("contracts", [])
        storage.set_couple(None)
        self._orig = (contracts_mod.AgentHarness, contracts_mod._repair_to_json)

    def tearDown(self):
        contracts_mod.AgentHarness, contracts_mod._repair_to_json = self._orig

    def _run(self, harness_answer, repair):
        contracts_mod.AgentHarness = lambda **kw: _FakeHarness(harness_answer)
        contracts_mod._repair_to_json = repair
        storage.set_couple(self.COUPLE)
        try:
            task = contracts_mod._analyze_contract_task(
                "Bella Vista", "contract.pdf", "x" * 200, False)
            return task(lambda e: None)
        finally:
            storage.set_couple(None)

    def test_prose_answer_gets_repaired(self):
        calls = []
        def repair(raw, on_event=None):
            calls.append(raw)
            return GOOD_JSON
        record = self._run(PROSE, repair)
        self.assertEqual(len(calls), 1)
        self.assertEqual(record["analysis"]["vendor_type"], "venue")
        self.assertEqual(record["analysis"]["red_flags"][0]["severity"], "red")
        self.assertNotIn("note", record["analysis"])

    def test_json_answer_skips_repair(self):
        calls = []
        record = self._run(GOOD_JSON, lambda raw, on_event=None: calls.append(raw))
        self.assertEqual(calls, [])
        self.assertEqual(record["analysis"]["summary"], "Mostly fair.")

    def test_failed_repair_keeps_prose_fallback(self):
        def repair(raw, on_event=None):
            raise RuntimeError("llm down")
        record = self._run(PROSE, repair)
        self.assertIn("summary", record["analysis"])   # raw prose surfaced
        self.assertIn("non-JSON", record["analysis"]["note"])

    def test_json_instruction_sits_after_the_contract_text(self):
        contracts_mod.AgentHarness = lambda **kw: _FakeHarness(GOOD_JSON)
        storage.set_couple(self.COUPLE)
        try:
            fake = _FakeHarness(GOOD_JSON)
            contracts_mod.AgentHarness = lambda **kw: fake
            contracts_mod._analyze_contract_task(
                "V", "f.pdf", "CONTRACT-TEXT-MARKER " * 20, False)(lambda e: None)
        finally:
            storage.set_couple(None)
        self.assertGreater(fake.prompt.rfind("ONLY the JSON object"),
                           fake.prompt.rfind("CONTRACT-TEXT-MARKER"))


if __name__ == "__main__":
    unittest.main()
