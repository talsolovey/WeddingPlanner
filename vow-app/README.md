# Vow

An AI wedding-planning agent. A Flask web app with a set of planning features
(budget, guests, contracts, weekly brief, overview) backed by an LLM agent
harness.

## Requirements

- Python 3.10+
- An OpenAI API key

## Run it locally

```bash
cd vow-app
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py
```

Then open <http://localhost:5050>.

### Configuration

The app reads environment variables from `vow-app/.env` (via `python-dotenv`).
This file is gitignored and never pushed. Create it with at least your API key:

```bash
OPENAI_API_KEY=sk-...
```

Other optional variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5050` | Port the dev server binds to. |
| `FLASK_DEBUG` | unset | Set to `1` for auto-reload during development. |
| `VOW_MAX_COST_USD` | `0.50` | Per-run spend cap for the agent harness. |
| `VOW_DATA_DIR` | bundled `data/` | Where the app reads/writes its JSON data. |

## Run the tests

```bash
python -m pytest
```

## Production

For deploying to Render (gunicorn, secrets, optional persistent disk), see
[`DEPLOY.md`](DEPLOY.md).
