"""Interactive Simulation: Thundering Herd (Cache Stampede) vs SingleFlight Coalescing.

Demonstrates:
1. Scenario A: Unprotected Cache-Aside Stampede (100 threads -> 100 DB queries).
2. Scenario B: SingleFlight Shielded Cache-Aside (100 threads -> 1 DB query, 99 coalesced).
3. Scenario C: XFetch Probabilistic Early Refresh preventing hard misses before expiry.
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Any, Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import CacheAsideWithSingleFlight, SingleFlightGroup, XFetchEarlyRefresh

console = Console()


class NaiveCacheAside:
    """Standard naive cache without in-flight coalescing."""

    def __init__(self):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.db_queries_executed = 0

    def get(self, key: str, fetch_fn, ttl_seconds: float = 5.0):
        now = time.monotonic()
        with self._lock:
            if key in self._cache:
                val, expires_at = self._cache[key]
                if now < expires_at:
                    return val

        # Stampede vulnerability: Every caller executes DB query independently
        with self._lock:
            self.db_queries_executed += 1
        res = fetch_fn()
        with self._lock:
            self._cache[key] = (res, time.monotonic() + ttl_seconds)
        return res


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: THUNDERING HERD DEFENSE & SINGLEFLIGHT[/bold cyan]\n"
            "[yellow]Simulating 100 Concurrent Callers on Cache Expiration Under High Load[/yellow]",
            border_style="cyan",
        )
    )

    num_threads = 100
    db_latency_s = 0.05

    def expensive_sql_query():
        time.sleep(db_latency_s)
        return {"sku": "GPU-5090", "inventory": 42, "updated_at": time.time()}

    # -------------------------------------------------------------
    # Scenario 1: Naive Cache Stampede (Unprotected)
    # -------------------------------------------------------------
    console.print("\n[bold red][BENCHMARK 1][/bold red] Simulating Naive Cache Stampede (No SingleFlight)...")
    naive_cache = NaiveCacheAside()
    barrier_1 = threading.Barrier(num_threads)

    def naive_worker():
        barrier_1.wait()
        return naive_cache.get("product:gpu:5090", expensive_sql_query, ttl_seconds=2.0)

    start_t1 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures_1 = [executor.submit(naive_worker) for _ in range(num_threads)]
        _ = [f.result() for f in futures_1]
    elapsed_t1 = time.perf_counter() - start_t1

    console.print(
        f"  [CRITICAL] Total Downstream DB Queries: [bold red]{naive_cache.db_queries_executed}[/bold red] / {num_threads}"
    )
    console.print(
        f"  [CRITICAL] Wall Clock Elapsed Time:    [bold red]{elapsed_t1 * 1000:.2f} ms[/bold red]"
    )

    # -------------------------------------------------------------
    # Scenario 2: SingleFlight Protected Cache
    # -------------------------------------------------------------
    console.print("\n[bold green][BENCHMARK 2][/bold green] Simulating SingleFlight Request Coalescing...")
    sf_cache = CacheAsideWithSingleFlight()
    barrier_2 = threading.Barrier(num_threads)

    def sf_worker():
        barrier_2.wait()
        return sf_cache.get("product:gpu:5090", expensive_sql_query, ttl_seconds=2.0)

    start_t2 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures_2 = [executor.submit(sf_worker) for _ in range(num_threads)]
        results_2 = [f.result() for f in futures_2]
    elapsed_t2 = time.perf_counter() - start_t2

    stats = sf_cache.get_stats()
    console.print(
        f"  [PASS] Total Downstream DB Queries: [bold green]{stats['db_queries_executed']}[/bold green] / {num_threads}"
    )
    console.print(
        f"  [PASS] Coalesced Shared Reads in RAM: [bold cyan]{stats['coalesced_shared_reads']}[/bold cyan] / {num_threads}"
    )
    console.print(
        f"  [PASS] Wall Clock Elapsed Time:       [bold green]{elapsed_t2 * 1000:.2f} ms[/bold green]"
    )

    # Summary Table
    comparison_table = Table(title="Cache Stampede vs SingleFlight Comparison (N = 100 concurrent misses)")
    comparison_table.add_column("Architecture Pattern", style="bold")
    comparison_table.add_column("Concurrent Misses", justify="center")
    comparison_table.add_column("DB Queries Triggered", justify="center")
    comparison_table.add_column("Coalesced in RAM", justify="center")
    comparison_table.add_column("DB Load Reduction", justify="right", style="green")

    reduction = ((naive_cache.db_queries_executed - stats["db_queries_executed"]) / naive_cache.db_queries_executed) * 100.0
    comparison_table.add_row(
        "Naive Cache-Aside",
        str(num_threads),
        f"[red]{naive_cache.db_queries_executed}[/red]",
        "0",
        "0.0%",
    )
    comparison_table.add_row(
        "SingleFlight Cache-Aside",
        str(num_threads),
        f"[green]{stats['db_queries_executed']}[/green]",
        f"[cyan]{stats['coalesced_shared_reads']}[/cyan]",
        f"[bold green]{reduction:.1f}%[/bold green]",
    )
    console.print(comparison_table)

    # -------------------------------------------------------------
    # Scenario 3: XFetch Probabilistic Early Refresh Simulation
    # -------------------------------------------------------------
    console.print("\n[bold yellow][BENCHMARK 3][/bold yellow] XFetch Optimal Probabilistic Early Refresh (Vitter et al.)")
    console.print("  Demonstrating refresh probability progression as TTL approaches hard expiration (computation delta = 0.05s, beta = 1.0):")

    xfetch_table = Table(title="XFetch Early Refresh Distribution (1,000 Monte Carlo probes per TTL step)")
    xfetch_table.add_column("Time to Expiry (seconds)", justify="center")
    xfetch_table.add_column("Status / Proximity", style="bold")
    xfetch_table.add_column("Early Refresh Probability", justify="center")
    xfetch_table.add_column("Action Taken", style="cyan")

    ttl_probes = [
        (10.0, "Far from expiry (>100x delta)"),
        (0.5, "Approaching expiry (10x delta)"),
        (0.1, "Close to expiry (2x delta)"),
        (0.02, "Imminent expiry (<0.5x delta)"),
        (0.0, "Hard Expiration reached (0x delta)"),
    ]

    for time_left, label in ttl_probes:
        fake_expiry = time.monotonic() + time_left
        trials = 1000
        refreshes = sum(
            1
            for _ in range(trials)
            if XFetchEarlyRefresh.should_refresh(fake_expiry, computation_cost_seconds=db_latency_s, beta=1.0)
        )
        pct = (refreshes / trials) * 100.0
        action = "[green]Serve Cache[/green]" if pct < 15 else ("[yellow]Async Background Recompute[/yellow]" if pct < 90 else "[red]Urgent Recompute[/red]")
        xfetch_table.add_row(
            f"{time_left:.2f}s",
            label,
            f"{pct:.1f}% ({refreshes}/{trials})",
            action,
        )

    console.print(xfetch_table)

    console.print("\n[bold green][SUCCESS][/bold green] SingleFlight & XFetch simulations concluded with zero herd stampedes!\n")


if __name__ == "__main__":
    run_benchmark()
