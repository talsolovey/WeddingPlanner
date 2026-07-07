"""Shared test helper: open a signed-in session on a Flask test client.

The auth gate (app/__init__.py) requires session["couple_id"] on every
non-public route. Tests sign in as the legacy couple by default so the
seeded flat DATA_DIR files keep being read, exactly as before auth existed.
"""

import storage

# The offline suite must never talk to real providers, even when the
# developer's vow-app/.env holds live credentials (load_dotenv pulls them
# into the process). Neutralize the WhatsApp provider for every test run;
# provider tests re-set these module attributes to fakes explicitly.
import app.whatsapp as _wa

_wa.ACCOUNT_SID = _wa.AUTH_TOKEN = ""
_wa.META_ACCESS_TOKEN = _wa.META_PHONE_NUMBER_ID = ""
_wa.SEND_INTERVAL = 0

COUPLE = storage.LEGACY_COUPLE_ID


def login(client, couple: str = COUPLE, email: str = "test@vow.app"):
    with client.session_transaction() as s:
        s["couple_id"] = couple
        s["email"] = email
    return client
