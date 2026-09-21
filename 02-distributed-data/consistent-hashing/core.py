"""Consistent Hashing Ring with Virtual Nodes and Replica Preference Lists.

Implements a thread-safe, continuous circular hash ring [0, 2^32 - 1] with:
1. Virtual node (vnode) dispersion to eliminate load skew/hotspots.
2. Weighted physical nodes for heterogeneous hardware.
3. DynamoDB-style preference lists for multi-node replica sets.
"""

import bisect
import hashlib
import threading
from typing import Callable, Dict, Iterable, List, Optional, Set


def default_hash_fn(key: str) -> int:
    """Produces a uniform 32-bit unsigned integer hash using MD5."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


class ConsistentHashRing:
    """Thread-safe Consistent Hash Ring implementation.

    Parameters:
        nodes: Initial collection of physical node identifiers.
        vnodes: Default number of virtual nodes per physical node (default: 150).
        hash_fn: Custom hash function mapping strings to integers.
    """

    def __init__(
        self,
        nodes: Optional[Iterable[str]] = None,
        vnodes: int = 150,
        hash_fn: Optional[Callable[[str], int]] = None,
    ):
        if vnodes <= 0:
            raise ValueError("vnodes must be greater than 0")

        self.vnodes = vnodes
        self._hash = hash_fn if hash_fn is not None else default_hash_fn

        self._ring: Dict[int, str] = {}              # Token -> Physical Node ID
        self._sorted_keys: List[int] = []            # Sorted ring tokens for binary search
        self._nodes: Dict[str, float] = {}           # Physical Node ID -> Weight
        self._lock = threading.RLock()

        if nodes:
            for node in nodes:
                self.add_node(node)

    @property
    def nodes(self) -> Set[str]:
        """Returns the set of active physical nodes on the ring."""
        with self._lock:
            return set(self._nodes.keys())

    def add_node(self, node: str, weight: float = 1.0) -> None:
        """Adds a physical node to the ring with an optional capacity weight.

        Args:
            node: Unique identifier for the physical node.
            weight: Multiplier for virtual nodes (e.g., 2.0 creates 2x vnodes).
        """
        if weight <= 0:
            raise ValueError("Weight must be greater than 0")

        with self._lock:
            # If node already exists, remove its existing tokens first
            if node in self._nodes:
                self.remove_node(node)

            self._nodes[node] = weight
            total_vnodes = max(1, int(self.vnodes * weight))

            for i in range(total_vnodes):
                token_key = f"{node}#vnode_{i}"
                token = self._hash(token_key)
                self._ring[token] = node
                bisect.insort(self._sorted_keys, token)

    def remove_node(self, node: str) -> None:
        """Removes a physical node and all its virtual tokens from the ring.

        Args:
            node: Unique identifier of the node to remove.
        """
        with self._lock:
            if node not in self._nodes:
                return

            del self._nodes[node]

            # Rebuild ring without the removed node's tokens
            tokens_to_remove = {token for token, owner in self._ring.items() if owner == node}
            for token in tokens_to_remove:
                del self._ring[token]

            self._sorted_keys = [token for token in self._sorted_keys if token not in tokens_to_remove]

    def get_node(self, key: str) -> Optional[str]:
        """Finds the primary physical node responsible for the given key.

        Walks clockwise along the ring from hash(key) to the first token.

        Args:
            key: The lookup key (e.g., cache key, partition key, user ID).

        Returns:
            The identifier of the owning physical node, or None if the ring is empty.
        """
        with self._lock:
            if not self._sorted_keys:
                return None

            key_hash = self._hash(key)
            idx = bisect.bisect_right(self._sorted_keys, key_hash)

            # Wrap around to the start of the ring if past the last token
            if idx == len(self._sorted_keys):
                idx = 0

            token = self._sorted_keys[idx]
            return self._ring[token]

    def get_preference_list(self, key: str, replication_factor: int = 3) -> List[str]:
        """Returns the next R distinct physical nodes responsible for replicating a key.

        Used in DynamoDB and Cassandra for quorum replication (W + R > N).
        Skips duplicate virtual nodes belonging to already selected physical nodes.

        Args:
            key: The partition key to replicate.
            replication_factor: Desired number of distinct physical replicas.

        Returns:
            List of distinct physical node IDs of length min(replication_factor, total_nodes).
        """
        with self._lock:
            if not self._sorted_keys:
                return []

            target_count = min(replication_factor, len(self._nodes))
            key_hash = self._hash(key)
            idx = bisect.bisect_right(self._sorted_keys, key_hash)

            selected_nodes: List[str] = []
            seen: Set[str] = set()

            total_tokens = len(self._sorted_keys)
            for step in range(total_tokens):
                curr_idx = (idx + step) % total_tokens
                token = self._sorted_keys[curr_idx]
                node = self._ring[token]

                if node not in seen:
                    seen.add(node)
                    selected_nodes.append(node)
                    if len(selected_nodes) == target_count:
                        break

            return selected_nodes

    def get_distribution(self, keys: Iterable[str]) -> Dict[str, int]:
        """Counts how many keys map to each physical node.

        Useful for measuring data skew and standard deviation across nodes.
        """
        with self._lock:
            counts = {node: 0 for node in self._nodes}
            for key in keys:
                node = self.get_node(key)
                if node:
                    counts[node] += 1
            return counts

    def __len__(self) -> int:
        """Returns the number of active physical nodes."""
        with self._lock:
            return len(self._nodes)
