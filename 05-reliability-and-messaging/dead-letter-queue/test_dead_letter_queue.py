"""Unit, Invariance, and Concurrency Tests for Dead Letter Queue & Exponential Backoff."""

from concurrent.futures import ThreadPoolExecutor
import time
import pytest

from core import (
    BackoffCalculator,
    JitterStrategy,
    MessageStatus,
    QueueEngine,
)


class TestBackoffCalculator:
    def test_none_jitter_is_deterministic(self):
        # Base 1.0, max 30.0
        assert BackoffCalculator.calculate_delay(1, base_delay_s=1.0, strategy=JitterStrategy.NONE) == 1.0
        assert BackoffCalculator.calculate_delay(2, base_delay_s=1.0, strategy=JitterStrategy.NONE) == 2.0
        assert BackoffCalculator.calculate_delay(3, base_delay_s=1.0, strategy=JitterStrategy.NONE) == 4.0
        assert BackoffCalculator.calculate_delay(4, base_delay_s=1.0, strategy=JitterStrategy.NONE) == 8.0

    def test_max_delay_cap(self):
        delay = BackoffCalculator.calculate_delay(10, base_delay_s=1.0, max_delay_s=16.0, strategy=JitterStrategy.NONE)
        assert delay == 16.0

    def test_full_jitter_bounds(self):
        for attempt in range(1, 6):
            expected_max = min(30.0, 1.0 * (2 ** (attempt - 1)))
            for _ in range(50):
                d = BackoffCalculator.calculate_delay(attempt, base_delay_s=1.0, strategy=JitterStrategy.FULL_JITTER)
                assert 0.0 <= d <= expected_max

    def test_equal_jitter_bounds(self):
        for attempt in range(1, 5):
            expected_max = 1.0 * (2 ** (attempt - 1))
            half = expected_max / 2.0
            for _ in range(50):
                d = BackoffCalculator.calculate_delay(attempt, base_delay_s=1.0, strategy=JitterStrategy.EQUAL_JITTER)
                assert half <= d <= expected_max


class TestQueueEngine:
    def test_successful_processing_first_try(self):
        queue = QueueEngine()
        msg = queue.enqueue({"order_id": 101})

        processed = []
        result = queue.process_one(lambda p: processed.append(p["order_id"]))

        assert result is not None
        res_msg, success = result
        assert success is True
        assert res_msg.status == MessageStatus.SUCCESS
        assert res_msg.attempts == 1
        assert queue.primary_queue_size == 0
        assert queue.completed_size == 1
        assert queue.dlq_size == 0
        assert processed == [101]

    def test_transient_failure_retries_and_succeeds(self):
        queue = QueueEngine(base_delay_s=0.01, jitter_strategy=JitterStrategy.NONE)
        msg = queue.enqueue({"task": "fetch_user"})

        attempts = 0

        def flaky_handler(p):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("Temporary upstream timeout")
            return "ok"

        # Attempt 1: Fails, scheduled for retry
        res1 = queue.process_one(flaky_handler)
        assert res1 is not None
        msg1, success1 = res1
        assert success1 is False
        assert msg1.attempts == 1
        assert msg1.status == MessageStatus.PENDING
        assert queue.dlq_size == 0
        assert queue.primary_queue_size == 1

        # Attempt 2: After backoff elapses, succeeds
        time.sleep(0.02)
        res2 = queue.process_one(flaky_handler)
        assert res2 is not None
        msg2, success2 = res2
        assert success2 is True
        assert msg2.attempts == 2
        assert msg2.status == MessageStatus.SUCCESS
        assert queue.primary_queue_size == 0
        assert queue.completed_size == 1

    def test_poison_pill_routes_to_dlq(self):
        """A message that fails 3 times is evicted from primary queue and isolated into DLQ."""
        queue = QueueEngine(base_delay_s=0.1, max_retries=3)
        msg = queue.enqueue({"malformed": "json_broken"})

        def broken_handler(p):
            raise ValueError("Invalid schema: missing user_id")

        # Run 3 attempts with simulated time advancement
        current_time = time.monotonic()
        for attempt_num in range(1, 4):
            res = queue.process_one(broken_handler, now=current_time)
            assert res is not None
            current_time += 1.0  # Advance simulated time past backoff

        # Verify state after exhausting retry quota
        assert queue.primary_queue_size == 0
        assert queue.dlq_size == 1
        dlq_msg = queue.get_dlq_messages()[0]
        assert dlq_msg.status == MessageStatus.DEAD_LETTERED
        assert dlq_msg.attempts == 3
        assert "Invalid schema: missing user_id" in dlq_msg.last_error_message
        assert len(dlq_msg.history) == 4  # ENQUEUED + 2 RETRY_SCHEDULED + DEAD_LETTERED

    def test_poison_pill_does_not_cause_head_of_line_blocking(self):
        """A failing message in backoff must not block independent valid messages behind it."""
        queue = QueueEngine(base_delay_s=5.0)  # 5 second backoff delay
        bad_msg = queue.enqueue({"bad": True})
        good_msg = queue.enqueue({"good": True})

        def handler(p):
            if "bad" in p:
                raise RuntimeError("Bad message fails")
            return "good_processed"

        # Attempt 1 processes bad_msg -> fails and schedules backoff for 5s
        res_bad = queue.process_one(handler)
        assert res_bad[1] is False
        assert res_bad[0].payload == {"bad": True}

        # Next process_one must immediately pick good_msg, NOT waiting for bad_msg's 5s backoff!
        res_good = queue.process_one(handler)
        assert res_good is not None
        assert res_good[1] is True
        assert res_good[0].payload == {"good": True}
        assert queue.completed_size == 1

    def test_dlq_redrive(self):
        """Operator can redrive messages from DLQ after fixing downstream issues."""
        queue = QueueEngine(base_delay_s=0.1, max_retries=2)
        queue.enqueue({"payload": "fixed_after_patch"})

        # Fail until DLQ using simulated time
        current_time = time.monotonic()
        for _ in range(2):
            queue.process_one(lambda p: (_ for _ in ()).throw(Exception("Bug in code")), now=current_time)
            current_time += 1.0

        assert queue.dlq_size == 1
        assert queue.primary_queue_size == 0

        # Operator triggers redrive
        redriven = queue.redrive_dlq()
        assert redriven == 1
        assert queue.dlq_size == 0
        assert queue.primary_queue_size == 1

        # Code has been patched! Now handler succeeds
        res = queue.process_one(lambda p: "SUCCESS_AFTER_PATCH")
        assert res is not None
        msg, success = res
        assert success is True
        assert msg.status == MessageStatus.SUCCESS
        assert queue.completed_size == 1

    def test_concurrent_queue_workers(self):
        """Multiple concurrent threads processing messages safely."""
        queue = QueueEngine(base_delay_s=0.001, max_retries=2)

        # Enqueue 40 valid messages and 10 broken messages
        for i in range(40):
            queue.enqueue({"type": "valid", "id": i})
        for i in range(10):
            queue.enqueue({"type": "broken", "id": i})

        def worker_loop():
            for _ in range(30):
                queue.process_one(lambda p: None if p["type"] == "valid" else (_ for _ in ()).throw(ValueError("Broken")))
                time.sleep(0.002)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker_loop) for _ in range(5)]
            _ = [f.result() for f in futures]

        stats = queue.get_stats()
        assert stats["success"] == 40
        assert stats["dlq_quarantined"] == 10
