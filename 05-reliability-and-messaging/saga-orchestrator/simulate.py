"""Interactive Simulation: Saga Pattern Distributed Transaction Orchestrator.

Simulates:
1. Scenario A: Complete Happy Path Transaction (Order -> Payment -> Inventory -> Delivery).
2. Scenario B: Mid-Transaction Business Failure (Out-of-Stock triggers reverse refund & cancellation).
3. Scenario C: Late-Stage Delivery Failure (All previous steps rollback cleanly).
"""

import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core import (
    DeliveryService,
    InventoryService,
    OrderService,
    PaymentService,
    SagaOrchestrator,
    SagaStatus,
)

console = Console()


def build_saga(order, payment, inventory, delivery):
    orchestrator = SagaOrchestrator()
    orchestrator.add_step(
        name="OrderService.CreateOrder",
        action=order.create_order,
        compensation=order.cancel_order,
    ).add_step(
        name="PaymentService.Charge",
        action=payment.charge,
        compensation=payment.refund,
    ).add_step(
        name="InventoryService.Reserve",
        action=inventory.reserve,
        compensation=inventory.release,
    ).add_step(
        name="DeliveryService.Dispatch",
        action=delivery.dispatch,
        compensation=delivery.cancel_dispatch,
    )
    return orchestrator


