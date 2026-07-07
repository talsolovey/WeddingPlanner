"""Eval harness tests — all offline (the harness is faked; no model calls).

What must hold:
- Every committed case file is structurally valid and its skill exists.
- The scorer computes recall/misses/noise correctly.
- run_case seeds fixtures under the eval couple, runs the production
  specialist prompt, and scores a faked answer end to end.
- The lessons toggle hides and restores LESSONS.md files.
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
from agent.registry import SKILLS_DIR           # noqa: E402
from evals.run_evals import (                   # noqa: E402
    load_cases, lessons_hidden, run_case, score_findings, validate_case,
)


class TestCasesAreValid(unittest.TestCase):
    def test_all_committed_cases_validate(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 4)  # the four specialist skills
        for case in cases:
            self.assertEqual(validate_case(case), [], case["_file"])

    def test_expected_skills_covered(self):
        skills = {c["skill"] for c in load_cases()}
        self.assertLessEqual({"contract-analyzer", "budget-forecaster",
                              "guest-list-manager", "guest-logistics"}, skills)


class TestScorer(unittest.TestCase):
    TRAPS = [
        {"id": "a", "any_of": ["alaska", "anchorage"]},
        {"id": "b", "any_of": ["22%"]},
    ]

    def test_full_recall_no_noise(self):
        findings = [{"title": "Jurisdiction in Alaska"},
                    {"title": "Hidden 22% fee"}]
        s = score_findings(findings, self.TRAPS)
        self.assertEqual(s["recall"], 1.0)
        self.assertEqual(s["misses"], [])
        self.assertEqual(s["noise_count"], 0)

    def test_miss_and_noise_counted(self):
        findings = [{"title": "Something about the weather"}]
        s = score_findings(findings, self.TRAPS)
        self.assertEqual(s["recall"], 0.0)
        self.assertEqual(set(s["misses"]), {"a", "b"})
        self.assertEqual(s["noise_count"], 1)

    def test_keyword_match_is_case_insensitive(self):
        s = score_findings([{"why": "courts of ANCHORAGE"}], self.TRAPS)
        self.assertIn("a", s["hits"])

    def test_empty_findings(self):
        s = score_findings([], self.TRAPS)
        self.assertEqual(s["recall"], 0.0)
        self.assertEqual(s["findings_count"], 0)


class _FakeHarness:
    def __init__(self, answer):
        self._answer = answer
        self.last_run_cost = 0.01

    def run(self, prompt):
        self.prompt = prompt
        return self._answer


class TestRunCase(unittest.TestCase):
    def test_seeds_fixtures_and_scores_production_prompt(self):
        case = next(c for c in load_cases("contract-analyzer"))
        answer = json.dumps({"findings": [
            {"title": "Disputes must go to Anchorage, Alaska", "priority": "high"},
            {"title": "A 22% service fee is added", "priority": "high"},
        ], "on_track": []})
        fake = _FakeHarness(answer)
        result = run_case(case, harness_factory=lambda: fake)
        self.assertIn("alaska-jurisdiction", result["hits"])
        self.assertIn("service-fee", result["hits"])
        self.assertGreater(len(result["misses"]), 0)  # we only caught 2 of 8
        # The production specialist prompt was used, with the case's dataset.
        self.assertIn("contract-analyzer", fake.prompt)
        self.assertIn('"contracts"', fake.prompt)
        # Fixtures landed under the eval couple, not the legacy data.
        storage.set_couple("eval-fixture")
        self.assertTrue(storage.exists("contracts"))
        storage.set_couple(None)

    def test_couple_context_restored_after_run(self):
        case = next(c for c in load_cases("budget-forecaster"))
        run_case(case, harness_factory=lambda: _FakeHarness("{}"))
        self.assertEqual(storage.current_couple(), storage.LEGACY_COUPLE_ID)


class TestLessonsToggle(unittest.TestCase):
    def test_lessons_hidden_and_restored(self):
        skill_dir = SKILLS_DIR / "contract-analyzer"
        lessons = skill_dir / "LESSONS.md"
        created = False
        if not lessons.exists():
            lessons.write_text("- test lesson\n")
            created = True
        try:
            with lessons_hidden():
                self.assertFalse(lessons.exists())
            self.assertTrue(lessons.exists())
        finally:
            if created:
                lessons.unlink()


if __name__ == "__main__":
    unittest.main()
