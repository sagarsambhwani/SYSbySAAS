"""Consistent Hashing vs Naive Modulo Simulation & Churn Benchmark.

Demonstrates:
1. The Crash Test: Key invalidation / churn when a node dies (Modulo vs Ring).
2. Cluster Scale-Out: Key migration when expanding cluster capacity.
3. Hotspot Elimination: Standard deviation of key allocation (1 vnode vs 150 vnodes).
"""

import hashlib
import statistics
from typing import Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import ConsistentHashRing

console = Console()


def naive_modulo_hash(key: str, num_nodes: int) -> int:
    """Naive modulo hashing: hash(key) % N"""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % num_nodes


def benchmark_crash_test(keys: List[str]):
    """Benchmark 1: Compare key invalidation when 1 node crashes out of 4."""
    console.print("\n[bold yellow]>>> Benchmark 1: The Crash Test (Node-4 Crashes)[/bold yellow]")
    console.print("[dim]Comparing key movement between Naive Modulo (4 -> 3) and Consistent Hashing (4 -> 3)[/dim]")

    # 1. Modulo Hashing Before & After
    modulo_before = [naive_modulo_hash(k, 4) for k in keys]
    modulo_after = [naive_modulo_hash(k, 3) for k in keys]
    modulo_moved = sum(1 for b, a in zip(modulo_before, modulo_after) if b != a)
    modulo_churn_pct = (modulo_moved / len(keys)) * 100

    # 2. Consistent Hashing Before & After
    nodes = ["Node-1", "Node-2", "Node-3", "Node-4"]
    ring = ConsistentHashRing(nodes=nodes, vnodes=150)
    ring_before = [ring.get_node(k) for k in keys]

    ring.remove_node("Node-4")
    ring_after = [ring.get_node(k) for k in keys]
    ring_moved = sum(1 for b, a in zip(ring_before, ring_after) if b != a)
    ring_churn_pct = (ring_moved / len(keys)) * 100

    table = Table(title="Crash Test Results (4 Nodes -> 3 Nodes)", show_header=True, header_style="bold cyan")
    table.add_column("Strategy", style="bold white", width=26)
    table.add_column("Total Keys", justify="right")
    table.add_column("Keys Moved / Invalidated", justify="right", style="red")
    table.add_column("Keys Preserved (Cache Hit)", justify="right", style="green")
    table.add_column("Churn Rate (%)", justify="right", style="magenta")

    table.add_row(
        "Naive Modulo (hash % N)",
        str(len(keys)),
        str(modulo_moved),
        str(len(keys) - modulo_moved),
        f"{modulo_churn_pct:.1f}%",
    )
    table.add_row(
        "Consistent Hashing (vnodes=150)",
        str(len(keys)),
        str(ring_moved),
        str(len(keys) - ring_moved),
        f"{ring_churn_pct:.1f}%",
    )

    console.print(table)
    console.print(
        f"[bold green][PASS] Consistent Hashing preserved {len(keys) - ring_moved} keys ({100 - ring_churn_pct:.1f}% cache hit rate) "
        f"compared to only {len(keys) - modulo_moved} keys ({100 - modulo_churn_pct:.1f}%) with Modulo![/bold green]"
    )


