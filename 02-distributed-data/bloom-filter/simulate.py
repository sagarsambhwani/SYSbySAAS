"""Bloom Filter vs Raw Disk I/O & Memory Footprint Simulator.

Demonstrates:
1. Disk I/O Shield: Intercepting non-existent queries in RAM before touching slow disk.
2. Memory Footprint Showdown: Comparing raw Python set() RAM vs Bloom Filter bit array.
3. Saturation Curve: Measuring false positive rate degradation as capacity is exceeded.
"""

import sys
import time
from typing import Dict, List, Set

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import StandardBloomFilter

console = Console()


class MockDiskStorageEngine:
    """Simulates a disk-backed key-value storage engine (like Cassandra SSTable)."""

    def __init__(self, records: Set[str], disk_seek_latency: float = 0.0005):
        self.disk_records = records
        self.disk_seek_latency = disk_seek_latency
        self.disk_reads = 0

    def read_from_disk(self, key: str) -> bool:
        """Simulates an expensive disk I/O seek."""
        self.disk_reads += 1
        time.sleep(self.disk_seek_latency)
        return key in self.disk_records


def benchmark_disk_shield():
    """Benchmark 1: Measure disk reads saved during an attack of non-existent queries."""
    console.print("\n[bold yellow]>>> Benchmark 1: The Disk I/O Shield Test[/bold yellow]")
    console.print("[dim]Simulating 10,000 queries (5,000 valid + 5,000 non-existent ghost queries)[/dim]")

    num_records = 5000
    valid_keys = {f"user_profile_id_{i:06d}" for i in range(num_records)}
    ghost_keys = [f"non_existent_key_{i:06d}" for i in range(num_records)]

    storage_without_bloom = MockDiskStorageEngine(valid_keys)
    storage_with_bloom = MockDiskStorageEngine(valid_keys)

    # Initialize Bloom filter with 1% false positive rate
    bf = StandardBloomFilter(capacity=num_records, error_rate=0.01)
    for k in valid_keys:
        bf.add(k)

    # Combined workload: alternating valid and non-existent queries
    workload = []
    valid_list = list(valid_keys)
    for i in range(num_records):
        workload.append(valid_list[i])
        workload.append(ghost_keys[i])

    # 1. Run WITHOUT Bloom Filter (Every query hits disk)
    t0 = time.monotonic()
    without_bloom_hits = 0
    for q in workload:
        if storage_without_bloom.read_from_disk(q):
            without_bloom_hits += 1
    duration_without = time.monotonic() - t0

    # 2. Run WITH Bloom Filter (Filter intercepts ghost queries in RAM)
    t0 = time.monotonic()
    with_bloom_hits = 0
    intercepted_in_ram = 0
    for q in workload:
        if not bf.contains(q):
            # 100% Guaranteed NOT in database -> Skip disk entirely!
            intercepted_in_ram += 1
        else:
            # Might be in database -> Verify on disk
            if storage_with_bloom.read_from_disk(q):
                with_bloom_hits += 1
    duration_with = time.monotonic() - t0

    table = Table(title="Disk I/O Shield Results", show_header=True, header_style="bold cyan")
    table.add_column("Strategy", style="bold white", width=28)
    table.add_column("Total Queries", justify="right")
    table.add_column("Disk Reads Executed", justify="right", style="red")
    table.add_column("Intercepted in RAM", justify="right", style="green")
    table.add_column("Execution Time", justify="right", style="yellow")

    table.add_row(
        "Without Bloom Filter",
        str(len(workload)),
        str(storage_without_bloom.disk_reads),
        "0 (0.0%)",
        f"{duration_without:.2f}s",
    )
    table.add_row(
        "With Bloom Filter (RAM Shield)",
        str(len(workload)),
        str(storage_with_bloom.disk_reads),
        f"{intercepted_in_ram} ({intercepted_in_ram / len(workload) * 100:.1f}%)",
        f"{duration_with:.2f}s",
    )

    console.print(table)
    disk_saved_pct = ((storage_without_bloom.disk_reads - storage_with_bloom.disk_reads) / storage_without_bloom.disk_reads) * 100
    console.print(
        f"[bold green][PASS] Bloom Filter eliminated {intercepted_in_ram} disk seeks ({disk_saved_pct:.1f}% disk I/O reduction), "
        f"speeding up processing by {duration_without / duration_with:.1f}x![/bold green]"
    )


