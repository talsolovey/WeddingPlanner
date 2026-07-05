"""Tests for the weekly-brief orchestrator (agent/orchestrator.py).

Covered, all offline (fake harnesses + a fake OpenAI client, no network):
  1. Fan-out + merge: three specialists run, findings reach the merge, the
     response keeps the {analysis, cost_usd, agents} contract the UI expects.
  2. Verifier pass: missed items come back tagged flagged_by="verifier".
  3. Isolation: each specialist gets its own harness instance (fresh context).
  4. Cost cap: once the orchestration budget is spent, verifier + merge are
     skipped and the brief degrades to raw findings instead of spending more.
  5. weeks_to_wedding is computed in code from guests settings.

Run from the vow-app/ directory:
    python -m pytest tests/ -v        (or)        python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))

from agent.orchestrator import (  # noqa: E402
    SPECIALISTS,
    WeeklyBriefOrchestrator,
    _extract_json,
    _weeks_to_wedding,
)
from agent.registry import DATA_DIR  # noqa: E402

TODAY = "2026-07-05"
WEDDING = (date.fromisoformat(TODAY) + timedelta(weeks=10)).isoformat()


def _seed_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "guests.json").write_text(json.dumps({
        "settings": {"wedding_date": WEDDING, "venue_capacity": 40,
                     "catering_per_head": 145, "rsvp_deadline": TODAY},
        "households": [{"name": "Patel", "attending_count": 5, "meals": 4}],
    }))
    (DATA_DIR / "budget.json").write_text(json.dumps(
        {"total_budget": 1000, "items": [{"category": "venue", "amount": 500}]}))
    (DATA_DIR / "contracts.json").write_text(json.dumps(
        {"contracts": [{"vendor": "Golden Hour", "service_charge": "22%"}]}))


class _FakeHarness:
    """Stands in for AgentHarness: returns canned specialist findings."""
    instances = []

    def __init__(self, on_event, cost=0.02, answer=None):
        self.on_event = on_event
        self.last_run_cost = cost
        self._answer = answer
        _FakeHarness.instances.append(self)

    def run(self, prompt):
        self.prompt = prompt
        if self._answer is not None:
            return self._answer
        # Name the specialist from the prompt so findings are distinguishable.
        name = next((n for n in SPECIALISTS if f"the {n} specialist" in prompt), "?")
        return json.dumps({
            "findings": [{"priority": "high", "area": name,
                          "title": f"{name} issue", "why": "w", "do": "d"}],
            "on_track": [f"{name} fine"],
        })


class _FakeChatClient:
    """Fake OpenAI client for the tool-free verifier + merge calls."""

    def __init__(self, verifier_missed=None, merge_answer=None):
        self.verifier_missed = verifier_missed if verifier_missed is not None else []
        self.merge_answer = merge_answer
        self.calls = []  # (label-ish system prefix, user) tuples
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, model=None, messages=None, **kwargs):
        system = messages[0]["content"]
        self.calls.append(system[:40])
        if "strict reviewer" in system:  # verifier call
            content = json.dumps({"missed": self.verifier_missed})
        else:  # merge call
            content = self.merge_answer or json.dumps({
                "as_of": "model-said-otherwise", "weeks_to_wedding": 999,
                "headline": "merged", "action_items": [], "on_track": [],
            })
        usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=50,
                                      total_tokens=150)
        msg = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                     usage=usage)


def _make_orch(verifier_missed=None, merge_answer=None, **kwargs):
    _FakeHarness.instances = []
    log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
    return WeeklyBriefOrchestrator(
        harness_factory=lambda on_event: _FakeHarness(on_event),
        client=_FakeChatClient(verifier_missed=verifier_missed,
                               merge_answer=merge_answer),
        run_log_path=log,
        **kwargs,
    )


class TestFanOutAndMerge(unittest.TestCase):
    def setUp(self):
        _seed_data()

    def test_three_specialists_run_and_response_shape_is_kept(self):
        orch = _make_orch()
        out = orch.run(TODAY)
        self.assertEqual(len(_FakeHarness.instances), 3)     # one per specialist
        self.assertIn("analysis", out)
        self.assertIn("cost_usd", out)
        self.assertEqual({a["name"] for a in out["agents"]}, set(SPECIALISTS))
        self.assertEqual(out["analysis"]["headline"], "merged")

    def test_isolation_each_specialist_gets_a_fresh_harness(self):
        orch = _make_orch()
        orch.run(TODAY)
        self.assertEqual(len(set(id(h) for h in _FakeHarness.instances)), 3)
        prompts = [h.prompt for h in _FakeHarness.instances]
        for name, spec in SPECIALISTS.items():
            matching = [p for p in prompts if f"the {name} specialist" in p]
            self.assertEqual(len(matching), 1, f"no isolated prompt for {name}")
            self.assertIn(spec["dataset"], matching[0])

    def test_code_owns_dates_not_the_model(self):
        # The fake merge model claims as_of/weeks that must be overruled.
        orch = _make_orch()
        out = orch.run(TODAY)
        self.assertEqual(out["analysis"]["as_of"], TODAY)
        self.assertEqual(out["analysis"]["weeks_to_wedding"], 10)

    def test_non_json_merge_degrades_to_raw_findings(self):
        orch = _make_orch(merge_answer="Here is your lovely brief, in prose!")
        out = orch.run(TODAY)
        # 3 specialists x 1 finding each survive into the degraded brief.
        self.assertEqual(len(out["analysis"]["action_items"]), 3)


class TestVerifier(unittest.TestCase):
    def setUp(self):
        _seed_data()

    def test_missed_items_are_appended_and_tagged(self):
        missed = [{"priority": "high", "area": "guests",
                   "title": "Patel meal gap", "why": "4 of 5", "do": "chase"}]
        # Non-JSON merge answer -> the brief degrades to the raw findings list,
        # letting us inspect exactly what the verifier appended.
        orch = _make_orch(verifier_missed=missed, merge_answer="prose")
        out = orch.run(TODAY)
        per_agent = {a["name"]: a for a in out["agents"]}
        for name in SPECIALISTS:
            self.assertEqual(per_agent[name]["verifier_added"], 1)
            self.assertEqual(per_agent[name]["findings"], 2)  # 1 own + 1 verifier
        tagged = [f for f in out["analysis"]["action_items"]
                  if f.get("flagged_by") == "verifier"]
        self.assertEqual(len(tagged), 3)  # one appended per specialist
        self.assertEqual(tagged[0]["title"], "Patel meal gap")

    def test_verifier_failure_keeps_specialist_findings(self):
        orch = _make_orch()

        def boom(**kwargs):
            raise RuntimeError("verifier down")
        orch._client.chat.completions.create = boom
        # Merge also uses the client, so patch _merge to isolate the verifier path.
        orch._merge = lambda results, today, weeks: {"headline": "ok",
                                                     "action_items": [], "on_track": []}
        out = orch.run(TODAY)
        per_agent = {a["name"]: a for a in out["agents"]}
        for name in SPECIALISTS:
            self.assertEqual(per_agent[name]["verifier_added"], 0)
            self.assertEqual(per_agent[name]["findings"], 1)  # nothing lost


class TestCostCap(unittest.TestCase):
    def setUp(self):
        _seed_data()

    def test_cap_skips_verifier_and_merge_but_still_returns_a_brief(self):
        # Specialists alone (3 x $0.02) blow a $0.05 budget.
        orch = _make_orch(max_total_cost_usd=0.05)
        out = orch.run(TODAY)
        self.assertEqual(len(orch._client.calls), 0)  # no verifier, no merge calls
        self.assertIn("action_items", out["analysis"])
        self.assertEqual(len(out["analysis"]["action_items"]), 3)
        self.assertIn("Cost cap", out["analysis"]["headline"])


class TestHelpers(unittest.TestCase):
    def test_extract_json_handles_fences_and_prose(self):
        obj = {"findings": []}
        for wrapper in [json.dumps(obj),
                        f"Sure!\n```json\n{json.dumps(obj)}\n```",
                        f"prefix {json.dumps(obj)} suffix"]:
            self.assertEqual(_extract_json(wrapper), obj)
        self.assertIsNone(_extract_json("no json here"))

    def test_weeks_to_wedding(self):
        today = date.fromisoformat(TODAY)
        data = {"settings": {"wedding_date": WEDDING}}
        self.assertEqual(_weeks_to_wedding(data, today), 10)
        self.assertIsNone(_weeks_to_wedding({}, today))
        self.assertEqual(
            _weeks_to_wedding({"settings": {"wedding_date": "2020-01-01"}}, today), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
