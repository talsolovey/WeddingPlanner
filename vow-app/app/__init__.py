"""Application factory: builds the Flask app and registers each feature.

Each feature lives in its own module (contracts, budget, guests, overview) as a
Flask blueprint, so routes and their data helpers sit together. Shared plumbing
(paths, background jobs, JSON parsing) lives in core.py."""

from flask import Flask, send_from_directory

from .core import PUBLIC_DIR, core_bp
from .contracts import contracts_bp
from .budget import budget_bp
from .guests import guests_bp
from .rsvp import rsvp_bp
from .seating import seating_bp
from .weekly_brief import weekly_brief_bp
from .overview import overview_bp


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path="")

    @app.get("/")
    def home_page():
        return send_from_directory(str(PUBLIC_DIR), "home.html")

    app.register_blueprint(core_bp)
    app.register_blueprint(contracts_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(guests_bp)
    app.register_blueprint(rsvp_bp)
    app.register_blueprint(seating_bp)
    app.register_blueprint(weekly_brief_bp)
    app.register_blueprint(overview_bp)
    return app
