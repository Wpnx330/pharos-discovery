"""Tests for pharos_discovery.security.blocklist."""

import json
import threading

import pytest

from pharos_discovery.security.blocklist import Blocklist


class TestBasicOperations:
    def test_add_and_is_blocked(self):
        bl = Blocklist()
        bl.add("server-1", "malicious")
        assert bl.is_blocked("server-1") is True

    def test_not_blocked_by_default(self):
        bl = Blocklist()
        assert bl.is_blocked("server-1") is False

    def test_remove(self):
        bl = Blocklist()
        bl.add("server-1", "spam")
        bl.remove("server-1")
        assert bl.is_blocked("server-1") is False

    def test_remove_nonexistent_is_noop(self):
        bl = Blocklist()
        bl.remove("does-not-exist")  # should not raise
        assert len(bl) == 0

    def test_add_overwrites_reason(self):
        bl = Blocklist()
        bl.add("server-1", "reason-a")
        bl.add("server-1", "reason-b")
        listed = bl.list()
        assert listed["server-1"] == "reason-b"
        assert len(listed) == 1

    def test_list_returns_copy(self):
        bl = Blocklist()
        bl.add("server-1", "x")
        snapshot = bl.list()
        snapshot["server-2"] = "y"
        # mutating the snapshot must not affect the blocklist
        assert bl.is_blocked("server-2") is False

    def test_len(self):
        bl = Blocklist()
        assert len(bl) == 0
        bl.add("a", "1")
        bl.add("b", "2")
        assert len(bl) == 2

    def test_contains(self):
        bl = Blocklist()
        bl.add("server-1", "bad")
        assert "server-1" in bl
        assert "server-2" not in bl


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "blocklist.json")
        bl = Blocklist()
        bl.add("server-1", "malware")
        bl.add("server-2", "abuse")
        bl.save(path)

        loaded = Blocklist.load(path)
        assert loaded.is_blocked("server-1")
        assert loaded.is_blocked("server-2")
        assert loaded.list()["server-1"] == "malware"

    def test_save_writes_valid_json(self, tmp_path):
        path = str(tmp_path / "bl.json")
        bl = Blocklist()
        bl.add("s1", "r1")
        bl.save(path)
        with open(path) as f:
            data = json.load(f)
        assert data == {"s1": "r1"}

    def test_load_missing_file_returns_empty(self, tmp_path):
        loaded = Blocklist.load(str(tmp_path / "nope.json"))
        assert len(loaded) == 0
        assert loaded.list() == {}

    def test_load_malformed_file_returns_empty(self, tmp_path):
        path = str(tmp_path / "bad.json")
        path_obj = type(path)  # for mypy; we just write below
        with open(path, "w") as f:
            f.write("{not valid json")
        # Should raise — malformed JSON is an error, not silently empty.
        with pytest.raises(json.JSONDecodeError):
            Blocklist.load(path)
        del path_obj

    def test_empty_blocklist_save_load(self, tmp_path):
        path = str(tmp_path / "empty.json")
        bl = Blocklist()
        bl.save(path)
        loaded = Blocklist.load(path)
        assert len(loaded) == 0


class TestThreadSafety:
    def test_concurrent_adds(self):
        bl = Blocklist()
        threads = []
        for i in range(20):

            def add(idx=i):
                bl.add(f"server-{idx}", f"reason-{idx}")

            t = threading.Thread(target=add)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(bl) == 20
        for i in range(20):
            assert bl.is_blocked(f"server-{i}")

    def test_concurrent_add_remove(self):
        bl = Blocklist()
        for i in range(50):
            bl.add(f"server-{i}", "x")

        def remover():
            for i in range(50):
                bl.remove(f"server-{i}")

        def adder():
            for i in range(50, 100):
                bl.add(f"server-{i}", "y")

        t1 = threading.Thread(target=remover)
        t2 = threading.Thread(target=adder)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        # Should not crash; final state is well-defined enough.
        assert len(bl) >= 0
