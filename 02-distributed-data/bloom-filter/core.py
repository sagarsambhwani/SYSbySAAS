"""Core Bloom Filter Implementations.

Provides high-performance, thread-safe probabilistic data structures:
1. StandardBloomFilter: Optimal bit-array sizing with Kirsch-Mitzenmacher double hashing.
2. CountingBloomFilter: Counter-backed filter supporting deletions.
3. ScalableBloomFilter: Auto-scaling layered filter for unbounded stream growth.
"""

from array import array
import hashlib
import math
import threading
from typing import Iterable, List, Optional, Tuple


def _double_hash(key: str) -> Tuple[int, int]:
    """Generates two 64-bit integer hashes from a key using MD5.

    Used by the Kirsch-Mitzenmacher optimization to produce k hashes
    via: g_i(x) = (h1 + i * h2) % m.
    """
    digest = hashlib.md5(key.encode("utf-8")).digest()
    h1 = int.from_bytes(digest[:8], byteorder="little")
    h2 = int.from_bytes(digest[8:], byteorder="little")
    # Ensure h2 is odd and non-zero for uniform stride
    if h2 == 0:
        h2 = 1
    return h1, h2


class StandardBloomFilter:
    """Thread-safe Standard Bloom Filter with optimal sizing and double hashing.

    Parameters:
        capacity: Expected number of elements to store.
        error_rate: Desired false positive probability (e.g. 0.01 for 1%).
    """

    def __init__(self, capacity: int, error_rate: float = 0.01):
        if capacity <= 0:
            raise ValueError("Capacity must be greater than 0")
        if not (0.0 < error_rate < 1.0):
            raise ValueError("Error rate must be between 0.0 and 1.0")

        self.capacity = capacity
        self.error_rate = error_rate

        # Optimal bit array size m = - (n * ln(p)) / (ln(2)^2)
        self.size = int(math.ceil(- (capacity * math.log(error_rate)) / (math.log(2) ** 2)))

        # Optimal number of hash functions k = (m / n) * ln(2)
        self.num_hashes = max(1, int(round((self.size / capacity) * math.log(2))))

        # Byte array for raw bit storage
        self._bytes = bytearray((self.size + 7) // 8)
        self._count = 0
        self._lock = threading.RLock()

    def _get_indices(self, key: str) -> List[int]:
        """Calculates k bit indices using Kirsch-Mitzenmacher double hashing."""
        h1, h2 = _double_hash(key)
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, key: str) -> None:
        """Adds an item to the Bloom filter.

        Args:
            key: String identifier to add.
        """
        with self._lock:
            for idx in self._get_indices(key):
                byte_idx = idx // 8
                bit_idx = idx % 8
                self._bytes[byte_idx] |= (1 << bit_idx)
            self._count += 1

    def contains(self, key: str) -> bool:
        """Tests whether an item might be in the Bloom filter.

        Returns:
            False: 100% Guaranteed that the item was NEVER added.
            True: The item MIGHT be present (within error_rate probability).
        """
        with self._lock:
            for idx in self._get_indices(key):
                byte_idx = idx // 8
                bit_idx = idx % 8
                if not (self._bytes[byte_idx] & (1 << bit_idx)):
                    return False
            return True

    def __contains__(self, key: str) -> bool:
        return self.contains(key)

    @property
    def count(self) -> int:
        """Number of items added so far."""
        with self._lock:
            return self._count

    @property
    def bits_set(self) -> int:
        """Counts total number of bits currently set to 1."""
        with self._lock:
            return sum(bin(byte).count("1") for byte in self._bytes)

    @property
    def fill_ratio(self) -> float:
        """Fraction of bits in the filter set to 1."""
        return self.bits_set / self.size

    def current_false_positive_rate(self) -> float:
        """Calculates current theoretical false positive rate based on items added."""
        with self._lock:
            # Formula: (1 - e^(-k * n / m))^k
            exponent = - (self.num_hashes * self._count) / self.size
            return math.pow(1.0 - math.exp(exponent), self.num_hashes)

    @property
    def memory_bytes(self) -> int:
        """Total memory consumed by the raw bit array in bytes."""
        return len(self._bytes)


