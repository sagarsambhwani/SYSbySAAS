"""Twitter Snowflake 64-bit Distributed Unique ID Generator.

Generates globally unique, roughly time-ordered 64-bit integers with zero
network coordination using bitwise packing:
  - 1 bit : Sign bit (always 0)
  - 41 bits: Milliseconds since custom epoch (~69.7 years)
  - 5 bits : Datacenter ID (0 - 31)
  - 5 bits : Worker / Machine ID (0 - 31)
  - 12 bits: Sequence number (0 - 4,095 per millisecond per worker)
"""

from datetime import datetime, timezone
import threading
import time
from typing import Any, Dict


class ClockBackwardDriftException(Exception):
    """Raised when system clock steps backwards beyond allowed tolerance."""
    pass


class SnowflakeGenerator:
    """Thread-safe 64-bit Snowflake ID Generator.

    Parameters:
        datacenter_id: Datacenter identifier (0 to 31).
        worker_id: Worker/machine identifier (0 to 31).
        epoch: Custom starting timestamp in milliseconds (default: Twitter epoch Nov 04, 2010).
        max_backward_ms: Maximum clock drift backwards tolerated before raising an exception.
    """

    # Canonical bit allocation
    WORKER_ID_BITS = 5
    DATACENTER_ID_BITS = 5
    SEQUENCE_BITS = 12

    # Maximum boundary limits
    MAX_WORKER_ID = (1 << WORKER_ID_BITS) - 1         # 31
    MAX_DATACENTER_ID = (1 << DATACENTER_ID_BITS) - 1 # 31
    SEQUENCE_MASK = (1 << SEQUENCE_BITS) - 1           # 4,095

    # Bit shift positions
    WORKER_ID_SHIFT = SEQUENCE_BITS                    # 12
    DATACENTER_ID_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS # 17
    TIMESTAMP_LEFT_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS + DATACENTER_ID_BITS # 22

    # Twitter canonical epoch (2010-11-04 01:42:54.657 UTC)
    TWITTER_EPOCH = 1288834974657

    def __init__(
        self,
        datacenter_id: int = 0,
        worker_id: int = 0,
        epoch: int = TWITTER_EPOCH,
        max_backward_ms: int = 5,
    ):
        if not (0 <= datacenter_id <= self.MAX_DATACENTER_ID):
            raise ValueError(f"datacenter_id must be between 0 and {self.MAX_DATACENTER_ID}")
        if not (0 <= worker_id <= self.MAX_WORKER_ID):
            raise ValueError(f"worker_id must be between 0 and {self.MAX_WORKER_ID}")

        self.datacenter_id = datacenter_id
        self.worker_id = worker_id
        self.epoch = epoch
        self.max_backward_ms = max_backward_ms

        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

    def _time_gen(self) -> int:
        """Returns the current system timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _wait_next_millis(self, last_timestamp: int) -> int:
        """Spins until the clock advances to the next millisecond."""
        timestamp = self._time_gen()
        while timestamp <= last_timestamp:
            timestamp = self._time_gen()
        return timestamp

    def next_id(self) -> int:
        """Generates the next globally unique 64-bit integer ID.

        Returns:
            int: 63-bit positive integer (fits in a standard signed 64-bit BIGINT).

        Raises:
            ClockBackwardDriftException: If system clock jumps backwards beyond max_backward_ms.
        """
        with self._lock:
            timestamp = self._time_gen()

            # Handle clock skew (NTP drift backward)
            if timestamp < self.last_timestamp:
                drift = self.last_timestamp - timestamp
                if drift <= self.max_backward_ms:
                    # Micro-drift: sleep until clock catches up
                    time.sleep(drift / 1000.0)
                    timestamp = self._time_gen()
                if timestamp < self.last_timestamp:
                    raise ClockBackwardDriftException(
                        f"Clock moved backwards by {self.last_timestamp - timestamp}ms! Refusing to generate ID."
                    )

            if timestamp == self.last_timestamp:
                # Same millisecond: advance sequence counter
                self.sequence = (self.sequence + 1) & self.SEQUENCE_MASK
                if self.sequence == 0:
                    # Sequence overflow (4,096 IDs generated in 1ms) -> wait for next ms
                    timestamp = self._wait_next_millis(self.last_timestamp)
            else:
                # New millisecond: reset sequence counter to 0
                self.sequence = 0

            self.last_timestamp = timestamp

            # Bitwise packing
            time_offset = timestamp - self.epoch
            snowflake_id = (
                (time_offset << self.TIMESTAMP_LEFT_SHIFT)
                | (self.datacenter_id << self.DATACENTER_ID_SHIFT)
                | (self.worker_id << self.WORKER_ID_SHIFT)
                | self.sequence
            )
            return snowflake_id

    def parse_id(self, snowflake_id: int) -> Dict[str, Any]:
        """Decomposes a 64-bit Snowflake ID into its constituent metadata.

        Args:
            snowflake_id: The integer ID to dissect.

        Returns:
            Dict containing timestamp_ms, datetime_utc, datacenter_id, worker_id, sequence.
        """
        sequence = snowflake_id & self.SEQUENCE_MASK
        worker_id = (snowflake_id >> self.WORKER_ID_SHIFT) & self.MAX_WORKER_ID
        datacenter_id = (snowflake_id >> self.DATACENTER_ID_SHIFT) & self.MAX_DATACENTER_ID
        time_offset = snowflake_id >> self.TIMESTAMP_LEFT_SHIFT
        timestamp_ms = self.epoch + time_offset

        dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)

        return {
            "id": snowflake_id,
            "binary": f"{snowflake_id:064b}",
            "timestamp_ms": timestamp_ms,
            "datetime_utc": dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC",
            "datacenter_id": datacenter_id,
            "worker_id": worker_id,
            "sequence": sequence,
        }