def benchmark_scale_out(keys: List[str]):
    """Benchmark 2: Compare key migration when scaling out from 4 to 5 nodes."""
    console.print("\n[bold yellow]>>> Benchmark 2: Cluster Scale-Out (Adding Node-5)[/bold yellow]")

    # 1. Modulo Hashing (4 -> 5)
    modulo_before = [naive_modulo_hash(k, 4) for k in keys]
    modulo_after = [naive_modulo_hash(k, 5) for k in keys]
    modulo_moved = sum(1 for b, a in zip(modulo_before, modulo_after) if b != a)
    modulo_churn_pct = (modulo_moved / len(keys)) * 100

    # 2. Consistent Hashing (4 -> 5)
    nodes = ["Node-1", "Node-2", "Node-3", "Node-4"]
    ring = ConsistentHashRing(nodes=nodes, vnodes=150)
    ring_before = [ring.get_node(k) for k in keys]

    ring.add_node("Node-5")
    ring_after = [ring.get_node(k) for k in keys]
    ring_moved = sum(1 for b, a in zip(ring_before, ring_after) if b != a)
    ring_churn_pct = (ring_moved / len(keys)) * 100

    table = Table(title="Scale-Out Results (4 Nodes -> 5 Nodes)", show_header=True, header_style="bold cyan")
    table.add_column("Strategy", style="bold white", width=26)
    table.add_column("Total Keys", justify="right")
    table.add_column("Keys Migrated", justify="right", style="yellow")
    table.add_column("Keys Unmoved", justify="right", style="green")
    table.add_column("Migration Rate (%)", justify="right", style="magenta")

    table.add_row(
        "Naive Modulo (hash % N)",
        str(len(keys)),
        str(modulo_moved),
        str(len(keys) - modulo_moved),
        f"{modulo_churn_pct:.1f}%",
    )
    table.add_row(
        "Consistent Hashing (vnodes=150)",
        str(len(keys)),
        str(ring_moved),
        str(len(keys) - ring_moved),
        f"{ring_churn_pct:.1f}%",
    )

    console.print(table)


def benchmark_vnode_dispersion(keys: List[str]):
    """Benchmark 3: Measure key variance & hotspot reduction with vnodes=1 vs vnodes=150."""
    console.print("\n[bold yellow]>>> Benchmark 3: Hotspot Reduction (1 vnode vs 150 vnodes)[/bold yellow]")

    nodes = ["Node-A", "Node-B", "Node-C", "Node-D"]

    # Test with 1 vnode (Naive Ring)
    ring_single = ConsistentHashRing(nodes=nodes, vnodes=1)
    dist_single = ring_single.get_distribution(keys)
    vals_single = list(dist_single.values())
    std_single = statistics.stdev(vals_single)

    # Test with 150 vnodes (Production Ring)
    ring_multi = ConsistentHashRing(nodes=nodes, vnodes=150)
    dist_multi = ring_multi.get_distribution(keys)
    vals_multi = list(dist_multi.values())
    std_multi = statistics.stdev(vals_multi)

    table = Table(title="Key Distribution & Hotspot Comparison (10,000 Keys across 4 Nodes)", show_header=True, header_style="bold magenta")
    table.add_column("Node Name", style="bold white", width=12)
    table.add_column("Without vnodes (V=1)", justify="right", style="red")
    table.add_column("With vnodes (V=150)", justify="right", style="green")
    table.add_column("Ideal Share", justify="right", style="cyan")

    ideal_share = len(keys) // len(nodes)
    for node in nodes:
        c1 = dist_single.get(node, 0)
        c2 = dist_multi.get(node, 0)
        table.add_row(node, f"{c1} ({c1/len(keys)*100:.1f}%)", f"{c2} ({c2/len(keys)*100:.1f}%)", f"{ideal_share} (25.0%)")

    console.print(table)

    summary_table = Table(title="Variance & Skew Metrics", show_header=True, header_style="bold yellow")
    summary_table.add_column("Configuration", style="bold white")
    summary_table.add_column("Min Keys", justify="right")
    summary_table.add_column("Max Keys", justify="right")
    summary_table.add_column("Standard Deviation", justify="right", style="magenta")

    summary_table.add_row("Without vnodes (V=1)", str(min(vals_single)), str(max(vals_single)), f"{std_single:.1f}")
    summary_table.add_row("With vnodes (V=150)", str(min(vals_multi)), str(max(vals_multi)), f"{std_multi:.1f}")

    console.print(summary_table)


def main():
    console.print(
        Panel.fit(
            "[bold green]SYSbySAAS: Consistent Hashing vs Naive Modulo Simulation[/bold green]\n"
            "[dim]Proving minimal key migration and hotspot elimination under cluster churn[/dim]",
            border_style="green",
        )
    )

    # 10,000 realistic keys (user sessions, document IDs)
    keys = [f"user_session_id_{i:06d}" for i in range(10000)]

    benchmark_crash_test(keys)
    benchmark_scale_out(keys)
    benchmark_vnode_dispersion(keys)


if __name__ == "__main__":
    main()
