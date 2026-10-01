"""Unit, Invariance, and Fault-Tolerance Tests for Quorum Consistency & Read-Repair."""

import time
import pytest

from core import (
    ConsistencyLevel,
    QuorumCoordinator,
    QuorumException,
    ReplicaNode,
    VersionedRecord,
)


@pytest.fixture
def cluster_3():
    nodes = [ReplicaNode(f"node_{i}") for i in range(3)]
    coord = QuorumCoordinator(nodes)
    yield coord, nodes
    coord.shutdown()


class TestStrictQuorumInvariant:
    def test_quorum_write_and_read_success(self, cluster_3):
        coord, nodes = cluster_3
        # Write with QUORUM (requires 2 of 3)
        record = coord.write("user:101", {"name": "Alice"}, ConsistencyLevel.QUORUM)
        assert record.version == 1

        # Read with QUORUM
        read_rec, info = coord.read("user:101", ConsistencyLevel.QUORUM)
        assert read_rec is not None
        assert read_rec.value == {"name": "Alice"}
        assert info["repaired_count"] == 0

    def test_pigeonhole_overlap_with_one_stale_node(self, cluster_3):
        """When R + W > N, even if 1 replica missed the write, read quorum is guaranteed fresh data."""
        coord, nodes = cluster_3

        # Simulate node_2 was offline during write
        nodes[2].is_alive = False
        coord.write("stock:NVDA", 125.50, ConsistencyLevel.QUORUM)
        assert nodes[0].get_record("stock:NVDA") is not None
        assert nodes[1].get_record("stock:NVDA") is not None
        assert nodes[2].get_record("stock:NVDA") is None

        # Node 2 comes back online with empty/stale data
        nodes[2].is_alive = True

        # Now read with QUORUM (any 2 nodes). At least 1 must have the fresh data!
        read_rec, info = coord.read("stock:NVDA", ConsistencyLevel.QUORUM)
        assert read_rec is not None
        assert read_rec.value == 125.50


class TestReadRepair:
    def test_synchronous_read_repair_heals_stale_node(self, cluster_3):
        coord, nodes = cluster_3

        # Manually create divergence: node 0 and 1 have version 2, node 2 has old version 1
        old_record = VersionedRecord("config:max_conn", 50, version=1, timestamp_ns=1000)
        new_record = VersionedRecord("config:max_conn", 100, version=2, timestamp_ns=2000)

        nodes[0].direct_set(new_record)
        nodes[1].direct_set(new_record)
        nodes[2].direct_set(old_record)

        # Read with QUORUM and sync repair
        val, info = coord.read("config:max_conn", ConsistencyLevel.QUORUM, async_repair=False)
        assert val is not None
        assert val.value == 100
        assert val.version == 2

        # Check telemetry
        stats = coord.get_stats()
        assert stats["read_repairs_triggered"] >= 1

        # CRITICAL INVARIANT: Node 2 must now be repaired to version 2!
        repaired_rec = nodes[2].get_record("config:max_conn")
        assert repaired_rec is not None
        assert repaired_rec.version == 2
        assert repaired_rec.value == 100

    def test_async_read_repair_heals_in_background(self, cluster_3):
        coord, nodes = cluster_3

        new_rec = VersionedRecord("doc:42", "v2_content", version=2, timestamp_ns=5000)
        nodes[0].direct_set(new_rec)
        nodes[1].direct_set(new_rec)
        # Node 2 has no data for doc:42

        val, info = coord.read("doc:42", ConsistencyLevel.QUORUM, async_repair=True)
        assert val.value == "v2_content"

        # Give background thread pool a moment to execute write
        time.sleep(0.05)

        repaired = nodes[2].get_record("doc:42")
        assert repaired is not None
        assert repaired.value == "v2_content"


class TestTunableConsistencyAndFailures:
    def test_write_all_fails_if_one_node_offline(self, cluster_3):
        coord, nodes = cluster_3
        nodes[2].is_alive = False

        # QUORUM write succeeds (2/3 ACKs)
        rec = coord.write("k1", "v1", ConsistencyLevel.QUORUM)
        assert rec.value == "v1"

        # ALL write fails because node 2 is unreachable
        with pytest.raises(QuorumException):
            coord.write("k1", "v2", ConsistencyLevel.ALL, timeout_seconds=0.1)

    def test_quorum_loss_when_majority_fails(self, cluster_3):
        coord, nodes = cluster_3
        # Kill 2 out of 3 nodes (majority down)
        nodes[1].is_alive = False
        nodes[2].is_alive = False

        # Neither QUORUM write nor QUORUM read can succeed
        with pytest.raises(QuorumException):
            coord.write("k2", "v_fail", ConsistencyLevel.QUORUM, timeout_seconds=0.1)

        with pytest.raises(QuorumException):
            coord.read("k2", ConsistencyLevel.QUORUM, timeout_seconds=0.1)

        # But consistency level ONE succeeds! (High availability at cost of consistency)
        nodes[0].direct_set(VersionedRecord("k2", "v_one", version=1, timestamp_ns=10))
        rec, _ = coord.read("k2", ConsistencyLevel.ONE)
        assert rec is not None
        assert rec.value == "v_one"


class TestLastWriteWins:
    def test_lww_out_of_order_protection(self):
        node = ReplicaNode("test_node")
        rec_v1 = VersionedRecord("item", "old", version=1, timestamp_ns=100)
        rec_v2 = VersionedRecord("item", "new", version=2, timestamp_ns=200)

        # Accept v2
        assert node.write(rec_v2) is True
        assert node.get_record("item").value == "new"

        # Stale v1 arrives later -> must be rejected
        assert node.write(rec_v1) is False
        assert node.get_record("item").value == "new"
