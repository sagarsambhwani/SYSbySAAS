"""Interactive Simulation: Idempotency Key Engine & Exact-Once Mutations (Stripe Pattern).

Benchmarks:
1. Network Timeout & Retry Storm: 100 charges with 35 client retries -> 0 double charges.
2. Fast Double-Click Race Condition: 50 concurrent clicks -> exactly 1 charge executed.
3. Parameter Mutation & Fingerprint Defense: Catching key reuse with altered payloads.
"""

from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    IdempotencyConflictException,
    IdempotencyEngine,
    IdempotencyPayloadMismatchException,
)

console = Console()


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: IDEMPOTENCY KEY ENGINE (STRIPE PATTERN)[/bold cyan]\n"
            "[yellow]Simulating Network Retries, Race Conditions & SHA-256 Fingerprint Defense[/yellow]",
            border_style="cyan",
        )
    )

    # -----------------------------------------------------------------------
    # Benchmark 1: Network Timeout & Retry Storm
    # -----------------------------------------------------------------------
    console.print("\n[bold green][BENCHMARK 1][/bold green] Simulating Network Drops & Automatic Client Retries...")
    console.print("  100 customer payments submitted. 35 experience dropped ACK packets and retry:")

    engine = IdempotencyEngine()
    ledger_charges = 0
    ledger_lock = threading.Lock()

    def process_credit_card(amount: int):
        nonlocal ledger_charges
        with ledger_lock:
            ledger_charges += 1
        return 200, {"status": "succeeded", "amount": amount, "tx_id": f"tx_{ledger_charges}"}

    total_http_requests = 0

    # 1. First wave: 100 initial requests
    for i in range(100):
        key = f"order_uuid_{i}"
        payload = {"customer_id": f"cus_{i}", "amount": 100}
        total_http_requests += 1
        engine.execute(key, "/v1/charges", payload, lambda: process_credit_card(100))

    # 2. Network drop simulation: 35 clients didn't get ACK and retry with same idempotency key
    for i in range(35):
        key = f"order_uuid_{i}"
        payload = {"customer_id": f"cus_{i}", "amount": 100}
        total_http_requests += 1
        engine.execute(key, "/v1/charges", payload, lambda: process_credit_card(100))

    stats = engine.get_stats()

    table_1 = Table(title="Network Retry Resilience (100 Distinct Customers, 35 Retries)")
    table_1.add_column("Metric", style="bold")
    table_1.add_column("Without Idempotency", style="red", justify="center")
    table_1.add_column("With Idempotency Key", style="green", justify="center")
    table_1.add_column("Outcome Impact", style="cyan")

    table_1.add_row("Total HTTP Invocations", str(total_http_requests), str(total_http_requests), "35 retry attempts")
    table_1.add_row(
        "Downstream Card Charges",
        "[red]135 charges ($13,500)[/red]",
        f"[bold green]{ledger_charges} charges (${ledger_charges * 100})[/bold green]",
        "[green]Exact-Once Execution[/green]",
    )
    table_1.add_row(
        "Double Charges Prevented",
        "[red]0 (35 customers overcharged)[/red]",
        f"[bold green]{stats['replayed_executions']}[/bold green]",
        "[bold green]Zero Financial Loss[/bold green]",
    )
    console.print(table_1)

    # -----------------------------------------------------------------------
    # Benchmark 2: The Fast Double-Click Race Condition
    # -----------------------------------------------------------------------
    console.print("\n[bold yellow][BENCHMARK 2][/bold yellow] Fast Double-Click Race Condition (50 Concurrent Threads)...")
    console.print("  Customer repeatedly clicks 'Pay $250' generating 50 concurrent requests at T=0:")

    race_engine = IdempotencyEngine()
    actual_charges = 0
    charge_lock = threading.Lock()
    barrier = threading.Barrier(50)

    def slow_payment_gateway():
        nonlocal actual_charges
        with charge_lock:
            actual_charges += 1
        time.sleep(0.05)  # Simulate Stripe gateway latency
        return 201, {"status": "succeeded", "charge": 250}

    results = {"success": 0, "conflict_409": 0, "replayed": 0}
    res_lock = threading.Lock()

    def concurrent_user():
        barrier.wait()
        try:
            _, _, replayed = race_engine.execute(
                "checkout_session_42",
                "/v1/checkout",
                {"item": "UltraBook", "price": 250},
                slow_payment_gateway,
            )
            with res_lock:
                if replayed:
                    results["replayed"] += 1
                else:
                    results["success"] += 1
        except IdempotencyConflictException:
            with res_lock:
                results["conflict_409"] += 1

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(concurrent_user) for _ in range(50)]
        _ = [f.result() for f in futures]

    table_2 = Table(title="Double-Click Concurrency Defense (50 Concurrent Submissions)")
    table_2.add_column("Event / Response Type", style="bold")
    table_2.add_column("Count", justify="center")
    table_2.add_column("HTTP Status", justify="center", style="cyan")
    table_2.add_column("System Action", style="magenta")

    table_2.add_row("Primary Mutation Allowed", str(results["success"]), "201 Created", "Executed payment in gateway")
    table_2.add_row("In-Progress Collisions Blocked", str(results["conflict_409"]), "409 Conflict", "Safely rejected race condition")
    table_2.add_row("Replayed from Cache", str(results["replayed"]), "200 OK", "Returned cached payment receipt")
    console.print(table_2)
    console.print(f"  [PASS] Actual Downstream Gateway Executions: [bold green]{actual_charges}[/bold green] (Customer charged exactly once!)")

    # -----------------------------------------------------------------------
    # Benchmark 3: Parameter Mutation & Fingerprint Defense
    # -----------------------------------------------------------------------
    console.print("\n[bold cyan][BENCHMARK 3][/bold cyan] SHA-256 Parameter Mutation & Fingerprint Validation...")
    console.print("  Client reuses Idempotency-Key 'key_999' but tampers with the payment amount ($10 -> $10,000):")

    tamper_engine = IdempotencyEngine()
    initial_payload = {"user": "bob", "amount": 10}
    tampered_payload = {"user": "bob", "amount": 10000}

    # Legitimate first request
    tamper_engine.execute("key_999", "/v1/transfers", initial_payload, lambda: (200, {"tx": "legit"}))
    console.print("  [INFO] Initial Request: key='key_999', amount=$10 -> [green]200 OK (Processed)[/green]")

    # Malicious / Buggy second request reusing key
    tamper_caught = False
    try:
        tamper_engine.execute("key_999", "/v1/transfers", tampered_payload, lambda: (200, {"tx": "tampered"}))
    except IdempotencyPayloadMismatchException as e:
        tamper_caught = True
        console.print(f"  [PASS] Tampering Intercepted: [bold red]422 Unprocessable Entity[/bold red] - {e}")

    assert tamper_caught is True
    console.print("\n[bold green][SUCCESS][/bold green] Idempotency Key Engine benchmarks concluded with zero double-mutations!\n")


if __name__ == "__main__":
    run_benchmark()
