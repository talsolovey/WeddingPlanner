"""Shared test helper: open a signed-in session on a Flask test client.

The auth gate (app/__init__.py) requires session["couple_id"] on every
non-public route. Tests sign in as the legacy couple by default so the
seeded flat DATA_DIR files keep being read, exactly as before auth existed.
"""

import storage

COUPLE = storage.LEGACY_COUPLE_ID


def login(client, couple: str = COUPLE, email: str = "test@vow.app"):
    with client.session_transaction() as s:
        s["couple_id"] = couple
        s["email"] = email
    return client
