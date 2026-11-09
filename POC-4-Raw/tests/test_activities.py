"""
Activities tested on their own with ActivityEnvironment (no Temporal server needed).
ActivityEnvironment gives the activity a fake "activity context", so activity.info(),
activity.heartbeat() and attempt numbers all work like in a real worker.
"""
import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from orders import services
from orders.models import Payment, Product, Reservation
from temporal_app.activities import demo, files, notifications, orders as order_activities, reports
from temporal_app.payloads import ApiCallParams, EmailParams, FileParams


def env_for_attempt(attempt: int = 1, heartbeat_details=()) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, attempt=attempt, heartbeat_details=list(heartbeat_details))
    return env


# ------------------------------------------------------------------ demo activities
async def test_external_api_fails_until_the_configured_attempt():
    params = ApiCallParams("api", fail_attempts=2)
    for attempt in (1, 2):
        with pytest.raises(ConnectionError):
            await env_for_attempt(attempt).run(demo.call_external_api, params)

    result = await env_for_attempt(3).run(demo.call_external_api, params)
    assert result == {"api": "api", "attempt": 3, "data": "ok"}


async def test_reject_request_is_non_retryable():
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(demo.reject_request, "no way")
    assert caught.value.non_retryable is True
    assert caught.value.type == "BusinessRuleViolation"


async def test_decline_by_type_is_a_plain_error_with_a_type():
    with pytest.raises(ApplicationError) as caught:
        await env_for_attempt(1).run(demo.decline_by_type, "PaymentDeclined")
    assert caught.value.type == "PaymentDeclined"
    assert caught.value.non_retryable is False  # the retry POLICY decides, not the error


