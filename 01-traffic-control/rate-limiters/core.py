"""Core Rate Limiting Algorithms.

This module provides thread-safe implementations of four foundational rate
limiting strategies used in distributed systems:
1. Token Bucket (Stripe, AWS API Gateway)
2. Leaky Bucket (Nginx, traffic shaping)
3. Sliding Window Log (Exact sliding window)
4. Sliding Window Counter (Cloudflare hybrid sliding window)
"""

from abc import ABC, abstractmethod
from collections import deque
import threading
import time
from typing import Any, Dict, Optional


class BaseRateLimiter(ABC):
    """Abstract Base Class for Rate Limiter implementations."""

    @abstractmethod
    def allow_request(self, tokens: int = 1) -> bool:
        """Determines whether a request with the given token cost is allowed.

        Args:
            tokens: Number of tokens/capacity required for this request.

        Returns:
            bool: True if the request is permitted, False otherwise.
        """
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Returns internal state telemetry and metrics for inspection."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Resets the internal state of the rate limiter."""
        pass


class TokenBucketRateLimiter(BaseRateLimiter):
    """Token Bucket Rate Limiter.

    Tokens are replenished at a continuous fixed rate up to a maximum burst
    capacity. When a request arrives, tokens are consumed. If insufficient
    tokens exist, the request is rejected or queued.

    Algorithmic Complexity:
        - Time: O(1) per request
        - Space: O(1)

    Real-world use:
        - Allows instantaneous bursts up to capacity while guaranteeing average rate.
        - Used by Stripe API, AWS API Gateway, GitHub API.
    """

    def __init__(self, capacity: float, refill_rate: float):
        """Initialize the Token Bucket.

        Args:
            capacity: Maximum number of tokens the bucket can hold (burst limit).
            refill_rate: Number of tokens added per second.
        """
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("Capacity and refill rate must be positive numbers.")

        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self.last_refill_time = time.monotonic()
        self._lock = threading.Lock()

        # Telemetry metrics
        self.total_requests = 0
        self.allowed_requests = 0
        self.rejected_requests = 0

    def _refill(self, now: float) -> None:
        """Internal helper to calculate and replenish tokens based on elapsed time."""
        elapsed = now - self.last_refill_time
        if elapsed > 0:
            added_tokens = elapsed * self.refill_rate
            self.tokens = min(self.capacity, self.tokens + added_tokens)
            self.last_refill_time = now

    def allow_request(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            self.total_requests += 1

            if self.tokens >= tokens:
                self.tokens -= tokens
                self.allowed_requests += 1
                return True
            else:
                self.rejected_requests += 1
                return False

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            return {
                "algorithm": "Token Bucket",
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
                "current_tokens": round(self.tokens, 2),
                "total_requests": self.total_requests,
                "allowed_requests": self.allowed_requests,
                "rejected_requests": self.rejected_requests,
            }

    def reset(self) -> None:
        with self._lock:
            self.tokens = float(self.capacity)
            self.last_refill_time = time.monotonic()
            self.total_requests = 0
            self.allowed_requests = 0
            self.rejected_requests = 0


class LeakyBucketRateLimiter(BaseRateLimiter):
    """Leaky Bucket Rate Limiter (Traffic Shaping / Metering).

    Requests enter a bucket (queue) of fixed capacity. Water (requests) leaks
    out of the bucket at a constant, uniform rate. If the bucket overflows,
    incoming requests are dropped.

    Algorithmic Complexity:
        - Time: O(1) per request
        - Space: O(1)

    Real-world use:
        - Smooths out bursts and outputs requests at a completely steady pace.
        - Used by Nginx `limit_req`, telecom networks, message queue ingest.
    """

    def __init__(self, capacity: float, leak_rate: float):
        """Initialize the Leaky Bucket.

        Args:
            capacity: Maximum water volume (buffer capacity) before dropping.
            leak_rate: Rate at which water leaks (requests processed) per second.
        """
        if capacity <= 0 or leak_rate <= 0:
            raise ValueError("Capacity and leak rate must be positive numbers.")

        self.capacity = float(capacity)
        self.leak_rate = float(leak_rate)
        self.water_level = 0.0
        self.last_leak_time = time.monotonic()
        self._lock = threading.Lock()

        # Telemetry metrics
        self.total_requests = 0
        self.allowed_requests = 0
        self.rejected_requests = 0

    def _leak(self, now: float) -> None:
        """Internal helper to calculate leaked water since last operation."""
        elapsed = now - self.last_leak_time
        if elapsed > 0:
            leaked_amount = elapsed * self.leak_rate
            self.water_level = max(0.0, self.water_level - leaked_amount)
            self.last_leak_time = now

    def allow_request(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            self._leak(now)
            self.total_requests += 1

            if self.water_level + tokens <= self.capacity:
                self.water_level += tokens
                self.allowed_requests += 1
                return True
            else:
                self.rejected_requests += 1
                return False

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._leak(now)
            return {
                "algorithm": "Leaky Bucket",
                "capacity": self.capacity,
                "leak_rate": self.leak_rate,
                "current_water_level": round(self.water_level, 2),
                "total_requests": self.total_requests,
                "allowed_requests": self.allowed_requests,
                "rejected_requests": self.rejected_requests,
            }

    def reset(self) -> None:
        with self._lock:
            self.water_level = 0.0
            self.last_leak_time = time.monotonic()
            self.total_requests = 0
            self.allowed_requests = 0
            self.rejected_requests = 0


class SlidingWindowLogRateLimiter(BaseRateLimiter):
    """Sliding Window Log Rate Limiter (Exact Sliding Window).

    Maintains an exact timestamped log of each accepted request. When a new
    request arrives, all timestamps older than (now - window_size) are purged.
    If the remaining log count is below the limit, the request is allowed.

    Algorithmic Complexity:
        - Time: O(M) where M is the number of expired timestamps to evict
        - Space: O(N) where N is the maximum requests allowed in the window

    Real-world use:
        - Strict security boundaries, sensitive financial transaction limits.
        - Provides 100% precision with no boundary-burst exploits.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        """Initialize Sliding Window Log.

        Args:
            max_requests: Maximum allowed requests within the time window.
            window_seconds: Time window duration in seconds.
        """
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("max_requests and window_seconds must be positive.")

        self.max_requests = int(max_requests)
        self.window_seconds = float(window_seconds)
        self.log: deque = deque()
        self._lock = threading.Lock()

        # Telemetry metrics
        self.total_requests = 0
        self.allowed_requests = 0
        self.rejected_requests = 0

    def allow_request(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            threshold = now - self.window_seconds
            self.total_requests += 1

            # Evict timestamps outside the sliding window
            while self.log and self.log[0] <= threshold:
                self.log.popleft()

            if len(self.log) + tokens <= self.max_requests:
                for _ in range(tokens):
                    self.log.append(now)
                self.allowed_requests += 1
                return True
            else:
                self.rejected_requests += 1
                return False

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            threshold = now - self.window_seconds
            while self.log and self.log[0] <= threshold:
                self.log.popleft()

            return {
                "algorithm": "Sliding Window Log",
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
                "current_log_size": len(self.log),
                "total_requests": self.total_requests,
                "allowed_requests": self.allowed_requests,
                "rejected_requests": self.rejected_requests,
            }

    def reset(self) -> None:
        with self._lock:
            self.log.clear()
            self.total_requests = 0
            self.allowed_requests = 0
            self.rejected_requests = 0


class SlidingWindowCounterRateLimiter(BaseRateLimiter):
    """Sliding Window Counter (Hybrid Memory-Optimized Approximator).

    Divides time into fixed discrete windows. Approximates the request count in
    the current sliding window by taking a weighted average of the previous
    window's count and the current window's count.

    Formula:
        weight = (window_size - (now - current_window_start)) / window_size
        estimated_requests = (previous_count * weight) + current_count

    Algorithmic Complexity:
        - Time: O(1)
        - Space: O(1) (requires only 2 counters)

    Real-world use:
        - Cloudflare edge rate limiting, high-throughput web APIs.
        - Provides low memory overhead while eliminating boundary spike exploits.
    """

    def __init__(self, max_requests: int, window_seconds: float):
        """Initialize Sliding Window Counter.

        Args:
            max_requests: Maximum requests allowed in the rolling window.
            window_seconds: Duration of the window in seconds.
        """
        if max_requests <= 0 or window_seconds <= 0:
            raise ValueError("max_requests and window_seconds must be positive.")

        self.max_requests = int(max_requests)
        self.window_seconds = float(window_seconds)
        self.current_window_start = time.monotonic()
        self.current_count = 0
        self.previous_count = 0
        self._lock = threading.Lock()

        # Telemetry metrics
        self.total_requests = 0
        self.allowed_requests = 0
        self.rejected_requests = 0

    def _slide_window(self, now: float) -> None:
        """Slides the window forward if elapsed time exceeds window duration."""
        elapsed = now - self.current_window_start
        if elapsed >= self.window_seconds:
            windows_passed = int(elapsed // self.window_seconds)
            if windows_passed == 1:
                self.previous_count = self.current_count
            else:
                self.previous_count = 0

            self.current_count = 0
            self.current_window_start += windows_passed * self.window_seconds

    def allow_request(self, tokens: int = 1) -> bool:
        with self._lock:
            now = time.monotonic()
            self._slide_window(now)
            self.total_requests += 1

            # Calculate time offset within current window
            time_into_current = now - self.current_window_start
            weight = max(0.0, (self.window_seconds - time_into_current) / self.window_seconds)
            estimated_count = (self.previous_count * weight) + self.current_count

            if estimated_count + tokens <= self.max_requests:
                self.current_count += tokens
                self.allowed_requests += 1
                return True
            else:
                self.rejected_requests += 1
                return False

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._slide_window(now)
            time_into_current = now - self.current_window_start
            weight = max(0.0, (self.window_seconds - time_into_current) / self.window_seconds)
            estimated_count = (self.previous_count * weight) + self.current_count

            return {
                "algorithm": "Sliding Window Counter",
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
                "estimated_current_load": round(estimated_count, 2),
                "total_requests": self.total_requests,
                "allowed_requests": self.allowed_requests,
                "rejected_requests": self.rejected_requests,
            }

    def reset(self) -> None:
        with self._lock:
            self.current_window_start = time.monotonic()
            self.current_count = 0
            self.previous_count = 0
            self.total_requests = 0
            self.allowed_requests = 0
            self.rejected_requests = 0
