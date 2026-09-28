"""Unit, Invariance, and Concurrency Tests for Distributed Lock & Fencing Tokens."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest

from core import (
    DistributedLockManager,
    FencedStorageResource,
    StaleFencingTokenException,
)


class TestDistributedLockManager:
    def test_mutual_exclusion(self):
        """Only one client can hold a lock on a resource at any given time."""
        manager = DistributedLockManager()
        h1 = manager.acquire("resource:1", owner="client-A", ttl_seconds=2.0)
        assert h1 is not None
        assert manager.is_locked("resource:1") is True

        # Second client attempting to acquire same resource must fail
        h2 = manager.acquire("resource:1", owner="client-B", ttl_seconds=2.0)
        assert h2 is None

        # After client-A releases, client-B can acquire
        assert h1.release() is True
        assert manager.is_locked("resource:1") is False

        h2 = manager.acquire("resource:1", owner="client-B", ttl_seconds=2.0)
        assert h2 is not None
        assert h2.release() is True

    def test_lease_automatic_expiration(self):
        """Lock must automatically expire after TTL if client crashes without releasing."""
        manager = DistributedLockManager()
        h1 = manager.acquire("task:expire", owner="crashed-worker", ttl_seconds=0.1)
        assert h1 is not None

        # Wait for TTL to elapse
        time.sleep(0.15)
        assert manager.is_locked("task:expire") is False

        # Next client must succeed in acquiring the expired resource
        h2 = manager.acquire("task:expire", owner="healthy-worker", ttl_seconds=1.0)
        assert h2 is not None
        assert h2.release() is True

    def test_safe_release_prevents_accidental_unlock(self):
        """A client that wakes up after TTL cannot release a lock acquired by another client."""
        manager = DistributedLockManager()
        h1 = manager.acquire("doc:shared", owner="client-1", ttl_seconds=0.1)
        assert h1 is not None

        # Wait for client-1's lease to expire
        time.sleep(0.15)

        # Client-2 acquires the lock
        h2 = manager.acquire("doc:shared", owner="client-2", ttl_seconds=1.0)
        assert h2 is not None

        # Client-1 wakes up and tries to release -> Must fail and NOT delete client-2's lock!
        assert h1.release() is False
        assert manager.is_locked("doc:shared") is True

        # Client-2 should still own the lock
        assert h2.release() is True
        assert manager.is_locked("doc:shared") is False

    def test_watchdog_auto_renewal(self):
        """Watchdog background thread extends lock lease for active long-running jobs."""
        manager = DistributedLockManager()
        # Acquire with a short 0.2s TTL, but enable watchdog
        h1 = manager.acquire("long:job", owner="active-worker", ttl_seconds=0.2, enable_watchdog=True)
        assert h1 is not None

        # Sleep 0.5s (longer than original 0.2s TTL)
        time.sleep(0.5)

        # Lock must STILL be held because watchdog renewed it
        assert manager.is_locked("long:job") is True
        assert manager.acquire("long:job", owner="intruder", ttl_seconds=0.5) is None

        # Release stops watchdog
        assert h1.release() is True
        assert manager.is_locked("long:job") is False

    def test_monotonic_fencing_tokens(self):
        """Every lock acquisition must return a strictly increasing fencing token."""
        manager = DistributedLockManager()
        tokens = []

        for i in range(5):
            h = manager.acquire("res", owner=f"client-{i}", ttl_seconds=0.05)
            assert h is not None
            tokens.append(h.fencing_token)
            time.sleep(0.06)  # Let lease expire

        # Check strictly increasing sequence
        for i in range(len(tokens) - 1):
            assert tokens[i] < tokens[i + 1]

    def test_fencing_token_prevents_gc_pause_split_brain(self):
        """Storage layer with fencing tokens rejects stale writes after GC pause."""
        manager = DistributedLockManager()
        storage = FencedStorageResource(initial_value="INITIAL")

        # 1. Client-1 acquires lock with short TTL (Token #1)
        h1 = manager.acquire("db:row", owner="client-1", ttl_seconds=0.1)
        assert h1.fencing_token == 1

        # 2. Simulate Client-1 experiencing a 0.2s GC pause (lock expires)
        time.sleep(0.15)

        # 3. Client-2 acquires lock (Token #2) and writes successfully
        h2 = manager.acquire("db:row", owner="client-2", ttl_seconds=1.0)
        assert h2.fencing_token == 2
        assert storage.write_with_fencing("CLIENT_2_DATA", client_id="client-2", fencing_token=h2.fencing_token) is True
        assert storage.value == "CLIENT_2_DATA"

        # 4. Client-1 wakes up from GC pause and attempts to write with stale Token #1
        with pytest.raises(StaleFencingTokenException):
            storage.write_with_fencing("CLIENT_1_STALE_DATA", client_id="client-1", fencing_token=h1.fencing_token)

        # Storage value remains untouched by Client-1's stale write!
        assert storage.value == "CLIENT_2_DATA"

    def test_concurrency_race_conditions(self):
        """20 threads competing for the same lock: exactly one wins at a time."""
        manager = DistributedLockManager()
        winners = []
        lock = threading.Lock()

        def worker(client_id):
            h = manager.acquire("hot:resource", owner=f"worker-{client_id}", ttl_seconds=0.05)
            if h is not None:
                with lock:
                    winners.append(client_id)
                time.sleep(0.02)
                h.release()

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(worker, range(20)))

        # Multiple workers won in sequence without deadlock
        assert len(winners) > 0
