"""Entry point. The app is assembled from per-feature blueprints in app/.

Local dev:   python server.py
Production:  gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT server:app
             (-w 1 keeps the in-memory job tracker in a single process)"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
