"""Budget tracker + the agent's realistic final-cost forecast.

The latest forecast is cached to disk (data/forecast.json) so the budget page
can show the forecast card instantly without an agent run on every visit."""

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request, send_from_directory

import storage
from agent.harness import AgentHarness
from .core import PUBLIC_DIR, parse_agent_json, rate_limit, run_job

budget_bp = Blueprint("budget", __name__)


def load_budget():
    return storage.load("budget", {"currency": "USD", "total_budget": 0, "items": []})


def save_budget(budget):
    storage.save("budget", budget)


@budget_bp.get("/budget")
def budget_page():
    return send_from_directory(str(PUBLIC_DIR), "budget.html")


@budget_bp.get("/api/budget")
def get_budget():
    return jsonify(load_budget())


@budget_bp.put("/api/budget/settings")
def update_budget_settings():
    data = request.get_json(force=True)
    budget = load_budget()
    try:
        budget["total_budget"] = max(0.0, float(data.get("total_budget", 0)))
    except (TypeError, ValueError):
        return jsonify({"error": "total_budget must be a number."}), 400
    budget["currency"] = str(data.get("currency", budget.get("currency", "USD")))[:3].upper()
    save_budget(budget)
    return jsonify(budget)


@budget_bp.post("/api/budget/items")
def add_budget_item():
    data = request.get_json(force=True)
    category = str(data.get("category", "")).strip()[:60]
    if not category:
        return jsonify({"error": "Category is required."}), 400

    def num(k):  # guardrail: numbers only, no negatives
        try:
            return max(0.0, float(data.get(k) or 0))
        except (TypeError, ValueError):
            return 0.0

    item = {
        "id": uuid.uuid4().hex[:8],
        "category": category,
        "vendor": str(data.get("vendor", "")).strip()[:100],
        "estimated": num("estimated"),
        "contracted": num("contracted"),
        "paid": num("paid"),
        "due_before_wedding": bool(data.get("due_before_wedding", True)),
        "notes": str(data.get("notes", "")).strip()[:300],
    }
    budget = load_budget()
    budget["items"].append(item)
    save_budget(budget)
    return jsonify(item)


@budget_bp.delete("/api/budget/items/<item_id>")
def delete_budget_item(item_id):
    budget = load_budget()
    before = len(budget["items"])
    budget["items"] = [i for i in budget["items"] if i["id"] != item_id]
    save_budget(budget)
    return jsonify({"ok": True, "removed": before - len(budget["items"])})


@budget_bp.get("/api/budget/forecast/latest")
def latest_forecast():
    cached = storage.load("forecast")
    if cached is None:
        return jsonify({"exists": False})
    return jsonify(dict(cached, exists=True))


@budget_bp.post("/api/budget/analyze")
@rate_limit()
def analyze_budget():
    budget = load_budget()
    if not budget["items"]:
        return jsonify({"error": "Add some budget items first."}), 400

    def task(on_event):
        # No data in the prompt: the agent must fetch it itself via read_data.
        harness = AgentHarness(verbose=False, on_event=on_event)
        answer = harness.run(
            "The couple wants their wedding budget reviewed: forecast the realistic final "
            "cost and flag risks. Read the budget data and any analyzed contracts. "
            "Respond with ONLY the JSON object defined in the budget-forecaster skill "
            "— no prose, no markdown, no headings."
        )
        result = {"analysis": parse_agent_json(answer),
                  "cost_usd": round(harness.last_run_cost, 4)}
        storage.save("forecast", dict(
            result, generated_at=datetime.now().isoformat(timespec="seconds")))
        return result

    return jsonify({"job_id": run_job(task)})
