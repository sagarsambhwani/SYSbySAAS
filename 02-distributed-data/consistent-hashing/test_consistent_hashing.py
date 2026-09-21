"""Unit and Concurrency Tests for Consistent Hashing Ring."""

from concurrent.futures import ThreadPoolExecutor
import random
import threading
import pytest

from core import ConsistentHashRing


class TestConsistentHashRing:
    def test_empty_ring(self):
        ring = ConsistentHashRing()
        assert ring.get_node("key1") is None
        assert ring.get_preference_list("key1", 3) == []
        assert len(ring) == 0

    def test_single_node_ownership(self):
        ring = ConsistentHashRing(nodes=["node-A"])
        for i in range(20):
            assert ring.get_node(f"key_{i}") == "node-A"
        assert ring.get_preference_list("key_1", 3) == ["node-A"]
        assert len(ring) == 1

    def test_deterministic_mapping(self):
        ring = ConsistentHashRing(nodes=["node-A", "node-B", "node-C"], vnodes=100)
        key = "user_account_99812"
        first_node = ring.get_node(key)

        # Same key must always map to the exact same node
        for _ in range(50):
            assert ring.get_node(key) == first_node

    def test_minimal_churn_on_node_addition(self):
        initial_nodes = ["node-A", "node-B", "node-C"]
        ring = ConsistentHashRing(nodes=initial_nodes, vnodes=150)

        keys = [f"cache_key_{i}" for i in range(1000)]
        initial_mappings = {k: ring.get_node(k) for k in keys}

        # Add 4th node
        ring.add_node("node-D")
        new_mappings = {k: ring.get_node(k) for k in keys}

        moved_keys = 0
        for k in keys:
            old_node = initial_mappings[k]
            new_node = new_mappings[k]
            if old_node != new_node:
                moved_keys += 1
                # If a key moved, it MUST have moved to the newly added node!
                # Keys should NEVER shuffle between surviving nodes (A, B, C)
                assert new_node == "node-D"

        # On 3 -> 4 nodes, expected movement is ~ 1/4 = 25% (allow +/- 10% variance)
        migration_rate = moved_keys / len(keys)
        assert 0.15 <= migration_rate <= 0.35

    def test_minimal_churn_on_node_removal(self):
        ring = ConsistentHashRing(nodes=["node-A", "node-B", "node-C", "node-D"], vnodes=150)
        keys = [f"session_token_{i}" for i in range(1000)]
        initial_mappings = {k: ring.get_node(k) for k in keys}

        # Remove node-D
        ring.remove_node("node-D")
        new_mappings = {k: ring.get_node(k) for k in keys}

        for k in keys:
            old_node = initial_mappings[k]
            new_node = new_mappings[k]
            if old_node != "node-D":
                # Keys that did NOT belong to node-D must remain on their original nodes
                assert new_node == old_node
            else:
                # Keys that belonged to node-D must now be migrated to surviving nodes
                assert new_node in {"node-A", "node-B", "node-C"}

    def test_preference_list_distinct_replicas(self):
        ring = ConsistentHashRing(nodes=["node-A", "node-B", "node-C", "node-D"], vnodes=100)

        for i in range(50):
            key = f"partition_key_{i}"
            replicas = ring.get_preference_list(key, replication_factor=3)
            # Must return exactly 3 replicas
            assert len(replicas) == 3
            # All replicas must be distinct physical nodes (no duplicates)
            assert len(set(replicas)) == 3
            # Primary owner is the first element
            assert replicas[0] == ring.get_node(key)

    def test_preference_list_exceeding_node_count(self):
        ring = ConsistentHashRing(nodes=["node-A", "node-B"], vnodes=50)
        replicas = ring.get_preference_list("test_key", replication_factor=5)
        # Cannot return more replicas than total physical nodes
        assert len(replicas) == 2
        assert set(replicas) == {"node-A", "node-B"}

    def test_weighted_nodes_distribution(self):
        # Using 300 vnodes for smoother statistical convergence
        ring = ConsistentHashRing(vnodes=300)
        ring.add_node("node-normal", weight=1.0)
        ring.add_node("node-heavy", weight=2.0)  # Double capacity

        keys = [f"item_{i}" for i in range(5000)]
        dist = ring.get_distribution(keys)

        # Heavy node should have approximately 2x the keys of normal node
        ratio = dist["node-heavy"] / dist["node-normal"]
        assert 1.5 <= ratio <= 2.7

    def test_concurrency_thread_safety(self):
        ring = ConsistentHashRing(nodes=["node-1", "node-2", "node-3"], vnodes=80)
        stop_event = threading.Event()
        errors = []

        def reader():
            while not stop_event.is_set():
                try:
                    k = f"key_{random.randint(0, 1000)}"
                    node = ring.get_node(k)
                    # Node must be a valid non-empty string starting with 'node-'
                    assert node is not None and node.startswith("node-")
                except Exception as e:
                    errors.append(e)

        def writer():
            for i in range(4, 10):
                ring.add_node(f"node-{i}")
                ring.remove_node(f"node-{i-1}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            reader_futures = [executor.submit(reader) for _ in range(8)]
            writer_future = executor.submit(writer)

            writer_future.result()
            stop_event.set()
            for rf in reader_futures:
                rf.result()

        assert len(errors) == 0
