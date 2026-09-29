"""SingleFlight (Request Coalescing) and Cache Stampede Defense.

Implements:
1. SingleFlightGroup: Deduplicates concurrent in-flight requests for identical keys into a single execution.
2. CacheAsideWithSingleFlight: Seamlessly shields downstream databases from Thundering Herds when cache keys expire.
3. XFetchEarlyRefresh: Optimal probabilistic cache refresh (Vitter et al.) before hard expiration.
"""

from dataclasses import dataclass, field
import math
import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class _Call:
    """Represents an in-flight function execution."""
    val: Any = None
    err: Optional[Exception] = None
    done: threading.Event = field(default_factory=threading.Event)
    waiters: int = 0


class SingleFlightGroup:
    """Thread-safe request coalescing engine (Go's sync/singleflight pattern).

    If multiple concurrent threads attempt to fetch the same key simultaneously,
    only the first thread executes the expensive function. All other callers
    wait on an event and share the identical result without repeating downstream work.
    """

    def __init__(self):
        self._calls: Dict[str, _Call] = {}
        self._lock = threading.Lock()

    def do(
        self,
        key: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Tuple[Any, bool]:
        """Executes and returns the result of the function, ensuring only one

        execution is in-flight for a given key at a time.

        Args:
            key: Unique deduplication key (e.g. 'user:profile:10492').
            fn: Expensive downstream function (e.g. SQL query or external API call).
            *args: Positional arguments for fn.
            **kwargs: Keyword arguments for fn.

        Returns:
            Tuple[Any, bool]: (result, is_shared) where is_shared is True if this
            caller waited and received a result computed by another concurrent thread.
        """
        with self._lock:
            if key in self._calls:
                call = self._calls[key]
                call.waiters += 1
                # Wait outside the lock for the primary execution to finish
                wait = True
            else:
                call = _Call()
                self._calls[key] = call
                wait = False

        if wait:
            call.done.wait()
            if call.err is not None:
                raise call.err
            return call.val, True

        # Primary thread executes the downstream function
        try:
            call.val = fn(*args, **kwargs)
        except Exception as e:
            call.err = e
        finally:
            with self._lock:
                call.done.set()
                if key in self._calls and self._calls[key] is call:
                    del self._calls[key]

        if call.err is not None:
            raise call.err
        return call.val, False

    def forget(self, key: str) -> None:
        """Removes a key from in-flight tracking early, allowing future calls to re-execute."""
        with self._lock:
            if key in self._calls:
                del self._calls[key]

    @property
    def in_flight_count(self) -> int:
        """Number of distinct keys currently executing."""
        with self._lock:
            return len(self._calls)


class CacheAsideWithSingleFlight:
    """In-memory Cache-Aside engine protected by SingleFlight request coalescing."""

    def __init__(self):
        # Maps key -> (value, expires_at_monotonic)
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._group = SingleFlightGroup()
        self._lock = threading.Lock()

        # Telemetry metrics
        self.cache_hits = 0
        self.cache_misses = 0
        self.db_queries_executed = 0
        self.coalesced_shared_reads = 0

    def get(
        self,
        key: str,
        fetch_fn: Callable[[], Any],
        ttl_seconds: float = 5.0,
    ) -> Tuple[Any, Dict[str, Any]]:
        """Retrieves an item from cache, or coalesces concurrent DB fetches on expiration.

        Returns:
            Tuple[Any, Dict]: (value, telemetry_info)
        """
        now = time.monotonic()

        # 1. Check in-memory cache
        with self._lock:
            if key in self._cache:
                val, expires_at = self._cache[key]
                if now < expires_at:
                    self.cache_hits += 1
                    return val, {"hit": True, "shared": False, "origin": "cache"}

            self.cache_misses += 1

        # 2. Cache Miss or Expired -> Use SingleFlight to coalesce concurrent misses
        def wrapped_db_fetch():
            with self._lock:
                self.db_queries_executed += 1
            result = fetch_fn()
            # Store in cache
            with self._lock:
                self._cache[key] = (result, time.monotonic() + ttl_seconds)
            return result

        val, is_shared = self._group.do(key, wrapped_db_fetch)

        if is_shared:
            with self._lock:
                self.coalesced_shared_reads += 1

        return val, {
            "hit": False,
            "shared": is_shared,
            "origin": "coalesced_memory" if is_shared else "database",
        }

    def invalidate(self, key: str) -> None:
        """Removes a key from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "db_queries_executed": self.db_queries_executed,
                "coalesced_shared_reads": self.coalesced_shared_reads,
            }


class XFetchEarlyRefresh:
    """Optimal Probabilistic Cache Expiration (XFetch by Vitter et al.).

    Voluntarily triggers a background refresh slightly before hard expiration,
    guaranteeing zero stampedes even under massive concurrency.

    Formula:
        time_left = expiry - now
        should_refresh if: -beta * delta * ln(random()) >= time_left
    """

    @staticmethod
    def should_refresh(
        expiry_monotonic: float,
        computation_cost_seconds: float,
        beta: float = 1.0,
    ) -> bool:
        """Calculates whether a cache entry should probabilistically refresh early.

        Args:
            expiry_monotonic: Monotonic timestamp of hard TTL expiration.
            computation_cost_seconds: Average time delta to recompute the value.
            beta: Aggressiveness factor (> 1.0 refreshes earlier; < 1.0 refreshes later).
        """
        now = time.monotonic()
        time_left = expiry_monotonic - now
        if time_left <= 0:
            return True

        rand_val = random.random()
        if rand_val <= 0:
            return True

        # Vitter's probabilistic early expiration threshold
        threshold = -beta * computation_cost_seconds * math.log(rand_val)
        return threshold >= time_left
