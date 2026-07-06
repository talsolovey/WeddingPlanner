"""Tests for the document storage layer — all offline.

The Supabase backend is exercised against a fake client (no network): load
miss/hit, upsert payload shape, and the write-through read cache. The file
backend and name validation are tested directly.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

VOW_APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VOW_APP))
os.environ.setdefault("VOW_DATA_DIR", tempfile.mkdtemp(prefix="vow-test-data-"))
os.environ.setdefault("VOW_STORAGE_BACKEND", "files")  # tests never touch Supabase

import storage  # noqa: E402


class TestNameValidation(unittest.TestCase):
    def test_rejects_path_tricks_and_junk(self):
        for bad in ("../etc/passwd", "a/b", "", "UPPER", "name.json", "a" * 61):
            with self.assertRaises(ValueError):
                storage.load(bad)
        storage.save("ok_name-1", {"x": 1})
        self.assertEqual(storage.load("ok_name-1"), {"x": 1})


class TestFileBackend(unittest.TestCase):
    def test_roundtrip_default_and_exists(self):
        backend = storage.FileBackend()
        self.assertIsNone(backend.load("nope"))
        backend.save("roundtrip", {"a": [1, 2]})
        self.assertEqual(backend.load("roundtrip"), {"a": [1, 2]})
        # corrupt file degrades to "missing", not an exception
        (storage.DATA_DIR / "broken.json").write_text("{not json")
        self.assertIsNone(backend.load("broken"))


class _FakeQuery:
    """Mimics the postgrest fluent chain for select/upsert."""

    def __init__(self, store, calls):
        self.store, self.calls = store, calls
        self._name = None
        self._payload = None

    def select(self, *_):
        return self

    def eq(self, _, name):
        self._name = name
        return self

    def limit(self, _):
        return self

    def upsert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._payload is not None:
            self.calls.append(("upsert", self._payload))
            self.store[self._payload["name"]] = self._payload["data"]
            return type("R", (), {"data": [self._payload]})()
        self.calls.append(("select", self._name))
        row = self.store.get(self._name)
        return type("R", (), {"data": [{"data": row}] if row is not None else []})()


class _FakeClient:
    def __init__(self):
        self.store, self.calls = {}, []

    def table(self, _):
        return _FakeQuery(self.store, self.calls)


class TestSupabaseBackend(unittest.TestCase):
    def _backend(self):
        backend = storage.SupabaseBackend.__new__(storage.SupabaseBackend)
        backend.client = _FakeClient()
        backend._cache = {}
        import threading
        backend._lock = threading.Lock()
        return backend

    def test_load_miss_then_save_then_hit(self):
        backend = self._backend()
        self.assertIsNone(backend.load("budget"))
        backend.save("budget", {"total": 5})
        self.assertEqual(backend.load("budget"), {"total": 5})
        kinds = [k for k, _ in backend.client.calls]
        self.assertEqual(kinds[0], "select")
        self.assertEqual(kinds[1], "upsert")

    def test_upsert_payload_shape(self):
        backend = self._backend()
        backend.save("guests", {"households": []})
        kind, payload = backend.client.calls[-1]
        self.assertEqual(kind, "upsert")
        self.assertEqual(payload["name"], "guests")
        self.assertEqual(payload["data"], {"households": []})
        self.assertIn("updated_at", payload)

    def test_cache_is_write_through(self):
        backend = self._backend()
        backend.save("seating", {"tables": [1]})
        # A fresh read within the TTL is served from cache — no select call.
        before = len(backend.client.calls)
        self.assertEqual(backend.load("seating"), {"tables": [1]})
        self.assertEqual(len(backend.client.calls), before)


class TestBackendSelection(unittest.TestCase):
    def test_forced_files_wins_over_credentials(self):
        old = storage._backend
        try:
            storage._backend = None
            os.environ["SUPABASE_URL"] = "https://example.supabase.co"
            os.environ["SUPABASE_SERVICE_KEY"] = "key"
            os.environ["VOW_STORAGE_BACKEND"] = "files"
            self.assertEqual(storage.backend_name(), "files")
        finally:
            storage._backend = old
            os.environ.pop("SUPABASE_URL", None)
            os.environ.pop("SUPABASE_SERVICE_KEY", None)
            os.environ["VOW_STORAGE_BACKEND"] = "files"


if __name__ == "__main__":
    unittest.main()
