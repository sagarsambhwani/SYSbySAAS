"""Unit, Invariance, and Concurrency Tests for SingleFlight & Cache Stampede Defense."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest

from core import (
    SingleFlightGroup,
    CacheAsideWithSingleFlight,
    XFetchEarlyRefresh,
)


class TestSingleFlightGroup:
    def test_single_call_execution(self):
        """A single call executes normally and returns is_shared=False."""
        group = SingleFlightGroup()
        res, is_shared = group.do("key:1", lambda: "computed_val")
        assert res == "computed_val"
        assert is_shared is False
        assert group.in_flight_count == 0

    def test_concurrent_call_coalescing(self):
        """50 concurrent threads for the same key must execute the function EXACTLY ONCE."""
        group = SingleFlightGroup()
        execution_count = 0
        lock = threading.Lock()

        def expensive_db_query():
            nonlocal execution_count
            with lock:
                execution_count += 1
            time.sleep(0.05)  # Simulate DB latency
            return "db_result"

        barrier = threading.Barrier(50)

        def worker():
            barrier.wait()
            return group.do("viral_tweet:99", expensive_db_query)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            results = [f.result() for f in futures]

        # All 50 threads must receive identical result
        for val, _ in results:
            assert val == "db_result"

        # The expensive function was executed EXACTLY ONCE!
        assert execution_count == 1

        # Exactly 1 caller had is_shared=False (the primary worker); 49 were shared
        shared_count = sum(1 for _, shared in results if shared is True)
        assert shared_count == 49
        assert group.in_flight_count == 0

    def test_independent_keys_are_not_coalesced(self):
        """Different keys must execute independently without false coalescing."""
        group = SingleFlightGroup()
        execution_count = 0
        lock = threading.Lock()

        def fetch(key_id):
            nonlocal execution_count
            with lock:
                execution_count += 1
            return f"val_{key_id}"

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(group.do, f"key_{i}", fetch, i)
                for i in range(10)
            ]
            results = [f.result() for f in futures]

        # 10 distinct keys = 10 distinct executions
        assert execution_count == 10
        for i, (val, shared) in enumerate(results):
            assert val == f"val_{i}"
            assert shared is False

    def test_exception_broadcasting_and_cleanup(self):
        """When primary call fails, the exception is broadcast to all waiters and key is cleared."""
        group = SingleFlightGroup()

        def failing_db():
            time.sleep(0.02)
            raise ConnectionError("PostgreSQL connection timeout")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(group.do, "broken_query", failing_db)
                for _ in range(10)
            ]
            for f in futures:
                with pytest.raises(ConnectionError, match="PostgreSQL connection timeout"):
                    f.result()

        # In-flight map must be clean so retries can execute
        assert group.in_flight_count == 0
        res, shared = group.do("broken_query", lambda: "recovered")
        assert res == "recovered"
        assert shared is False

    def test_forget_key(self):
        """forget() clears the in-flight key early."""
        group = SingleFlightGroup()
        barrier = threading.Barrier(2)

        def slow_fn():
            barrier.wait()
            time.sleep(0.05)
            return "ok"

        # Start primary thread
        t = threading.Thread(target=group.do, args=("early:key", slow_fn))
        t.start()
        barrier.wait()

        assert group.in_flight_count == 1
        group.forget("early:key")
        assert group.in_flight_count == 0
        t.join()


class TestCacheAsideWithSingleFlight:
    def test_cache_hit_and_miss(self):
        cache = CacheAsideWithSingleFlight()
        db_calls = 0

        def db_fetch():
            nonlocal db_calls
            db_calls += 1
            return "user_alice_data"

        # 1. First fetch -> Cache Miss -> Queries DB
        val1, info1 = cache.get("user:alice", db_fetch, ttl_seconds=1.0)
        assert val1 == "user_alice_data"
        assert info1["hit"] is False
        assert db_calls == 1

        # 2. Second fetch -> Cache Hit -> Returns from RAM
        val2, info2 = cache.get("user:alice", db_fetch, ttl_seconds=1.0)
        assert val2 == "user_alice_data"
        assert info2["hit"] is True
        assert db_calls == 1  # No extra DB call!

    def test_thundering_herd_stampede_protection(self):
        """50 concurrent requests hitting an expired cache key trigger exactly 1 DB query."""
        cache = CacheAsideWithSingleFlight()
        db_calls = 0
        lock = threading.Lock()

        def slow_db():
            nonlocal db_calls
            with lock:
                db_calls += 1
            time.sleep(0.05)
            return "fresh_data"

        # Pre-seed cache with an already expired item
        barrier = threading.Barrier(50)

        def worker():
            barrier.wait()
            return cache.get("hot_feed", slow_db, 2.0)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            results = [f.result() for f in futures]

        for val, _ in results:
            assert val == "fresh_data"

        # CRITICAL INVARIANT: Exactly 1 DB query executed despite 50 concurrent misses!
        assert db_calls == 1
        stats = cache.get_stats()
        assert stats["db_queries_executed"] == 1
        assert stats["coalesced_shared_reads"] == 49


class TestXFetchEarlyRefresh:
    def test_expired_key_always_refreshes(self):
        now = time.monotonic()
        # Key expired 1 second ago
        assert XFetchEarlyRefresh.should_refresh(expiry_monotonic=now - 1.0, computation_cost_seconds=0.1) is True

    def test_fresh_key_rarely_refreshes(self):
        now = time.monotonic()
        # Key expires in 1,000 seconds with computation cost of 0.01s
        assert XFetchEarlyRefresh.should_refresh(expiry_monotonic=now + 1000.0, computation_cost_seconds=0.01) is False
