"""Unit, Compensation Invariant, and Rollback Tests for Saga Pattern Orchestrator."""

import pytest

from core import (
    DeliveryService,
    InventoryService,
    OrderService,
    PaymentService,
    SagaOrchestrator,
    SagaStatus,
    StepStatus,
)


@pytest.fixture
def services():
    order = OrderService()
    payment = PaymentService(balance=500)
    inventory = InventoryService(stock={"laptop": 5})
    delivery = DeliveryService(drivers_available=True)
    return order, payment, inventory, delivery


def build_ecommerce_saga(order, payment, inventory, delivery):
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


class TestSagaOrchestration:
    def test_successful_end_to_end_saga(self, services):
        order, payment, inventory, delivery = services
        saga = build_ecommerce_saga(order, payment, inventory, delivery)

        status, ctx = saga.execute({
            "items": ["laptop"],
            "item": "laptop",
            "quantity": 1,
            "amount": 200,
        })

        assert status == SagaStatus.SUCCESS
        assert "order_id" in ctx
        assert "payment_tx_id" in ctx
        assert "reservation_id" in ctx
        assert "delivery_id" in ctx

        # Verify side effects across services
        assert order.orders[ctx["order_id"]]["status"] == "CREATED"
        assert payment.balance == 300  # 500 - 200
        assert inventory.stock["laptop"] == 4  # 5 - 1
        assert delivery.deliveries[ctx["delivery_id"]] == "DISPATCHED"

    def test_rollback_on_payment_failure(self, services):
        order, payment, inventory, delivery = services
        saga = build_ecommerce_saga(order, payment, inventory, delivery)

        # Insufficient funds ($600 > $500 balance)
        status, ctx = saga.execute({
            "items": ["laptop"],
            "item": "laptop",
            "quantity": 1,
            "amount": 600,
        })

        assert status == SagaStatus.ROLLED_BACK
        assert "order_id" in ctx
        assert "payment_tx_id" not in ctx

        # Invariant: Order was created then compensated (cancelled)
        assert order.orders[ctx["order_id"]]["status"] == "CANCELLED"
        # Invariant: Payment balance was untouched
        assert payment.balance == 500
        # Invariant: Inventory & Delivery never touched
        assert inventory.stock["laptop"] == 5
        assert len(delivery.deliveries) == 0

    def test_rollback_on_inventory_out_of_stock(self, services):
        order, payment, inventory, delivery = services
        saga = build_ecommerce_saga(order, payment, inventory, delivery)

        # Request 10 laptops (only 5 in stock)
        status, ctx = saga.execute({
            "items": ["laptop"],
            "item": "laptop",
            "quantity": 10,
            "amount": 200,
        })

        assert status == SagaStatus.ROLLED_BACK
        assert "order_id" in ctx
        assert "payment_tx_id" in ctx
        assert "reservation_id" not in ctx

        # Order must be cancelled
        assert order.orders[ctx["order_id"]]["status"] == "CANCELLED"
        # Payment must be refunded: balance restored from 300 back to 500!
        assert payment.balance == 500
        assert ctx["payment_tx_id"] in payment.refunds
        # Inventory untouched
        assert inventory.stock["laptop"] == 5
        # Delivery never touched
        assert len(delivery.deliveries) == 0

    def test_rollback_on_delivery_failure(self, services):
        order, payment, inventory, delivery = services
        delivery.drivers_available = False  # Step 4 will fail!
        saga = build_ecommerce_saga(order, payment, inventory, delivery)

        status, ctx = saga.execute({
            "items": ["laptop"],
            "item": "laptop",
            "quantity": 1,
            "amount": 150,
        })

        assert status == SagaStatus.ROLLED_BACK

        # Reverse compensations must have executed:
        # Step 3 compensated: inventory stock restored
        assert inventory.stock["laptop"] == 5
        # Step 2 compensated: payment refunded
        assert payment.balance == 500
        # Step 1 compensated: order cancelled
        assert order.orders[ctx["order_id"]]["status"] == "CANCELLED"

    def test_failed_compensation_flags_critical_status(self):
        """If a compensation transaction throws an unhandled error, Saga must flag FAILED_COMPENSATION."""
        saga = SagaOrchestrator()

        def broken_compensation(ctx):
            raise ConnectionError("Payment Gateway unreachable during refund!")

        saga.add_step(
            name="Step1",
            action=lambda ctx: "done_step1",
            compensation=broken_compensation,
        ).add_step(
            name="Step2",
            action=lambda ctx: (_ for _ in ()).throw(ValueError("Step2 Business Failure")),
            compensation=None,
        )

        status, _ = saga.execute()
        assert status == SagaStatus.FAILED_COMPENSATION

    def test_saga_audit_log_completeness(self, services):
        order, payment, inventory, delivery = services
        saga = build_ecommerce_saga(order, payment, inventory, delivery)

        status, ctx = saga.execute({
            "items": ["laptop"],
            "item": "laptop",
            "quantity": 1,
            "amount": 100,
        })

        entries = saga.log.get_entries(saga.saga_id)
        events = [e.event for e in entries]

        assert "SAGA_STARTED" in events
        assert "STEP_STARTED" in events
        assert "STEP_COMPLETED" in events
        assert "SAGA_COMPLETED_SUCCESS" in events
        assert len(entries) >= 10
