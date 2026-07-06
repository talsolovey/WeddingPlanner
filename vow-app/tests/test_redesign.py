"""Tests for the redesign's new backend pieces — all offline (no model calls):

  1. Profile: field validation (photo type/size, priorities whitelist + cap),
     and the sync of wedding date / budget cap into their source-of-truth files.
  2. Invitation waves: default plan creation, recipients recomputed at read
     time (repliers skipped automatically), send-now freezing recipients and
     bumping reminder counts, the 3-reminder cap, the due-date scheduler tick,
     and sent waves being immutable.
  3. Checklist: auto-check rules driven by live app data, manual toggle
     winning over the rule.
  4. Timeline: event add/delete with validation, small-hours sorting (01:00
     lands after 23:30), unbooked-vendor slots marked hollow.

Run from the vow-app/ directory:
    python -m unittest discover tests
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

import app.core as core          # noqa: E402
from app import create_app      # noqa: E402

DATA_DIR = Path(os.environ["VOW_DATA_DIR"])


def _seed():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "guests.json").write_text(json.dumps({
        "settings": {"currency": "USD", "venue_capacity": 40,
                     "catering_per_head": 145, "rsvp_deadline": "2026-10-01",
                     "wedding_date": "2026-11-12"},
        "households": [
            {"id": "g01", "household": "Cohen Family", "side": "partner_a",
             "party_size": 4, "rsvp": "pending", "attending_count": 0,
             "plus_one_allowed": False, "plus_one_name": "", "notes": ""},
            {"id": "g02", "household": "Levi Family", "side": "partner_b",
             "party_size": 2, "rsvp": "confirmed", "attending_count": 2,
             "plus_one_allowed": False, "plus_one_name": "", "notes": ""},
            {"id": "g03", "household": "Mizrahi Family", "side": "partner_a",
             "party_size": 3, "rsvp": "no_response", "attending_count": 0,
             "plus_one_allowed": False, "plus_one_name": "", "notes": ""},
        ],
    }))
    (DATA_DIR / "budget.json").write_text(json.dumps({
        "currency": "USD", "total_budget": 0, "items": [
            {"id": "b01", "category": "venue", "vendor": "Pavilion",
             "estimated": 0, "contracted": 50000, "paid": 10000,
             "due_before_wedding": True, "notes": ""},
        ],
    }))
    (DATA_DIR / "seating.json").write_text(json.dumps({"tables": []}))
    for name in ("profile.json", "invitations.json", "checklist.json",
                 "timeline.json"):
        path = DATA_DIR / name
        if path.exists():
            path.unlink()


class RedesignBase(unittest.TestCase):
    def setUp(self):
        _seed()
        core._CALL_TIMES.clear()
        self.client = create_app().test_client()


class TestProfile(RedesignBase):
    def test_defaults_and_roundtrip(self):
        profile = self.client.get("/api/profile").get_json()
        self.assertFalse(profile["onboarded"])
        res = self.client.put("/api/profile", json={
            "partner_a": "Tal", "partner_b": "Omer", "venue": "The Pavilion",
            "priorities": ["Food & wine", "Low stress"], "onboarded": True,
        })
        self.assertEqual(res.status_code, 200)
        profile = self.client.get("/api/profile").get_json()
        self.assertEqual(profile["partner_a"], "Tal")
        self.assertEqual(profile["priorities"], ["Food & wine", "Low stress"])
        self.assertTrue(profile["onboarded"])

    def test_photo_must_be_an_image_data_url(self):
        res = self.client.put("/api/profile", json={"photo": "javascript:alert(1)"})
        self.assertEqual(res.status_code, 400)
        res = self.client.put("/api/profile", json={"photo": "data:image/jpeg;base64,abc"})
        self.assertEqual(res.status_code, 200)

    def test_priorities_whitelisted_and_capped_at_three(self):
        res = self.client.put("/api/profile", json={
            "priorities": ["Food & wine", "hack the DB", "Photography",
                           "Low stress", "The dress"]})
        got = res.get_json()["priorities"]
        self.assertNotIn("hack the DB", got)
        self.assertLessEqual(len(got), 3)

    def test_sync_into_guests_and_budget(self):
        self.client.put("/api/profile", json={
            "wedding_date": "2027-05-20", "budget_estimate": 123000})
        guests = self.client.get("/api/guests").get_json()
        self.assertEqual(guests["settings"]["wedding_date"], "2027-05-20")
        budget = self.client.get("/api/budget").get_json()
        self.assertEqual(budget["total_budget"], 123000)
        # An existing cap is never overwritten by the estimate.
        self.client.put("/api/profile", json={"budget_estimate": 999})
        budget = self.client.get("/api/budget").get_json()
        self.assertEqual(budget["total_budget"], 123000)


class TestInvitations(RedesignBase):
    def test_default_plan_has_six_waves_anchored_to_dates(self):
        view = self.client.get("/api/invitations").get_json()
        ids = [w["id"] for w in view["waves"]]
        self.assertEqual(ids, ["save_the_date", "invitation", "reminder_1",
                               "final_reminder", "day_of", "thank_you"])
        final = next(w for w in view["waves"] if w["id"] == "final_reminder")
        self.assertEqual(final["send_on"], "2026-09-17")  # 2 weeks pre-deadline

    def test_recipients_skip_repliers(self):
        view = self.client.get("/api/invitations").get_json()
        reminder = next(w for w in view["waves"] if w["id"] == "reminder_1")
        names = {r["household"] for r in reminder["recipients"]}
        self.assertEqual(names, {"Cohen Family", "Mizrahi Family"})
        day_of = next(w for w in view["waves"] if w["id"] == "day_of")
        self.assertEqual([r["household"] for r in day_of["recipients"]],
                         ["Levi Family"])

    def test_send_now_freezes_recipients_and_counts(self):
        # Move the wave to the future first so the scheduler doesn't auto-send.
        self.client.put("/api/invitations/waves/reminder_1",
                        json={"send_on": "2099-01-01"})
        res = self.client.post("/api/invitations/waves/reminder_1/send")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["sent"]["count"], 2)
        # A sent wave can't be edited or re-sent.
        self.assertEqual(self.client.post(
            "/api/invitations/waves/reminder_1/send").status_code, 400)
        self.assertEqual(self.client.put(
            "/api/invitations/waves/reminder_1",
            json={"message": "x"}).status_code, 400)

    def test_three_reminder_cap(self):
        data = json.loads((DATA_DIR / "invitations.json").read_text()) if (
            DATA_DIR / "invitations.json").exists() else None
        # Prime: Cohen has already had 3 reminders, Mizrahi 1.
        self.client.get("/api/invitations")
        data = json.loads((DATA_DIR / "invitations.json").read_text())
        data["reminder_counts"] = {"g01": 3, "g03": 1}
        (DATA_DIR / "invitations.json").write_text(json.dumps(data))
        view = self.client.get("/api/invitations").get_json()
        reminder = next(w for w in view["waves"] if w["id"] == "reminder_1")
        self.assertEqual([r["household"] for r in reminder["recipients"]],
                         ["Mizrahi Family"])

    def test_due_waves_auto_send_on_read(self):
        self.client.get("/api/invitations")  # creates the plan on disk
        data = json.loads((DATA_DIR / "invitations.json").read_text())
        for w in data["waves"]:
            if w["id"] == "reminder_1":
                w["send_on"] = (date.today() - timedelta(days=1)).isoformat()
        (DATA_DIR / "invitations.json").write_text(json.dumps(data))
        view = self.client.get("/api/invitations").get_json()
        reminder = next(w for w in view["waves"] if w["id"] == "reminder_1")
        self.assertEqual(reminder["status"], "sent")
        data = json.loads((DATA_DIR / "invitations.json").read_text())
        self.assertEqual(data["reminder_counts"], {"g01": 1, "g03": 1})

    def test_bad_date_rejected(self):
        self.assertEqual(self.client.put(
            "/api/invitations/waves/reminder_1",
            json={"send_on": "not-a-date"}).status_code, 400)


class TestChecklist(RedesignBase):
    def _seed_checklist(self):
        (DATA_DIR / "checklist.json").write_text(json.dumps({"phases": [
            {"key": "p0", "title": "A year out", "items": [
                {"id": "c1", "label": "Book the venue",
                 "auto_rule": "venue_booked", "manual": None,
                 "href": "/vendors", "area": "Vendors"},
                {"id": "c2", "label": "Write your vows",
                 "auto_rule": "", "manual": None, "href": "", "area": ""},
            ]},
        ]}))

    def test_auto_rule_checks_from_live_data(self):
        self._seed_checklist()
        view = self.client.get("/api/checklist").get_json()
        venue = view["phases"][0]["items"][0]
        self.assertTrue(venue["done"])
        self.assertTrue(venue["auto"])
        vows = view["phases"][0]["items"][1]
        self.assertFalse(vows["done"])

    def test_manual_toggle_wins_over_rule(self):
        self._seed_checklist()
        view = self.client.put("/api/checklist/items/c1",
                               json={"done": False}).get_json()
        venue = view["phases"][0]["items"][0]
        self.assertFalse(venue["done"])   # unchecked an auto item — it sticks
        self.assertFalse(venue["auto"])
        self.assertEqual(self.client.put(
            "/api/checklist/items/nope", json={"done": True}).status_code, 404)


class TestTimeline(RedesignBase):
    def test_add_sorts_small_hours_to_the_end(self):
        for t, title in [("23:30", "Cake"), ("01:00", "Send-off"),
                         ("17:00", "Ceremony")]:
            res = self.client.post("/api/timeline/events",
                                   json={"time": t, "title": title})
            self.assertEqual(res.status_code, 200)
        view = self.client.get("/api/timeline").get_json()
        self.assertEqual([e["title"] for e in view["events"]],
                         ["Ceremony", "Cake", "Send-off"])

    def test_event_validation_and_delete(self):
        self.assertEqual(self.client.post(
            "/api/timeline/events", json={"time": "17:00"}).status_code, 400)
        view = self.client.post("/api/timeline/events",
                                json={"time": "17:00", "title": "Ceremony"}).get_json()
        event_id = view["events"][0]["id"]
        view = self.client.delete("/api/timeline/events/" + event_id).get_json()
        self.assertEqual(view["events"], [])

    def test_unbooked_vendor_slot_is_hollow(self):
        self.client.post("/api/timeline/events", json={
            "time": "21:00", "title": "Dancing", "vendor_category": "music/DJ"})
        self.client.post("/api/timeline/events", json={
            "time": "17:00", "title": "Ceremony", "vendor_category": "venue"})
        view = self.client.get("/api/timeline").get_json()
        by_title = {e["title"]: e for e in view["events"]}
        self.assertTrue(by_title["Dancing"]["vendor_unbooked"])    # no DJ contract
        self.assertFalse(by_title["Ceremony"]["vendor_unbooked"])  # venue signed


if __name__ == "__main__":
    unittest.main()
