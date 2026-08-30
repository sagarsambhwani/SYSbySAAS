"""Unit and Concurrency Tests for Rate Limiting Algorithms."""

from concurrent.futures import ThreadPoolExecutor
import time
import pytest

from core import (
    TokenBucketRateLimiter,
    LeakyBucketRateLimiter,
    SlidingWindowLogRateLimiter,
    SlidingWindowCounterRateLimiter,
)


class TestTokenBucketRateLimiter:
    def test_burst_and_refill(self):
        # Capacity of 5 tokens, refills 2 tokens per second
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=2)

        # Consume all 5 burst tokens immediately
        for _ in range(5):
            assert limiter.allow_request() is True

        # 6th request should fail
        assert limiter.allow_request() is False

        # Wait 1.05s -> should have refilled ~2 tokens
        time.sleep(1.05)
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is False

    def test_multi_token_consumption(self):
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=1)
        assert limiter.allow_request(tokens=6) is True
        assert limiter.allow_request(tokens=5) is False
        assert limiter.allow_request(tokens=4) is True

    def test_concurrency_thread_safety(self):
        # Capacity 50, refill 0.1/s -> under 100 concurrent threads, EXACTLY 50 must succeed
        limiter = TokenBucketRateLimiter(capacity=50, refill_rate=0.1)

        def worker():
            return limiter.allow_request()

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: worker(), range(100)))

        allowed = sum(1 for r in results if r is True)
        assert allowed == 50
        assert limiter.get_stats()["allowed_requests"] == 50
        assert limiter.get_stats()["rejected_requests"] == 50


class TestLeakyBucketRateLimiter:
    def test_fill_and_leak(self):
        # Capacity 3, leaks 2 per second
        limiter = LeakyBucketRateLimiter(capacity=3, leak_rate=2)

        # Fill capacity
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is False  # Bucket is full

        # Wait 1.05s -> leaks ~2 units
        time.sleep(1.05)
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is False

    def test_concurrency_thread_safety(self):
        limiter = LeakyBucketRateLimiter(capacity=40, leak_rate=0.1)

        def worker():
            return limiter.allow_request()

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: worker(), range(100)))

        allowed = sum(1 for r in results if r is True)
        assert allowed == 40


class TestSlidingWindowLogRateLimiter:
    def test_exact_window_sliding(self):
        # Max 3 requests in a 1-second window
        limiter = SlidingWindowLogRateLimiter(max_requests=3, window_seconds=1.0)

        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True
        assert limiter.allow_request() is False

        # Wait for window to expire
        time.sleep(1.05)
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True

    def test_concurrency_thread_safety(self):
        limiter = SlidingWindowLogRateLimiter(max_requests=35, window_seconds=2.0)

        def worker():
            return limiter.allow_request()

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: worker(), range(100)))

        allowed = sum(1 for r in results if r is True)
        assert allowed == 35


class TestSlidingWindowCounterRateLimiter:
    def test_counter_progression(self):
        # Max 4 requests in 1.0s window
        limiter = SlidingWindowCounterRateLimiter(max_requests=4, window_seconds=1.0)

        # Consume all 4 requests
        for _ in range(4):
            assert limiter.allow_request() is True
        assert limiter.allow_request() is False

        # After 0.5s into next window (t = 1.5s total), weight of previous window is ~0.5
        # 4 * 0.5 = 2.0 estimated count -> can fit up to 2 more requests
        time.sleep(1.55)
        # Weight of previous window is now ~0.45 -> 4 * 0.45 = 1.8. 1.8 + 1 = 2.8 <= 4
        assert limiter.allow_request() is True
        assert limiter.allow_request() is True

        # After 2 full windows, previous window count is cleared
        time.sleep(2.05)
        for _ in range(4):
            assert limiter.allow_request() is True
        assert limiter.allow_request() is False

    def test_concurrency_thread_safety(self):
        limiter = SlidingWindowCounterRateLimiter(max_requests=45, window_seconds=3.0)

        def worker():
            return limiter.allow_request()

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(lambda _: worker(), range(100)))

        allowed = sum(1 for r in results if r is True)
        assert allowed == 45
