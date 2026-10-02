"""Idempotency Key Engine with SHA-256 Fingerprinting and Atomic Replay (Stripe Pattern).

Guarantees:
1. Exactly-Once Execution: Eliminates double-charges and duplicate mutations across network retries.
2. In-Progress Collision Locking: Prevents concurrent race conditions when identical keys arrive simultaneously.
3. Payload Fingerprint Integrity: Enforces that idempotency keys cannot be recycled with differing parameters.
4. Atomic Response Caching: Replays cached responses directly from storage for identical retries.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
import uuid


class IdempotencyStatus(str, Enum):
    STARTED = "STARTED"        # Mutation is currently executing in a worker thread
    COMMITTED = "COMMITTED"    # Mutation finished successfully; response is cached
    FAILED = "FAILED"          # Mutation raised an error; key can be retried


class IdempotencyException(Exception):
    """Base exception for idempotency engine errors."""
    pass


class IdempotencyConflictException(IdempotencyException):
    """Raised when a concurrent request with the same idempotency key is already in-flight."""
    pass


class IdempotencyPayloadMismatchException(IdempotencyException):
    """Raised when an idempotency key is reused with differing request parameters."""
    pass


@dataclass
class IdempotencyRecord:
    key: str
    request_hash: str
    status: IdempotencyStatus
    response_code: Optional[int] = None
    response_body: Optional[Any] = None
    created_at: float = 0.0
    expires_at: float = 0.0
    lock_token: str = ""
    lock_expires_at: float = 0.0


def compute_request_fingerprint(path: str, payload: Any) -> str:
    """Computes a deterministic SHA-256 digest of the request path and serialized payload."""
    serialized_payload = json.dumps(payload, sort_keys=True, default=str)
    canonical_string = f"{path}::{serialized_payload}"
    return hashlib.sha256(canonical_string.encode("utf-8")).hexdigest()


class IdempotencyEngine:
    """Production-grade Idempotency Key Manager."""

    def __init__(self):
        self._records: Dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()

        # Telemetry
        self.total_requests = 0
        self.new_executions = 0
        self.replayed_executions = 0
        self.conflict_rejections = 0
        self.mismatch_rejections = 0

    def execute(
        self,
        key: str,
        path: str,
        payload: Any,
        handler: Callable[[], Tuple[int, Any]],
        ttl_seconds: float = 86400.0,
        lock_timeout_seconds: float = 30.0,
    ) -> Tuple[int, Any, bool]:
        """Executes a handler idempotently.

        Args:
            key: Client-provided unique idempotency key (e.g. UUIDv4).
            path: Target endpoint or route (e.g. '/v1/charges').
            payload: Request arguments/body to be fingerprinted.
            handler: Callable that executes the mutation, returning (http_status, response_body).
            ttl_seconds: Time to live for cached committed response (default 24h).
            lock_timeout_seconds: Duration before an in-progress lock expires if worker dies.

        Returns:
            Tuple[int, Any, bool]: (http_status, response_body, is_replayed)
        """
        request_hash = compute_request_fingerprint(path, payload)
        lock_token = str(uuid.uuid4())
        now = time.monotonic()

        with self._lock:
            self.total_requests += 1

            if key in self._records:
                record = self._records[key]

                # Check if committed record has expired
                if now >= record.expires_at:
                    del self._records[key]
                else:
                    # 1. Validate payload fingerprint
                    if record.request_hash != request_hash:
                        self.mismatch_rejections += 1
                        raise IdempotencyPayloadMismatchException(
                            f"Idempotency key '{key}' was previously used with differing request parameters."
                        )

                    # 2. Check if already committed -> Atomic Replay
                    if record.status == IdempotencyStatus.COMMITTED:
                        self.replayed_executions += 1
                        return record.response_code, record.response_body, True

                    # 3. Check if currently executing
                    if record.status == IdempotencyStatus.STARTED:
                        if now < record.lock_expires_at:
                            self.conflict_rejections += 1
                            raise IdempotencyConflictException(
                                f"A request with idempotency key '{key}' is currently in progress."
                            )
                        # Lock expired (worker crash) -> Re-acquire lock below

            # 4. Acquire in-progress lock
            record = IdempotencyRecord(
                key=key,
                request_hash=request_hash,
                status=IdempotencyStatus.STARTED,
                created_at=now,
                expires_at=now + ttl_seconds,
                lock_token=lock_token,
                lock_expires_at=now + lock_timeout_seconds,
            )
            self._records[key] = record
            self.new_executions += 1

        # 5. Execute downstream mutation outside the global lock
        try:
            status_code, body = handler()
        except Exception as e:
            with self._lock:
                # Mark as failed and delete so client can retry cleanly
                if key in self._records and self._records[key].lock_token == lock_token:
                    del self._records[key]
            raise e

        # 6. Commit response atomically
        with self._lock:
            if key in self._records and self._records[key].lock_token == lock_token:
                record = self._records[key]
                record.status = IdempotencyStatus.COMMITTED
                record.response_code = status_code
                record.response_body = body
                record.expires_at = time.monotonic() + ttl_seconds

        return status_code, body, False

    def get_record(self, key: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            return self._records.get(key)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._records.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "new_executions": self.new_executions,
                "replayed_executions": self.replayed_executions,
                "conflict_rejections": self.conflict_rejections,
                "mismatch_rejections": self.mismatch_rejections,
                "active_records": len(self._records),
            }
