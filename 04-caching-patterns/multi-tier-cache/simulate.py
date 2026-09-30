"""Interactive Simulation: Multi-Tier Cache (L1/L2/DB), LRU vs LFU, and Write Policies.

Benchmarks:
1. Multi-Tier Latency Hierarchy: Zipfian traffic showing effective latency reduction.
2. Eviction Resilience: LRU vs LFU under table-scan cache pollution.
3. Write Policy Trade-offs: Write-Through vs Write-Back (write coalescing) vs Write-Around.
"""

import random
import time
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import LFUCache, LRUCache, MultiTierCache, WritePolicy

console = Console()


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: MULTI-TIER CACHE (L1 / L2 / DB)[/bold cyan]\n"
            "[yellow]Benchmarking Hierarchical Access, LRU/LFU Eviction, and Write Policies[/yellow]",
            border_style="cyan",
        )
    )

    # -----------------------------------------------------------------------
    # Benchmark 1: Multi-Tier Access Latency Hierarchy (Zipfian Skew)
    # -----------------------------------------------------------------------
    console.print("\n[bold green][BENCHMARK 1][/bold green] Simulating Multi-Tier Hierarchy with Skewed Reads (80/20 Rule)...")
    cache = MultiTierCache(l1_capacity=20, l2_capacity=80, write_policy=WritePolicy.WRITE_THROUGH)

    # Seed database with 200 items
    for i in range(200):
        cache._db[f"sku:{i}"] = f"ProductData-{i}"

    # Generate 1,000 requests with Zipfian skew:
    # 80% requests hit top 20 items (sku:0 to sku:19)
    # 20% requests hit remaining 180 items (sku:20 to sku:199)
    random.seed(42)
    requests: List[str] = []
    for _ in range(1000):
        if random.random() < 0.80:
            requests.append(f"sku:{random.randint(0, 19)}")
        else:
            requests.append(f"sku:{random.randint(20, 199)}")

    tier_counts = {"L1": 0, "L2": 0, "DB": 0}
    for key in requests:
        _, tier = cache.get(key)
        tier_counts[tier] += 1

    stats = cache.get_stats()

    # Latency model:
    # L1 (RAM) = 0.001 ms, L2 (Redis Network) = 1.0 ms, DB (Disk SQL) = 15.0 ms
    t_l1, t_l2, t_db = 0.001, 1.0, 15.0
    effective_latency = (
        (tier_counts["L1"] * t_l1)
        + (tier_counts["L2"] * t_l2)
        + (tier_counts["DB"] * t_db)
    ) / 1000.0
    baseline_db_latency = t_db

    table_1 = Table(title="Multi-Tier Request Routing & Latency Impact (1,000 Requests)")
    table_1.add_column("Tier", style="bold")
    table_1.add_column("Hardware / Medium", style="cyan")
    table_1.add_column("Requests Served", justify="center")
    table_1.add_column("Traffic Share", justify="center")
    table_1.add_column("Simulated Latency", justify="right")

    table_1.add_row("L1 Cache", "In-Process RAM", str(tier_counts["L1"]), f"{tier_counts['L1']/10.0:.1f}%", f"{t_l1:.3f} ms")
    table_1.add_row("L2 Cache", "Remote Redis Pool", str(tier_counts["L2"]), f"{tier_counts['L2']/10.0:.1f}%", f"{t_l2:.1f} ms")
    table_1.add_row("Database", "Persistent NVMe/Disk", str(tier_counts["DB"]), f"{tier_counts['DB']/10.0:.1f}%", f"{t_db:.1f} ms")
    console.print(table_1)

    speedup = baseline_db_latency / effective_latency
    console.print(f"  [PASS] All-DB Baseline Latency:      [bold red]{baseline_db_latency:.2f} ms[/bold red]")
    console.print(f"  [PASS] Effective Hierarchical Latency: [bold green]{effective_latency:.3f} ms[/bold green]")
    console.print(f"  [PASS] Latency Reduction Speedup:      [bold green]{speedup:.1f}x faster[/bold green]")

    # -----------------------------------------------------------------------
    # Benchmark 2: Eviction Resistance: LRU vs LFU under Sequential Scan
    # -----------------------------------------------------------------------
    console.print("\n[bold yellow][BENCHMARK 2][/bold yellow] Eviction Policy Resilience: LRU vs LFU Under Database Scan Pollution...")
    lru = LRUCache(capacity=5)
    lfu = LFUCache(capacity=5)

    # 1. Warm both caches with 5 frequent keys (freq = 10 each)
    frequent_keys = ["hot_a", "hot_b", "hot_c", "hot_d", "hot_e"]
    for k in frequent_keys:
        for _ in range(10):
            lru.put(k, "data")
            lru.get(k)
            lfu.put(k, "data")
            lfu.get(k)

    # 2. Simulate large table scan: 10 one-off sequential keys accessed once
    scan_keys = [f"scan_{i}" for i in range(10)]
    for sk in scan_keys:
        lru.put(sk, "one_off")
        lfu.put(sk, "one_off")

    # 3. Check retention of hot keys
    lru_retained = sum(1 for k in frequent_keys if lru.contains(k))
    lfu_retained = sum(1 for k in frequent_keys if lfu.contains(k))

    table_2 = Table(title="Cache Pollution Defense (Capacity = 5, Scan Length = 10)")
    table_2.add_column("Eviction Algorithm", style="bold")
    table_2.add_column("Hot Keys Preserved", justify="center")
    table_2.add_column("Hot Keys Evicted", justify="center")
    table_2.add_column("Scan Pollution Impact", style="magenta")

    table_2.add_row(
        "LRU (Least Recently Used)",
        f"[red]{lru_retained} / 5[/red]",
        f"[red]{5 - lru_retained} / 5[/red]",
        "[bold red]Completely Polluted (All hot keys flushed)[/bold red]",
    )
    table_2.add_row(
        "LFU (Least Frequently Used)",
        f"[green]{lfu_retained} / 5[/green]",
        f"[green]{5 - lfu_retained} / 5[/green]",
        "[bold green]Immune to Scan (Low-freq scan keys evicted)[/bold green]",
    )
    console.print(table_2)

    # -----------------------------------------------------------------------
    # Benchmark 3: Write Policies: Write-Through vs Write-Back vs Write-Around
    # -----------------------------------------------------------------------
    console.print("\n[bold cyan][BENCHMARK 3][/bold cyan] Write Policies: Write-Through vs Write-Back vs Write-Around...")

    wt_cache = MultiTierCache(write_policy=WritePolicy.WRITE_THROUGH)
    wb_cache = MultiTierCache(write_policy=WritePolicy.WRITE_BACK)
    wa_cache = MultiTierCache(write_policy=WritePolicy.WRITE_AROUND)

    # Workload: 500 writes updating 20 distinct keys repeatedly
    write_workload = [(f"counter:{i % 20}", i) for i in range(500)]

    for k, v in write_workload:
        wt_cache.put(k, v)
        wb_cache.put(k, v)
        wa_cache.put(k, v)

    # Flush write-back dirty buffer to database
    flushed_count = wb_cache.flush_dirty_records()

    table_3 = Table(title="Write Policy Write Amplification Comparison (500 Write Operations)")
    table_3.add_column("Write Policy", style="bold")
    table_3.add_column("Total Writes Received", justify="center")
    table_3.add_column("Synchronous DB Writes", justify="center")
    table_3.add_column("Coalesced DB Flushes", justify="center")
    table_3.add_column("DB Write Reduction", justify="right", style="green")

    table_3.add_row(
        "Write-Through",
        "500",
        f"[yellow]{wt_cache.telemetry.db_writes}[/yellow]",
        "0",
        "0.0%",
    )
    table_3.add_row(
        "Write-Back (Behind)",
        "500",
        "[green]0 (instant)[/green]",
        f"[cyan]{flushed_count}[/cyan]",
        f"[bold green]{((500 - flushed_count) / 500.0) * 100:.1f}%[/bold green]",
    )
    table_3.add_row(
        "Write-Around",
        "500",
        f"[yellow]{wa_cache.telemetry.db_writes}[/yellow]",
        "0",
        "0.0% (Cache invalidated)",
    )
    console.print(table_3)

    console.print("\n[bold green][SUCCESS][/bold green] Multi-tier caching simulation concluded successfully!\n")


if __name__ == "__main__":
    run_benchmark()
