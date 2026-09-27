"""Unit and Concurrency Tests for Snowflake ID Generator."""

from concurrent.futures import ThreadPoolExecutor
import time
import pytest

from core import SnowflakeGenerator, ClockBackwardDriftException


class TestSnowflakeGenerator:
    def test_id_structure_and_bounds(self):
        """Generated ID must be a positive integer fitting within 64-bit signed BIGINT."""
        gen = SnowflakeGenerator(datacenter_id=1, worker_id=2)
        sid = gen.next_id()

        assert isinstance(sid, int)
        assert sid > 0
        assert sid < (1 << 63)  # Fits in 63 bits (positive signed 64-bit int)

    def test_monotonic_sorting(self):
        """Sequential IDs generated over time must strictly increase."""
        gen = SnowflakeGenerator(datacenter_id=1, worker_id=1)
        ids = [gen.next_id() for _ in range(500)]

        # Verify strict ascending order
        for i in range(len(ids) - 1):
            assert ids[i] < ids[i + 1], f"ID {ids[i]} was not strictly smaller than {ids[i+1]}"

    def test_parse_id_round_trip(self):
        """parse_id must accurately reconstruct all metadata encoded into the ID."""
        datacenter_id = 7
        worker_id = 19
        gen = SnowflakeGenerator(datacenter_id=datacenter_id, worker_id=worker_id)

        t_before = int(time.time() * 1000)
        sid = gen.next_id()
        t_after = int(time.time() * 1000)

        meta = gen.parse_id(sid)
        assert meta["id"] == sid
        assert meta["datacenter_id"] == datacenter_id
        assert meta["worker_id"] == worker_id
        assert t_before <= meta["timestamp_ms"] <= t_after
        assert meta["sequence"] >= 0
        assert "UTC" in meta["datetime_utc"]

    def test_sequence_increment_in_same_millisecond(self):
        """Multiple IDs generated in the exact same millisecond must increment sequence."""
        gen = SnowflakeGenerator(datacenter_id=0, worker_id=0)
        id1 = gen.next_id()
        id2 = gen.next_id()

        meta1 = gen.parse_id(id1)
        meta2 = gen.parse_id(id2)

        if meta1["timestamp_ms"] == meta2["timestamp_ms"]:
            assert meta2["sequence"] == meta1["sequence"] + 1

    def test_sequence_overflow_in_tight_loop(self):
        """Generating > 4,096 IDs in a tight loop must handle sequence overflow cleanly."""
        gen = SnowflakeGenerator(datacenter_id=2, worker_id=5)
        # Generate 5,000 IDs (exceeds the 4,096 per-ms limit)
        ids = [gen.next_id() for _ in range(5000)]

        # Must have zero duplicate IDs
        assert len(ids) == len(set(ids))

    def test_multi_worker_independence(self):
        """Independent workers generating IDs at the exact same instant must never collide."""
        gen_a = SnowflakeGenerator(datacenter_id=1, worker_id=1)
        gen_b = SnowflakeGenerator(datacenter_id=1, worker_id=2)
        gen_c = SnowflakeGenerator(datacenter_id=2, worker_id=1)

        ids_a = [gen_a.next_id() for _ in range(1000)]
        ids_b = [gen_b.next_id() for _ in range(1000)]
        ids_c = [gen_c.next_id() for _ in range(1000)]

        all_ids = set(ids_a + ids_b + ids_c)
        assert len(all_ids) == 3000

    def test_clock_backward_drift_exception(self):
        """Generator must raise ClockBackwardDriftException on large backward clock jumps."""
        gen = SnowflakeGenerator(datacenter_id=0, worker_id=0, max_backward_ms=5)

        # Set fake last timestamp in the future
        with gen._lock:
            gen.last_timestamp = gen._time_gen() + 1000  # 1000ms in the future

        with pytest.raises(ClockBackwardDriftException):
            gen.next_id()

    def test_concurrency_thread_safety(self):
        """Multi-threaded concurrent calls on single generator must produce zero duplicates."""
        gen = SnowflakeGenerator(datacenter_id=3, worker_id=4)
        generated_ids = []
        lock = gen._lock

        def worker():
            local_ids = [gen.next_id() for _ in range(250)]
            return local_ids

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(10)]
            for f in futures:
                generated_ids.extend(f.result())

        # 10 workers * 250 = 2,500 IDs
        assert len(generated_ids) == 2500
        assert len(generated_ids) == len(set(generated_ids))
