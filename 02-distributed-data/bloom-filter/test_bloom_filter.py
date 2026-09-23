"""Unit, Statistical, and Concurrency Tests for Bloom Filters."""

from concurrent.futures import ThreadPoolExecutor
import random
import pytest

from core import (
    StandardBloomFilter,
    CountingBloomFilter,
    ScalableBloomFilter,
)


class TestStandardBloomFilter:
    def test_zero_false_negatives(self):
        """The core invariant of Bloom filters: NEVER produce a false negative."""
        bf = StandardBloomFilter(capacity=1000, error_rate=0.01)
        items = [f"user_session_{i}" for i in range(1000)]

        for item in items:
            bf.add(item)

        # Every single added item MUST return True
        for item in items:
            assert bf.contains(item) is True
            assert item in bf

        assert bf.count == 1000

    def test_statistical_false_positive_rate(self):
        """Verify empirical false positive rate conforms to target error_rate (1%)."""
        capacity = 10000
        target_error = 0.01  # 1%
        bf = StandardBloomFilter(capacity=capacity, error_rate=target_error)

        # Add 10,000 items
        inserted = [f"account_{i}" for i in range(capacity)]
        for item in inserted:
            bf.add(item)

        # Test 10,000 completely non-existent items
        non_existent = [f"ghost_{i}" for i in range(capacity)]
        false_positives = sum(1 for item in non_existent if bf.contains(item))

        empirical_rate = false_positives / capacity
        # With 10,000 trials, 1% target should land between 0.6% and 1.5% with high probability
        assert 0.005 <= empirical_rate <= 0.018, f"Empirical rate {empirical_rate} diverged from 0.01"

    def test_filter_properties_and_telemetry(self):
        bf = StandardBloomFilter(capacity=500, error_rate=0.05)
        assert bf.count == 0
        assert bf.bits_set == 0
        assert bf.fill_ratio == 0.0

        bf.add("test_key")
        assert bf.count == 1
        assert bf.bits_set > 0
        assert bf.memory_bytes > 0
        assert 0.0 < bf.current_false_positive_rate() < 0.05

    def test_concurrency_thread_safety(self):
        bf = StandardBloomFilter(capacity=2000, error_rate=0.01)

        def worker(start, end):
            for i in range(start, end):
                bf.add(f"item_{i}")
                assert bf.contains(f"item_{i}") is True

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(worker, i * 200, (i + 1) * 200)
                for i in range(8)
            ]
            for f in futures:
                f.result()

        assert bf.count == 1600


class TestCountingBloomFilter:
    def test_add_and_remove(self):
        cbf = CountingBloomFilter(capacity=500, error_rate=0.01)

        cbf.add("transient_key")
        assert cbf.contains("transient_key") is True
        assert cbf.count == 1

        # Remove the item
        assert cbf.remove("transient_key") is True
        assert cbf.count == 0

        # Once removed, it should no longer be present
        assert cbf.contains("transient_key") is False

    def test_remove_non_existent_item(self):
        cbf = CountingBloomFilter(capacity=100, error_rate=0.01)
        assert cbf.remove("never_added") is False


class TestScalableBloomFilter:
    def test_auto_layer_scaling(self):
        # Initial capacity of 50 per layer
        sbf = ScalableBloomFilter(initial_capacity=50, error_rate=0.02, growth_rate=2)
        assert sbf.num_layers == 1

        # Add 150 items -> forces filter to spawn multiple layers
        items = [f"stream_event_{i}" for i in range(150)]
        for item in items:
            sbf.add(item)

        assert sbf.num_layers >= 2
        assert sbf.total_items == 150

        # All 150 items across all layers must be found
        for item in items:
            assert sbf.contains(item) is True
            assert item in sbf

        # Non-existent item should return False (with high probability)
        assert sbf.contains("completely_fake_event_9999") is False
