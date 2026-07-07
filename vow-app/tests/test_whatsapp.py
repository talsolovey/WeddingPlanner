"""WhatsApp nudge tests (offline — Twilio is faked at the _twilio_send seam).

What must hold:
- Phone normalization turns local formats into E.164 digits.
- The endpoint is session-gated and knows the household.
- Unconfigured Twilio / missing phone / unjoined recipient -> wa.me fallback.
- A successful send marks the household and logs activity.
"""

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
import app.whatsapp as wa                       # noqa: E402
from app import create_app                     # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from authtest import login                      # noqa: E402


def _seed_couple(couple):
    storage.set_couple(couple)
    storage.save("guests", {
        "settings": {"wedding_date": "2026-10-10"},
        "households": [
            {"id": "h1", "household": "The Cohens", "party_size": 2,
             "phone": "050-123 4567", "rsvp": "pending"},
            {"id": "h2", "household": "The Levis", "party_size": 3,
             "phone": "", "rsvp": "pending"},
        ]})
    storage.save("profile", {"partner_a": "Tal", "partner_b": "Noa"})
    storage.set_couple(None)


class TestNormalizePhone(unittest.TestCase):
    def test_local_format_gets_country_code(self):
        self.assertEqual(wa.normalize_phone("050-123 4567", "972"), "972501234567")

    def test_international_passthrough(self):
        self.assertEqual(wa.normalize_phone("+972 50 123 4567", "972"), "972501234567")
        self.assertEqual(wa.normalize_phone("0044 20 7946 0000", "972"), "442079460000")

    def test_garbage_is_empty(self):
        self.assertEqual(wa.normalize_phone("call me maybe", "972"), "")
        self.assertEqual(wa.normalize_phone("123", "972"), "")
        self.assertEqual(wa.normalize_phone("", "972"), "")


class NudgeBase(unittest.TestCase):
    COUPLE = "wa-test"

    def setUp(self):
        core._CALL_TIMES.clear()
        _seed_couple(self.COUPLE)
        self.client = login(create_app().test_client(), couple=self.COUPLE)
        self._orig = (wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send)

    def tearDown(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send = self._orig


class TestNudgeEndpoint(NudgeBase):
    def test_requires_session(self):
        fresh = create_app().test_client()
        self.assertEqual(fresh.post("/api/guests/h1/nudge").status_code, 401)

    def test_unknown_household_404s(self):
        self.assertEqual(self.client.post("/api/guests/nope/nudge").status_code, 404)

    def test_unconfigured_twilio_falls_back_with_message(self):
        wa.ACCOUNT_SID = ""
        body = self.client.post("/api/guests/h1/nudge").get_json()
        self.assertFalse(body["sent"])
        self.assertEqual(body["reason"], "twilio_not_configured")
        self.assertIn("wa.me/972501234567", body["wa_url"])
        self.assertIn("Tal%20%26%20Noa", body["wa_url"])   # names in the text
        self.assertIn(f"/rsvp/{self.COUPLE}/", body["wa_url"])  # magic link

    def test_missing_phone_falls_back_without_number(self):
        wa.ACCOUNT_SID = ""
        body = self.client.post("/api/guests/h2/nudge").get_json()
        self.assertEqual(body["reason"], "no_valid_phone")
        self.assertIn("wa.me/?text=", body["wa_url"])

    def test_successful_send_marks_and_logs(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN = "ACfake", "tok"
        sent = {}
        def fake_send(to, msg):
            sent["to"], sent["msg"] = to, msg
            return 201, {"sid": "SM123", "status": "queued"}
        wa._twilio_send = fake_send
        body = self.client.post("/api/guests/h1/nudge").get_json()
        self.assertTrue(body["sent"])
        self.assertEqual(body["sid"], "SM123")
        self.assertEqual(sent["to"], "972501234567")
        self.assertIn("Tal & Noa's wedding on October 10", sent["msg"])
        storage.set_couple(self.COUPLE)
        guests = storage.load("guests")
        self.assertTrue(guests["households"][0].get("last_nudged_at"))
        acts = storage.load("activity", [])
        self.assertIn("nudged the The Cohens", acts[-1]["text"])
        storage.set_couple(None)

    def test_unjoined_recipient_falls_back(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN = "ACfake", "tok"
        wa._twilio_send = lambda to, msg: (400, {"code": 63015})
        body = self.client.post("/api/guests/h1/nudge").get_json()
        self.assertFalse(body["sent"])
        self.assertEqual(body["reason"], "recipient_not_in_sandbox")
        self.assertIn("wa.me/972501234567", body["wa_url"])

    def test_other_twilio_error_falls_back_with_code(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN = "ACfake", "tok"
        wa._twilio_send = lambda to, msg: (400, {"code": 21606})
        body = self.client.post("/api/guests/h1/nudge").get_json()
        self.assertEqual(body["reason"], "twilio_error_21606")


if __name__ == "__main__":
    unittest.main()
