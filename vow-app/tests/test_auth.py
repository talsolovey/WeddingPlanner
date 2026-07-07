"""Auth gate + multi-couple scoping tests (offline — Supabase is faked).

What must hold:
- Unauthenticated: pages redirect to /login, /api/* return 401, and the
  public surface (login page, auth API, RSVP links, assets) stays open.
- Signup/login proxy Supabase Auth (faked here) and open a server session.
- The OAuth token endpoint VERIFIES the token before opening a session.
- Two signed-in couples read and write completely separate documents.
- Background jobs run under the couple that started them.
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
import app.auth as auth                         # noqa: E402
import app.core as core                         # noqa: E402
from app import create_app                     # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from authtest import login                  # noqa: E402

DATA_DIR = Path(os.environ["VOW_DATA_DIR"])


class AuthBase(unittest.TestCase):
    def setUp(self):
        core._CALL_TIMES.clear()
        self.client = create_app().test_client()


class TestGate(AuthBase):
    def test_pages_redirect_to_login(self):
        for path in ["/budget", "/guests", "/seating", "/onboarding"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login", r.headers["Location"])

    def test_root_serves_public_landing_when_signed_out(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"carries the clipboard", r.data)  # landing, not dashboard
        self.assertNotIn(b"focus-card", r.data)

    def test_api_returns_401(self):
        for path in ["/api/profile", "/api/budget", "/api/overview"]:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 401, path)

    def test_public_surface_open(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/auth/callback").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/config").status_code, 200)
        self.assertEqual(self.client.get("/vow.css").status_code, 200)
        # RSVP endpoints stay guest-reachable (404 = handled, not blocked).
        r = self.client.get("/api/rsvp/default/nosuchtoken")
        self.assertEqual(r.status_code, 404)

    def test_html_files_not_reachable_as_static(self):
        r = self.client.get("/home.html")
        self.assertEqual(r.status_code, 302)

    def test_signed_in_passes(self):
        login(self.client)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/profile").status_code, 200)

    def test_logout_closes_session(self):
        login(self.client)
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/profile").status_code, 401)


class TestAuthEndpoints(AuthBase):
    """Supabase Auth is faked at the _gotrue seam; no network."""

    def setUp(self):
        super().setUp()
        self._orig = (auth.SUPABASE_URL, auth.PUBLISHABLE_KEY, auth._gotrue)
        auth.SUPABASE_URL = "https://fake.supabase.co"
        auth.PUBLISHABLE_KEY = "sb_publishable_fake"

    def tearDown(self):
        auth.SUPABASE_URL, auth.PUBLISHABLE_KEY, auth._gotrue = self._orig

    def test_login_opens_session(self):
        auth._gotrue = lambda *a, **k: (200, {
            "access_token": "tok", "user": {"id": "couple-1", "email": "a@b.co"}})
        r = self.client.post("/api/auth/login",
                             json={"email": "a@b.co", "password": "hunter2!"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["couple_id"], "couple-1")
        me = self.client.get("/api/auth/me").get_json()
        self.assertEqual(me["couple_id"], "couple-1")

    def test_bad_password_is_401(self):
        auth._gotrue = lambda *a, **k: (400, {"error_description": "Invalid login credentials"})
        r = self.client.post("/api/auth/login",
                             json={"email": "a@b.co", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_signup_requires_decent_password(self):
        r = self.client.post("/api/auth/signup",
                             json={"email": "a@b.co", "password": "short"})
        self.assertEqual(r.status_code, 400)

    def test_signup_with_email_confirmation_pending(self):
        auth._gotrue = lambda *a, **k: (200, {"user": {"id": "u1", "email": "a@b.co"}})
        r = self.client.post("/api/auth/signup",
                             json={"email": "a@b.co", "password": "longenough1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get("confirm_email"))
        # No session was opened.
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_oauth_token_is_verified_not_trusted(self):
        auth._gotrue = lambda *a, **k: (401, {})
        r = self.client.post("/api/auth/token", json={"access_token": "forged"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_oauth_token_valid_opens_session(self):
        auth._gotrue = lambda *a, **k: (200, {"id": "couple-g", "email": "g@b.co"})
        r = self.client.post("/api/auth/token", json={"access_token": "good"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").get_json()["couple_id"], "couple-g")

    def test_unconfigured_auth_is_503(self):
        auth.SUPABASE_URL = ""
        r = self.client.post("/api/auth/login", json={"email": "a@b.co", "password": "x" * 8})
        self.assertEqual(r.status_code, 503)


class TestCoupleIsolation(AuthBase):
    def test_two_couples_see_separate_data(self):
        a = login(create_app().test_client(), couple="couple-a")
        b = login(create_app().test_client(), couple="couple-b")
        a.put("/api/profile", json={"partner_a": "Ada", "venue": "Haifa"})
        b.put("/api/profile", json={"partner_a": "Noa", "venue": "Jaffa"})
        self.assertEqual(a.get("/api/profile").get_json()["partner_a"], "Ada")
        self.assertEqual(b.get("/api/profile").get_json()["partner_a"], "Noa")
        # And on disk: separate per-couple document trees.
        self.assertTrue((DATA_DIR / "couples" / "couple-a" / "profile.json").exists())
        self.assertTrue((DATA_DIR / "couples" / "couple-b" / "profile.json").exists())

    def test_couple_scoped_storage_files(self):
        storage.set_couple("couple-x")
        storage.save("budget", {"total_budget": 7})
        storage.set_couple(None)
        self.assertNotEqual(storage.load("budget", {}).get("total_budget"), 7)
        storage.set_couple("couple-x")
        self.assertEqual(storage.load("budget", {})["total_budget"], 7)
        storage.set_couple(None)

    def test_rsvp_links_carry_couple_id(self):
        c = login(create_app().test_client(), couple="couple-a")
        # Seed a household for couple-a through the API surface.
        c.put("/api/profile", json={"partner_a": "Ada"})
        storage.set_couple("couple-a")
        storage.save("guests", {"settings": {}, "households": [
            {"id": "h1", "household": "The Tests", "party_size": 2}]})
        storage.set_couple(None)
        links = c.post("/api/guests/rsvp-links").get_json()["links"]
        self.assertTrue(links[0]["url"].startswith("/rsvp/couple-a/"))

    def test_job_poll_scoped_to_couple(self):
        storage.set_couple("couple-a")
        job_id = core.run_job(lambda emit: "done")
        storage.set_couple(None)
        import time
        for _ in range(50):
            if core.JOBS[job_id]["done"]:
                break
            time.sleep(0.01)
        a = login(create_app().test_client(), couple="couple-a")
        b = login(create_app().test_client(), couple="couple-b")
        self.assertEqual(a.get(f"/api/jobs/{job_id}").status_code, 200)
        self.assertEqual(b.get(f"/api/jobs/{job_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
