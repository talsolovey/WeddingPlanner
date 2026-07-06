"""Document storage for the couple's wedding data.

Every dataset in Vow is one JSON document ("budget", "guests", "seating", ...).
This module is the single place they are read and written; both the web layer
(app/*) and the agent's read_data/write_data tools go through it.

Backend is picked from the environment at first use:

- SUPABASE_URL + SUPABASE_SERVICE_KEY set  ->  Supabase Postgres. Documents
  live in one table (see supabase_schema.sql):
      vow_documents(name text primary key, data jsonb, updated_at timestamptz)
  The service key stays server-side only; RLS is enabled with no public
  policies, so the anon key can't read the couple's data.
- otherwise  ->  local JSON files in DATA_DIR (VOW_DATA_DIR overridable),
  exactly the format the app always used — so dev and the offline test suite
  keep working with no setup, and existing data needs no conversion.

Reads from Supabase are cached for a few seconds (single-process app; the
cache is write-through) to keep pages that read several documents snappy.
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DATA_DIR = Path(os.environ.get("VOW_DATA_DIR", BASE / "data"))
TABLE = os.environ.get("VOW_SUPABASE_TABLE", "vow_documents")
CACHE_TTL_SECONDS = 3

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,60}$")


def _check_name(name: str):
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"Invalid document name: {name!r}")


# ---------- file backend (default) ----------

class FileBackend:
    name = "files"

    def load(self, doc: str):
        path = DATA_DIR / f"{doc}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, doc: str, data):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"{doc}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False))


# ---------- Supabase backend ----------

class SupabaseBackend:
    name = "supabase"

    def __init__(self, url: str, key: str):
        from supabase import create_client  # imported lazily: optional dep
        self.client = create_client(url, key)
        self._cache = {}  # doc -> (data, monotonic timestamp)
        self._lock = threading.Lock()

    def load(self, doc: str):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(doc)
            if hit and now - hit[1] < CACHE_TTL_SECONDS:
                return hit[0]
        result = (self.client.table(TABLE).select("data")
                  .eq("name", doc).limit(1).execute())
        data = result.data[0]["data"] if result.data else None
        with self._lock:
            self._cache[doc] = (data, now)
        return data

    def save(self, doc: str, data):
        self.client.table(TABLE).upsert({
            "name": doc,
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        with self._lock:
            self._cache[doc] = (data, time.monotonic())


_backend = None
_backend_lock = threading.Lock()


def backend():
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                forced = os.environ.get("VOW_STORAGE_BACKEND", "").strip().lower()
                url = os.environ.get("SUPABASE_URL", "").strip()
                key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
                use_supabase = (forced == "supabase"
                                or (forced != "files" and url and key))
                _backend = SupabaseBackend(url, key) if use_supabase else FileBackend()
    return _backend


# ---------- public API ----------

def load(name: str, default=None):
    """Read a document; returns `default` if it doesn't exist (or is corrupt)."""
    _check_name(name)
    data = backend().load(name)
    return default if data is None else data


def save(name: str, data):
    """Write a document (whole-document semantics, like the files always had)."""
    _check_name(name)
    backend().save(name, data)


def exists(name: str) -> bool:
    _check_name(name)
    return backend().load(name) is not None


def backend_name() -> str:
    return backend().name
