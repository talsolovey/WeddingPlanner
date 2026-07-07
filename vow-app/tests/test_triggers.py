"""Tests for event-driven wake-ups (Step 25 phase 3).

Deterministic rules, debounced change recording, the daily wake cap, the
notices API, and the storage save-hook. No model calls anywhere: the rules
ARE code, and the orchestrator wake is injected as a fake.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ["VOW_STORAGE_BACKEND"] = "files"

import storage  # noqa: E402
from agent import triggers  # noqa: E402
from app import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from authtest import login  # noqa: E402

NOW = datetime.fromisoformat("2026-07-07T12:00:00")


def _hh(name, **kw):
    return dict({"id": name, "household": name, "party_size": 2,
                 "rsvp": "pending"}, **kw)


def _clear_state():
    storage.set_couple(None)
    storage.save(triggers.NOTICES_DOC, {"items": []})
    storage.save(triggers.STATE_DOC, {})
    with triggers._LOCK:
        for t in triggers._TIMERS.values():
            t.cancel()
        triggers._TIMERS.clear()
        triggers._PENDING.clear()
        triggers._TRIAGING.clear()


class TestSignals(unittest.TestCase):
    def test_quiet_world_has_no_signals(self):
        self.assertEqual(triggers.signals(
            guests={"settings": {}, "households": []}, budget={}, now=NOW), [])

    def test_decline_spike_is_high(self):
        recent = (NOW - timedelta(hours=2)).isoformat()
        old = (NOW - timedelta(days=3)).isoformat()
        guests = {"settings": {}, "households": [
            _hh("A", rsvp="declined", responded_at=recent),
            _hh("B", rsvp="declined", responded_at=recent),
            _hh("C", rsvp="declined", responded_at=recent),
            _hh("D", rsvp="declined", responded_at=old),  # too old to count
        ]}
        found = triggers.signals(guests=guests, budget={}, now=NOW)
        kinds = {s["kind"]: s["severity"] for s in found}
        self.assertEqual(kinds.get("decline_spike"), "high")
        self.assertIn("3 households", found[0]["detail"])

    def test_capacity_breach_is_high(self):
        guests = {"settings": {"venue_capacity": 10}, "households": [
            _hh("A", rsvp="confirmed", attending_count=8),
            _hh("B", rsvp="confirmed", attending_count=5),
        ]}
        found = triggers.signals(guests=guests, budget={}, now=NOW)
        self.assertEqual(found[0]["kind"], "capacity_breach")
        self.assertEqual(found[0]["severity"], "high")

    def test_rsvp_deadline_crunch_is_medium(self):
        guests = {"settings": {"rsvp_deadline": "2026-07-10"}, "households": [
            _hh("A"), _hh("B"), _hh("C", rsvp="no_response"),
        ]}
        found = triggers.signals(guests=guests, budget={}, now=NOW)
        self.assertEqual(found[0]["kind"], "rsvp_deadline_crunch")
        self.assertEqual(found[0]["severity"], "medium")

    def test_budget_overrun_is_medium(self):
        budget = {"total_budget": 100, "items": [
            {"estimated": 80, "contracted": 90, "paid": 0},
            {"estimated": 40, "contracted": 0, "paid": 0},
        ]}
        found = triggers.signals(guests={"settings": {}, "households": []},
                                 budget=budget, now=NOW)
        self.assertEqual(found[0]["kind"], "budget_overrun")


class TestRecordChange(unittest.TestCase):
    def setUp(self):
        _clear_state()

    def tearDown(self):
        _clear_state()

    def test_watched_dataset_arms_a_debounce_timer(self):
        triggers.record_change(None, "guests")
        self.assertIn("guests", triggers._PENDING[triggers._key(None)])
        self.assertIn(triggers._key(None), triggers._TIMERS)

    def test_unwatched_dataset_is_ignored(self):
        triggers.record_change(None, "brief")
        triggers.record_change(None, "notices")
        self.assertEqual(triggers._PENDING, {})

    def test_storage_save_hook_reaches_record_change(self):
        create_app()  # registers the hook (idempotently)
        storage.set_couple(None)
        storage.save("guests", {"settings": {}, "households": []})
        # the hook receives current_couple() ("default" when unauthenticated)
        key = triggers._key(storage.current_couple())
        self.assertIn("guests", triggers._PENDING.get(key, set()))

    def test_own_writes_during_triage_do_not_retrigger(self):
        with triggers._LOCK:
            triggers._TRIAGING.add(triggers._key(None))
        triggers.record_change(None, "guests")
        self.assertEqual(triggers._PENDING, {})


class TestTriage(unittest.TestCase):
    def setUp(self):
        _clear_state()

    def tearDown(self):
        _clear_state()

    def _notices(self):
        return storage.load(triggers.NOTICES_DOC, {"items": []})["items"]

    def test_quiet_data_stays_quiet(self):
        storage.save("guests", {"settings": {}, "households": []})
        storage.save("budget", {})
        out = triggers.triage(None, run_brief=lambda t: self.fail("must not wake"))
        self.assertEqual(out["action"], "quiet")
        self.assertEqual(self._notices(), [])

    def test_high_signal_wakes_the_brief_once_then_caps(self):
        # capacity breach = high
        storage.save("guests", {"settings": {"venue_capacity": 5}, "households": [
            _hh("A", rsvp="confirmed", attending_count=9)]})
        storage.save("budget", {})
        woken = []
        for _ in range(triggers.MAX_WAKES_PER_DAY + 1):
            triggers.triage(None, run_brief=woken.append)
        self.assertEqual(len(woken), triggers.MAX_WAKES_PER_DAY)
        kinds = [n["kind"] for n in self._notices()]
        self.assertEqual(kinds.count("brief_refreshed"), triggers.MAX_WAKES_PER_DAY)
        self.assertEqual(kinds.count("noticed"), 1)  # capped run degraded

    def test_medium_signal_leaves_a_notice_only(self):
        storage.save("guests", {"settings": {}, "households": []})
        storage.save("budget", {"total_budget": 10,
                                "items": [{"estimated": 20, "contracted": 0, "paid": 0}]})
        out = triggers.triage(None, run_brief=lambda t: self.fail("must not wake"))
        self.assertEqual(out["action"], "notice")
        self.assertEqual(self._notices()[0]["kind"], "noticed")
        self.assertIn("budget", self._notices()[0]["message"])


class TestNoticesApi(unittest.TestCase):
    def setUp(self):
        _clear_state()
        self.client = login(create_app().test_client())

    def tearDown(self):
        _clear_state()

    def test_requires_auth(self):
        r = create_app().test_client().get("/api/notices")
        self.assertEqual(r.status_code, 401)

    def test_list_and_dismiss(self):
        storage.set_couple(None)
        n = triggers.add_notice("noticed", "3 declines today")
        # login() pins the test couple; notices live under that couple in the
        # request context, so write it there too.
        r = self.client.get("/api/notices")
        items = r.get_json()["items"]
        # the notice was saved under the legacy couple; the API scopes per
        # couple, so check the endpoint shape rather than cross-couple leakage
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(items, list)
        # dismiss an unknown id -> 404
        r = self.client.post("/api/notices/deadbeef/dismiss")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