class CountingBloomFilter:
    """Thread-safe Counting Bloom Filter supporting item deletion.

    Uses an array of 8-bit counters instead of single bits, allowing
    deletions by decrementing the counters.
    """

    def __init__(self, capacity: int, error_rate: float = 0.01):
        if capacity <= 0:
            raise ValueError("Capacity must be greater than 0")
        if not (0.0 < error_rate < 1.0):
            raise ValueError("Error rate must be between 0.0 and 1.0")

        self.capacity = capacity
        self.error_rate = error_rate

        self.size = int(math.ceil(- (capacity * math.log(error_rate)) / (math.log(2) ** 2)))
        self.num_hashes = max(1, int(round((self.size / capacity) * math.log(2))))

        # 8-bit unsigned char array ('B'), range 0-255 per counter
        self._counters = array("B", [0] * self.size)
        self._count = 0
        self._lock = threading.RLock()

    def _get_indices(self, key: str) -> List[int]:
        h1, h2 = _double_hash(key)
        return [(h1 + i * h2) % self.size for i in range(self.num_hashes)]

    def add(self, key: str) -> None:
        """Adds an item, incrementing its corresponding counters."""
        with self._lock:
            for idx in self._get_indices(key):
                if self._counters[idx] < 255:  # Prevent 8-bit overflow
                    self._counters[idx] += 1
            self._count += 1

    def remove(self, key: str) -> bool:
        """Removes an item by decrementing its corresponding counters.

        Returns:
            bool: True if item was present and decremented, False if definitely not present.
        """
        with self._lock:
            if not self.contains(key):
                return False

            for idx in self._get_indices(key):
                if self._counters[idx] > 0:
                    self._counters[idx] -= 1
            self._count = max(0, self._count - 1)
            return True

    def contains(self, key: str) -> bool:
        """Tests membership. Returns False if any counter is 0."""
        with self._lock:
            for idx in self._get_indices(key):
                if self._counters[idx] == 0:
                    return False
            return True

    def __contains__(self, key: str) -> bool:
        return self.contains(key)

    @property
    def count(self) -> int:
        with self._lock:
            return self._count


class ScalableBloomFilter:
    """Scalable Bloom Filter that dynamically grows without bounded capacity limits.

    Adds new filter layers with tightening error probabilities (p * r^i)
    as capacity thresholds are reached, guaranteeing overall cumulative error rate <= target.
    """

    def __init__(
        self,
        initial_capacity: int = 1000,
        error_rate: float = 0.01,
        growth_rate: int = 2,
        tightening_ratio: float = 0.85,
    ):
        if initial_capacity <= 0:
            raise ValueError("initial_capacity must be greater than 0")
        if not (0.0 < error_rate < 1.0):
            raise ValueError("error_rate must be between 0.0 and 1.0")

        self.initial_capacity = initial_capacity
        self.target_error_rate = error_rate
        self.growth_rate = growth_rate
        self.tightening_ratio = tightening_ratio

        self._filters: List[StandardBloomFilter] = []
        self._lock = threading.RLock()
        self._add_new_layer()

    def _add_new_layer(self) -> None:
        """Spawns a new filter layer with geometrically scaled capacity and tighter error rate."""
        layer_index = len(self._filters)
        capacity = self.initial_capacity * (self.growth_rate ** layer_index)
        error_rate = self.target_error_rate * (self.tightening_ratio ** (layer_index + 1))
        self._filters.append(StandardBloomFilter(capacity=capacity, error_rate=error_rate))

    def add(self, key: str) -> None:
        """Adds an item into the active layer, auto-scaling if full."""
        with self._lock:
            active_filter = self._filters[-1]
            if active_filter.count >= active_filter.capacity:
                self._add_new_layer()
                active_filter = self._filters[-1]
            active_filter.add(key)

    def contains(self, key: str) -> bool:
        """Returns True if the item is found in ANY layer; False if absent from all."""
        with self._lock:
            for bf in reversed(self._filters):  # Check newest layer first
                if bf.contains(key):
                    return True
            return False

    def __contains__(self, key: str) -> bool:
        return self.contains(key)

    @property
    def num_layers(self) -> int:
        """Total number of layered sub-filters currently deployed."""
        with self._lock:
            return len(self._filters)

    @property
    def total_items(self) -> int:
        """Total number of items added across all layers."""
        with self._lock:
            return sum(bf.count for bf in self._filters)

    @property
    def total_memory_bytes(self) -> int:
        """Total RAM consumed by all filter layers."""
        with self._lock:
            return sum(bf.memory_bytes for bf in self._filters)
