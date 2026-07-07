"""Document storage for each couple's wedding data.

Every dataset in Vow is one JSON document ("budget", "guests", "seating", ...).
This module is the single place they are read and written; both the web layer
(app/*) and the agent's read_data/write_data tools go through it.

Multi-couple: every document belongs to a couple (the Supabase Auth user id).
The current couple is carried in a context variable — the web layer sets it
per request from the session, background jobs inherit it explicitly, and
headless runs (agent CLI, MCP server) set it via the VOW_COUPLE_ID env var.
When no couple is set, the legacy id "default" is used, which is where the
pre-auth data was migrated — so old tests, dev flows and the autonomous kit
keep working unchanged.

Backend is picked from the environment at first use:

- SUPABASE_URL + SUPABASE_SERVICE_KEY set  ->  Supabase Postgres. Documents
  live in one table (see supabase_schema.sql):
      vow_documents(couple_id text, name text, data jsonb, updated_at
      timestamptz, primary key (couple_id, name))
  The service key stays server-side only; RLS is enabled with no public
  policies, so the anon/publishable key can't read any couple's data.
- otherwise  ->  local JSON files. The legacy couple keeps the original flat
  DATA_DIR/*.json layout; other couples live in DATA_DIR/couples/<id>/*.json.

Reads from Supabase are cached for a few seconds (single-process app; the
cache is write-through) to keep pages that read several documents snappy.
"""

import contextvars
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

# The couple id every pre-auth document was migrated under.
LEGACY_COUPLE_ID = "default"

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,60}$")
_COUPLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_current_couple: contextvars.ContextVar = contextvars.ContextVar(
    "vow_current_couple", default=None)


def _check_name(name: str):
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"Invalid document name: {name!r}")


def _check_couple(couple_id: str):
    if not _COUPLE_RE.match(couple_id or ""):
        raise ValueError(f"Invalid couple id: {couple_id!r}")


# ---------- couple context ----------

def set_couple(couple_id):
    """Set the couple whose documents load()/save() touch (None = legacy)."""
    if couple_id is not None:
        _check_couple(couple_id)
    _current_couple.set(couple_id)


def current_couple() -> str:
    """The effective couple id: request context, else VOW_COUPLE_ID, else legacy."""
    ctx = _current_couple.get()
    if ctx:
        return ctx
    env = os.environ.get("VOW_COUPLE_ID", "").strip()
    return env if env else LEGACY_COUPLE_ID


# ---------- file backend (default) ----------

class FileBackend:
    name = "files"

    def _dir(self, couple: str) -> Path:
        # Legacy couple keeps the original flat layout (existing data + tests).
        if couple == LEGACY_COUPLE_ID:
            return DATA_DIR
        return DATA_DIR / "couples" / couple

    def load(self, couple: str, doc: str):
        path = self._dir(couple) / f"{doc}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, couple: str, doc: str, data):
        d = self._dir(couple)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{doc}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False))


# ---------- Supabase backend ----------

class SupabaseBackend:
    name = "supabase"

    def __init__(self, url: str, key: str):
        from supabase import create_client  # imported lazily: optional dep
        self.client = create_client(url, key)
        self._cache = {}  # (couple, doc) -> (data, monotonic timestamp)
        self._lock = threading.Lock()

    def load(self, couple: str, doc: str):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get((couple, doc))
            if hit and now - hit[1] < CACHE_TTL_SECONDS:
                return hit[0]
        result = (self.client.table(TABLE).select("data")
                  .eq("couple_id", couple).eq("name", doc).limit(1).execute())
        data = result.data[0]["data"] if result.data else None
        with self._lock:
            self._cache[(couple, doc)] = (data, now)
        return data

    def save(self, couple: str, doc: str, data):
        self.client.table(TABLE).upsert({
            "couple_id": couple,
            "name": doc,
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        with self._lock:
            self._cache[(couple, doc)] = (data, time.monotonic())


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

# Save hooks: observers notified after every successful write (couple, name).
# Used by agent.triggers so Vow can *notice* data changes (event-driven
# wake-ups) without storage knowing anything about the agent. Hooks must
# never break a save — they are isolated behind a bare except.
_ON_SAVE_HOOKS = []


def register_save_hook(fn):
    """Idempotent: registering the same function twice keeps one copy."""
    if fn not in _ON_SAVE_HOOKS:
        _ON_SAVE_HOOKS.append(fn)


def load(name: str, default=None):
    """Read a document for the current couple; `default` if missing/corrupt."""
    _check_name(name)
    data = backend().load(current_couple(), name)
    return default if data is None else data


def save(name: str, data):
    """Write a document for the current couple (whole-document semantics)."""
    _check_name(name)
    backend().save(current_couple(), name, data)
    for hook in list(_ON_SAVE_HOOKS):
        try:
            hook(current_couple(), name)
        except Exception:
            pass  # observers never break writes


# Per-(couple, document) locks for atomic read-modify-write cycles. Plain
# load()+save() is fine for request handlers (short windows, single worker),
# but long-running writers (paced background jobs) must use mutate() so they
# can't clobber concurrent edits with a stale copy of the document.
_DOC_LOCKS = {}
_DOC_LOCKS_GUARD = threading.Lock()


def _doc_lock(couple: str, name: str) -> threading.Lock:
    key = (couple, name)
    with _DOC_LOCKS_GUARD:
        if key not in _DOC_LOCKS:
            _DOC_LOCKS[key] = threading.Lock()
        return _DOC_LOCKS[key]


def mutate(name: str, fn, default=None):
    """Atomically load -> fn(data) -> save for the current couple. fn gets the
    freshest copy and returns the document to store (or None to skip the
    write). Returns whatever was stored (or the loaded data on skip)."""
    _check_name(name)
    with _doc_lock(current_couple(), name):
        data = load(name, default)
        updated = fn(data)
        if updated is not None:
            save(name, updated)
            return updated
        return data


def exists(name: str) -> bool:
    _check_name(name)
    return backend().load(current_couple(), name) is not None


def backend_name() -> str:
    return backend().name
