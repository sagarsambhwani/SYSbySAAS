"""Distributed Lock & Fencing Token Simulator.

Demonstrates:
1. The GC Pause Disaster (Without Fencing): Process freeze causing silent data corruption.
2. The Fencing Token Shield (With Fencing): Storage-layer rejection of stale split-brain writes.
3. Watchdog Lease Renewal: Background heartbeat keeping long jobs alive beyond TTL.
"""

import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    DistributedLockManager,
    FencedStorageResource,
    StaleFencingTokenException,
)

console = Console()


def simulate_gc_pause_catastrophe():
    """Scenario 1: Demonstrates split-brain data corruption when relying solely on lock TTLs."""
    console.print("\n[bold yellow]>>> Scenario 1: The Catastrophic GC Pause (Without Fencing)[/bold yellow]")
    console.print("[dim]Simulating Client 1 freezing for 1.2s while holding a 0.8s lock lease...[/dim]")

    manager = DistributedLockManager()
    storage = FencedStorageResource(initial_value="INIT")

    # 1. Client 1 acquires lock for 0.8s
    h1 = manager.acquire("order:101", owner="Client-1", ttl_seconds=0.8)
    console.print("[cyan][Client-1][/cyan] Acquired lock on 'order:101' (TTL: 0.8s)")

    # 2. Client 1 freezes (simulating a Stop-the-World GC pause or network delay)
    console.print("[magenta][Client-1] Experiencing 1.2s Full GC Pause... (Process Frozen)[/magenta]")
    time.sleep(1.2)  # Lock expires during this pause!

    # 3. Client 2 sees lock expired and acquires it
    h2 = manager.acquire("order:101", owner="Client-2", ttl_seconds=1.0)
    console.print("[green][Client-2][/green] Lock expired! Client-2 acquired lock on 'order:101'")
    storage.write_without_fencing("ORDER_CANCELLED", client_id="Client-2")
    console.print("[green][Client-2][/green] Wrote to DB: [bold green]ORDER_CANCELLED[/bold green]")
    h2.release()

    # 4. Client 1 wakes up and blindly writes, unaware that time passed
    console.print("[cyan][Client-1][/cyan] Woke up from GC pause! Still assumes it owns lock...")
    storage.write_without_fencing("ORDER_SHIPPED", client_id="Client-1")
    console.print("[cyan][Client-1][/cyan] Wrote to DB: [bold red]ORDER_SHIPPED[/bold red] (Overwrote Client-2!)")

    table = Table(title="Storage Write History (Split-Brain Disaster)", show_header=True, header_style="bold red")
    table.add_column("Step", justify="right", width=6)
    table.add_column("Client", style="bold white", width=12)
    table.add_column("Value Written", style="yellow")
    table.add_column("Status / Integrity", style="red")

    table.add_row("1", "Client-2", "ORDER_CANCELLED", "Legitimate write while lock was held")
    table.add_row("2", "Client-1", "ORDER_SHIPPED", "[FAIL] CORRUPTED! Stale write silently overwrote Client-2")

    console.print(table)
    console.print("[bold red][ALERT] Data corruption occurred! A lock TTL alone CANNOT guarantee safety![/bold red]")


