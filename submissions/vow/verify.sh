#!/usr/bin/env bash
# Vow — judge verification. Offline: no API keys, no network calls to models.
# Runs the full test suite (185 tests) and validates the eval harness.
set -e
cd "$(dirname "$0")/../../vow-app"

echo "== installing deps =="
pip install -q -r requirements.txt pytest

echo "== test suite (offline, external services faked) =="
python -m pytest tests/ -q

echo "== eval harness dry-run (validates planted-trap cases) =="
python -m evals.run_evals --dry-run

echo "== all green =="
