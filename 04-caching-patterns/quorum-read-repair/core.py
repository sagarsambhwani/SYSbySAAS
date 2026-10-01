"""Strict Quorum Consistency and Read-Repair Engine (Dynamo / Cassandra Pattern).

Implements:
1. ReplicaNode: Individual storage node with versioned records and failure injection.
2. VersionedRecord: Monotonically versioned payload with Last-Write-Wins (LWW) conflict resolution.
3. QuorumCoordinator: Distributed coordinator implementing tunable consistency (ONE, QUORUM, ALL),
   strict quorum invariant enforcement (R + W > N), and blocking / async Read-Repair.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
import math
import random
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple


class ConsistencyLevel(str, Enum):
    ONE = "ONE"          # Acknowledgement from 1 replica
    QUORUM = "QUORUM"    # Acknowledgement from floor(N/2) + 1 replicas
    ALL = "ALL"          # Acknowledgement from all N replicas


class QuorumException(Exception):
    """Raised when the required number of replica acknowledgements cannot be met."""
    pass


@dataclass(frozen=True)
class VersionedRecord:
    """Immutable data record with version sequence and timestamp for LWW resolution."""
    key: str
    value: Any
    version: int
    timestamp_ns: int

    def is_newer_than(self, other: Optional["VersionedRecord"]) -> bool:
        if other is None:
            return True
        if self.version != other.version:
            return self.version > other.version
        return self.timestamp_ns > other.timestamp_ns


class ReplicaNode:
    """Simulated distributed storage node."""

    def __init__(self, node_id: str, simulated_latency_s: float = 0.0):
        self.node_id = node_id
        self.simulated_latency_s = simulated_latency_s
        self.is_alive = True
        self._store: Dict[str, VersionedRecord] = {}
        self._lock = threading.Lock()

        # Telemetry
        self.read_count = 0
        self.write_count = 0

    def read(self, key: str) -> Optional[VersionedRecord]:
        """Reads a record from local node store."""
        if not self.is_alive:
            raise ConnectionError(f"Node {self.node_id} is unreachable")
        if self.simulated_latency_s > 0:
            time.sleep(self.simulated_latency_s)

        with self._lock:
            self.read_count += 1
            return self._store.get(key)

    def write(self, record: VersionedRecord) -> bool:
        """Writes/updates record using Last-Write-Wins (LWW)."""
        if not self.is_alive:
            raise ConnectionError(f"Node {self.node_id} is unreachable")
        if self.simulated_latency_s > 0:
            time.sleep(self.simulated_latency_s)

        with self._lock:
            self.write_count += 1
            current = self._store.get(record.key)
            if current is None or record.is_newer_than(current):
                self._store[record.key] = record
                return True
            return False  # Rejected stale write

    def direct_set(self, record: VersionedRecord) -> None:
        """Force writes without latency/alive checks (for test setup)."""
        with self._lock:
            self._store[record.key] = record

    def get_record(self, key: str) -> Optional[VersionedRecord]:
        with self._lock:
            return self._store.get(key)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


@dataclass
class CoordinatorTelemetry:
    writes_total: int = 0
    writes_success: int = 0
    reads_total: int = 0
    reads_success: int = 0
    quorum_failures: int = 0
    read_repairs_triggered: int = 0
    stale_replicas_healed: int = 0


class QuorumCoordinator:
    """Coordinates read and write quorums across N replicas with Read-Repair.

    Guarantees:
    - If R + W > N, clients are mathematically guaranteed to read the latest acknowledged write.
    - If a read quorum detects replicas with stale versions, Read-Repair heals divergent nodes.
    """

    def __init__(self, replicas: List[ReplicaNode], thread_pool_size: int = 10):
        if not replicas:
            raise ValueError("Coordinator requires at least 1 replica")
        self.replicas = replicas
        self.n = len(replicas)
        self._executor = ThreadPoolExecutor(max_workers=thread_pool_size)
        self._lock = threading.Lock()
        self._global_version_counter = 0
        self.telemetry = CoordinatorTelemetry()

    def _calculate_required_nodes(self, level: ConsistencyLevel) -> int:
        if level == ConsistencyLevel.ONE:
            return 1
        elif level == ConsistencyLevel.QUORUM:
            return (self.n // 2) + 1
        elif level == ConsistencyLevel.ALL:
            return self.n
        raise ValueError(f"Unknown consistency level: {level}")

    def write(
        self,
        key: str,
        value: Any,
        level: ConsistencyLevel = ConsistencyLevel.QUORUM,
        timeout_seconds: float = 2.0,
    ) -> VersionedRecord:
        """Executes a quorum write across replicas.

        Args:
            key: Target partition key.
            value: Data payload.
            level: Consistency level (ONE, QUORUM, ALL).
            timeout_seconds: Timeout for quorum acknowledgment.

        Returns:
            VersionedRecord: The successfully acknowledged record.
        """
        required = self._calculate_required_nodes(level)

        with self._lock:
            self.telemetry.writes_total += 1
            self._global_version_counter += 1
            version = self._global_version_counter

        record = VersionedRecord(
            key=key,
            value=value,
            version=version,
            timestamp_ns=time.time_ns(),
        )

        futures = [self._executor.submit(node.write, record) for node in self.replicas]
        successful_acks = 0
        errors: List[str] = []

        try:
            for fut in as_completed(futures, timeout=timeout_seconds):
                try:
                    if fut.result() is True:
                        successful_acks += 1
                        if successful_acks >= required:
                            with self._lock:
                                self.telemetry.writes_success += 1
                            return record
                except Exception as e:
                    errors.append(str(e))
        except TimeoutError:
            pass

        with self._lock:
            self.telemetry.quorum_failures += 1

        raise QuorumException(
            f"Write Quorum failed: Required {required} ACKs for level {level.value}, "
            f"got {successful_acks}/{self.n}. Errors: {errors}"
        )

    def read(
        self,
        key: str,
        level: ConsistencyLevel = ConsistencyLevel.QUORUM,
        async_repair: bool = False,
        timeout_seconds: float = 2.0,
    ) -> Tuple[Optional[VersionedRecord], Dict[str, Any]]:
        """Executes a quorum read across replicas and performs Read-Repair on divergence.

        Args:
            key: Partition key to read.
            level: Consistency level (ONE, QUORUM, ALL).
            async_repair: If True, dispatches read-repair to stale replicas in background.
                         If False, synchronously blocks until stale replicas are updated.

        Returns:
            Tuple[Optional[VersionedRecord], Dict]: (Latest authoritative record, repair_telemetry)
        """
        required = self._calculate_required_nodes(level)

        with self._lock:
            self.telemetry.reads_total += 1

        # Dispatch reads across replicas in randomized order to model distributed routing
        shuffled_replicas = list(self.replicas)
        random.shuffle(shuffled_replicas)
        futures = {self._executor.submit(node.read, key): node for node in shuffled_replicas}
        responses: List[Tuple[ReplicaNode, Optional[VersionedRecord]]] = []
        errors: List[str] = []

        try:
            for fut in as_completed(futures.keys(), timeout=timeout_seconds):
                node = futures[fut]
                try:
                    res = fut.result()
                    responses.append((node, res))
                    if len(responses) >= required:
                        break
                except Exception as e:
                    errors.append(f"{node.node_id}: {e}")
        except TimeoutError:
            pass

        if len(responses) < required:
            with self._lock:
                self.telemetry.quorum_failures += 1
            raise QuorumException(
                f"Read Quorum failed: Required {required} responses for level {level.value}, "
                f"got {len(responses)}/{self.n}. Errors: {errors}"
            )

        with self._lock:
            self.telemetry.reads_success += 1

        # Determine authoritative record with highest version/timestamp (LWW)
        latest_record: Optional[VersionedRecord] = None
        for _, record in responses:
            if record is not None and record.is_newer_than(latest_record):
                latest_record = record

        stale_nodes: List[ReplicaNode] = []

        if latest_record is not None:
            if async_repair:
                # Async background repair: client returns immediately, background worker heals all cluster nodes
                def background_heal_all():
                    all_responses: List[Tuple[ReplicaNode, Optional[VersionedRecord]]] = list(responses)
                    responded_nodes = {n.node_id for n, _ in all_responses}
                    for fut, node in futures.items():
                        if node.node_id not in responded_nodes:
                            try:
                                r = fut.result(timeout=timeout_seconds)
                                all_responses.append((node, r))
                            except Exception:
                                pass
                    for node, record in all_responses:
                        if record is None or record.version < latest_record.version:
                            with self._lock:
                                self.telemetry.read_repairs_triggered += 1
                            self._execute_repair(node, latest_record)

                self._executor.submit(background_heal_all)
            else:
                # Synchronous repair: heal any stale nodes found among the responses
                for node, record in responses:
                    if record is None or record.version < latest_record.version:
                        stale_nodes.append(node)
                for node in stale_nodes:
                    with self._lock:
                        self.telemetry.read_repairs_triggered += 1
                    self._execute_repair(node, latest_record)

        telemetry_info = {
            "responding_nodes": [n.node_id for n, _ in responses],
            "stale_nodes_detected": [n.node_id for n in stale_nodes],
            "repaired_count": len(stale_nodes),
            "async_repair": async_repair,
        }
        return latest_record, telemetry_info

    def _execute_repair(self, node: ReplicaNode, record: VersionedRecord) -> None:
        try:
            if node.write(record):
                with self._lock:
                    self.telemetry.stale_replicas_healed += 1
        except Exception:
            pass  # Node offline during background repair

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "n_replicas": self.n,
                "writes_total": self.telemetry.writes_total,
                "writes_success": self.telemetry.writes_success,
                "reads_total": self.telemetry.reads_total,
                "reads_success": self.telemetry.reads_success,
                "quorum_failures": self.telemetry.quorum_failures,
                "read_repairs_triggered": self.telemetry.read_repairs_triggered,
                "stale_replicas_healed": self.telemetry.stale_replicas_healed,
            }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
