"""Wave delivery tests (offline — the WhatsApp provider is faked).

What must hold:
- personalize() fills [name] and [rsvp link] per household; empty message
  falls back to the default reminder.
- deliver_wave sends to every frozen recipient with a valid phone, records
  per-recipient sent/failed results on the wave, and marks delivery finished.
- Sending a wave via the endpoint starts delivery only when a provider is
  configured; unconfigured stays pure bookkeeping (no delivery key).
- Mixed outcomes (bad phone / unjoined recipient / success) are all recorded.
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

COUPLE = "wave-test"


def _seed():
    storage.set_couple(COUPLE)
    storage.save("guests", {
        "settings": {"wedding_date": "2026-10-10", "rsvp_deadline": "2026-09-20"},
        "households": [
            {"id": "h1", "household": "Cohen Family", "party_size": 2,
             "phone": "050-111 2222", "rsvp": "pending"},
            {"id": "h2", "household": "Levi Family", "party_size": 3,
             "phone": "not a phone", "rsvp": "pending"},
            {"id": "h3", "household": "Mizrahi Family", "party_size": 2,
             "phone": "052-333 4444", "rsvp": "confirmed"},  # replied → skipped by reminders
        ]})
    storage.save("profile", {"partner_a": "Tal", "partner_b": "Noa"})
    # Start invitations fresh each test.
    (Path(os.environ["VOW_DATA_DIR"]) / "couples" / COUPLE / "invitations.json").unlink(missing_ok=True)
    storage.set_couple(None)


class WaveBase(unittest.TestCase):
    def setUp(self):
        core._CALL_TIMES.clear()
        _seed()
        self.client = login(create_app().test_client(), couple=COUPLE)
        self._orig = (wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send,
                      wa.SEND_INTERVAL, wa.PROVIDER)
        wa.SEND_INTERVAL = 0
        wa.PROVIDER = "twilio"

    def tearDown(self):
        (wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send,
         wa.SEND_INTERVAL, wa.PROVIDER) = self._orig

    def _wave(self, view, wid):
        return next(w for w in view["waves"] if w["id"] == wid)


class TestPersonalize(unittest.TestCase):
    def setUp(self):
        _seed()
        storage.set_couple(COUPLE)
        self.guests = storage.load("guests")

    def tearDown(self):
        storage.set_couple(None)

    def test_placeholders_filled(self):
        h = self.guests["households"][0]
        text = wa.personalize("Dear [name], rsvp: [rsvp link]", self.guests, h,
                              "https://vow.example")
        self.assertIn("Dear Cohen Family", text)
        self.assertIn(f"https://vow.example/rsvp/{COUPLE}/", text)

    def test_empty_message_falls_back_to_default(self):
        h = self.guests["households"][0]
        text = wa.personalize("", self.guests, h, "https://vow.example")
        self.assertIn("Tal & Noa's wedding", text)
        self.assertIn(f"/rsvp/{COUPLE}/", text)


class TestDeliverWave(WaveBase):
    def test_mixed_outcomes_recorded(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN = "ACfake", "tok"
        results = {"972501112222": (201, {"sid": "SM1", "status": "queued"}),
                   "972523334444": (400, {"code": 63015})}
        wa._twilio_send = lambda to, msg: results[to]

        # "invitation" goes to everyone; send it via the endpoint.
        r = self.client.post("/api/invitations/waves/invitation/send")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["sent"]["delivery_job"])

        import time
        for _ in range(100):
            if all(j["done"] for j in core.JOBS.values()):
                break
            time.sleep(0.02)

        storage.set_couple(COUPLE)
        data = storage.load("invitations")
        storage.set_couple(None)
        wave = next(w for w in data["waves"] if w["id"] == "invitation")
        d = wave["delivery"]
        self.assertTrue(d["finished"])
        self.assertEqual(d["sent"], ["h1"])
        reasons = {f["id"]: f["reason"] for f in d["failed"]}
        self.assertEqual(reasons["h2"], "no_valid_phone")
        self.assertEqual(reasons["h3"], "recipient_not_in_sandbox")

    def test_unconfigured_send_is_bookkeeping_only(self):
        wa.ACCOUNT_SID = ""
        r = self.client.post("/api/invitations/waves/invitation/send")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["sent"]["delivery_job"])
        storage.set_couple(COUPLE)
        data = storage.load("invitations")
        storage.set_couple(None)
        wave = next(w for w in data["waves"] if w["id"] == "invitation")
        self.assertEqual(wave["status"], "sent")
        self.assertNotIn("delivery", wave)

    def test_personalized_message_reaches_provider(self):
        wa.ACCOUNT_SID, wa.AUTH_TOKEN = "ACfake", "tok"
        seen = {}
        def fake(to, msg):
            seen[to] = msg
            return 201, {"sid": "SM", "status": "queued"}
        wa._twilio_send = fake

        # Give the wave a custom message with placeholders first.
        self.client.put("/api/invitations/waves/invitation",
                        json={"message": "Hello [name]! RSVP: [rsvp link]"})
        self.client.post("/api/invitations/waves/invitation/send")
        import time
        for _ in range(100):
            if all(j["done"] for j in core.JOBS.values()):
                break
            time.sleep(0.02)
        self.assertIn("Hello Cohen Family!", seen["972501112222"])
        self.assertIn(f"/rsvp/{COUPLE}/", seen["972501112222"])
        self.assertIn("Hello Mizrahi Family!", seen["972523334444"])


if __name__ == "__main__":
    unittest.main()
