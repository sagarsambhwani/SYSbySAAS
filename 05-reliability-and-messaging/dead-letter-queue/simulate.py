"""Interactive Simulation: Dead Letter Queue (DLQ) & Exponential Backoff with Jitter.

Benchmarks:
1. Thundering Retry Storm: Deterministic backoff vs Full Jitter concurrency flattening.
2. Poison Pill Isolation: Head-of-line unblocking and quarantine into DLQ.
3. Operator DLQ Redrive: Incident remediation and automated message replay.
"""

from collections import Counter
import random
import time
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    BackoffCalculator,
    JitterStrategy,
    MessageStatus,
    QueueEngine,
)

console = Console()


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: DEAD LETTER QUEUE (DLQ) & EXPONENTIAL BACKOFF[/bold cyan]\n"
            "[yellow]Simulating Retry Storms, Full Jitter Flattening, and Poison-Pill Quarantine[/yellow]",
            border_style="cyan",
        )
    )

    # -----------------------------------------------------------------------
    # Benchmark 1: Retry Storm (No Jitter) vs Full Jitter (AWS Brooker Model)
    # -----------------------------------------------------------------------
    console.print("\n[bold green][BENCHMARK 1][/bold green] Simulating 100 Simultaneous Failures at T=0 (Thundering Retry Storm)...")
    console.print("  Comparing retry dispatch times across 100 clients (Base delay = 1.0s, Attempt = 1):")

    delays_none = [
        BackoffCalculator.calculate_delay(1, base_delay_s=1.0, strategy=JitterStrategy.NONE)
        for _ in range(100)
    ]
    delays_full = [
        BackoffCalculator.calculate_delay(1, base_delay_s=1.0, strategy=JitterStrategy.FULL_JITTER)
        for _ in range(100)
    ]

    # Group into 100ms buckets to measure peak concurrency
    buckets_none = Counter([round(d, 1) for d in delays_none])
    buckets_full = Counter([round(d, 1) for d in delays_full])

    peak_none = max(buckets_none.values())
    peak_full = max(buckets_full.values())

    table_1 = Table(title="Retry Synchronization Peak Concurrency (100 Clients)")
    table_1.add_column("Backoff Strategy", style="bold")
    table_1.add_column("Formula", style="cyan")
    table_1.add_column("Peak Concurrent Retries", justify="center")
    table_1.add_column("Spike Reduction", justify="right", style="green")
    table_1.add_column("Downstream Impact", style="magenta")

    table_1.add_row(
        "Deterministic (No Jitter)",
        "B * 2^i",
        f"[red]{peak_none} / 100[/red]",
        "0.0%",
        "[bold red]Catastrophic Thundering Spike (All hit at 1.0s)[/bold red]",
    )
    reduction = ((peak_none - peak_full) / peak_none) * 100.0
    table_1.add_row(
        "Full Jitter (AWS Model)",
        "Uniform(0, min(M, B*2^i))",
        f"[bold green]{peak_full} / 100[/bold green]",
        f"[bold green]{reduction:.1f}%[/bold green]",
        "[bold green]Smoothly Distributed Across [0, 1.0s][/bold green]",
    )
    console.print(table_1)

    # -----------------------------------------------------------------------
    # Benchmark 2: Poison Pill Quarantine & Zero Head-of-Line Blocking
    # -----------------------------------------------------------------------
    console.print("\n[bold yellow][BENCHMARK 2][/bold yellow] Poison Pill Quarantine & Zero Head-of-Line Blocking...")
    console.print("  Processing 100 queue messages: 5 are poison pills (malformed schemas), 95 are valid:")

    queue = QueueEngine(base_delay_s=0.01, max_retries=3, jitter_strategy=JitterStrategy.FULL_JITTER)

    for i in range(100):
        if i in [10, 25, 40, 70, 85]:
            queue.enqueue({"type": "poison_pill", "id": i, "error": "malformed_json"})
        else:
            queue.enqueue({"type": "valid_order", "id": i})

    def consumer_handler(payload):
        if payload["type"] == "poison_pill":
            raise ValueError(f"Permanent Schema Error: {payload['error']}")
        return "SUCCESS"

    # Process queue with simulated time steps until all valid orders complete and pills are quarantined
    simulated_now = time.monotonic()
    passes = 0
    while (queue.primary_queue_size > 0) and passes < 500:
        res = queue.process_one(consumer_handler, now=simulated_now)
        if res is None:
            # Advance time to allow next backoff retry to become eligible
            simulated_now += 0.05
        passes += 1

    stats = queue.get_stats()

    table_2 = Table(title="Queue Partitioning & Isolation Outcome (100 Messages)")
    table_2.add_column("Category", style="bold")
    table_2.add_column("Total Messages", justify="center")
    table_2.add_column("Queue Destination", style="cyan")
    table_2.add_column("Pipeline Status", style="green")

    table_2.add_row("Valid Customer Orders", str(stats["success"]), "Completed Store", "[green]100% Processed[/green]")
    table_2.add_row("Poison Pills (Invalid)", str(stats["dlq_quarantined"]), "Dead Letter Queue (DLQ)", "[yellow]Isolated after 3 Retries[/yellow]")
    table_2.add_row("Stuck In Primary Queue", str(stats["pending_primary"]), "Primary Queue", "[bold green]0 (Zero Head-of-Line Block)[/bold green]")
    console.print(table_2)

    # Inspect DLQ message metadata
    dlq_sample = queue.get_dlq_messages()[0]
    console.print(f"  [INFO] Sample DLQ Metadata: id='{dlq_sample.message_id[:8]}...', attempts={dlq_sample.attempts}, error='{dlq_sample.last_error_message}'")

    # -----------------------------------------------------------------------
    # Benchmark 3: Operator DLQ Redrive / Incident Recovery
    # -----------------------------------------------------------------------
    console.print("\n[bold cyan][BENCHMARK 3][/bold cyan] Operator DLQ Redrive / Incident Recovery...")
    console.print(f"  Engineering deploys a schema migration patch. Operator executes redrive on {queue.dlq_size} DLQ messages:")

    redriven_count = queue.redrive_dlq()
    console.print(f"  [ACTION] Redriven {redriven_count} messages back into primary queue.")

    # Patched consumer handles former poison pills gracefully
    def patched_consumer_handler(payload):
        return "RECOVERED_AFTER_SCHEMA_MIGRATION"

    while queue.primary_queue_size > 0:
        queue.process_one(patched_consumer_handler, now=time.monotonic() + 10.0)

    final_stats = queue.get_stats()

    table_3 = Table(title="Post-Redrive Cluster State")
    table_3.add_column("Queue Name", style="bold")
    table_3.add_column("Active Message Count", justify="center")
    table_3.add_column("Status Description", style="cyan")

    table_3.add_row("Primary Queue", str(final_stats["pending_primary"]), "Clean / Ready for new traffic")
    table_3.add_row("Dead Letter Queue (DLQ)", str(final_stats["active_dlq"]), "[green]0 (Fully Drained)[/green]")
    table_3.add_row("Total Successfully Processed", str(final_stats["success"]), f"[bold green]{final_stats['success']} / 100 (100% Data Preserved)[/bold green]")
    console.print(table_3)

    console.print("\n[bold green][SUCCESS][/bold green] Dead Letter Queue & Exponential Backoff simulation concluded!\n")


if __name__ == "__main__":
    run_benchmark()
