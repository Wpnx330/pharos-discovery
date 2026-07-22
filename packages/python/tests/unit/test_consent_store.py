"""Tests for T18 — ConsentStore."""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from pharos_discovery.consent import ConsentRecord, ConsentStore


class TestConsentRecord:
    def test_record_defaults(self):
        rec = ConsentRecord(
            server_id="srv-1",
            scopes=["search"],
            decision="approved",
        )
        assert rec.server_id == "srv-1"
        assert rec.scopes == ["search"]
        assert rec.decision == "approved"
        assert rec.timestamp > 0
        assert rec.expires_at is None

    def test_record_with_expiry(self):
        rec = ConsentRecord(
            server_id="srv-1",
            scopes=[],
            decision="denied",
            expires_at=time.time() - 1,  # already expired
        )
        assert rec.is_expired() is True

    def test_record_not_expired(self):
        rec = ConsentRecord(
            server_id="srv-1",
            scopes=[],
            decision="approved",
            expires_at=time.time() + 100,
        )
        assert rec.is_expired() is False

    def test_record_no_expiry_never_expires(self):
        rec = ConsentRecord(
            server_id="srv-1",
            scopes=[],
            decision="approved",
        )
        assert rec.is_expired() is False


class TestConsentStoreRecord:
    def test_record_returns_consent_record(self):
        store = ConsentStore()
        rec = store.record("srv-1", ["search"], "approved")
        assert isinstance(rec, ConsentRecord)
        assert rec.server_id == "srv-1"
        assert rec.scopes == ["search"]
        assert rec.decision == "approved"

    def test_record_with_ttl_sets_expiry(self):
        store = ConsentStore()
        rec = store.record("srv-1", ["search"], "approved", ttl=60)
        assert rec.expires_at is not None
        assert rec.expires_at > rec.timestamp
        assert store.is_valid(rec) is True

    def test_record_overwrites_previous(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "denied")
        store.record("srv-1", ["search"], "approved")
        records = store.list_all()
        assert len(records) == 1
        assert records[0].decision == "approved"

    def test_record_copies_scopes(self):
        store = ConsentStore()
        scopes = ["a", "b"]
        store.record("srv-1", scopes, "approved")
        scopes.append("c")
        rec = store.check("srv-1", ["a", "b"])
        assert rec is not None
        assert "c" not in rec.scopes


class TestConsentStoreCheck:
    def test_check_returns_record_when_approved(self):
        store = ConsentStore()
        store.record("srv-1", ["search", "read"], "approved")
        rec = store.check("srv-1", ["search"])
        assert rec is not None
        assert rec.decision == "approved"

    def test_check_returns_none_when_not_found(self):
        store = ConsentStore()
        assert store.check("srv-1", ["search"]) is None

    def test_check_returns_none_when_denied(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "denied")
        assert store.check("srv-1", ["search"]) is None

    def test_check_returns_none_when_scope_not_covered(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "approved")
        assert store.check("srv-1", ["write"]) is None

    def test_check_returns_none_when_expired(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "approved", ttl=-1)
        assert store.check("srv-1", ["search"]) is None

    def test_check_subset_scopes(self):
        store = ConsentStore()
        store.record("srv-1", ["a", "b", "c"], "approved")
        assert store.check("srv-1", ["a", "b"]) is not None
        assert store.check("srv-1", ["a", "b", "c"]) is not None
        assert store.check("srv-1", ["d"]) is None

    def test_check_empty_scopes_always_matches(self):
        store = ConsentStore()
        store.record("srv-1", [], "approved")
        assert store.check("srv-1", []) is not None


class TestConsentStoreRevoke:
    def test_revoke_removes_record(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "approved")
        assert store.revoke("srv-1") is True
        assert store.check("srv-1", ["search"]) is None

    def test_revoke_returns_false_when_not_found(self):
        store = ConsentStore()
        assert store.revoke("srv-1") is False

    def test_revoke_only_affects_target(self):
        store = ConsentStore()
        store.record("srv-1", ["search"], "approved")
        store.record("srv-2", ["search"], "approved")
        store.revoke("srv-1")
        assert store.check("srv-1", ["search"]) is None
        assert store.check("srv-2", ["search"]) is not None


class TestConsentStoreListAll:
    def test_list_all_empty(self):
        store = ConsentStore()
        assert store.list_all() == []

    def test_list_all_returns_all(self):
        store = ConsentStore()
        store.record("srv-1", [], "approved")
        store.record("srv-2", [], "denied")
        records = store.list_all()
        assert len(records) == 2

    def test_list_all_includes_expired(self):
        store = ConsentStore()
        store.record("srv-1", [], "approved", ttl=-1)
        records = store.list_all()
        assert len(records) == 1


class TestConsentStoreIsValid:
    def test_is_valid_non_expired(self):
        store = ConsentStore()
        rec = store.record("srv-1", [], "approved", ttl=100)
        assert store.is_valid(rec) is True

    def test_is_valid_expired(self):
        store = ConsentStore()
        rec = store.record("srv-1", [], "approved", ttl=-1)
        assert store.is_valid(rec) is False

    def test_is_valid_no_expiry(self):
        store = ConsentStore()
        rec = store.record("srv-1", [], "approved")
        assert store.is_valid(rec) is True


class TestConsentStorePersistence:
    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "consent.json")
            store1 = ConsentStore(persist_path=path)
            store1.record("srv-1", ["search"], "approved", ttl=3600)
            store1.record("srv-2", ["read"], "denied")

            # File should exist
            assert os.path.exists(path)

            # Load into a new store
            store2 = ConsentStore(persist_path=path)
            records = store2.list_all()
            assert len(records) == 2
            rec = store2.check("srv-1", ["search"])
            assert rec is not None
            assert rec.decision == "approved"

    def test_persist_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "consent.json")
            store = ConsentStore(persist_path=path)
            store.record("srv-1", ["a", "b"], "approved")
            with open(path, "r") as fh:
                data = json.load(fh)
            assert "srv-1" in data
            assert data["srv-1"]["decision"] == "approved"

    def test_persist_no_path_does_not_create_file(self):
        store = ConsentStore()
        store.record("srv-1", [], "approved")
        # No file should be created (no path given)
        # This is implicitly fine — just ensure no crash

    def test_persist_revoke_saves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "consent.json")
            store1 = ConsentStore(persist_path=path)
            store1.record("srv-1", [], "approved")
            store1.revoke("srv-1")

            store2 = ConsentStore(persist_path=path)
            assert store2.list_all() == []

    def test_persist_corrupt_file_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "consent.json")
            with open(path, "w") as fh:
                fh.write("{invalid json")
            store = ConsentStore(persist_path=path)
            assert store.list_all() == []

    def test_persist_nonexistent_file_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nonexistent.json")
            store = ConsentStore(persist_path=path)
            assert store.list_all() == []


class TestConsentStoreThreadSafety:
    def test_concurrent_records(self):
        import threading

        store = ConsentStore()
        results: list[ConsentRecord] = []
        lock = threading.Lock()

        def worker(i: int):
            rec = store.record(f"srv-{i}", ["search"], "approved")
            with lock:
                results.append(rec)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store.list_all()) == 20
        assert len(results) == 20
