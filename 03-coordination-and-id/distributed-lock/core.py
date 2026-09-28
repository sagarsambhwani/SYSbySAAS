"""Distributed Lock Manager with Lease Renewal (Watchdog) and Fencing Tokens.

Implements:
1. DistributedLockManager: Atomic lock acquisition, TTL expiration, and safe release.
2. Watchdog: Background heartbeat thread extending lease for active healthy jobs.
3. Monotonic Fencing Tokens: Sequence counter preventing split-brain writes after GC pauses.
4. FencedStorageResource: Storage target validating fencing tokens on write.
"""

from dataclasses import dataclass
import threading
import time
from typing import Any, Dict, List, Optional


class StaleFencingTokenException(Exception):
    """Raised when a storage write is attempted with an obsolete/stale fencing token."""
    pass


class LockAcquisitionException(Exception):
    """Raised when lock acquisition fails or times out."""
    pass


@dataclass
class LockHandle:
    """Represents an acquired distributed lock held by a client."""
    resource: str
    owner: str
    fencing_token: int
    ttl_seconds: float
    acquired_at: float
    manager: "DistributedLockManager"
    _stop_watchdog: Optional[threading.Event] = None
    _watchdog_thread: Optional[threading.Thread] = None

    def release(self) -> bool:
        """Releases this lock via its manager."""
        return self.manager.release(self)

    def renew(self, extension_seconds: Optional[float] = None) -> bool:
        """Manually renews the lock lease."""
        ext = extension_seconds if extension_seconds is not None else self.ttl_seconds
        return self.manager.renew(self, ext)


class DistributedLockManager:
    """Thread-safe Distributed Lock Manager simulating a Redis/etcd locking service.

    Guarantees:
    - Mutual Exclusion: Only one client can hold a lock on a resource at any time.
    - Deadlock Freedom: Every lock has a TTL and will automatically expire.
    - Safe Release: A client can never accidentally release another client's lock.
    - Monotonic Fencing: Every acquisition receives a strictly increasing integer token.
    """

    def __init__(self):
        self._locks: Dict[str, Dict[str, Any]] = {}
        self._fencing_counter: int = 0
        self._lock = threading.RLock()

    @property
    def current_fencing_token(self) -> int:
        with self._lock:
            return self._fencing_counter

    def acquire(
        self,
        resource: str,
        owner: str,
        ttl_seconds: float = 3.0,
        enable_watchdog: bool = False,
    ) -> Optional[LockHandle]:
        """Atomically acquires a distributed lock on a resource.

        Args:
            resource: Identifier of the shared resource (e.g. 'order:10492').
            owner: Unique identifier of the client holding the lock.
            ttl_seconds: Lease duration before lock automatically expires.
            enable_watchdog: If True, spawns a daemon thread to extend TTL periodically.

        Returns:
            LockHandle if successfully acquired; None if resource is currently locked.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        with self._lock:
            now = time.monotonic()
            current_lock = self._locks.get(resource)

            # Check if existing lock is active or has expired
            if current_lock is not None and now < current_lock["expires_at"]:
                # Resource is currently locked by someone else (or same owner)
                return None

            # Resource is free or prior lease expired!
            self._fencing_counter += 1
            token = self._fencing_counter

            expires_at = now + ttl_seconds
            self._locks[resource] = {
                "owner": owner,
                "fencing_token": token,
                "expires_at": expires_at,
            }

            handle = LockHandle(
                resource=resource,
                owner=owner,
                fencing_token=token,
                ttl_seconds=ttl_seconds,
                acquired_at=now,
                manager=self,
            )

            if enable_watchdog:
                self._start_watchdog(handle)

            return handle

    def _start_watchdog(self, handle: LockHandle) -> None:
        """Starts a background daemon thread that renews the lease periodically."""
        stop_event = threading.Event()
        handle._stop_watchdog = stop_event

        renewal_interval = max(0.05, handle.ttl_seconds / 3.0)

        def watchdog_loop():
            while not stop_event.wait(renewal_interval):
                renewed = self.renew(handle, handle.ttl_seconds)
                if not renewed:
                    # Lost lock ownership or lock was externally released
                    break

        t = threading.Thread(target=watchdog_loop, daemon=True, name=f"watchdog-{handle.resource}")
        handle._watchdog_thread = t
        t.start()

    def renew(self, handle: LockHandle, extension_seconds: float) -> bool:
        """Extends the lease duration for an active lock.

        Returns:
            bool: True if renewed successfully; False if expired or owned by another.
        """
        with self._lock:
            now = time.monotonic()
            current_lock = self._locks.get(handle.resource)

            if (
                current_lock is not None
                and current_lock["owner"] == handle.owner
                and current_lock["fencing_token"] == handle.fencing_token
                and now < current_lock["expires_at"]
            ):
                current_lock["expires_at"] = now + extension_seconds
                return True
            return False

    def release(self, handle: LockHandle) -> bool:
        """Atomically releases a lock if and only if the caller still owns it.

        Prevents the classic bug where a slow client wakes up and releases
        a lock that was already re-assigned to a new client.
        """
        if handle._stop_watchdog is not None:
            handle._stop_watchdog.set()

        with self._lock:
            now = time.monotonic()
            current_lock = self._locks.get(handle.resource)

            if (
                current_lock is not None
                and current_lock["owner"] == handle.owner
                and current_lock["fencing_token"] == handle.fencing_token
                and now < current_lock["expires_at"]
            ):
                del self._locks[handle.resource]
                return True
            return False

    def is_locked(self, resource: str) -> bool:
        """Checks if a resource is currently locked by an unexpired lease."""
        with self._lock:
            current_lock = self._locks.get(resource)
            if current_lock is None:
                return False
            return time.monotonic() < current_lock["expires_at"]


class FencedStorageResource:
    """Simulates a storage engine (e.g. DB, S3 bucket) validating fencing tokens on write."""

    def __init__(self, initial_value: Any = None):
        self.value = initial_value
        self.highest_fencing_token: int = 0
        self.write_history: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def write_without_fencing(self, value: Any, client_id: str) -> None:
        """Vulnerable write method that ignores tokens (illustrates split-brain disaster)."""
        with self._lock:
            self.value = value
            self.write_history.append({
                "client": client_id,
                "value": value,
                "fencing_token": None,
                "timestamp": time.monotonic(),
            })

    def write_with_fencing(self, value: Any, client_id: str, fencing_token: int) -> bool:
        """Protected write method enforcing strictly monotonic fencing tokens.

        Raises:
            StaleFencingTokenException: If fencing_token <= highest_fencing_token.
        """
        with self._lock:
            if fencing_token <= self.highest_fencing_token:
                raise StaleFencingTokenException(
                    f"Rejected write from {client_id}: Fencing token #{fencing_token} is stale! "
                    f"(Highest seen token: #{self.highest_fencing_token})"
                )

            self.highest_fencing_token = fencing_token
            self.value = value
            self.write_history.append({
                "client": client_id,
                "value": value,
                "fencing_token": fencing_token,
                "timestamp": time.monotonic(),
            })
            return True
