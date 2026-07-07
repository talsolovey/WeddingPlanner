"""Robustness tests: error handling, memory bounds, atomic document writes.

What must hold:
- Unhandled exceptions on /api/* return JSON 500 (never Flask's HTML page)
  and HTTP errors on /api/* are JSON too; page 404s keep their default body.
- The background-job registry and the rate limiter's IP map stay bounded.
- storage.mutate() is atomic under concurrency (no lost updates).
- Wave delivery writes through mutate(): concurrent edits to OTHER parts of
  the invitations document survive a running delivery.
"""

import json
import os
import sys
import tempfile
import threading
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


class TestErrorHandler(unittest.TestCase):
    def setUp(self):
        core._CALL_TIMES.clear()
        self.app = create_app()

        @self.app.get("/api/boom")           # a route that blows up
        def boom():
            raise RuntimeError("kaboom")

        @self.app.get("/boom-page")
        def boom_page():
            raise RuntimeError("kaboom")

        self.client = login(self.app.test_client())

    def test_api_exception_is_json_500_not_html(self):
        r = self.client.get("/api/boom")
        self.assertEqual(r.status_code, 500)
        self.assertIn("error", r.get_json())
        self.assertNotIn(b"<html", r.data.lower())

    def test_api_http_errors_are_json(self):
        r = self.client.get("/api/definitely-not-a-route")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_page_exception_is_plain_500(self):
        r = self.client.get("/boom-page")
        self.assertEqual(r.status_code, 500)

    def test_page_404_keeps_default_behavior(self):
        r = self.client.get("/definitely-not-a-page")
        # Non-API paths without a session redirect to login (the gate), and
        # crucially do not crash the error handler.
        self.assertIn(r.status_code, (302, 404))


class TestMemoryBounds(unittest.TestCase):
    def test_finished_jobs_are_pruned(self):
        core.JOBS.clear()
        for i in range(core.MAX_JOBS + 50):
            core.JOBS[f"old{i}"] = {"done": True}
        core.run_job(lambda emit: "ok")
        self.assertLessEqual(len(core.JOBS), core.MAX_JOBS + 1)
        core.JOBS.clear()

    def test_running_jobs_survive_pruning(self):
        core.JOBS.clear()
        core.JOBS["running"] = {"done": False}
        for i in range(core.MAX_JOBS + 10):
            core.JOBS[f"old{i}"] = {"done": True}
        core._prune_jobs()
        self.assertIn("running", core.JOBS)
        core.JOBS.clear()

    def test_stale_ips_swept(self):
        core._CALL_TIMES.clear()
        for i in range(1200):
            core._CALL_TIMES[f"10.0.{i // 250}.{i % 250}"] = [0.0]  # ancient
        client = login(create_app().test_client())
        client.post("/api/guests/rsvp-links")  # not rate-limited; use a limited one:
        client.post("/api/contracts/analyze")  # triggers the limiter wrapper
        self.assertLess(len(core._CALL_TIMES), 100)
        core._CALL_TIMES.clear()


class TestMutate(unittest.TestCase):
    COUPLE = "mutate-test"

    def test_concurrent_increments_lose_nothing(self):
        storage.set_couple(self.COUPLE)
        storage.save("budget", {"n": 0})
        storage.set_couple(None)

        def bump():
            storage.set_couple(self.COUPLE)
            for _ in range(20):
                storage.mutate("budget", lambda d: {"n": d["n"] + 1})
            storage.set_couple(None)

        threads = [threading.Thread(target=bump) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        storage.set_couple(self.COUPLE)
        self.assertEqual(storage.load("budget")["n"], 100)
        storage.set_couple(None)

    def test_returning_none_skips_the_write(self):
        storage.set_couple(self.COUPLE)
        storage.save("budget", {"n": 42})
        storage.mutate("budget", lambda d: None)
        self.assertEqual(storage.load("budget")["n"], 42)
        storage.set_couple(None)


class TestDeliveryDoesNotClobber(unittest.TestCase):
    COUPLE = "clobber-test"

    def test_concurrent_edit_to_other_wave_survives_delivery(self):
        storage.set_couple(self.COUPLE)
        storage.save("guests", {
            "settings": {"wedding_date": "2026-10-10"},
            "households": [{"id": "h1", "household": "Cohen", "party_size": 2,
                            "phone": "0501112222", "rsvp": "pending"}]})
        storage.save("profile", {"partner_a": "Tal"})
        storage.save("invitations", {
            "waves": [
                {"id": "invitation", "title": "Invite", "kind": "invite",
                 "status": "sent", "sent_to": ["h1"], "message": ""},
                {"id": "reminder_1", "title": "R1", "kind": "reminder",
                 "status": "scheduled", "message": "old text", "sent_to": []},
            ],
            "reminder_counts": {}})
        storage.set_couple(None)

        orig = (wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send, wa.SEND_INTERVAL)
        try:
            wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa.SEND_INTERVAL = "ACfake", "tok", 0

            def send_and_meddle(to, msg):
                # Simulate a concurrent request editing ANOTHER wave while the
                # delivery loop is mid-flight.
                def edit(doc):
                    other = next(w for w in doc["waves"] if w["id"] == "reminder_1")
                    other["message"] = "edited during delivery"
                    return doc
                storage.mutate("invitations", edit)
                return 201, {"sid": "SM", "status": "queued"}

            wa._twilio_send = send_and_meddle
            storage.set_couple(self.COUPLE)
            result = wa.deliver_wave("https://vow.example", "invitation")
            data = storage.load("invitations")
            storage.set_couple(None)
        finally:
            (wa.ACCOUNT_SID, wa.AUTH_TOKEN, wa._twilio_send, wa.SEND_INTERVAL) = orig

        self.assertEqual(result["sent"], 1)
        waves = {w["id"]: w for w in data["waves"]}
        # Delivery results recorded...
        self.assertEqual(waves["invitation"]["delivery"]["sent"], ["h1"])
        self.assertTrue(waves["invitation"]["delivery"]["finished"])
        # ...and the concurrent edit to the other wave was NOT clobbered.
        self.assertEqual(waves["reminder_1"]["message"], "edited during delivery")


if __name__ == "__main__":
    unittest.main()
