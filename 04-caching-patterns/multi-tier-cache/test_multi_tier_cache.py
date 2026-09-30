"""Comprehensive Unit, Invariance, and Concurrency Tests for Multi-Tier Cache."""

from concurrent.futures import ThreadPoolExecutor
import threading
import pytest

from core import (
    LRUCache,
    LFUCache,
    MultiTierCache,
    WritePolicy,
)


class TestLRUCache:
    def test_basic_put_get(self):
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("c") is None

    def test_eviction_order(self):
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        # Access "a", making "b" the least recently used
        assert cache.get("a") == 1
        # Insert "c", which should evict "b"
        evicted = cache.put("c", 3)
        assert evicted == ("b", 2)
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert len(cache) == 2
        assert cache.evictions == 1

    def test_overwrite_updates_recency_without_eviction(self):
        cache = LRUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)
        # Update existing key "a"
        cache.put("a", 10)
        # Insert "c", should evict "b" because "a" was updated recently
        evicted = cache.put("c", 3)
        assert evicted == ("b", 2)
        assert cache.get("a") == 10
        assert cache.get("b") is None

    def test_delete_and_clear(self):
        cache = LRUCache(capacity=3)
        cache.put("x", 100)
        assert cache.delete("x") is True
        assert cache.delete("x") is False
        assert cache.get("x") is None

        cache.put("y", 200)
        cache.put("z", 300)
        cache.clear()
        assert len(cache) == 0
        assert cache.get("y") is None


class TestLFUCache:
    def test_lfu_eviction_by_frequency(self):
        cache = LFUCache(capacity=2)
        cache.put("a", 1)
        cache.put("b", 2)

        # Access "a" multiple times (freq of "a" = 3, freq of "b" = 1)
        cache.get("a")
        cache.get("a")

        # Insert "c", which should evict "b" (freq 1 < freq 3)
        evicted = cache.put("c", 3)
        assert evicted == ("b", 2)
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.evictions == 1

    def test_lfu_tie_breaking_by_recency(self):
        """When multiple items have the same minimum frequency, evict the least recently used."""
        cache = LFUCache(capacity=2)
        cache.put("a", 1)  # freq 1
        cache.put("b", 2)  # freq 1
        # Both "a" and "b" have freq 1. "a" was inserted first, so "a" is older/LRU.
        evicted = cache.put("c", 3)
        assert evicted == ("a", 1)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_lfu_update_increases_frequency(self):
        cache = LFUCache(capacity=2)
        cache.put("k1", "v1")  # freq 1
        cache.put("k2", "v2")  # freq 1
        cache.put("k1", "v1_updated")  # freq becomes 2
        # Now k1 has freq 2, k2 has freq 1. Inserting k3 must evict k2!
        evicted = cache.put("k3", "v3")
        assert evicted == ("k2", "v2")
        assert cache.get("k2") is None
        assert cache.get("k1") == "v1_updated"


class TestMultiTierCacheHierarchy:
    def test_tier_promotion_and_backfill(self):
        # L1 capacity = 2, L2 capacity = 4
        cache = MultiTierCache(l1_capacity=2, l2_capacity=4, eviction_policy="LRU")

        # Pre-seed DB directly
        cache._db["user:1"] = {"name": "Alice"}
        cache._db["user:2"] = {"name": "Bob"}
        cache._db["user:3"] = {"name": "Charlie"}

        # 1. First get must miss L1 and L2, fetching from DB
        val, source = cache.get("user:1")
        assert val == {"name": "Alice"}
        assert source == "DB"

        # 2. Second get must hit L1 directly
        val, source = cache.get("user:1")
        assert val == {"name": "Alice"}
        assert source == "L1"

        # Fill L1 to force eviction into L2
        cache.get("user:2")  # L1 now has [user:2, user:1]
        cache.get("user:3")  # L1 now has [user:3, user:2], user:1 evicted from L1

        # In L1, user:1 was evicted, but L2 still holds user:1
        val, source = cache.get("user:1")
        assert val == {"name": "Alice"}
        assert source == "L2"

        # user:1 is promoted back into L1
        val, source = cache.get("user:1")
        assert source == "L1"

    def test_missing_key_returns_miss(self):
        cache = MultiTierCache()
        val, source = cache.get("nonexistent_key")
        assert val is None
        assert source == "MISS"


class TestWritePolicies:
    def test_write_through_policy(self):
        """Write-Through synchronously updates L1, L2, and DB."""
        cache = MultiTierCache(write_policy=WritePolicy.WRITE_THROUGH)
        cache.put("item:100", "payload_100")

        # Verify present in all three tiers immediately
        assert cache.l1.get("item:100") == "payload_100"
        assert cache.l2.get("item:100") == "payload_100"
        assert cache._db["item:100"] == "payload_100"
        assert cache.telemetry.db_writes == 1
        assert cache.dirty_keys_count == 0

    def test_write_back_policy(self):
        """Write-Back updates L1 and L2 immediately, queues DB write until flush."""
        cache = MultiTierCache(write_policy=WritePolicy.WRITE_BACK)
        cache.put("metric:active_users", 4200)

        # Immediate presence in cache
        assert cache.l1.get("metric:active_users") == 4200
        assert cache.l2.get("metric:active_users") == 4200
        # Not yet written to DB!
        assert "metric:active_users" not in cache._db
        assert cache.telemetry.db_writes == 0
        assert cache.dirty_keys_count == 1

        # Explicit flush batch writes to DB
        flushed = cache.flush_dirty_records()
        assert flushed == 1
        assert cache._db["metric:active_users"] == 4200
        assert cache.telemetry.db_writes == 1
        assert cache.dirty_keys_count == 0

    def test_write_around_policy(self):
        """Write-Around writes directly to DB and invalidates cache tiers."""
        cache = MultiTierCache(write_policy=WritePolicy.WRITE_AROUND)

        # Seed L1 and L2
        cache.l1.put("post:1", "old_content")
        cache.l2.put("post:1", "old_content")

        # Write new content with write-around
        cache.put("post:1", "new_content")

        # L1 and L2 must be invalidated
        assert cache.l1.get("post:1") is None
        assert cache.l2.get("post:1") is None
        assert cache._db["post:1"] == "new_content"

        # Subsequent get reads from DB and repopulates L1 & L2
        val, source = cache.get("post:1")
        assert val == "new_content"
        assert source == "DB"
        assert cache.l1.get("post:1") == "new_content"


class TestMultiTierConcurrency:
    def test_concurrent_reads_and_writes(self):
        cache = MultiTierCache(l1_capacity=20, l2_capacity=50, write_policy=WritePolicy.WRITE_THROUGH)
        num_threads = 30
        barrier = threading.Barrier(num_threads)

        def worker(idx: int):
            barrier.wait()
            key = f"key:{idx % 10}"
            cache.put(key, f"val_{idx}")
            val, _ = cache.get(key)
            assert val is not None

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            _ = [f.result() for f in futures]

        stats = cache.get_stats()
        assert stats["db_writes"] == num_threads
        assert len(cache.l1) <= 20
        assert len(cache.l2) <= 50
