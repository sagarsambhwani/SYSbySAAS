"""Multi-Threaded Rate Limiting Simulator & Telemetry Dashboard.

Simulates real-world traffic patterns (steady state, sudden burst/flash crowd,
and recovery) across all 4 rate-limiting algorithms, measuring allowed vs
dropped requests and visualizing the differences in real-time.
"""

from concurrent.futures import ThreadPoolExecutor
import random
import time
from typing import Dict, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    BaseRateLimiter,
    TokenBucketRateLimiter,
    LeakyBucketRateLimiter,
    SlidingWindowLogRateLimiter,
    SlidingWindowCounterRateLimiter,
)

console = Console()


def run_traffic_phase(
    phase_name: str,
    duration_seconds: float,
    reqs_per_second: int,
    limiters: Dict[str, BaseRateLimiter],
    concurrency: int = 10,
) -> Dict[str, Dict[str, int]]:
    """Runs a specific traffic load phase across all rate limiters concurrently."""
    console.print(f"\n[bold yellow]>>> Starting Phase: {phase_name} ({reqs_per_second} req/s for {duration_seconds}s)[/bold yellow]")

    phase_stats = {
        name: {"allowed": 0, "rejected": 0, "total": 0}
        for name in limiters.keys()
    }

    start_time = time.monotonic()
    delay_between_requests = 1.0 / reqs_per_second

    def send_request():
        for name, limiter in limiters.items():
            allowed = limiter.allow_request()
            phase_stats[name]["total"] += 1
            if allowed:
                phase_stats[name]["allowed"] += 1
            else:
                phase_stats[name]["rejected"] += 1

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        while (time.monotonic() - start_time) < duration_seconds:
            executor.submit(send_request)
            time.sleep(delay_between_requests + random.uniform(-0.002, 0.002) if delay_between_requests > 0.002 else delay_between_requests)

    # Render Phase Table
    table = Table(title=f"Results: {phase_name}", show_header=True, header_style="bold cyan")
    table.add_column("Algorithm", style="bold white", width=25)
    table.add_column("Total Requests", justify="right")
    table.add_column("Allowed", justify="right", style="green")
    table.add_column("Rejected / Throttled", justify="right", style="red")
    table.add_column("Pass Rate (%)", justify="right", style="magenta")

    for name, stats in phase_stats.items():
        total = stats["total"]
        allowed = stats["allowed"]
        rejected = stats["rejected"]
        pass_rate = (allowed / total * 100) if total > 0 else 0.0
        table.add_row(
            name,
            str(total),
            str(allowed),
            str(rejected),
            f"{pass_rate:.1f}%",
        )

    console.print(table)
    return phase_stats


def main():
    console.print(
        Panel.fit(
            "[bold green]SYSbySAAS: Distributed Rate Limiting Simulation[/bold green]\n"
            "[dim]Comparing Token Bucket, Leaky Bucket, Sliding Window Log & Counter under load[/dim]",
            border_style="green",
        )
    )

    # Configure limiters with uniform limit: ~20 requests per second (or capacity 20)
    limiters: Dict[str, BaseRateLimiter] = {
        "Token Bucket (Cap: 20, Rate: 10/s)": TokenBucketRateLimiter(capacity=20, refill_rate=10),
        "Leaky Bucket (Cap: 20, Rate: 10/s)": LeakyBucketRateLimiter(capacity=20, leak_rate=10),
        "Sliding Window Log (Max: 10, Win: 1s)": SlidingWindowLogRateLimiter(max_requests=10, window_seconds=1.0),
        "Sliding Window Counter (Max: 10, Win: 1s)": SlidingWindowCounterRateLimiter(max_requests=10, window_seconds=1.0),
    }

    # 1. Phase 1: Steady normal traffic (8 req/s - within limits)
    run_traffic_phase("1. Steady Traffic (Normal Load)", duration_seconds=2.0, reqs_per_second=8, limiters=limiters)

    # 2. Phase 2: Sudden Flash Crowd Burst (50 req/s - heavy oversubscription)
    run_traffic_phase("2. Flash Crowd Burst (50 req/s)", duration_seconds=2.0, reqs_per_second=50, limiters=limiters)

    # 3. Phase 3: Post-Burst Recovery (8 req/s)
    run_traffic_phase("3. Traffic Calms Down (Recovery)", duration_seconds=2.0, reqs_per_second=8, limiters=limiters)

    # Overall Summary Table
    console.print("\n[bold green]================ FINAL SUMMARY TELEMETRY ================[/bold green]")
    summary_table = Table(title="Overall Cumulative Telemetry", show_header=True, header_style="bold magenta")
    summary_table.add_column("Algorithm", style="bold white", width=30)
    summary_table.add_column("Total Requests", justify="right")
    summary_table.add_column("Allowed", justify="right", style="green")
    summary_table.add_column("Throttled", justify="right", style="red")
    summary_table.add_column("Final Internal State", justify="left", style="yellow")

    for name, limiter in limiters.items():
        stats = limiter.get_stats()
        state_repr = ", ".join(f"{k}={v}" for k, v in stats.items() if k not in ("algorithm", "total_requests", "allowed_requests", "rejected_requests"))
        summary_table.add_row(
            name,
            str(stats["total_requests"]),
            str(stats["allowed_requests"]),
            str(stats["rejected_requests"]),
            state_repr,
        )

    console.print(summary_table)


if __name__ == "__main__":
    main()
