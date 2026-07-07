"""Tests for the document storage layer — all offline.

The Supabase backend is exercised against a fake client (no network): load
miss/hit, upsert payload shape (incl. couple_id), and the write-through read
cache. The file backend, couple scoping and name validation are tested
directly.
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

LEGACY = storage.LEGACY_COUPLE_ID


class TestNameValidation(unittest.TestCase):
    def test_rejects_path_tricks_and_junk(self):
        for bad in ("../etc/passwd", "a/b", "", "UPPER", "name.json", "a" * 61):
            with self.assertRaises(ValueError):
                storage.load(bad)
        storage.save("ok_name-1", {"x": 1})
        self.assertEqual(storage.load("ok_name-1"), {"x": 1})

    def test_rejects_bad_couple_ids(self):
        for bad in ("../up", "a b", "", "x" * 65):
            with self.assertRaises(ValueError):
                storage.set_couple(bad)


class TestFileBackend(unittest.TestCase):
    def test_roundtrip_default_and_exists(self):
        backend = storage.FileBackend()
        self.assertIsNone(backend.load(LEGACY, "nope"))
        backend.save(LEGACY, "roundtrip", {"a": [1, 2]})
        self.assertEqual(backend.load(LEGACY, "roundtrip"), {"a": [1, 2]})
        # corrupt file degrades to "missing", not an exception
        (storage.DATA_DIR / "broken.json").write_text("{not json")
        self.assertIsNone(backend.load(LEGACY, "broken"))

    def test_legacy_couple_keeps_flat_layout(self):
        backend = storage.FileBackend()
        backend.save(LEGACY, "flatdoc", {"v": 1})
        self.assertTrue((storage.DATA_DIR / "flatdoc.json").exists())

    def test_other_couples_get_their_own_tree(self):
        backend = storage.FileBackend()
        backend.save("couple-9", "budget", {"v": 9})
        self.assertTrue(
            (storage.DATA_DIR / "couples" / "couple-9" / "budget.json").exists())
        # and it never bleeds into the legacy documents
        self.assertNotEqual(backend.load(LEGACY, "budget"), {"v": 9})


class _FakeQuery:
    """Mimics the postgrest fluent chain for select/upsert."""

    def __init__(self, store, calls):
        self.store, self.calls = store, calls
        self._filters = {}
        self._payload = None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _):
        return self

    def upsert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._payload is not None:
            self.calls.append(("upsert", self._payload))
            key = (self._payload["couple_id"], self._payload["name"])
            self.store[key] = self._payload["data"]
            return type("R", (), {"data": [self._payload]})()
        key = (self._filters.get("couple_id"), self._filters.get("name"))
        self.calls.append(("select", key))
        row = self.store.get(key)
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
        self.assertIsNone(backend.load(LEGACY, "budget"))
        backend.save(LEGACY, "budget", {"total": 5})
        self.assertEqual(backend.load(LEGACY, "budget"), {"total": 5})
        kinds = [k for k, _ in backend.client.calls]
        self.assertEqual(kinds[0], "select")
        self.assertEqual(kinds[1], "upsert")

    def test_upsert_payload_shape(self):
        backend = self._backend()
        backend.save("couple-1", "guests", {"households": []})
        kind, payload = backend.client.calls[-1]
        self.assertEqual(kind, "upsert")
        self.assertEqual(payload["couple_id"], "couple-1")
        self.assertEqual(payload["name"], "guests")
        self.assertEqual(payload["data"], {"households": []})
        self.assertIn("updated_at", payload)

    def test_cache_is_write_through(self):
        backend = self._backend()
        backend.save(LEGACY, "seating", {"tables": [1]})
        # A fresh read within the TTL is served from cache — no select call.
        before = len(backend.client.calls)
        self.assertEqual(backend.load(LEGACY, "seating"), {"tables": [1]})
        self.assertEqual(len(backend.client.calls), before)

    def test_cache_keyed_per_couple(self):
        backend = self._backend()
        backend.save("couple-a", "budget", {"total": 1})
        backend.save("couple-b", "budget", {"total": 2})
        self.assertEqual(backend.load("couple-a", "budget"), {"total": 1})
        self.assertEqual(backend.load("couple-b", "budget"), {"total": 2})


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
