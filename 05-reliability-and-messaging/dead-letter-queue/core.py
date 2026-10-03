"""Dead Letter Queue (DLQ) & Exponential Backoff with Full Jitter Engine.

Implements:
1. BackoffCalculator: Computes Exponential Backoff with None, Full, Equal, and Decorrelated Jitter (Marc Brooker / AWS algorithm).
2. Message: Rich message container tracking payload, retry attempts, failure stack traces, and transition history.
3. QueueEngine: Message queue coordinator featuring retry quotas, poison-pill DLQ routing, and operator redrive / replay.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid


class JitterStrategy(str, Enum):
    NONE = "NONE"                          # Standard exponential: B * 2^i
    FULL_JITTER = "FULL_JITTER"            # Uniform(0, min(M, B * 2^i))
    EQUAL_JITTER = "EQUAL_JITTER"          # Half fixed, half jitter: (T/2) + Uniform(0, T/2)
    DECORRELATED = "DECORRELATED"          # min(M, Uniform(B, prev_sleep * 3))


class MessageStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    DEAD_LETTERED = "DEAD_LETTERED"
    REDRIVEN = "REDRIVEN"


class BackoffCalculator:
    """Calculates backoff delays with various jitter distributions."""

    @staticmethod
    def calculate_delay(
        attempt: int,
        base_delay_s: float = 0.5,
        max_delay_s: float = 30.0,
        strategy: JitterStrategy = JitterStrategy.FULL_JITTER,
        prev_delay_s: float = 0.0,
    ) -> float:
        """Calculates backoff duration in seconds.

        Args:
            attempt: Current retry count (1-indexed).
            base_delay_s: Base exponential multiplier B.
            max_delay_s: Ceiling maximum delay M.
            strategy: Jitter strategy algorithm.
            prev_delay_s: Previous sleep delay (for decorrelated jitter).
        """
        temp = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))

        if strategy == JitterStrategy.NONE:
            return temp

        elif strategy == JitterStrategy.FULL_JITTER:
            # Marc Brooker's Full Jitter: Sleep = Uniform(0, min(M, B * 2^i))
            return random.uniform(0, temp)

        elif strategy == JitterStrategy.EQUAL_JITTER:
            # Half deterministic, half random: Sleep = (temp / 2) + Uniform(0, temp / 2)
            half = temp / 2.0
            return half + random.uniform(0, half)

        elif strategy == JitterStrategy.DECORRELATED:
            # Decorrelated Jitter: Sleep = min(M, Uniform(B, prev * 3))
            low = base_delay_s
            high = max(low, prev_delay_s * 3.0)
            return min(max_delay_s, random.uniform(low, high))

        return temp


@dataclass
class Message:
    """Queue unit of work tracking payload, attempts, and diagnostic errors."""
    payload: Any
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempts: int = 0
    max_retries: int = 3
    status: MessageStatus = MessageStatus.PENDING
    available_at: float = field(default_factory=time.monotonic)
    error_trace: Optional[str] = None
    last_error_message: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    history: List[Dict[str, Any]] = field(default_factory=list)


class QueueEngine:
    """Production Message Queue with Exponential Backoff, DLQ Routing, and Redrive."""

    def __init__(
        self,
        base_delay_s: float = 0.1,
        max_delay_s: float = 5.0,
        max_retries: int = 3,
        jitter_strategy: JitterStrategy = JitterStrategy.FULL_JITTER,
    ):
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.max_retries = max_retries
        self.jitter_strategy = jitter_strategy

        self._primary_queue: List[Message] = []
        self._dlq: List[Message] = []
        self._completed: List[Message] = []
        self._lock = threading.Lock()

        # Telemetry
        self.enqueued_count = 0
        self.success_count = 0
        self.retries_scheduled = 0
        self.dlq_count = 0
        self.redriven_count = 0

    def enqueue(self, payload: Any, max_retries: Optional[int] = None) -> Message:
        """Enqueues a message into the primary processing queue."""
        with self._lock:
            msg = Message(
                payload=payload,
                max_retries=max_retries if max_retries is not None else self.max_retries,
                available_at=time.monotonic(),
            )
            msg.history.append({"event": "ENQUEUED", "time": time.monotonic()})
            self._primary_queue.append(msg)
            self.enqueued_count += 1
            return msg

    def process_one(
        self,
        handler: Callable[[Any], Any],
        now: Optional[float] = None,
    ) -> Optional[Tuple[Message, bool]]:
        """Processes the next eligible message in the primary queue.

        Args:
            handler: Callable that processes message.payload. Raises exception on failure.
            now: Monotonic timestamp for time travel simulation (defaults to time.monotonic()).

        Returns:
            Optional[Tuple[Message, bool]]: (Message, is_success) or None if no messages are ready.
        """
        current_time = now if now is not None else time.monotonic()
        msg_to_process: Optional[Message] = None

        with self._lock:
            # Find next message ready for processing (available_at <= current_time)
            for i, msg in enumerate(self._primary_queue):
                if msg.available_at <= current_time and msg.status != MessageStatus.PROCESSING:
                    msg_to_process = self._primary_queue.pop(i)
                    msg_to_process.status = MessageStatus.PROCESSING
                    msg_to_process.attempts += 1
                    break

        if msg_to_process is None:
            return None

        # Execute handler outside the lock
        try:
            handler(msg_to_process.payload)
            success = True
            error_msg = None
        except Exception as e:
            success = False
            error_msg = str(e)

        with self._lock:
            if success:
                msg_to_process.status = MessageStatus.SUCCESS
                msg_to_process.history.append({
                    "event": "SUCCESS",
                    "attempt": msg_to_process.attempts,
                    "time": time.monotonic(),
                })
                self._completed.append(msg_to_process)
                self.success_count += 1
                return msg_to_process, True

            # Failure branch: Check retry quota
            msg_to_process.last_error_message = error_msg
            if msg_to_process.attempts < msg_to_process.max_retries:
                # Schedule transient retry with exponential backoff
                delay = BackoffCalculator.calculate_delay(
                    attempt=msg_to_process.attempts,
                    base_delay_s=self.base_delay_s,
                    max_delay_s=self.max_delay_s,
                    strategy=self.jitter_strategy,
                )
                msg_to_process.status = MessageStatus.PENDING
                msg_to_process.available_at = current_time + delay
                msg_to_process.history.append({
                    "event": "RETRY_SCHEDULED",
                    "attempt": msg_to_process.attempts,
                    "delay_s": round(delay, 4),
                    "error": error_msg,
                })
                self._primary_queue.append(msg_to_process)
                self.retries_scheduled += 1
                return msg_to_process, False

            else:
                # Poison Pill: Max retries exhausted -> Route to Dead Letter Queue
                msg_to_process.status = MessageStatus.DEAD_LETTERED
                msg_to_process.history.append({
                    "event": "DEAD_LETTERED",
                    "attempt": msg_to_process.attempts,
                    "error": error_msg,
                })
                self._dlq.append(msg_to_process)
                self.dlq_count += 1
                return msg_to_process, False

    def redrive_dlq(self, max_messages: int = 100) -> int:
        """Operator action: moves dead-lettered messages back to primary queue for reprocessing."""
        with self._lock:
            count = min(len(self._dlq), max_messages)
            redriven: List[Message] = []
            for _ in range(count):
                msg = self._dlq.pop(0)
                msg.status = MessageStatus.REDRIVEN
                msg.attempts = 0  # Reset retry budget for redriven message
                msg.available_at = time.monotonic()
                msg.history.append({"event": "REDRIVEN", "time": time.monotonic()})
                self._primary_queue.append(msg)
                redriven.append(msg)

            self.redriven_count += count
            return count

    @property
    def primary_queue_size(self) -> int:
        with self._lock:
            return len(self._primary_queue)

    @property
    def dlq_size(self) -> int:
        with self._lock:
            return len(self._dlq)

    @property
    def completed_size(self) -> int:
        with self._lock:
            return len(self._completed)

    def get_dlq_messages(self) -> List[Message]:
        with self._lock:
            return list(self._dlq)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enqueued": self.enqueued_count,
                "success": self.success_count,
                "retries_scheduled": self.retries_scheduled,
                "dlq_quarantined": self.dlq_count,
                "redriven": self.redriven_count,
                "pending_primary": len(self._primary_queue),
                "active_dlq": len(self._dlq),
            }