def benchmark_memory_footprint():
    """Benchmark 2: Compare RAM footprint of Python set() vs Bloom Filter across 1,000,000 items."""
    console.print("\n[bold yellow]>>> Benchmark 2: Memory Footprint Showdown (1,000,000 Items)[/bold yellow]")

    num_items = 1_000_000
    target_error = 0.01  # 1%

    bf = StandardBloomFilter(capacity=num_items, error_rate=target_error)
    bf_bytes = bf.memory_bytes
    bf_mb = bf_bytes / (1024 * 1024)

    # Estimate Python set memory for 1M UUID-like strings (avg 36 bytes string + hash set node overhead)
    # A Python 64-bit set of 1M strings consumes ~64MB to 80MB
    sample_key = "user_account_uuid_998124810283"
    string_bytes = sys.getsizeof(sample_key)
    # Hash table entry overhead in Python is ~64 bytes per entry
    estimated_set_bytes = num_items * (string_bytes + 64)
    estimated_set_mb = estimated_set_bytes / (1024 * 1024)

    table = Table(title="RAM Consumption: 1,000,000 Items", show_header=True, header_style="bold magenta")
    table.add_column("Data Structure", style="bold white", width=25)
    table.add_column("Total Bits Required", justify="right")
    table.add_column("RAM Usage", justify="right", style="cyan")
    table.add_column("Bytes Per Item", justify="right", style="yellow")
    table.add_column("Memory Savings", justify="right", style="green")

    table.add_row(
        "Python set() (In-Memory)",
        f"{estimated_set_bytes * 8:,}",
        f"{estimated_set_mb:.1f} MB",
        f"~{estimated_set_bytes / num_items:.1f} bytes",
        "Baseline (0%)",
    )
    table.add_row(
        "StandardBloomFilter (1%)",
        f"{bf.size:,}",
        f"{bf_mb:.2f} MB",
        f"{bf_bytes / num_items:.2f} bytes (~9.6 bits)",
        f"{(1 - (bf_mb / estimated_set_mb)) * 100:.1f}% less RAM",
    )

    console.print(table)


def benchmark_saturation_curve():
    """Benchmark 3: Measure empirical false positive rate as filter gets filled past capacity."""
    console.print("\n[bold yellow]>>> Benchmark 3: Saturation Curve (Error Rate Under Load)[/bold yellow]")

    nominal_capacity = 10000
    target_error = 0.01
    bf = StandardBloomFilter(capacity=nominal_capacity, error_rate=target_error)

    test_ghosts = [f"probe_ghost_{i}" for i in range(2000)]

    table = Table(title=f"Saturation vs False Positive Rate (Target: {target_error * 100:.1f}%)", show_header=True, header_style="bold cyan")
    table.add_column("Load (% of Capacity)", style="bold white", width=22)
    table.add_column("Items Inserted", justify="right")
    table.add_column("Bit Fill Ratio", justify="right", style="cyan")
    table.add_column("Theoretical FP Rate", justify="right", style="yellow")
    table.add_column("Empirical FP Rate", justify="right", style="magenta")

    test_points = [
        (2500, "25% (Under-utilized)"),
        (5000, "50% (Half Full)"),
        (10000, "100% (Nominal Capacity)"),
        (15000, "150% (Overloaded)"),
        (25000, "250% (Saturated)"),
    ]

    total_added = 0
    for count, label in test_points:
        delta = count - total_added
        for i in range(total_added, count):
            bf.add(f"payload_key_{i}")
        total_added = count

        fp_count = sum(1 for g in test_ghosts if bf.contains(g))
        empirical_fp = (fp_count / len(test_ghosts)) * 100
        theoretical_fp = bf.current_false_positive_rate() * 100

        table.add_row(
            label,
            f"{count:,}",
            f"{bf.fill_ratio * 100:.1f}%",
            f"{theoretical_fp:.2f}%",
            f"{empirical_fp:.2f}%",
        )

    console.print(table)


def main():
    console.print(
        Panel.fit(
            "[bold green]SYSbySAAS: Bloom Filter Simulation & Benchmarks[/bold green]\n"
            "[dim]Demonstrating disk seek elimination, RAM compression, and error saturation[/dim]",
            border_style="green",
        )
    )

    benchmark_disk_shield()
    benchmark_memory_footprint()
    benchmark_saturation_curve()


if __name__ == "__main__":
    main()
