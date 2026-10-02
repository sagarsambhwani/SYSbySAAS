"""Unit, Concurrency, and Integrity Tests for Idempotency Key Engine."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest

from core import (
    IdempotencyConflictException,
    IdempotencyEngine,
    IdempotencyPayloadMismatchException,
    IdempotencyStatus,
)


class TestIdempotencyEngine:
    def test_fresh_execution(self):
        engine = IdempotencyEngine()
        called = 0

        def charge_handler():
            nonlocal called
            called += 1
            return 201, {"charge_id": "ch_123", "status": "succeeded"}

        code, body, is_replayed = engine.execute(
            key="idem_001",
            path="/v1/charges",
            payload={"amount": 5000, "currency": "usd"},
            handler=charge_handler,
        )

        assert code == 201
        assert body["charge_id"] == "ch_123"
        assert is_replayed is False
        assert called == 1

        stats = engine.get_stats()
        assert stats["new_executions"] == 1
        assert stats["replayed_executions"] == 0

    def test_identical_retry_replay(self):
        """Retrying with identical key and payload returns cached response without re-executing handler."""
        engine = IdempotencyEngine()
        called = 0

        def charge_handler():
            nonlocal called
            called += 1
            return 200, {"charge_id": "ch_abc", "timestamp": time.time()}

        payload = {"amount": 2500, "customer": "cus_99"}

        # First request (fresh)
        code1, body1, replayed1 = engine.execute("idem_002", "/v1/charges", payload, charge_handler)
        assert code1 == 200
        assert replayed1 is False
        assert called == 1

        # Second request (retry)
        code2, body2, replayed2 = engine.execute("idem_002", "/v1/charges", payload, charge_handler)
        assert code2 == 200
        assert replayed2 is True
        assert body2 == body1  # Exact cached payload
        assert called == 1     # HANDLER NEVER RE-EXECUTED!

        # Third request
        code3, body3, replayed3 = engine.execute("idem_002", "/v1/charges", payload, charge_handler)
        assert replayed3 is True
        assert called == 1

    def test_payload_mismatch_rejected(self):
        """Reusing an idempotency key with differing parameters must be rejected with an integrity error."""
        engine = IdempotencyEngine()

        payload_original = {"amount": 1000, "user": "alice"}
        payload_tampered = {"amount": 50000, "user": "alice"}  # Altered amount

        # First request succeeds
        engine.execute("idem_003", "/v1/charges", payload_original, lambda: (200, {"ok": True}))

        # Second request with tampered payload must fail
        with pytest.raises(IdempotencyPayloadMismatchException):
            engine.execute("idem_003", "/v1/charges", payload_tampered, lambda: (200, {"ok": True}))

    def test_path_mismatch_rejected(self):
        """Reusing an idempotency key on a different route must be rejected."""
        engine = IdempotencyEngine()
        payload = {"id": "123"}

        engine.execute("idem_004", "/v1/charges", payload, lambda: (200, {"ok": True}))

        with pytest.raises(IdempotencyPayloadMismatchException):
            engine.execute("idem_004", "/v1/refunds", payload, lambda: (200, {"ok": True}))

    def test_in_flight_concurrent_collision(self):
        """Concurrent requests with identical key while first is in-progress must raise conflict."""
        engine = IdempotencyEngine()
        started_event = threading.Event()
        release_event = threading.Event()

        def slow_handler():
            started_event.set()
            release_event.wait(timeout=2.0)
            return 200, {"result": "done"}

        # Thread 1 starts execution
        t1 = threading.Thread(
            target=lambda: engine.execute("idem_005", "/v1/payments", {"val": 1}, slow_handler)
        )
        t1.start()

        started_event.wait(timeout=1.0)

        # Thread 2 attempts identical execution while Thread 1 is still running
        with pytest.raises(IdempotencyConflictException):
            engine.execute("idem_005", "/v1/payments", {"val": 1}, lambda: (200, {"result": "done"}))

        # Release thread 1
        release_event.set()
        t1.join()

    def test_downstream_failure_clears_record_for_retry(self):
        """If downstream handler raises an exception, the key is unlocked to allow subsequent retries."""
        engine = IdempotencyEngine()
        attempts = 0

        def failing_then_succeeding():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("Database connection timed out")
            return 200, {"status": "recovered"}

        payload = {"order_id": "ord_777"}

        # Attempt 1 fails
        with pytest.raises(ConnectionError):
            engine.execute("idem_006", "/v1/orders", payload, failing_then_succeeding)

        # Attempt 2 should be allowed to run and succeed
        code, body, replayed = engine.execute("idem_006", "/v1/orders", payload, failing_then_succeeding)
        assert code == 200
        assert body["status"] == "recovered"
        assert replayed is False
        assert attempts == 2

    def test_ttl_expiration(self):
        """After TTL expires, the key is evicted and treated as a fresh request."""
        engine = IdempotencyEngine()
        payload = {"ping": "pong"}

        engine.execute("idem_007", "/v1/ping", payload, lambda: (200, "first"), ttl_seconds=0.05)

        # Immediate retry -> replayed
        _, val1, replayed1 = engine.execute("idem_007", "/v1/ping", payload, lambda: (200, "second"))
        assert val1 == "first"
        assert replayed1 is True

        # Wait for TTL expiration
        time.sleep(0.06)

        # New request after expiration -> fresh execution
        _, val2, replayed2 = engine.execute("idem_007", "/v1/ping", payload, lambda: (200, "second"))
        assert val2 == "second"
        assert replayed2 is False

    def test_multithreaded_burst_concurrency(self):
        """50 concurrent threads submitting identical key simultaneously: exactly 1 executes."""
        engine = IdempotencyEngine()
        execution_count = 0
        lock = threading.Lock()
        barrier = threading.Barrier(50)

        def payment_handler():
            nonlocal execution_count
            with lock:
                execution_count += 1
            time.sleep(0.05)
            return 201, {"payment_id": "pay_999"}

        payload = {"amount": 1000}
        successes = 0
        conflicts = 0

        def worker():
            barrier.wait()
            try:
                code, _, replayed = engine.execute("burst_key", "/v1/pay", payload, payment_handler)
                return "SUCCESS"
            except IdempotencyConflictException:
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker) for _ in range(50)]
            results = [f.result() for f in futures]

        # Handler was executed EXACTLY ONCE
        assert execution_count == 1
        success_count = results.count("SUCCESS")
        conflict_count = results.count("CONFLICT")
        assert success_count >= 1
        assert success_count + conflict_count == 50
