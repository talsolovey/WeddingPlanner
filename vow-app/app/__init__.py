"""Application factory: builds the Flask app and registers each feature.

Each feature lives in its own module (contracts, budget, guests, overview) as a
Flask blueprint, so routes and their data helpers sit together. Shared plumbing
(paths, background jobs, JSON parsing) lives in core.py.

Auth gate: every route requires a signed-in couple (Flask session opened by
app/auth.py) except the public surface: the login/OAuth pages, the auth API,
the guest RSVP flow, and static assets. Each authenticated request pins the
storage layer to that couple's documents."""

import os
import secrets
from datetime import timedelta

from flask import Flask, jsonify, redirect, request, send_from_directory, session

import storage
from .core import PUBLIC_DIR, core_bp
from .auth import auth_bp
from .contracts import contracts_bp
from .budget import budget_bp
from .guests import guests_bp
from .rsvp import rsvp_bp
from .seating import seating_bp
from .weekly_brief import weekly_brief_bp
from .overview import overview_bp
from .profile import profile_bp
from .chat import chat_bp
from .invitations import invitations_bp
from .timeline import timeline_bp
from .checklist import checklist_bp

# Paths anyone may hit without a session.
PUBLIC_PATHS = {"/login", "/auth/callback", "/favicon.ico"}
PUBLIC_PREFIXES = ("/api/auth/", "/rsvp/", "/api/rsvp/")
# Static assets the public pages (login, RSVP form) need.
ASSET_SUFFIXES = (".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp",
                  ".ico", ".woff", ".woff2", ".map")


def _is_public(path: str) -> bool:
    return (path in PUBLIC_PATHS
            or path.startswith(PUBLIC_PREFIXES)
            or path.lower().endswith(ASSET_SUFFIXES))


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path="")

    # Sessions: signed HttpOnly cookie. Set VOW_SECRET_KEY in production so
    # sessions survive restarts; without it a random key is used (dev/tests).
    app.secret_key = os.environ.get("VOW_SECRET_KEY") or secrets.token_hex(32)
    app.permanent_session_lifetime = timedelta(days=30)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    @app.before_request
    def auth_gate():
        if _is_public(request.path) or request.method == "OPTIONS":
            return None
        couple = session.get("couple_id")
        if not couple:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in."}), 401
            return redirect("/login")
        try:
            storage.set_couple(couple)
        except ValueError:
            session.clear()
            return redirect("/login")
        return None

    @app.teardown_request
    def reset_couple(exc=None):
        storage.set_couple(None)

    @app.get("/")
    def home_page():
        return send_from_directory(str(PUBLIC_DIR), "home.html")

    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(guests_bp)
    app.register_blueprint(rsvp_bp)
    app.register_blueprint(seating_bp)
    app.register_blueprint(weekly_brief_bp)
    app.register_blueprint(overview_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(invitations_bp)
    app.register_blueprint(timeline_bp)
    app.register_blueprint(checklist_bp)
    return app
