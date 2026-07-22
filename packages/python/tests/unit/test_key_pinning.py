"""Tests for pharos_discovery.security.key_pinning."""

import hashlib
import json
import threading

import pytest

from pharos_discovery.errors import SignatureVerificationFailed
from pharos_discovery.security.key_pinning import KeyPinStore


class TestHashing:
    def test_hash_public_key_matches_sha256(self):
        key = "my-public-key"
        expected = hashlib.sha256(key.encode("utf-8")).hexdigest()
        assert KeyPinStore.hash_public_key(key) == expected

    def test_hash_is_hex_string(self):
        h = KeyPinStore.hash_public_key("abc")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_keys_different_hashes(self):
        assert KeyPinStore.hash_public_key("a") != KeyPinStore.hash_public_key("b")


class TestPinning:
    def test_pin_and_is_pinned(self):
        store = KeyPinStore()
        store.pin("server-1", "deadbeef")
        assert store.is_pinned("server-1") is True

    def test_unpin(self):
        store = KeyPinStore()
        store.pin("server-1", "deadbeef")
        store.unpin("server-1")
        assert store.is_pinned("server-1") is False

    def test_unpin_nonexistent_is_noop(self):
        store = KeyPinStore()
        store.unpin("nope")  # should not raise
        assert len(store) == 0

    def test_pin_overwrites(self):
        store = KeyPinStore()
        store.pin("server-1", "hash-a")
        store.pin("server-1", "hash-b")
        # verify against the newer hash
        key = "key-for-hash-b"
        store.pin("server-1", KeyPinStore.hash_public_key(key))
        assert store.verify("server-1", key) is True

    def test_contains(self):
        store = KeyPinStore()
        store.pin("s1", "h")
        assert "s1" in store
        assert "s2" not in store

    def test_len(self):
        store = KeyPinStore()
        store.pin("a", "1")
        store.pin("b", "2")
        assert len(store) == 2


class TestVerify:
    def test_verify_matching_key(self):
        store = KeyPinStore()
        key = "public-key-123"
        h = KeyPinStore.hash_public_key(key)
        store.pin("server-1", h)
        assert store.verify("server-1", key) is True

    def test_verify_mismatch_raises(self):
        store = KeyPinStore()
        store.pin("server-1", KeyPinStore.hash_public_key("real-key"))
        with pytest.raises(SignatureVerificationFailed):
            store.verify("server-1", "different-key")

    def test_verify_unpinned_returns_true(self):
        store = KeyPinStore()
        # No pin set — verification should pass (no constraint).
        assert store.verify("server-1", "any-key") is True

    def test_verify_exception_has_detail(self):
        store = KeyPinStore()
        store.pin("server-1", "0" * 64)
        with pytest.raises(SignatureVerificationFailed) as exc_info:
            store.verify("server-1", "wrong")
        assert "server-1" in str(exc_info.value.detail)

    def test_verify_multiple_servers_independent(self):
        store = KeyPinStore()
        k1, k2 = "key-one", "key-two"
        store.pin("s1", KeyPinStore.hash_public_key(k1))
        store.pin("s2", KeyPinStore.hash_public_key(k2))
        assert store.verify("s1", k1) is True
        assert store.verify("s2", k2) is True
        with pytest.raises(SignatureVerificationFailed):
            store.verify("s1", k2)


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "pins.json")
        store = KeyPinStore()
        store.pin("s1", "hash1")
        store.pin("s2", "hash2")
        store.save(path)

        loaded = KeyPinStore.load(path)
        assert loaded.is_pinned("s1")
        assert loaded.is_pinned("s2")
        # Internal pins restored (hash values match)
        with loaded._lock:
            assert loaded._pins["s1"] == "hash1"

    def test_load_missing_returns_empty(self, tmp_path):
        loaded = KeyPinStore.load(str(tmp_path / "nope.json"))
        assert len(loaded) == 0

    def test_save_writes_valid_json(self, tmp_path):
        path = str(tmp_path / "pins.json")
        store = KeyPinStore()
        store.pin("s1", "h1")
        store.save(path)
        with open(path) as f:
            data = json.load(f)
        assert data == {"s1": "h1"}

    def test_persisted_pin_still_verifies(self, tmp_path):
        path = str(tmp_path / "pins.json")
        store = KeyPinStore()
        key = "the-real-key"
        store.pin("s1", KeyPinStore.hash_public_key(key))
        store.save(path)
        loaded = KeyPinStore.load(path)
        assert loaded.verify("s1", key) is True
        with pytest.raises(SignatureVerificationFailed):
            loaded.verify("s1", "wrong-key")


class TestThreadSafety:
    def test_concurrent_pins(self):
        store = KeyPinStore()
        threads = []
        for i in range(20):

            def pin(idx=i):
                store.pin(f"s-{idx}", f"hash-{idx}")

            t = threading.Thread(target=pin)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        assert len(store) == 20