# ------------------------------------------------------------------ file processing + heartbeat
@pytest.fixture
def text_file(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("\n".join("one two three" for _ in range(25)), encoding="utf-8")
    return path


async def test_process_file_heartbeats_every_ten_lines(text_file):
    beats = []
    env = env_for_attempt()
    env.on_heartbeat = lambda *details: beats.append(details)

    result = await env.run(files.process_file, FileParams(path=str(text_file), delay_per_line=0))

    assert result.lines == 25 and result.words == 75
    assert beats == [(10, 30), (20, 60)]  # (lines done, words so far)
    assert result.resumed_from_line == 0


async def test_process_file_resumes_from_the_last_heartbeat(text_file):
    """Attempt 2 gets the details the dead attempt 1 sent, and continues from there."""
    env = env_for_attempt(attempt=2, heartbeat_details=[20, 60])

    result = await env.run(files.process_file, FileParams(path=str(text_file), delay_per_line=0))

    assert result.resumed_from_line == 20
    assert result.lines == 25
    assert result.words == 75  # same total as a full run: no line counted twice or skipped


async def test_process_file_crash_only_happens_on_the_first_attempt(text_file):
    params = FileParams(path=str(text_file), delay_per_line=0, crash_at_line=15)
    with pytest.raises(RuntimeError):
        await env_for_attempt(1).run(files.process_file, params)

    result = await env_for_attempt(2, heartbeat_details=[10, 30]).run(files.process_file, params)
    assert result.lines == 25 and result.words == 75


async def test_archive_file_moves_it_and_can_repeat(text_file, settings, tmp_path):
    settings.DATA_DIR = tmp_path / "data"
    first = await ActivityEnvironment().run(files.archive_file, str(text_file))
    second = await ActivityEnvironment().run(files.archive_file, str(text_file))  # already moved

    assert first == second
    assert not text_file.exists()
    assert (tmp_path / "data" / "processed" / "data.txt").exists()


async def test_list_inbox_files(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "b.txt").write_text("x")
    (tmp_path / "inbox" / "a.txt").write_text("x")
    (tmp_path / "inbox" / "skip.csv").write_text("x")

    found = await ActivityEnvironment().run(files.list_inbox_files)
    assert [Path(name).name for name in found] == ["a.txt", "b.txt"]


# ------------------------------------------------------------------ order activities (sync, DB)
@pytest.fixture
def order(db):
    Product.objects.create(sku="WIDGET", name="Widget", price=Decimal("25.00"), stock=10)
    return services.create_order(sku="WIDGET", quantity=2, customer_email="a@example.com")


def test_validate_order_activity(order):
    result = ActivityEnvironment().run(order_activities.validate_order, str(order.id))
    assert result.requires_approval is False
    assert result.customer_email == "a@example.com"
    assert result.total == "50.00"


def test_validate_order_bad_email_is_non_retryable(db):
    Product.objects.create(sku="WIDGET", name="Widget", price=Decimal("25.00"), stock=10)
    bad = services.create_order(sku="WIDGET", quantity=1, customer_email="nope")
    with pytest.raises(ApplicationError) as caught:
        ActivityEnvironment().run(order_activities.validate_order, str(bad.id))
    assert caught.value.type == "InvalidOrder" and caught.value.non_retryable


def test_unknown_order_is_non_retryable(db):
    with pytest.raises(ApplicationError) as caught:
        ActivityEnvironment().run(
            order_activities.check_availability, "00000000-0000-0000-0000-000000000000"
        )
    assert caught.value.type == "OrderNotFound" and caught.value.non_retryable


def test_check_availability_activity_raises_product_unavailable(order):
    Product.objects.filter(sku="WIDGET").update(stock=1)
    with pytest.raises(ApplicationError) as caught:
        ActivityEnvironment().run(order_activities.check_availability, str(order.id))
    assert caught.value.type == "ProductUnavailable" and caught.value.non_retryable


def test_reserve_inventory_activity_is_idempotent(order):
    env = ActivityEnvironment()
    assert env.run(order_activities.reserve_inventory, str(order.id)) is True
    assert env.run(order_activities.reserve_inventory, str(order.id)) is False  # a retry
    assert Product.objects.get(sku="WIDGET").stock == 8
    assert Reservation.objects.filter(order=order).count() == 1


def test_payment_declined_is_non_retryable(order):
    order.simulate = "payment_declined"
    order.save()
    with pytest.raises(ApplicationError) as caught:
        ActivityEnvironment().run(order_activities.charge_payment, str(order.id))
    assert caught.value.type == "PaymentDeclined" and caught.value.non_retryable
    assert Payment.objects.filter(order=order).count() == 0


def test_payment_api_down_is_retryable_for_the_first_two_attempts(order):
    order.simulate = "payment_api_down"
    order.save()
    for attempt in (1, 2):
        with pytest.raises(ApplicationError) as caught:
            env_for_attempt(attempt).run(order_activities.charge_payment, str(order.id))
        assert caught.value.type == "GatewayUnavailable"
        assert caught.value.non_retryable is False

    payment_id = env_for_attempt(3).run(order_activities.charge_payment, str(order.id))
    assert Payment.objects.get(order=order).pk == payment_id


def test_charge_payment_activity_twice_charges_once(order):
    env = ActivityEnvironment()
    first = env.run(order_activities.charge_payment, str(order.id))
    second = env.run(order_activities.charge_payment, str(order.id))
    assert first == second
    assert Payment.objects.filter(order=order).count() == 1


def test_compensation_activities_undo_the_steps(order):
    env = ActivityEnvironment()
    env.run(order_activities.reserve_inventory, str(order.id))
    env.run(order_activities.charge_payment, str(order.id))

    assert env.run(order_activities.refund_payment, str(order.id)) is True
    assert env.run(order_activities.release_inventory, str(order.id)) is True
    assert Product.objects.get(sku="WIDGET").stock == 10
    assert Payment.objects.get(order=order).status == Payment.Status.REFUNDED


def test_update_order_status_activity(order):
    ActivityEnvironment().run(order_activities.update_order_status, str(order.id), "FAILED", "boom")
    order.refresh_from_db()
    assert (order.status, order.failure_reason) == ("FAILED", "boom")


def test_confirmation_email_is_sent(order, mailoutbox):
    message = ActivityEnvironment().run(
        order_activities.send_order_confirmation, str(order.id), "me@example.com"
    )
    assert "me@example.com" in message
    assert len(mailoutbox) == 1 and mailoutbox[0].to == ["me@example.com"]


def test_confirmation_email_failure_is_retryable(order):
    order.simulate = "email_down"
    order.save()
    with pytest.raises(ConnectionError):  # a plain exception: Temporal will retry it
        ActivityEnvironment().run(order_activities.send_order_confirmation, str(order.id), "me@example.com")


# ------------------------------------------------------------------ notifications and reports
def test_send_email_fails_first_attempts_then_sends(mailoutbox):
    params = EmailParams(to="x@example.com", subject="Hi", body="Body", fail_attempts=1)
    with pytest.raises(ConnectionError):
        env_for_attempt(1).run(notifications.send_email, params)
    assert env_for_attempt(2).run(notifications.send_email, params) == "email sent to x@example.com"
    assert len(mailoutbox) == 1


def test_record_notification_uses_workflow_id_as_idempotency_key(db):
    params = EmailParams(to="x@example.com", subject="Hi", body="Body")
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id="wf-123")
    first = env.run(notifications.record_notification, params)
    second = env.run(notifications.record_notification, params)  # a retry
    assert first == second


def test_generate_report_writes_a_json_file(order, settings, tmp_path):
    settings.REPORTS_DIR = tmp_path / "reports"
    path = ActivityEnvironment().run(reports.generate_report)
    written = list((tmp_path / "reports").glob("report-*.json"))
    assert [str(p) for p in written] == [path]
    assert '"PENDING"' in written[0].read_text(encoding="utf-8")
