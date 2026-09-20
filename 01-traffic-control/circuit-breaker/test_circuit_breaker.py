"""Unit and Concurrency Tests for Circuit Breaker Pattern."""

from concurrent.futures import ThreadPoolExecutor
import time
import pytest

from core import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenException,
    CallTimeoutException,
)


class TestCircuitBreaker:
    def test_closed_state_success(self):
        cb = CircuitBreaker(failure_rate_threshold=0.5, min_throughput=5)

        def mock_service(x):
            return x * 2

        for i in range(10):
            assert cb.call(mock_service, i) == i * 2

        assert cb.state == CircuitState.CLOSED
        stats = cb.get_stats()
        assert stats["successful_calls"] == 10
        assert stats["failed_calls"] == 0
        assert stats["rejected_calls"] == 0

    def test_tripping_to_open_on_failure_threshold(self):
        # Window 10, min_throughput 4, threshold 0.5 (50%)
        cb = CircuitBreaker(failure_rate_threshold=0.5, min_throughput=4, window_size=10)

        def failing_service():
            raise RuntimeError("Database connection failure")

        # 2 failures -> 2/2 = 100%, but min_throughput is 4, so still CLOSED
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(failing_service)
        assert cb.state == CircuitState.CLOSED

        # 2 more failures -> 4/4 = 100% >= 50% and throughput >= 4 -> Trips to OPEN!
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(failing_service)
        assert cb.state == CircuitState.OPEN

    def test_fail_fast_when_open(self):
        cb = CircuitBreaker(failure_rate_threshold=0.5, min_throughput=2, recovery_timeout=5.0)
        call_counter = [0]

        def target_func():
            call_counter[0] += 1
            raise ValueError("Service down")

        # Trip to OPEN
        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(target_func)
        assert cb.state == CircuitState.OPEN
        assert call_counter[0] == 2

        # In OPEN state: must fail-fast without calling target_func
        for _ in range(5):
            with pytest.raises(CircuitBreakerOpenException):
                cb.call(target_func)

        # Target func was NOT called during OPEN state
        assert call_counter[0] == 2
        assert cb.get_stats()["rejected_calls"] == 5

    def test_fallback_mechanism(self):
        cb = CircuitBreaker(failure_rate_threshold=0.5, min_throughput=2, recovery_timeout=5.0)

        def buggy_primary():
            raise TimeoutError("OpenAI API unreachable")

        def fallback_model():
            return "Claude 3.5 Sonnet response (fallback)"

        # Trip to OPEN using fallback
        res1 = cb.call(buggy_primary, fallback=fallback_model)
        res2 = cb.call(buggy_primary, fallback=fallback_model)
        assert res1 == "Claude 3.5 Sonnet response (fallback)"
        assert res2 == "Claude 3.5 Sonnet response (fallback)"
        assert cb.state == CircuitState.OPEN

        # While OPEN, fallback is executed immediately without trying primary
        res3 = cb.call(buggy_primary, fallback=fallback_model)
        assert res3 == "Claude 3.5 Sonnet response (fallback)"
        assert cb.get_stats()["fallback_calls"] == 3

    def test_timeout_protection(self):
        cb = CircuitBreaker(
            failure_rate_threshold=0.5,
            min_throughput=2,
            execution_timeout=0.1,  # 100ms timeout
        )

        def slow_service():
            time.sleep(0.3)
            return "Too late"

        with pytest.raises(CallTimeoutException):
            cb.call(slow_service)

        stats = cb.get_stats()
        assert stats["failed_calls"] == 1

    def test_half_open_recovery_and_healing(self):
        cb = CircuitBreaker(
            failure_rate_threshold=0.5,
            min_throughput=2,
            recovery_timeout=0.3,  # Short cooldown for testing
            half_open_trials=2,
        )

        is_healthy = [False]

        def dynamic_service():
            if not is_healthy[0]:
                raise RuntimeError("Unhealthy")
            return "Recovered OK"

        # Trip to OPEN
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(dynamic_service)
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout to pass
        time.sleep(0.35)
        assert cb.state == CircuitState.HALF_OPEN

        # Make service healthy now
        is_healthy[0] = True

        # First canary trial
        assert cb.call(dynamic_service) == "Recovered OK"
        assert cb.state == CircuitState.HALF_OPEN

        # Second canary trial (meets half_open_trials=2) -> Heals to CLOSED!
        assert cb.call(dynamic_service) == "Recovered OK"
        assert cb.state == CircuitState.CLOSED

    def test_half_open_retrip_on_trial_failure(self):
        cb = CircuitBreaker(
            failure_rate_threshold=0.5,
            min_throughput=2,
            recovery_timeout=0.3,
            half_open_trials=3,
        )

        def flaky_service():
            raise RuntimeError("Still failing")

        # Trip to OPEN
        for _ in range(2):
            with pytest.raises(RuntimeError):
                cb.call(flaky_service)
        assert cb.state == CircuitState.OPEN

        # Wait for HALF_OPEN
        time.sleep(0.35)
        assert cb.state == CircuitState.HALF_OPEN

        # Canary failure immediately snaps back to OPEN
        with pytest.raises(RuntimeError):
            cb.call(flaky_service)
        assert cb.state == CircuitState.OPEN

    def test_decorator_syntax(self):
        cb = CircuitBreaker(failure_rate_threshold=0.5, min_throughput=2)

        def default_fallback():
            return "fallback_value"

        @cb(fallback=default_fallback)
        def my_api():
            raise ConnectionError("down")

        res = my_api()
        assert res == "fallback_value"

    def test_concurrency_thread_safety(self):
        cb = CircuitBreaker(
            failure_rate_threshold=0.5,
            min_throughput=10,
            window_size=20,
            recovery_timeout=0.5,
        )

        def mixed_workload(i):
            if i % 2 == 0:
                return "ok"
            raise RuntimeError("error")

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(lambda i: cb.call(mixed_workload, i, fallback=lambda x: "fallback"), range(100)))

        stats = cb.get_stats()
        # Ensure total operations match perfectly without deadlocks or missed counts
        assert stats["total_calls"] == 100
        assert stats["total_calls"] == stats["successful_calls"] + stats["failed_calls"] + stats["rejected_calls"]
