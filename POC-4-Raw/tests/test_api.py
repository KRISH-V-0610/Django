"""
The HTTP API with the Temporal client MOCKED: these tests need no Temporal server.
They check what the API sends to Temporal (start / signal / query) and how it turns
Temporal and database problems into HTTP status codes.

Async tests that use the async ORM need `transaction=True`: the ORM calls run in another
thread, which would not see rows kept inside a test transaction.
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ninja.testing import TestAsyncClient
from temporalio.client import WorkflowExecutionStatus, WorkflowQueryFailedError
from temporalio.service import RPCError, RPCStatusCode

from config.urls import api
from orders.models import Order, Product
from temporal_app.constants import ORDERS_TASK_QUEUE
from temporal_app.workflows.order import OrderWorkflow

pytestmark = pytest.mark.django_db(transaction=True)


# ------------------------------------------------------------------ fakes
class FakeTemporal:
    """Stands in for temporalio.client.Client. Every call is recorded by an AsyncMock."""

    def __init__(self):
        self.start_workflow = AsyncMock()
        self.handle = MagicMock()
        self.handle.signal = AsyncMock()
        self.handle.describe = AsyncMock(
            return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
        )
        self.handle.query = AsyncMock(side_effect=self._answer_query)
        self.get_workflow_handle = MagicMock(return_value=self.handle)

    @staticmethod
    async def _answer_query(query, **kwargs):
        if query is OrderWorkflow.status:
            return {
                "order_id": "x",
                "status": "AWAITING_APPROVAL",
                "approved": False,
                "cancel_requested": False,
                "confirmation_email": "a@example.com",
            }
        return {"completed_steps": ["validate"], "total_steps": 7, "percent": 14}


@pytest.fixture
def temporal(monkeypatch):
    fake = FakeTemporal()

    async def fake_get_client():
        return fake

    monkeypatch.setattr("orders.api.get_client", fake_get_client)
    return fake


@pytest.fixture
def client():
    return TestAsyncClient(api)


@pytest.fixture
async def widget():
    return await Product.objects.acreate(sku="WIDGET", name="Widget", price=Decimal("25.00"), stock=10)


@pytest.fixture
async def order(widget):
    return await Order.objects.acreate(
        product=widget, quantity=2, customer_email="a@example.com", total_amount=Decimal("50.00")
    )


ORDER_BODY = {"sku": "WIDGET", "quantity": 2, "customer_email": "a@example.com"}


def rpc_error(status: RPCStatusCode) -> RPCError:
    return RPCError("boom", status, b"")


# ------------------------------------------------------------------ POST /orders
async def test_create_order_stores_the_order_and_starts_the_workflow(client, temporal, widget):
    response = await client.post("/orders", json=ORDER_BODY)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert Decimal(body["total_amount"]) == Decimal("50.00")
    assert body["workflow_id"] == f"order-{body['id']}"

    temporal.start_workflow.assert_awaited_once()
    call = temporal.start_workflow.await_args
    assert call.args == (OrderWorkflow.run, body["id"])
    assert call.kwargs["id"] == f"order-{body['id']}"  # business id = workflow id
    assert call.kwargs["task_queue"] == ORDERS_TASK_QUEUE
    assert await Order.objects.acount() == 1


async def test_create_order_keeps_the_simulation_flag(client, temporal, widget):
    response = await client.post("/orders", json={**ORDER_BODY, "simulate": "payment_declined"})
    assert response.status_code == 201
    assert response.json()["simulate"] == "payment_declined"


async def test_create_order_unknown_sku(client, temporal):
    response = await client.post("/orders", json={**ORDER_BODY, "sku": "NOPE"})

    assert response.status_code == 404
    temporal.start_workflow.assert_not_awaited()
    assert await Order.objects.acount() == 0


@pytest.mark.parametrize("quantity", [0, -3])
async def test_create_order_rejects_a_bad_quantity(client, temporal, widget, quantity):
    response = await client.post("/orders", json={**ORDER_BODY, "quantity": quantity})

    assert response.status_code == 422
    temporal.start_workflow.assert_not_awaited()


async def test_create_order_rejects_an_unknown_simulation(client, temporal, widget):
    response = await client.post("/orders", json={**ORDER_BODY, "simulate": "meteor_strike"})

    assert response.status_code == 422
    assert await Order.objects.acount() == 0


@pytest.mark.parametrize("error", [RuntimeError("connection refused"), OSError("network down")])
async def test_create_order_when_temporal_is_down(client, temporal, widget, error):
    temporal.start_workflow.side_effect = error

    response = await client.post("/orders", json=ORDER_BODY)

    assert response.status_code == 503
    stored = await Order.objects.aget()
    assert stored.status == Order.Status.FAILED  # not left PENDING forever
    assert stored.failure_reason == "could not start workflow"


async def test_create_order_when_the_client_cannot_connect(client, monkeypatch, widget):
    async def broken_get_client():
        raise RuntimeError("Failed client connect")

    monkeypatch.setattr("orders.api.get_client", broken_get_client)

    response = await client.post("/orders", json=ORDER_BODY)

    assert response.status_code == 503
    assert (await Order.objects.aget()).status == Order.Status.FAILED


# ------------------------------------------------------------------ GET /orders/{id}
async def test_get_order(client, temporal, order):
    response = await client.get(f"/orders/{order.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(order.id)
    assert body["sku"] == "WIDGET"
    assert body["status"] == "PENDING"
    temporal.start_workflow.assert_not_awaited()  # plain database read, Temporal not involved


async def test_get_unknown_order(client, temporal):
    response = await client.get(f"/orders/{uuid.uuid4()}")
    assert response.status_code == 404


# ------------------------------------------------------------------ GET /orders/{id}/status
async def test_status_combines_describe_and_both_queries(client, temporal, order):
    response = await client.get(f"/orders/{order.id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "AWAITING_APPROVAL"  # from the `status` query
    assert body["progress"]["percent"] == 14  # from the `progress` query
    assert body["workflow_status"] == "RUNNING"  # from describe()
    assert body["workflow_id"] == f"order-{order.id}"
    temporal.get_workflow_handle.assert_called_with(f"order-{order.id}")


async def test_status_of_unknown_order(client, temporal):
    response = await client.get(f"/orders/{uuid.uuid4()}/status")
    assert response.status_code == 404


async def test_status_when_no_worker_answers_the_query(client, temporal, order):
    temporal.handle.query.side_effect = rpc_error(RPCStatusCode.DEADLINE_EXCEEDED)

    response = await client.get(f"/orders/{order.id}/status")

    assert response.status_code == 503
    assert "worker" in response.json()["detail"]


async def test_status_when_the_workflow_does_not_exist(client, temporal, order):
    temporal.handle.describe.side_effect = rpc_error(RPCStatusCode.NOT_FOUND)

    response = await client.get(f"/orders/{order.id}/status")

    assert response.status_code == 409


async def test_status_when_the_query_is_rejected(client, temporal, order):
    temporal.handle.query.side_effect = WorkflowQueryFailedError("query handler failed")

    response = await client.get(f"/orders/{order.id}/status")

    assert response.status_code == 409


# ------------------------------------------------------------------ signals
async def test_approve_sends_the_approve_signal(client, temporal, order):
    response = await client.post(f"/orders/{order.id}/approve")

    assert response.status_code == 200
    assert response.json() == {"order_id": str(order.id), "signal": "approve", "sent": True}
    temporal.get_workflow_handle.assert_called_with(f"order-{order.id}")
    temporal.handle.signal.assert_awaited_once_with(OrderWorkflow.approve)


async def test_cancel_sends_the_default_reason(client, temporal, order):
    response = await client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 200
    temporal.handle.signal.assert_awaited_once_with(OrderWorkflow.cancel, "cancelled by customer")


async def test_cancel_sends_a_custom_reason(client, temporal, order):
    response = await client.post(f"/orders/{order.id}/cancel?reason=changed my mind")

    assert response.status_code == 200
    temporal.handle.signal.assert_awaited_once_with(OrderWorkflow.cancel, "changed my mind")


async def test_update_email_sends_the_new_address(client, temporal, order):
    response = await client.post(f"/orders/{order.id}/update-email", json={"email": "new@example.com"})

    assert response.status_code == 200
    assert response.json()["signal"] == "update_email"
    temporal.handle.signal.assert_awaited_once_with(OrderWorkflow.update_email, "new@example.com")


@pytest.mark.parametrize("action", ["approve", "cancel", "update-email"])
async def test_signal_for_unknown_order_is_404_and_never_reaches_temporal(client, temporal, action):
    response = await client.post(f"/orders/{uuid.uuid4()}/{action}", json={"email": "x@example.com"})

    assert response.status_code == 404
    temporal.handle.signal.assert_not_awaited()


async def test_signal_to_a_finished_workflow_is_409(client, temporal, order):
    temporal.handle.signal.side_effect = rpc_error(RPCStatusCode.NOT_FOUND)

    response = await client.post(f"/orders/{order.id}/approve")

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


async def test_signal_when_temporal_is_unavailable(client, temporal, order):
    temporal.handle.signal.side_effect = rpc_error(RPCStatusCode.UNAVAILABLE)

    response = await client.post(f"/orders/{order.id}/approve")

    assert response.status_code == 503
