"""Loads dev-only seed data from data/samples/.

Sample data is kept out of the code on purpose: to ship to production you just
delete the data/samples/ folder, and load_sample() returns None so the
load-sample endpoints degrade gracefully."""

import json

from .core import DATA_DIR

SAMPLES_DIR = DATA_DIR / "samples"


def load_sample(name: str):
    """Return the parsed sample for 'budget' or 'guests', or None if the
    samples folder/file has been removed (e.g. in production)."""
    path = SAMPLES_DIR / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
