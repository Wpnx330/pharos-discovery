"""Tests for pharos_discovery.cache module."""

import time

from pharos_discovery.cache import ServerCardCache


class TestPutGet:
    def test_put_get(self):
        cache = ServerCardCache(ttl_seconds=300)
        card = {"id": "server-1", "display_name": "Test Server"}
        cache.put("server-1", card)
        result, etag = cache.get("server-1")
        assert result == card
        assert etag is None

    def test_get_missing(self):
        cache = ServerCardCache()
        result, etag = cache.get("nonexistent")
        assert result is None
        assert etag is None


class TestExpiration:
    def test_expired(self):
        cache = ServerCardCache(ttl_seconds=0)
        cache.put("server-1", {"id": "server-1"})
        # TTL=0 means immediately expired
        time.sleep(0.01)
        result, _ = cache.get("server-1")
        assert result is None

    def test_not_expired(self):
        cache = ServerCardCache(ttl_seconds=300)
        cache.put("server-1", {"id": "server-1"})
        result, _ = cache.get("server-1")
        assert result is not None


class TestInvalidate:
    def test_invalidate(self):
        cache = ServerCardCache()
        cache.put("server-1", {"id": "server-1"})
        assert cache.size == 1
        cache.invalidate("server-1")
        result, _ = cache.get("server-1")
        assert result is None
        assert cache.size == 0

    def test_invalidate_missing(self):
        cache = ServerCardCache()
        cache.invalidate("nonexistent")  # should not raise
        assert cache.size == 0


class TestClear:
    def test_clear(self):
        cache = ServerCardCache()
        cache.put("s1", {"id": "s1"})
        cache.put("s2", {"id": "s2"})
        cache.put("s3", {"id": "s3"})
        assert cache.size == 3
        cache.clear()
        assert cache.size == 0


class TestCleanupExpired:
    def test_cleanup_expired(self):
        cache = ServerCardCache(ttl_seconds=300)
        cache.put("s1", {"id": "s1"})
        cache.put("s2", {"id": "s2"})
        cache.put("s3", {"id": "s3"})

        # Manually expire s1 and s2 by backdating their cached_at
        cache._store["s1"]["cached_at"] = time.monotonic() - 301
        cache._store["s2"]["cached_at"] = time.monotonic() - 301

        removed = cache.cleanup_expired()
        assert removed == 2
        assert cache.size == 1
        result, _ = cache.get("s3")
        assert result is not None

    def test_cleanup_none_expired(self):
        cache = ServerCardCache(ttl_seconds=300)
        cache.put("s1", {"id": "s1"})
        removed = cache.cleanup_expired()
        assert removed == 0


class TestConditionalProperty:
    def test_conditional_property(self):
        cache = ServerCardCache(conditional=True)
        assert cache.conditional is True
        cache2 = ServerCardCache(conditional=False)
        assert cache2.conditional is False


class TestETag:
    def test_etag(self):
        cache = ServerCardCache()
        card = {"id": "server-1"}
        cache.put("server-1", card, etag="abc123")
        result, etag = cache.get("server-1")
        assert result == card
        assert etag == "abc123"

    def test_no_etag(self):
        cache = ServerCardCache()
        cache.put("server-1", {"id": "server-1"})
        _, etag = cache.get("server-1")
        assert etag is None
