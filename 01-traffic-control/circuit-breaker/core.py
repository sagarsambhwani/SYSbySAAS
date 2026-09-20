"""Core Circuit Breaker Implementation with Fallback and Self-Healing.

Implements the three-state finite state machine (CLOSED, OPEN, HALF_OPEN)
with sliding-window failure metrics, execution timeouts, and fallback hooks.
"""

from collections import deque
from enum import Enum
import functools
import threading
import time
from typing import Any, Callable, Deque, Dict, Optional, Tuple


class CircuitState(Enum):
    """The three discrete states of a Circuit Breaker."""
    CLOSED = "CLOSED"        # Normal operation: traffic flows to downstream
    OPEN = "OPEN"            # Tripped: fail-fast immediately without calling downstream
    HALF_OPEN = "HALF_OPEN"  # Canary testing: admit a limited number of trial probes


class CircuitBreakerOpenException(Exception):
    """Raised when an operation is attempted while the circuit is in the OPEN state."""
    pass


class CallTimeoutException(Exception):
    """Raised when an operation exceeds its configured execution timeout."""
    pass


class CircuitBreaker:
    """Thread-safe, sliding-window Circuit Breaker with fallback execution.

    Parameters:
        failure_rate_threshold: Percentage (0.0 to 1.0) of failures required to trip.
        recovery_timeout: Seconds to remain in OPEN before probing in HALF_OPEN.
        min_throughput: Minimum requests needed in sliding window before evaluating failure rate.
        window_size: Number of recent calls tracked in the sliding ring buffer.
        half_open_trials: Number of consecutive successful canary requests to heal to CLOSED.
        execution_timeout: Maximum seconds a call is allowed to take before being marked as failure.
        name: Identifier for logging and metrics.
    """

    def __init__(
        self,
        failure_rate_threshold: float = 0.5,
        recovery_timeout: float = 5.0,
        min_throughput: int = 5,
        window_size: int = 10,
        half_open_trials: int = 3,
        execution_timeout: Optional[float] = None,
        name: str = "default_circuit_breaker",
    ):
        if not (0.0 < failure_rate_threshold <= 1.0):
            raise ValueError("failure_rate_threshold must be between 0.0 and 1.0")

        self.failure_rate_threshold = failure_rate_threshold
        self.recovery_timeout = recovery_timeout
        self.min_throughput = min_throughput
        self.window_size = window_size
        self.half_open_trials = half_open_trials
        self.execution_timeout = execution_timeout
        self.name = name

        # State management
        self._state: CircuitState = CircuitState.CLOSED
        self._last_state_change: float = time.monotonic()
        self._opened_at: Optional[float] = None
        self._half_open_successes: int = 0
        self._lock = threading.Lock()

        # Sliding window buffer: stores (timestamp, success_bool, latency)
        self._window: Deque[Tuple[float, bool, float]] = deque(maxlen=window_size)

        # Cumulative telemetry counters
        self.total_calls = 0
        self.successful_calls = 0
        self.failed_calls = 0
        self.rejected_calls = 0
        self.fallback_calls = 0

    @property
    def state(self) -> CircuitState:
        """Inspect the current state, checking if cooldown elapsed for OPEN state."""
        with self._lock:
            self._evaluate_state_transition()
            return self._state

    def _evaluate_state_transition(self) -> None:
        """Internal helper to transition OPEN -> HALF_OPEN after recovery timeout."""
        if self._state == CircuitState.OPEN:
            now = time.monotonic()
            if self._opened_at is not None and (now - self._opened_at) >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Updates the circuit state and resets canary counters."""
        self._state = new_state
        self._last_state_change = time.monotonic()

        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
            self._half_open_successes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_successes = 0
        elif new_state == CircuitState.CLOSED:
            self._opened_at = None
            self._half_open_successes = 0
            self._window.clear()  # Fresh sliding window upon healing

    def _record_success(self, latency: float) -> None:
        """Records a successful call and checks for healing in HALF_OPEN."""
        now = time.monotonic()
        self.successful_calls += 1
        self._window.append((now, True, latency))

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.half_open_trials:
                self._transition_to(CircuitState.CLOSED)

    def _record_failure(self, latency: float) -> None:
        """Records a failed call and evaluates tripping conditions."""
        now = time.monotonic()
        self.failed_calls += 1
        self._window.append((now, False, latency))

        if self._state == CircuitState.HALF_OPEN:
            # In canary trial, even a single failure snaps breaker back to OPEN
            self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.CLOSED:
            # In CLOSED state, check if sliding window meets threshold
            if len(self._window) >= self.min_throughput:
                failures = sum(1 for _, success, _ in self._window if not success)
                failure_rate = failures / len(self._window)
                if failure_rate >= self.failure_rate_threshold:
                    self._transition_to(CircuitState.OPEN)

    def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        fallback: Optional[Callable[..., Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Executes a function protected by the Circuit Breaker.

        Args:
            func: Target downstream function to execute.
            *args: Positional arguments for func.
            fallback: Optional fallback function called when circuit is OPEN or call fails.
            **kwargs: Keyword arguments for func.

        Returns:
            The return value of func, or fallback if invoked.

        Raises:
            CircuitBreakerOpenException: If circuit is OPEN and no fallback is provided.
            Exception: Re-raises the downstream exception if no fallback is provided.
        """
        with self._lock:
            self._evaluate_state_transition()
            self.total_calls += 1

            if self._state == CircuitState.OPEN:
                self.rejected_calls += 1
                if fallback is not None:
                    self.fallback_calls += 1
                    return fallback(*args, **kwargs)
                raise CircuitBreakerOpenException(
                    f"Circuit '{self.name}' is OPEN. Fast-failing downstream request."
                )

        # Execute downstream call
        start_time = time.monotonic()
        try:
            result = self._execute_with_timeout(func, *args, **kwargs)
            latency = time.monotonic() - start_time
            with self._lock:
                self._record_success(latency)
            return result

        except Exception as exc:
            latency = time.monotonic() - start_time
            with self._lock:
                self._record_failure(latency)

            if fallback is not None:
                with self._lock:
                    self.fallback_calls += 1
                return fallback(*args, **kwargs)
            raise exc

    def _execute_with_timeout(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Executes the function, raising CallTimeoutException if execution exceeds timeout."""
        if self.execution_timeout is None:
            return func(*args, **kwargs)

        result_box = []
        error_box = []

        def worker():
            try:
                result_box.append(func(*args, **kwargs))
            except Exception as e:
                error_box.append(e)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=self.execution_timeout)

        if thread.is_alive():
            raise CallTimeoutException(
                f"Execution timed out after {self.execution_timeout}s"
            )
        if error_box:
            raise error_box[0]
        return result_box[0] if result_box else None

    def __call__(self, fallback: Optional[Callable[..., Any]] = None) -> Callable:
        """Decorator support: @circuit_breaker(fallback=my_fallback)"""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return self.call(func, *args, fallback=fallback, **kwargs)
            return wrapper
        return decorator

    def get_stats(self) -> Dict[str, Any]:
        """Telemetry snapshot of current state and metrics."""
        with self._lock:
            self._evaluate_state_transition()
            failures = sum(1 for _, success, _ in self._window if not success)
            window_len = len(self._window)
            current_failure_rate = (failures / window_len) if window_len > 0 else 0.0

            return {
                "name": self.name,
                "state": self._state.value,
                "total_calls": self.total_calls,
                "successful_calls": self.successful_calls,
                "failed_calls": self.failed_calls,
                "rejected_calls": self.rejected_calls,
                "fallback_calls": self.fallback_calls,
                "window_samples": window_len,
                "current_failure_rate": round(current_failure_rate * 100, 1),
                "half_open_successes": self._half_open_successes,
            }

    def reset(self) -> None:
        """Resets the circuit breaker to clean initial state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._half_open_successes = 0
            self._window.clear()
            self.total_calls = 0
            self.successful_calls = 0
            self.failed_calls = 0
            self.rejected_calls = 0
            self.fallback_calls = 0
