"""Tests for outcome tracking (act -> observe), Step 25 phase 2.

All deterministic, no model calls: nudge->reply arithmetic, brief
follow-through clustering, the facts bundle, and the responded_at stamp
on the public RSVP endpoint.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ["VOW_STORAGE_BACKEND"] = "files"

import storage  # noqa: E402
from agent import outcomes  # noqa: E402
from app import create_app  # noqa: E402

NOW = datetime.fromisoformat("2026-07-07T12:00:00")


def _hh(name, **kw):
    return dict({"id": name, "household": name, "party_size": 2,
                 "rsvp": "pending"}, **kw)


class TestNudgeOutcomes(unittest.TestCase):
    def test_no_nudges_means_no_signal(self):
        guests = {"households": [_hh("A"), _hh("B", rsvp="confirmed")]}
        self.assertIsNone(outcomes.nudge_outcomes(guests, NOW))

    def test_replied_vs_silent(self):
        guests = {"households": [
            # replied the day after the nudge
            _hh("Replied", rsvp="confirmed",
                last_nudged_at="2026-07-01T10:00:00",
                responded_at="2026-07-02T09:00:00"),
            # nudged 5 days ago, still pending -> silent
            _hh("Silent", last_nudged_at="2026-07-02T10:00:00"),
            # nudged yesterday -> too soon to call silent
            _hh("Fresh", last_nudged_at="2026-07-06T13:00:00"),
            # responded BEFORE the nudge (old reply) and still pending -> silent
            _hh("Stale", last_nudged_at="2026-07-01T10:00:00",
                responded_at="2026-06-20T09:00:00"),
        ]}
        out = outcomes.nudge_outcomes(guests, NOW)
        self.assertEqual(out["households_nudged"], 4)
        self.assertEqual(out["replied_after_nudge"], 1)
        self.assertEqual(out["reply_rate"], 0.25)
        names = [s["household"] for s in out["still_silent"]]
        self.assertEqual(set(names), {"Silent", "Stale"})
        # sorted by longest-ignored first
        self.assertEqual(names[0], "Stale")

    def test_junk_timestamps_are_ignored(self):
        guests = {"households": [_hh("A", last_nudged_at="not-a-date")]}
        self.assertIsNone(outcomes.nudge_outcomes(guests, NOW))


class TestFollowThrough(unittest.TestCase):
    def test_reworded_advice_clusters_across_runs(self):
        history = {"runs": [
            {"date": "2026-06-23", "items": [
                {"title": "Book the florist deposit", "area": "budget"},
                {"title": "Chase the Patel RSVP", "area": "guests"},
            ]},
            {"date": "2026-06-30", "items": [
                {"title": "Florist deposit still needs booking", "area": "budget"},
            ]},
            {"date": "2026-07-07", "items": [
                {"title": "Book florist deposit", "area": "budget"},
            ]},
        ]}
        reps = outcomes.repeated_items(history)
        self.assertEqual(len(reps), 1)
        self.assertEqual(reps[0]["times_suggested"], 3)
        self.assertEqual(reps[0]["area"], "budget")
        self.assertEqual(reps[0]["first_seen"], "2026-06-23")

    def test_same_words_different_area_do_not_cluster(self):
        history = {"runs": [
            {"date": "d1", "items": [{"title": "Confirm final numbers", "area": "guests"}]},
            {"date": "d2", "items": [{"title": "Confirm final numbers", "area": "budget"}]},
        ]}
        self.assertEqual(outcomes.repeated_items(history), [])

    def test_record_brief_run_appends_and_trims(self):
        storage.set_couple(None)
        storage.save(outcomes.HISTORY_DOC, {"runs": []})
        result = {"analysis": {"action_items": [
            {"title": "Do the thing", "area": "budget", "priority": "high"}]}}
        for i in range(outcomes.KEEP_RUNS + 3):
            outcomes.record_brief_run(result, today=f"2026-01-{i + 1:02d}")
        runs = storage.load(outcomes.HISTORY_DOC)["runs"]
        self.assertEqual(len(runs), outcomes.KEEP_RUNS)
        self.assertEqual(runs[-1]["items"][0]["title"], "Do the thing")


class TestWeeklyExtraFacts(unittest.TestCase):
    def test_bundle_contains_outcomes_and_repeats(self):
        storage.set_couple(None)
        storage.save("guests", {"settings": {}, "households": [
            _hh("Silent", last_nudged_at="2026-01-01T10:00:00")]})
        storage.save(outcomes.HISTORY_DOC, {"runs": [
            {"date": "d1", "items": [{"title": "Book the band", "area": "budget"}]},
            {"date": "d2", "items": [{"title": "Book the band", "area": "budget"}]},
        ]})
        facts = outcomes.weekly_extra_facts()
        self.assertIn("nudge_outcomes", facts)
        self.assertIn("repeatedly_suggested", facts)
        self.assertIn("note", facts)

    def test_empty_world_yields_lean_facts(self):
        storage.set_couple(None)
        storage.save("guests", {"settings": {}, "households": []})
        storage.save(outcomes.HISTORY_DOC, {"runs": []})
        facts = outcomes.weekly_extra_facts()
        self.assertNotIn("nudge_outcomes", facts)
        self.assertNotIn("repeatedly_suggested", facts)


class TestRespondedAtStamp(unittest.TestCase):
    def test_public_rsvp_submit_stamps_responded_at(self):
        storage.set_couple(None)
        storage.save("guests", {
            "settings": {"wedding_date": "2026-09-01"},
            "households": [_hh("Levi", rsvp_token="a" * 20, party_size=2)],
        })
        client = create_app().test_client()
        r = client.post(f"/api/rsvp/{storage.LEGACY_COUPLE_ID}/{'a' * 20}",
                        json={"rsvp": "confirmed", "attending_count": 2})
        self.assertEqual(r.status_code, 200, r.get_json())
        h = storage.load("guests")["households"][0]
        self.assertIn("responded_at", h)
        # parses as a real timestamp
        datetime.fromisoformat(h["responded_at"])


if __name__ == "__main__":
    unittest.main()