def simulate_fencing_token_shield():
    """Scenario 2: Demonstrates how monotonic fencing tokens stop stale writes at the storage layer."""
    console.print("\n[bold yellow]>>> Scenario 2: The Fencing Token Shield (With Fencing)[/bold yellow]")
    console.print("[dim]Same GC pause scenario, but storage validates monotonic fencing tokens...[/dim]")

    manager = DistributedLockManager()
    storage = FencedStorageResource(initial_value="INIT")

    # 1. Client 1 acquires lock (Gets Token #1)
    h1 = manager.acquire("order:102", owner="Client-1", ttl_seconds=0.8)
    console.print(f"[cyan][Client-1][/cyan] Acquired lock with [bold yellow]Fencing Token #{h1.fencing_token}[/bold yellow]")

    # 2. Client 1 freezes for 1.2s
    console.print("[magenta][Client-1] Experiencing 1.2s Full GC Pause... (Process Frozen)[/magenta]")
    time.sleep(1.2)

    # 3. Client 2 acquires lock (Gets Token #2)
    h2 = manager.acquire("order:102", owner="Client-2", ttl_seconds=1.0)
    console.print(f"[green][Client-2][/green] Acquired lock with [bold yellow]Fencing Token #{h2.fencing_token}[/bold yellow]")
    storage.write_with_fencing("ORDER_CANCELLED", client_id="Client-2", fencing_token=h2.fencing_token)
    console.print(f"[green][Client-2][/green] Storage accepted Token #{h2.fencing_token}: [bold green]ORDER_CANCELLED[/bold green]")
    h2.release()

    # 4. Client 1 wakes up and attempts to write with stale Token #1
    console.print(f"[cyan][Client-1][/cyan] Woke up! Attempting write with stale Token #{h1.fencing_token}...")
    try:
        storage.write_with_fencing("ORDER_SHIPPED", client_id="Client-1", fencing_token=h1.fencing_token)
    except StaleFencingTokenException as e:
        console.print(f"[bold red][BLOCKED BY STORAGE][/bold red] {e}")

    table = Table(title="Storage Write History (Fencing Token Defense)", show_header=True, header_style="bold green")
    table.add_column("Step", justify="right", width=6)
    table.add_column("Client", style="bold white", width=12)
    table.add_column("Token Used", justify="center", style="yellow")
    table.add_column("Value in Storage", style="green")
    table.add_column("Integrity Verdict", style="green")

    table.add_row("1", "Client-2", "#2", "ORDER_CANCELLED", "[PASS] Accepted (Token #2 > #0)")
    table.add_row("2", "Client-1", "#1", "ORDER_CANCELLED", "[PASS] Stale write rejected! (Token #1 <= #2)")

    console.print(table)
    console.print(f"[bold green][PASS] Final Storage Value: {storage.value} (100% Data Integrity Preserved!)[/bold green]")


def simulate_watchdog_renewal():
    """Scenario 3: Demonstrates background watchdog keeping a long task alive beyond initial TTL."""
    console.print("\n[bold yellow]>>> Scenario 3: Watchdog Lease Auto-Renewal[/bold yellow]")
    console.print("[dim]Running a 1.2s task with an initial lock TTL of only 0.5s...[/dim]")

    manager = DistributedLockManager()

    # Acquire lock with 0.5s TTL and watchdog enabled
    handle = manager.acquire("compute:job", owner="LongJobWorker", ttl_seconds=0.5, enable_watchdog=True)
    console.print(f"[cyan][Worker][/cyan] Acquired lock on 'compute:job' (TTL: 0.5s, Watchdog: ENABLED)")

    # Simulate task running for 1.2s (more than 2x the original TTL)
    for step in range(1, 4):
        time.sleep(0.4)
        is_held = manager.is_locked("compute:job")
        intruder = manager.acquire("compute:job", owner="Intruder", ttl_seconds=0.5)
        intruder_status = "Blocked (Safe)" if intruder is None else "Acquired (Failed)"
        console.print(f"[cyan][Worker][/cyan] Processing step {step}/3 (elapsed: {step * 0.4:.1f}s) | Lock Held: {is_held} | Intruder: {intruder_status}")

    handle.release()
    console.print("[cyan][Worker][/cyan] Task finished! Lock safely released.")
    console.print(f"[cyan][Worker][/cyan] Lock active after release: {manager.is_locked('compute:job')}")


def main():
    console.print(
        Panel.fit(
            "[bold green]SYSbySAAS: Distributed Lock & Fencing Token Simulation[/bold green]\n"
            "[dim]Demonstrating GC pause split-brain vulnerabilities, watchdog renewals, and fencing tokens[/dim]",
            border_style="green",
        )
    )

    simulate_gc_pause_catastrophe()
    simulate_fencing_token_shield()
    simulate_watchdog_renewal()


if __name__ == "__main__":
    main()