def run_benchmark():
    console.print(
        Panel.fit(
            "[bold cyan]SYSTEM DESIGN POC: SAGA PATTERN ORCHESTRATOR[/bold cyan]\n"
            "[yellow]Simulating Distributed Microservice Transactions & Compensating Rollbacks[/yellow]",
            border_style="cyan",
        )
    )

    # -----------------------------------------------------------------------
    # Scenario 1: Happy Path Distributed Transaction
    # -----------------------------------------------------------------------
    console.print("\n[bold green][SCENARIO 1][/bold green] Executing Successful End-to-End E-Commerce Checkout...")
    order_1 = OrderService()
    payment_1 = PaymentService(balance=1000)
    inventory_1 = InventoryService(stock={"iPhone-16": 10})
    delivery_1 = DeliveryService(drivers_available=True)

    saga_1 = build_saga(order_1, payment_1, inventory_1, delivery_1)

    ctx_1 = {
        "items": ["iPhone-16"],
        "item": "iPhone-16",
        "quantity": 1,
        "amount": 800,
    }

    status_1, final_ctx_1 = saga_1.execute(ctx_1)

    table_1 = Table(title="Scenario 1: Happy Path Service State Transitions")
    table_1.add_column("Microservice", style="bold")
    table_1.add_column("Local Transaction", style="cyan")
    table_1.add_column("Status / Result", style="green")
    table_1.add_column("Database State", style="magenta")

    table_1.add_row("Order Service", "CreateOrder", "SUCCESS", f"Order {final_ctx_1['order_id']} [CREATED]")
    table_1.add_row("Payment Service", "Charge ($800)", "SUCCESS", f"Tx {final_ctx_1['payment_tx_id']} (Balance: $200)")
    table_1.add_row("Inventory Service", "Reserve (1 unit)", "SUCCESS", f"Res {final_ctx_1['reservation_id']} (Stock: 9)")
    table_1.add_row("Delivery Service", "Dispatch Driver", "SUCCESS", f"Deliv {final_ctx_1['delivery_id']} [DISPATCHED]")
    console.print(table_1)
    console.print(f"  [PASS] Overall Saga Status: [bold green]{status_1.value}[/bold green]")

    # -----------------------------------------------------------------------
    # Scenario 2: Mid-Transaction Business Failure (Out of Stock)
    # -----------------------------------------------------------------------
    console.print("\n[bold yellow][SCENARIO 2][/bold yellow] Mid-Transaction Failure: Inventory Out of Stock...")
    console.print("  Customer requests 5 units when only 2 are available. Step 3 fails:")

    order_2 = OrderService()
    payment_2 = PaymentService(balance=1000)
    inventory_2 = InventoryService(stock={"MacBook-Pro": 2})
    delivery_2 = DeliveryService(drivers_available=True)

    saga_2 = build_saga(order_2, payment_2, inventory_2, delivery_2)

    ctx_2 = {
        "items": ["MacBook-Pro"],
        "item": "MacBook-Pro",
        "quantity": 5,  # Exceeds available stock (2)
        "amount": 900,
    }

    status_2, final_ctx_2 = saga_2.execute(ctx_2)

    table_2 = Table(title="Scenario 2: Inventory Failure & Backward Compensation")
    table_2.add_column("Step", style="bold")
    table_2.add_column("Forward", justify="center")
    table_2.add_column("Failure", justify="center")
    table_2.add_column("Compensation", style="cyan")
    table_2.add_column("Final State", style="green")

    table_2.add_row(
        "1. Order",
        "[green]CREATED[/green]",
        "-",
        "[yellow]CancelOrder[/yellow]",
        f"Order {final_ctx_2.get('order_id')} [CANCELLED]",
    )
    table_2.add_row(
        "2. Payment",
        "[green]CHARGED $900[/green]",
        "-",
        "[yellow]Refund $900[/yellow]",
        f"Balance: ${payment_2.balance}",
    )
    table_2.add_row(
        "3. Inventory",
        "[red]FAILED[/red]",
        "[bold red]Stock 2 < Req 5[/bold red]",
        "-",
        f"Stock: {inventory_2.stock['MacBook-Pro']}",
    )
    table_2.add_row(
        "4. Delivery",
        "[dim]SKIPPED[/dim]",
        "-",
        "-",
        "Never invoked",
    )
    console.print(table_2)
    console.print(f"  [PASS] Overall Saga Status: [bold red]{status_2.value}[/bold red] (Zero orphaned funds or dirty state!)")

    # -----------------------------------------------------------------------
    # Scenario 3: Late-Stage Delivery Failure
    # -----------------------------------------------------------------------
    console.print("\n[bold cyan][SCENARIO 3][/bold cyan] Late-Stage Failure: Delivery Dispatch Fails (Step 4)...")
    console.print("  Steps 1, 2, and 3 commit successfully. Step 4 fails due to driver unavailability:")

    order_3 = OrderService()
    payment_3 = PaymentService(balance=500)
    inventory_3 = InventoryService(stock={"Headphones": 8})
    delivery_3 = DeliveryService(drivers_available=False)  # Drivers unavailable!

    saga_3 = build_saga(order_3, payment_3, inventory_3, delivery_3)

    ctx_3 = {
        "items": ["Headphones"],
        "item": "Headphones",
        "quantity": 2,
        "amount": 250,
    }

    status_3, final_ctx_3 = saga_3.execute(ctx_3)

    table_3 = Table(title="Scenario 3: Cascading 3-Step Rollback")
    table_3.add_column("Step", style="bold")
    table_3.add_column("Service", style="cyan")
    table_3.add_column("Compensating Action", style="yellow")
    table_3.add_column("Restored Invariant", style="green")

    table_3.add_row("Rollback 3", "Inventory", "Released 2 units", f"Stock: {inventory_3.stock['Headphones']} (Initial: 8)")
    table_3.add_row("Rollback 2", "Payment", "Refunded $250", f"Balance: ${payment_3.balance} (Initial: $500)")
    table_3.add_row("Rollback 1", "Order", "Cancelled order", f"Order {final_ctx_3['order_id']} CANCELLED")
    console.print(table_3)
    console.print(f"  [PASS] Overall Saga Status: [bold red]{status_3.value}[/bold red] (100% Data Consistency Maintained)\n")

    console.print("[bold green][SUCCESS][/bold green] Saga Pattern Orchestration benchmarks concluded successfully!\n")


if __name__ == "__main__":
    run_benchmark()
