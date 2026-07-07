"""Authentication: Supabase Auth (email+password and Google OAuth) in front
of a server-side Flask session.

Security model:
- Supabase Auth is the identity provider only. The server proxies signup and
  login using the *publishable* key (safe to expose; it can't read data), and
  the resulting user id becomes the couple_id every document is scoped by.
- The session is a signed, HttpOnly Flask cookie holding just the couple id
  and email — the client never holds or sends Supabase tokens after login,
  so there is nothing to spoof: data access is decided by the cookie only.
- Google OAuth uses Supabase's hosted flow. The browser comes back to
  /auth/callback with tokens in the URL fragment; a tiny page POSTs the
  access token to /api/auth/token, which VERIFIES it against Supabase
  (GET /auth/v1/user) before opening the session. An invalid or forged
  token never becomes a session.
- Login endpoints are rate-limited (they're a public brute-force surface).
"""

import os

from flask import Blueprint, jsonify, request, send_from_directory, session

from .core import PUBLIC_DIR, rate_limit

auth_bp = Blueprint("auth", __name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
# The publishable (a.k.a. anon) key — client-safe, used only to talk to
# Supabase Auth. Data access always goes through the service key in storage.py.
PUBLISHABLE_KEY = (os.environ.get("SUPABASE_PUBLISHABLE_KEY")
                   or os.environ.get("SUPABASE_ANON_KEY", "")).strip()


def _auth_ready() -> bool:
    return bool(SUPABASE_URL and PUBLISHABLE_KEY)


def _gotrue(method: str, path: str, json_body=None, bearer: str = None):
    """One call to the Supabase Auth (GoTrue) REST API. Returns (status, body).

    Kept as a single seam so tests can monkeypatch it — the offline suite
    never talks to the network.
    """
    import httpx  # dependency of supabase-py, always present with it
    headers = {"apikey": PUBLISHABLE_KEY}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    resp = httpx.request(method, f"{SUPABASE_URL}/auth/v1{path}",
                         json=json_body, headers=headers, timeout=15)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def _open_session(user: dict):
    session.clear()
    session["couple_id"] = user["id"]
    session["email"] = user.get("email", "")
    session.permanent = True


def _error_message(body: dict) -> str:
    return (body.get("msg") or body.get("message")
            or body.get("error_description") or "Authentication failed.")


# ---------- pages ----------

@auth_bp.get("/login")
def login_page():
    return send_from_directory(str(PUBLIC_DIR), "login.html")


@auth_bp.get("/auth/callback")
def oauth_callback_page():
    """Landing page for the Supabase OAuth redirect (tokens arrive in the URL
    fragment, which only the browser can read)."""
    return send_from_directory(str(PUBLIC_DIR), "auth-callback.html")


# ---------- API ----------

@auth_bp.post("/api/auth/signup")
@rate_limit(max_calls=5, window=300)
def signup():
    if not _auth_ready():
        return jsonify({"error": "Authentication isn't configured on this server."}), 503
    data = request.get_json(force=True, silent=True) or {}
    email = str(data.get("email", "")).strip().lower()[:200]
    password = str(data.get("password", ""))[:200]
    if "@" not in email or len(password) < 8:
        return jsonify({"error": "Enter a valid email and a password of 8+ characters."}), 400

    status, body = _gotrue("POST", "/signup", {"email": email, "password": password})
    if status != 200:
        return jsonify({"error": _error_message(body)}), 400

    # If email confirmation is ON in Supabase, there's no session yet.
    user = body.get("user") or (body if body.get("id") else None)
    if body.get("access_token") and user:
        _open_session(user)
        return jsonify({"ok": True, "couple_id": user["id"], "email": email})
    return jsonify({"ok": True, "confirm_email": True,
                    "message": "Check your inbox to confirm your email, then sign in."})


@auth_bp.post("/api/auth/login")
@rate_limit(max_calls=10, window=300)
def login():
    if not _auth_ready():
        return jsonify({"error": "Authentication isn't configured on this server."}), 503
    data = request.get_json(force=True, silent=True) or {}
    email = str(data.get("email", "")).strip().lower()[:200]
    password = str(data.get("password", ""))[:200]
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    status, body = _gotrue("POST", "/token?grant_type=password",
                           {"email": email, "password": password})
    if status != 200 or not body.get("access_token"):
        return jsonify({"error": _error_message(body) or "Wrong email or password."}), 401
    _open_session(body["user"])
    return jsonify({"ok": True, "couple_id": body["user"]["id"], "email": email})


@auth_bp.post("/api/auth/token")
@rate_limit(max_calls=10, window=300)
def token_login():
    """Google (or any Supabase OAuth) login: the callback page sends the
    access token; we verify it with Supabase before trusting anything."""
    if not _auth_ready():
        return jsonify({"error": "Authentication isn't configured on this server."}), 503
    data = request.get_json(force=True, silent=True) or {}
    token = str(data.get("access_token", ""))[:4000]
    if not token:
        return jsonify({"error": "Missing access token."}), 400

    status, body = _gotrue("GET", "/user", bearer=token)
    if status != 200 or not body.get("id"):
        return jsonify({"error": "That sign-in couldn't be verified."}), 401
    _open_session(body)
    return jsonify({"ok": True, "couple_id": body["id"],
                    "email": body.get("email", "")})


@auth_bp.get("/api/auth/config")
def config():
    """Public, non-secret config the login page needs (OAuth redirect base)."""
    return jsonify({"supabase_url": SUPABASE_URL if _auth_ready() else ""})


@auth_bp.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.get("/api/auth/me")
def me():
    if not session.get("couple_id"):
        return jsonify({"error": "Not signed in."}), 401
    return jsonify({"couple_id": session["couple_id"],
                    "email": session.get("email", "")})
