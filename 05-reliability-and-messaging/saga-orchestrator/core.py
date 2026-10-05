"""Saga Pattern Orchestrator for Distributed Microservice Transactions.

Implements:
1. SagaStep: Encapsulates forward local transaction T_i and semantic compensation C_i.
2. SagaLog: Append-only state transition journal for auditing, recovery, and replay.
3. SagaOrchestrator: Centralized coordinator executing forward workflows and orchestrating
   reverse compensation rollbacks on failures.
4. Microservice Simulators: Order, Payment, Inventory, and Delivery services with isolated state.
"""

from dataclasses import dataclass, field
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid


class SagaStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    COMPENSATING = "COMPENSATING"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED_COMPENSATION = "FAILED_COMPENSATION"  # Critical operator intervention required


class StepStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    FAILED = "FAILED"


@dataclass
class SagaStep:
    """Represents a discrete step in a distributed saga."""
    name: str
    action: Callable[[Dict[str, Any]], Any]
    compensation: Optional[Callable[[Dict[str, Any]], Any]] = None
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    executed_at: float = 0.0
    compensated_at: float = 0.0


@dataclass
class SagaLogEntry:
    timestamp: float
    saga_id: str
    event: str
    step_name: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class SagaLog:
    """Thread-safe append-only journal of all saga transitions."""

    def __init__(self):
        self._entries: List[SagaLogEntry] = []
        self._lock = threading.Lock()

    def log(self, saga_id: str, event: str, step_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._entries.append(
                SagaLogEntry(
                    timestamp=time.time(),
                    saga_id=saga_id,
                    event=event,
                    step_name=step_name,
                    details=details,
                )
            )

    def get_entries(self, saga_id: Optional[str] = None) -> List[SagaLogEntry]:
        with self._lock:
            if saga_id:
                return [e for e in self._entries if e.saga_id == saga_id]
            return list(self._entries)


class SagaOrchestrator:
    """Centralized Saga Orchestrator executing forward actions and backward compensations."""

    def __init__(self, saga_id: Optional[str] = None, saga_log: Optional[SagaLog] = None):
        self.saga_id = saga_id or str(uuid.uuid4())
        self.log = saga_log or SagaLog()
        self.steps: List[SagaStep] = []
        self.status = SagaStatus.PENDING
        self.context: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def add_step(
        self,
        name: str,
        action: Callable[[Dict[str, Any]], Any],
        compensation: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> "SagaOrchestrator":
        """Appends a local transaction step and its compensating transaction."""
        self.steps.append(SagaStep(name=name, action=action, compensation=compensation))
        return self

    def execute(self, initial_context: Optional[Dict[str, Any]] = None) -> Tuple[SagaStatus, Dict[str, Any]]:
        """Executes the Saga workflow.

        Forward flow: T_1 -> T_2 -> ... -> T_n
        Rollback flow (if T_k fails): C_{k-1} -> C_{k-2} -> ... -> C_1
        """
        with self._lock:
            self.context = dict(initial_context or {})
            self.status = SagaStatus.RUNNING
            self.log.log(self.saga_id, "SAGA_STARTED", details={"initial_context": self.context})

        completed_steps: List[SagaStep] = []
        failed_step: Optional[SagaStep] = None

        # 1. Forward Execution Phase
        for step in self.steps:
            step.status = StepStatus.EXECUTING
            step.executed_at = time.time()
            self.log.log(self.saga_id, "STEP_STARTED", step_name=step.name)

            try:
                result = step.action(self.context)
                step.result = result
                step.status = StepStatus.COMPLETED
                completed_steps.append(step)
                self.log.log(self.saga_id, "STEP_COMPLETED", step_name=step.name, details={"result": str(result)})

            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                failed_step = step
                self.log.log(self.saga_id, "STEP_FAILED", step_name=step.name, details={"error": str(e)})
                break

        # If all steps succeeded, Saga is COMPLETE
        if failed_step is None:
            with self._lock:
                self.status = SagaStatus.SUCCESS
                self.log.log(self.saga_id, "SAGA_COMPLETED_SUCCESS", details={"final_context": self.context})
            return self.status, self.context

        # 2. Backward Compensation Phase (Rollback)
        with self._lock:
            self.status = SagaStatus.COMPENSATING
            self.log.log(self.saga_id, "SAGA_COMPENSATION_STARTED", details={"triggered_by": failed_step.name})

        compensation_failures = 0

        # Compensate completed steps in REVERSE order
        for step in reversed(completed_steps):
            if step.compensation is None:
                continue

            step.status = StepStatus.COMPENSATING
            self.log.log(self.saga_id, "COMPENSATION_STARTED", step_name=step.name)

            try:
                step.compensation(self.context)
                step.status = StepStatus.COMPENSATED
                step.compensated_at = time.time()
                self.log.log(self.saga_id, "COMPENSATION_COMPLETED", step_name=step.name)
            except Exception as e:
                compensation_failures += 1
                self.log.log(
                    self.saga_id,
                    "COMPENSATION_FAILED",
                    step_name=step.name,
                    details={"error": str(e)},
                )

        with self._lock:
            if compensation_failures > 0:
                self.status = SagaStatus.FAILED_COMPENSATION
                self.log.log(self.saga_id, "SAGA_COMPENSATION_FAILED_CRITICAL")
            else:
                self.status = SagaStatus.ROLLED_BACK
                self.log.log(self.saga_id, "SAGA_ROLLED_BACK_CLEANLY")

        return self.status, self.context


# ---------------------------------------------------------------------------
# Microservice Simulators (Isolated Databases for Testing & Simulation)
# ---------------------------------------------------------------------------

class OrderService:
    def __init__(self):
        self.orders: Dict[str, Dict[str, Any]] = {}

    def create_order(self, ctx: Dict[str, Any]) -> str:
        order_id = f"ord_{len(self.orders) + 1}"
        self.orders[order_id] = {"status": "CREATED", "items": ctx.get("items", []), "total": ctx.get("amount", 0)}
        ctx["order_id"] = order_id
        return order_id

    def cancel_order(self, ctx: Dict[str, Any]) -> None:
        order_id = ctx.get("order_id")
        if order_id in self.orders:
            self.orders[order_id]["status"] = "CANCELLED"


class PaymentService:
    def __init__(self, balance: int = 1000):
        self.balance = balance
        self.charges: Dict[str, int] = {}
        self.refunds: Dict[str, int] = {}

    def charge(self, ctx: Dict[str, Any]) -> str:
        amount = ctx.get("amount", 0)
        if self.balance < amount:
            raise ValueError(f"Insufficient funds: Balance ${self.balance} < ${amount}")
        self.balance -= amount
        tx_id = f"tx_{len(self.charges) + 1}"
        self.charges[tx_id] = amount
        ctx["payment_tx_id"] = tx_id
        return tx_id

    def refund(self, ctx: Dict[str, Any]) -> None:
        tx_id = ctx.get("payment_tx_id")
        if tx_id in self.charges and tx_id not in self.refunds:
            amount = self.charges[tx_id]
            self.balance += amount
            self.refunds[tx_id] = amount


class InventoryService:
    def __init__(self, stock: Dict[str, int] = None):
        self.stock = stock or {"item_a": 10, "item_b": 5}
        self.reservations: Dict[str, Dict[str, int]] = {}

    def reserve(self, ctx: Dict[str, Any]) -> str:
        item = ctx.get("item", "item_a")
        qty = ctx.get("quantity", 1)
        if self.stock.get(item, 0) < qty:
            raise ValueError(f"Out of stock for {item}: Available {self.stock.get(item, 0)} < Requested {qty}")
        self.stock[item] -= qty
        res_id = f"res_{len(self.reservations) + 1}"
        self.reservations[res_id] = {"item": item, "qty": qty}
        ctx["reservation_id"] = res_id
        return res_id

    def release(self, ctx: Dict[str, Any]) -> None:
        res_id = ctx.get("reservation_id")
        if res_id in self.reservations:
            res = self.reservations.pop(res_id)
            self.stock[res["item"]] += res["qty"]


class DeliveryService:
    def __init__(self, drivers_available: bool = True):
        self.drivers_available = drivers_available
        self.deliveries: Dict[str, str] = {}

    def dispatch(self, ctx: Dict[str, Any]) -> str:
        if not self.drivers_available:
            raise RuntimeError("No drivers available in the delivery zone")
        deliv_id = f"deliv_{len(self.deliveries) + 1}"
        self.deliveries[deliv_id] = "DISPATCHED"
        ctx["delivery_id"] = deliv_id
        return deliv_id

    def cancel_dispatch(self, ctx: Dict[str, Any]) -> None:
        deliv_id = ctx.get("delivery_id")
        if deliv_id in self.deliveries:
            self.deliveries[deliv_id] = "CANCELLED"
