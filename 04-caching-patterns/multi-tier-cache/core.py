"""Multi-Tier (L1/L2/DB) Caching Engine with LRU/LFU Eviction and Write Policies.

Includes:
1. LRUCache: O(1) Least Recently Used cache using Doubly Linked List + Hash Map.
2. LFUCache: O(1) Least Frequently Used cache using Doubly Linked List per frequency.
3. MultiTierCache: Hierarchical caching combining ultra-fast L1, high-capacity L2, and DB.
4. Write Policies: Write-Through, Write-Back (Write-Behind), and Write-Around.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# 1. LRU Cache (Doubly Linked List + Hash Map)
# ---------------------------------------------------------------------------

class _LRUNode:
    """Internal node for Doubly Linked List."""
    def __init__(self, key: str = "", val: Any = None):
        self.key = key
        self.val = val
        self.prev: Optional["_LRUNode"] = None
        self.next: Optional["_LRUNode"] = None


class LRUCache:
    """Thread-safe O(1) Least Recently Used (LRU) Cache."""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self._map: Dict[str, _LRUNode] = {}
        self._lock = threading.Lock()

        # Dummy sentinel boundaries
        self._head = _LRUNode()
        self._tail = _LRUNode()
        self._head.next = self._tail
        self._tail.prev = self._head

        self.evictions = 0

    def _add_to_front(self, node: _LRUNode) -> None:
        """Inserts node immediately after head sentinel (most recently used)."""
        node.next = self._head.next
        node.prev = self._head
        self._head.next.prev = node
        self._head.next = node

    def _remove_node(self, node: _LRUNode) -> None:
        """Removes an existing node from the linked list."""
        node.prev.next = node.next
        node.next.prev = node.prev

    def _move_to_front(self, node: _LRUNode) -> None:
        self._remove_node(node)
        self._add_to_front(node)

    def _pop_tail(self) -> _LRUNode:
        """Pops and returns the least recently used node (before tail sentinel)."""
        res = self._tail.prev
        self._remove_node(res)
        return res

    def get(self, key: str) -> Optional[Any]:
        """Retrieves an item and updates its recency in O(1) time."""
        with self._lock:
            if key not in self._map:
                return None
            node = self._map[key]
            self._move_to_front(node)
            return node.val

    def put(self, key: str, val: Any) -> Optional[Tuple[str, Any]]:
        """Inserts/updates an item. Returns (evicted_key, evicted_val) if eviction occurred."""
        with self._lock:
            if key in self._map:
                node = self._map[key]
                node.val = val
                self._move_to_front(node)
                return None

            new_node = _LRUNode(key, val)
            self._map[key] = new_node
            self._add_to_front(new_node)

            if len(self._map) > self.capacity:
                evicted = self._pop_tail()
                del self._map[evicted.key]
                self.evictions += 1
                return (evicted.key, evicted.val)
            return None

    def delete(self, key: str) -> bool:
        """Removes a key from cache in O(1) time."""
        with self._lock:
            if key in self._map:
                node = self._map.pop(key)
                self._remove_node(node)
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._head.next = self._tail
            self._tail.prev = self._head

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._map


# ---------------------------------------------------------------------------
# 2. LFU Cache (Frequency Map + Doubly Linked Lists)
# ---------------------------------------------------------------------------

class _LFUNode:
    def __init__(self, key: str = "", val: Any = None, freq: int = 1):
        self.key = key
        self.val = val
        self.freq = freq
        self.prev: Optional["_LFUNode"] = None
        self.next: Optional["_LFUNode"] = None


class _DoublyLinkedList:
    def __init__(self):
        self.head = _LFUNode()
        self.tail = _LFUNode()
        self.head.next = self.tail
        self.tail.prev = self.head
        self.size = 0

    def add_first(self, node: _LFUNode) -> None:
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node
        self.size += 1

    def remove(self, node: _LFUNode) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev
        self.size -= 1

    def pop_last(self) -> Optional[_LFUNode]:
        if self.size == 0:
            return None
        last = self.tail.prev
        self.remove(last)
        return last


class LFUCache:
    """Thread-safe O(1) Least Frequently Used (LFU) Cache.

    Uses a frequency bucket map where each frequency maintains an LRU list.
    Ties in frequency are broken by recency (oldest evicted first).
    """

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self._node_map: Dict[str, _LFUNode] = {}
        self._freq_map: Dict[int, _DoublyLinkedList] = defaultdict(_DoublyLinkedList)
        self._min_freq = 0
        self._lock = threading.Lock()
        self.evictions = 0

    def _increment_freq(self, node: _LFUNode) -> None:
        freq = node.freq
        self._freq_map[freq].remove(node)

        # Update min_freq if the list for min_freq became empty
        if self._min_freq == freq and self._freq_map[freq].size == 0:
            self._min_freq += 1

        node.freq += 1
        self._freq_map[node.freq].add_first(node)

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._node_map:
                return None
            node = self._node_map[key]
            self._increment_freq(node)
            return node.val

    def put(self, key: str, val: Any) -> Optional[Tuple[str, Any]]:
        """Inserts/updates an item. Returns (evicted_key, evicted_val) if eviction occurred."""
        with self._lock:
            if key in self._node_map:
                node = self._node_map[key]
                node.val = val
                self._increment_freq(node)
                return None

            evicted_pair = None
            if len(self._node_map) >= self.capacity:
                # Evict least frequently used node (oldest in min_freq list)
                min_list = self._freq_map[self._min_freq]
                evicted = min_list.pop_last()
                if evicted:
                    del self._node_map[evicted.key]
                    self.evictions += 1
                    evicted_pair = (evicted.key, evicted.val)

            new_node = _LFUNode(key, val, freq=1)
            self._node_map[key] = new_node
            self._freq_map[1].add_first(new_node)
            self._min_freq = 1
            return evicted_pair

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._node_map:
                node = self._node_map.pop(key)
                self._freq_map[node.freq].remove(node)
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._node_map.clear()
            self._freq_map.clear()
            self._min_freq = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._node_map)

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._node_map


# ---------------------------------------------------------------------------
# 3. Write Policies & Multi-Tier Hierarchy
# ---------------------------------------------------------------------------

class WritePolicy(str, Enum):
    WRITE_THROUGH = "write_through"  # Sync write to L1, L2, and DB before return
    WRITE_BACK = "write_back"        # Sync write to L1 & L2; async dirty flush to DB
    WRITE_AROUND = "write_around"    # Sync write directly to DB; invalidate cache


@dataclass
class CacheTelemetry:
    l1_hits: int = 0
    l1_misses: int = 0
    l2_hits: int = 0
    l2_misses: int = 0
    db_reads: int = 0
    db_writes: int = 0
    dirty_flushes: int = 0


class MultiTierCache:
    """Multi-Tier Hierarchical Cache with configurable eviction and write policies.

    Structure:
    - Tier 1 (L1): Local process in-memory cache (ultra-low latency, smallest capacity).
    - Tier 2 (L2): Shared/network cache simulation (low latency, larger capacity).
    - Tier 3 (DB): Persistent storage / database of record (highest latency, unlimited).
    """

    def __init__(
        self,
        l1_capacity: int = 50,
        l2_capacity: int = 200,
        eviction_policy: str = "LRU",
        write_policy: WritePolicy = WritePolicy.WRITE_THROUGH,
    ):
        self.eviction_policy = eviction_policy.upper()
        self.write_policy = write_policy

        if self.eviction_policy == "LFU":
            self.l1 = LFUCache(l1_capacity)
            self.l2 = LFUCache(l2_capacity)
        else:
            self.l1 = LRUCache(l1_capacity)
            self.l2 = LRUCache(l2_capacity)

        self._db: Dict[str, Any] = {}
        self._dirty_keys: Set[str] = set()
        self._lock = threading.Lock()
        self.telemetry = CacheTelemetry()

    def get(self, key: str) -> Tuple[Optional[Any], str]:
        """Retrieves a key through the hierarchy: L1 -> L2 -> DB.

        Returns:
            Tuple[Optional[Any], str]: (value, source_tier) where source_tier is
            'L1', 'L2', 'DB', or 'MISS'.
        """
        # 1. Check L1 Cache
        val = self.l1.get(key)
        if val is not None:
            with self._lock:
                self.telemetry.l1_hits += 1
            return val, "L1"

        with self._lock:
            self.telemetry.l1_misses += 1

        # 2. Check L2 Cache
        val = self.l2.get(key)
        if val is not None:
            with self._lock:
                self.telemetry.l2_hits += 1
            # Promote to L1
            self.l1.put(key, val)
            return val, "L2"

        with self._lock:
            self.telemetry.l2_misses += 1

        # 3. Read Database of record
        with self._lock:
            if key in self._db:
                self.telemetry.db_reads += 1
                val = self._db[key]
            else:
                return None, "MISS"

        # Backfill L2 and L1 caches
        self.l2.put(key, val)
        self.l1.put(key, val)
        return val, "DB"

    def put(self, key: str, val: Any) -> None:
        """Stores a key-value pair according to the configured WritePolicy."""
        if self.write_policy == WritePolicy.WRITE_THROUGH:
            # Sync write to L1, L2, and DB
            self.l1.put(key, val)
            self.l2.put(key, val)
            with self._lock:
                self._db[key] = val
                self.telemetry.db_writes += 1

        elif self.write_policy == WritePolicy.WRITE_BACK:
            # Fast write to L1 and L2, mark key dirty for async flush
            self.l1.put(key, val)
            self.l2.put(key, val)
            with self._lock:
                self._dirty_keys.add(key)

        elif self.write_policy == WritePolicy.WRITE_AROUND:
            # Direct write to DB; invalidate cache layers
            with self._lock:
                self._db[key] = val
                self.telemetry.db_writes += 1
            self.l1.delete(key)
            self.l2.delete(key)

    def flush_dirty_records(self) -> int:
        """Applies to WRITE_BACK policy: flushes all dirty keys to the persistent DB."""
        with self._lock:
            count = len(self._dirty_keys)
            for k in list(self._dirty_keys):
                # Fetch latest value from L1 or L2
                val = self.l1.get(k)
                if val is None:
                    val = self.l2.get(k)
                if val is not None:
                    self._db[k] = val
                    self.telemetry.db_writes += 1
            self._dirty_keys.clear()
            self.telemetry.dirty_flushes += count
            return count

    def invalidate(self, key: str) -> None:
        """Invalidates key in L1 and L2 caches."""
        self.l1.delete(key)
        self.l2.delete(key)

    @property
    def dirty_keys_count(self) -> int:
        with self._lock:
            return len(self._dirty_keys)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total_reads = self.telemetry.l1_hits + self.telemetry.l1_misses
            l1_hit_rate = (self.telemetry.l1_hits / total_reads * 100.0) if total_reads > 0 else 0.0
            overall_hits = self.telemetry.l1_hits + self.telemetry.l2_hits
            overall_hit_rate = (overall_hits / total_reads * 100.0) if total_reads > 0 else 0.0

            return {
                "l1_size": len(self.l1),
                "l2_size": len(self.l2),
                "db_records": len(self._db),
                "dirty_keys": len(self._dirty_keys),
                "l1_hits": self.telemetry.l1_hits,
                "l1_misses": self.telemetry.l1_misses,
                "l2_hits": self.telemetry.l2_hits,
                "l2_misses": self.telemetry.l2_misses,
                "db_reads": self.telemetry.db_reads,
                "db_writes": self.telemetry.db_writes,
                "l1_hit_rate_pct": round(l1_hit_rate, 2),
                "overall_cache_hit_rate_pct": round(overall_hit_rate, 2),
                "l1_evictions": self.l1.evictions,
                "l2_evictions": self.l2.evictions,
            }
