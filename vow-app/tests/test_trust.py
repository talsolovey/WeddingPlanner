"""Tests for trust tiers (Step 25 phase 4): graduated autonomy that is
earned by approvals, revoked by rejections, and always couple-controllable.

Offline: WhatsApp is faked at the send_whatsapp seam; no model calls.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ["VOW_STORAGE_BACKEND"] = "files"

import storage  # noqa: E402
from agent import triggers, trust  # noqa: E402
from app import create_app, whatsapp  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from authtest import login  # noqa: E402


def _hh(name, **kw):
    return dict({"id": name, "household": name, "party_size": 2,
                 "rsvp": "pending"}, **kw)


def _crunch_guests():
    """Data that fires the rsvp_deadline_crunch signal with phoneable targets."""
    from datetime import date, timedelta
    deadline = (date.today() + timedelta(days=3)).isoformat()
    return {"settings": {"rsvp_deadline": deadline}, "households": [
        _hh("Levi", phone="0501234567"),
        _hh("Patel", phone="0507654321", rsvp="no_response"),
        _hh("Cohen", phone="0509999999"),
        _hh("NoPhone"),
    ]}


def _reset():
    storage.set_couple(None)
    storage.save(triggers.NOTICES_DOC, {"items": []})
    storage.save(triggers.STATE_DOC, {})
    storage.save(trust.TRUST_DOC, {})
    storage.save("budget", {})
    with triggers._LOCK:
        for t in triggers._TIMERS.values():
            t.cancel()
        triggers._TIMERS.clear()
        triggers._PENDING.clear()
        triggers._TRIAGING.clear()


class TrustBase(unittest.TestCase):
    def setUp(self):
        _reset()
        self._real_send = whatsapp.send_whatsapp
        whatsapp.send_whatsapp = lambda phone, body: (True, "", {"sid": "fake"})

    def tearDown(self):
        whatsapp.send_whatsapp = self._real_send
        _reset()

    def _notices(self, kind=None):
        items = storage.load(triggers.NOTICES_DOC, {"items": []})["items"]
        return [n for n in items if kind is None or n["kind"] == kind]


class TestTiers(TrustBase):
    def test_defaults(self):
        self.assertEqual(trust.tier("send_nudge"), 2)
        self.assertEqual(trust.tier("refresh_brief"), 1)
        self.assertEqual(trust.tier("nonexistent"), 2)  # unknown never auto-runs

    def test_couple_can_set_only_1_or_2(self):
        self.assertEqual(trust.set_tier("send_nudge", 1)["tier"], 1)
        self.assertEqual(trust.set_tier("send_nudge", 2)["tier"], 2)
        with self.assertRaises(ValueError):
            trust.set_tier("send_nudge", 0)
        with self.assertRaises(ValueError):
            trust.set_tier("made_up_action", 1)

    def test_streak_promotes_and_notifies(self):
        for _ in range(trust.PROMOTE_AFTER - 1):
            spec = trust.record_decision("send_nudge", approved=True)
            self.assertEqual(spec["tier"], 2)
        spec = trust.record_decision("send_nudge", approved=True)
        self.assertEqual(spec["tier"], 1)
        self.assertTrue(spec["earned"])
        self.assertEqual(len(self._notices("promoted")), 1)

    def test_rejection_resets_streak_and_revokes_earned_tier(self):
        for _ in range(trust.PROMOTE_AFTER):
            trust.record_decision("send_nudge", approved=True)
        spec = trust.record_decision("send_nudge", approved=False)
        self.assertEqual(spec["tier"], 2)
        self.assertEqual(spec["streak"], 0)
        self.assertFalse(spec["earned"])

    def test_rejection_does_not_demote_a_couple_chosen_tier(self):
        trust.set_tier("send_nudge", 1)  # chosen, not earned
        spec = trust.record_decision("send_nudge", approved=False)
        self.assertEqual(spec["tier"], 1)


class TestTriageProposals(TrustBase):
    def test_tier2_creates_a_proposal_and_sends_nothing(self):
        storage.save("guests", _crunch_guests())
        sent = []
        whatsapp.send_whatsapp = lambda p, b: sent.append(p) or (True, "", {})
        out = triggers.triage(None, run_brief=lambda t: None)
        self.assertEqual(out["nudge"]["action"], "proposed")
        self.assertEqual(sent, [])
        props = self._notices("proposal")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["action"], "send_nudge")
        # only phoneable households are targeted
        self.assertEqual(set(props[0]["payload"]["household_ids"]),
                         {"Levi", "Patel", "Cohen"})

    def test_no_duplicate_open_proposal(self):
        storage.save("guests", _crunch_guests())
        triggers.triage(None, run_brief=lambda t: None)
        triggers.triage(None, run_brief=lambda t: None)
        self.assertEqual(len(self._notices("proposal")), 1)

    def test_tier1_executes_and_reports(self):
        trust.set_tier("send_nudge", 1)
        storage.save("guests", _crunch_guests())
        out = triggers.triage(None, run_brief=lambda t: None)
        self.assertEqual(out["nudge"]["action"], "executed")
        self.assertEqual(out["nudge"]["sent"], 3)
        self.assertEqual(len(self._notices("acted")), 1)
        self.assertEqual(len(self._notices("proposal")), 0)
        # nudges were stamped so the outcome loop can judge them later
        guests = storage.load("guests")
        stamped = [h for h in guests["households"] if h.get("last_nudged_at")]
        self.assertEqual(len(stamped), 3)


class TestApprovalEndpoints(TrustBase):
    def setUp(self):
        super().setUp()
        self.client = login(create_app().test_client())

    def test_approve_executes_and_records(self):
        # a proposal exists (written under the logged-in couple's context is
        # not needed: authtest login pins the same default couple in tests)
        storage.save("guests", _crunch_guests())
        n = triggers.add_notice("proposal", "nudge?", action="send_nudge",
                                payload={"household_ids": ["Levi", "NoPhone"]})
        r = self.client.post(f"/api/notices/{n['id']}/approve")
        body = r.get_json()
        self.assertEqual(r.status_code, 200, body)
        self.assertEqual(body["result"]["sent"], 1)      # Levi
        self.assertEqual(body["result"]["failed"], 1)    # NoPhone
        self.assertEqual(body["trust"]["streak"], 1)
        # approved proposal is now closed
        r2 = self.client.post(f"/api/notices/{n['id']}/approve")
        self.assertEqual(r2.status_code, 409)

    def test_dismissing_a_proposal_counts_as_rejection(self):
        trust.record_decision("send_nudge", approved=True)
        n = triggers.add_notice("proposal", "nudge?", action="send_nudge",
                                payload={"household_ids": []})
        r = self.client.post(f"/api/notices/{n['id']}/dismiss")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(trust.get_trust()["send_nudge"]["streak"], 0)

    def test_plain_notice_is_not_approvable(self):
        n = triggers.add_notice("noticed", "just info")
        r = self.client.post(f"/api/notices/{n['id']}/approve")
        self.assertEqual(r.status_code, 400)

    def test_trust_api_get_and_put(self):
        r = self.client.get("/api/trust")
        self.assertEqual(r.status_code, 200)
        self.assertIn("send_nudge", r.get_json()["actions"])
        r = self.client.put("/api/trust/send_nudge", json={"tier": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(trust.tier("send_nudge"), 1)
        r = self.client.put("/api/trust/send_nudge", json={"tier": 5})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
