"""Couple profile from onboarding: names, date, venue, photo, priorities.

The photo is a user-uploaded data URL (the arch mask is CSS on the client);
it's size-capped and type-checked. Saving the profile also syncs the wedding
date into the guests settings and the budget cap into the budget file, so the
rest of the app reads one source of truth.
"""

import copy
import json

from flask import Blueprint, jsonify, request, send_from_directory

from .core import DATA_DIR, PUBLIC_DIR
from .budget import load_budget, save_budget
from .guests import load_guests, save_guests

profile_bp = Blueprint("profile", __name__)

PROFILE_PATH = DATA_DIR / "profile.json"
MAX_PHOTO_CHARS = 2_000_000  # ~1.5 MB of base64 image
PRIORITY_CHOICES = {
    "Food & wine", "Music & party", "Photography", "Flowers & decor",
    "The dress", "Guest experience", "Staying on budget", "Low stress",
}

DEFAULT_PROFILE = {
    "partner_a": "", "partner_b": "", "wedding_date": "", "venue": "",
    "photo": "", "guests_estimate": 0, "budget_estimate": 0,
    "priorities": [], "onboarded": False,
}


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        try:
            data = json.loads(PROFILE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(DEFAULT_PROFILE)
        merged = copy.deepcopy(DEFAULT_PROFILE)
        merged.update({k: data.get(k, v) for k, v in DEFAULT_PROFILE.items()})
        return merged
    return copy.deepcopy(DEFAULT_PROFILE)


def save_profile(profile: dict):
    PROFILE_PATH.parent.mkdir(exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))


@profile_bp.get("/login")
def login_page():
    return send_from_directory(str(PUBLIC_DIR), "login.html")


@profile_bp.get("/onboarding")
def onboarding_page():
    return send_from_directory(str(PUBLIC_DIR), "onboarding.html")


@profile_bp.get("/api/profile")
def get_profile():
    return jsonify(load_profile())


@profile_bp.put("/api/profile")
def update_profile():
    data = request.get_json(force=True, silent=True) or {}
    profile = load_profile()

    for key in ("partner_a", "partner_b"):
        if key in data:
            profile[key] = str(data.get(key) or "").strip()[:60]
    if "wedding_date" in data:
        profile["wedding_date"] = str(data.get("wedding_date") or "")[:20]
    if "venue" in data:
        profile["venue"] = str(data.get("venue") or "").strip()[:100]
    if "photo" in data:
        photo = str(data.get("photo") or "")
        if photo and not photo.startswith("data:image/"):
            return jsonify({"error": "Photo must be an image."}), 400
        if len(photo) > MAX_PHOTO_CHARS:
            return jsonify({"error": "Photo is too large — try a smaller one."}), 400
        profile["photo"] = photo
    if "guests_estimate" in data:
        try:
            profile["guests_estimate"] = max(0, min(2000, int(float(data["guests_estimate"]))))
        except (TypeError, ValueError):
            pass
    if "budget_estimate" in data:
        try:
            profile["budget_estimate"] = max(0.0, float(data["budget_estimate"]))
        except (TypeError, ValueError):
            pass
    if "priorities" in data and isinstance(data["priorities"], list):
        profile["priorities"] = [p for p in data["priorities"] if p in PRIORITY_CHOICES][:3]
    if "onboarded" in data:
        profile["onboarded"] = bool(data["onboarded"])

    save_profile(profile)

    # Keep the app's single sources of truth in sync with onboarding.
    if profile["wedding_date"]:
        guests = load_guests()
        if guests["settings"].get("wedding_date") != profile["wedding_date"]:
            guests["settings"]["wedding_date"] = profile["wedding_date"]
            save_guests(guests)
    if profile["budget_estimate"]:
        budget = load_budget()
        if not budget.get("total_budget"):
            budget["total_budget"] = profile["budget_estimate"]
            save_budget(budget)

    return jsonify(profile)
