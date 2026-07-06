"""Tests for the Vow MCP server tools (mcp_server.py) — all offline.

Covered, no network and no `mcp` package needed (the tool functions stay
importable without it):
  1. get_wedding_status: pure read — valid JSON, correct rollups (budget
     committed/paid, RSVP counts, contract red-flag counts, weeks to wedding),
     and NO writes to any document.
  2. run_weekly_brief: drives an injected fake orchestrator (the same seam the
     orchestrator tests use), returns the {analysis, cost_usd, agents,
     generated_at} contract, and caches the brief so the home dashboard
     (GET /api/weekly-brief/latest) can serve it — the self-notification.
  3. Failure path: an orchestrator crash comes back as {"error": ...} instead
     of raising, so the unattended agent can fall back to get_wedding_status.

Run from the vow-app/ directory:
    python -m pytest tests/ -v        (or)        python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ.setdefault("VOW_STORAGE_BACKEND", "files")  # tests never touch Supabase

import mcp_server  # noqa: E402
import storage  # noqa: E402

WEDDING = (date.today() + timedelta(weeks=10)).isoformat()


def _seed_data():
    storage.save("profile", {
        "partner_a": "Noa", "partner_b": "Adam", "venue": "The Vineyard Pavilion",
        "wedding_date": WEDDING,
    })
    storage.save("budget", {
        "currency": "USD", "total_budget": 50000,
        "items": [
            {"category": "Venue", "vendor": "Vineyard", "contracted": 30000, "paid": 10000},
            {"category": "Photo", "estimated": 5000},
        ],
    })
    storage.save("guests", {
        "settings": {"wedding_date": WEDDING},
        "households": [
            {"id": "h1", "household": "Levi", "party_size": 2,
             "rsvp": "confirmed", "attending_count": 2},
            {"id": "h2", "household": "Patel", "party_size": 3, "rsvp": "pending"},
            {"id": "h3", "household": "Cohen", "party_size": 1, "rsvp": "declined"},
        ],
    })
    storage.save("contracts", [
        {"id": "c01", "vendor": "Vineyard - Venue",
         "analysis": {"red_flags": [{"clause": "a"}, {"clause": "b"}]}},
    ])
    storage.save("seating", {"tables": []})


class _FakeOrchestrator:
    """Returns a canned brief; records what it was asked for."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def run(self, today=None, extra_facts=None):
        self.calls.append({"today": today, "extra_facts": extra_facts})
        if self.fail:
            raise RuntimeError("model unavailable")
        return {
            "analysis": {"headline": "All quiet", "action_items": [], "on_track": ["budget"]},
            "cost_usd": 0.05,
            "agents": [{"name": "contracts", "cost_usd": 0.01}],
        }


class TestGetWeddingStatus(unittest.TestCase):
    def setUp(self):
        _seed_data()
        storage.save("brief", {"generated_at": "2026-07-01T08:00:00"})

    def test_snapshot_rollups(self):
        snap = json.loads(mcp_server.get_wedding_status())
        self.assertEqual(snap["couple"], "Noa & Adam")
        self.assertEqual(snap["wedding_date"], WEDDING)
        self.assertEqual(snap["weeks_to_wedding"], 10)
        self.assertEqual(snap["budget"]["committed"], 35000)
        self.assertEqual(snap["budget"]["paid"], 10000)
        self.assertEqual(snap["budget"]["over_budget_by"], 0)
        self.assertEqual(snap["guests"]["invited_people"], 6)
        self.assertEqual(snap["guests"]["confirmed_people"], 2)
        self.assertEqual(snap["guests"]["households_by_rsvp"],
                         {"confirmed": 1, "pending": 1, "declined": 1})
        self.assertEqual(snap["contracts"], [{"vendor": "Vineyard - Venue", "red_flags": 2}])
        # the live conflict engine runs inside the snapshot: Levi is confirmed
        # but unseated, and the deterministic engine flags exactly that
        self.assertEqual(len(snap["seating_conflicts"]), 1)
        self.assertIn("Levi", snap["seating_conflicts"][0])
        self.assertEqual(snap["latest_brief_generated_at"], "2026-07-01T08:00:00")

    def test_read_only_and_degrades_on_empty_data(self):
        before = mcp_server.get_wedding_status()
        # calling it must not write anything
        self.assertEqual(mcp_server.get_wedding_status(), before)
        # empty datasets -> still valid JSON, no exception
        for doc in ("profile", "budget", "guests", "contracts"):
            storage.save(doc, [] if doc == "contracts" else {})
        snap = json.loads(mcp_server.get_wedding_status())
        self.assertIsNone(snap["weeks_to_wedding"])
        self.assertEqual(snap["guests"]["households"], 0)


class TestRunWeeklyBrief(unittest.TestCase):
    def setUp(self):
        _seed_data()
        storage.save("brief", {})

    def tearDown(self):
        mcp_server._orchestrator_factory = None

    def test_runs_orchestrator_and_caches_brief(self):
        fake = _FakeOrchestrator()
        mcp_server._orchestrator_factory = lambda: fake

        result = json.loads(mcp_server.run_weekly_brief())

        # contract the unattended agent (and the UI) rely on
        self.assertEqual(result["analysis"]["headline"], "All quiet")
        self.assertEqual(result["cost_usd"], 0.05)
        self.assertIn("generated_at", result)
        # orchestrator got today + the code-computed seating facts
        self.assertEqual(fake.calls[0]["today"], date.today().isoformat())
        self.assertIn("seating_conflicts", fake.calls[0]["extra_facts"])
        # the self-notification: home dashboard reads this document back
        cached = storage.load("brief")
        self.assertEqual(cached["analysis"]["headline"], "All quiet")
        self.assertEqual(cached["generated_at"], result["generated_at"])

    def test_failure_returns_error_not_exception(self):
        mcp_server._orchestrator_factory = lambda: _FakeOrchestrator(fail=True)
        result = json.loads(mcp_server.run_weekly_brief())
        self.assertEqual(result, {"error": "model unavailable"})
        # a failed run must not clobber the cached brief
        self.assertEqual(storage.load("brief"), {})


if __name__ == "__main__":
    unittest.main()
