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
from .whatsapp import whatsapp_bp
from .timeline import timeline_bp
from .checklist import checklist_bp
from .notices import notices_bp

# Paths anyone may hit without a session. "/" is public because it serves the
# marketing landing page to signed-out visitors (the dashboard to signed-in).
PUBLIC_PATHS = {"/", "/login", "/auth/callback", "/favicon.ico"}
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

    # Error visibility: fetch() clients expect JSON, never Flask's HTML error
    # page — and every unhandled exception must land in the logs, not vanish.
    @app.errorhandler(Exception)
    def handle_any_error(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"error": e.description}), e.code
            return e  # normal page 404s etc. keep their default pages
        app.logger.exception("unhandled error on %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Something went wrong on our side — "
                                     "it's been logged."}), 500
        return "Something went wrong on our side — it's been logged.", 500

    @app.get("/")
    def home_page():
        # Signed-in couples land on their dashboard; visitors get the pitch.
        if session.get("couple_id"):
            return send_from_directory(str(PUBLIC_DIR), "home.html")
        return send_from_directory(str(PUBLIC_DIR), "landing.html")

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
    app.register_blueprint(whatsapp_bp)
    app.register_blueprint(timeline_bp)
    app.register_blueprint(checklist_bp)
    app.register_blueprint(notices_bp)

    # Event-driven wake-ups: every data write is observed by agent.triggers,
    # which debounces bursts and may leave a notice or (capped) refresh the
    # brief. Registration is idempotent, so repeated create_app() is safe.
    from agent.triggers import record_change
    storage.register_save_hook(record_change)
    return app
