"""Topic B: spans, attributes, parent/child links, exceptions and context propagation."""
import pytest
from opentelemetry.trace import SpanKind, StatusCode

from core.telemetry import extract_context, get_tracer, inject_context
from jobs.external_service import call_external_service
from jobs.models import Job
from jobs.producer import enqueue_job
from jobs.worker import Worker


def by_name(spans):
    return {span.name: span for span in spans.get_finished_spans()}


def test_custom_span_has_attributes(spans):
    with get_tracer().start_as_current_span("unit.work") as span:
        span.set_attribute("order.id", 42)
        span.set_attribute("order.vip", True)

    span = by_name(spans)["unit.work"]
    assert span.attributes["order.id"] == 42
    assert span.attributes["order.vip"] is True
    assert span.end_time >= span.start_time


def test_child_span_points_to_its_parent(spans):
    with get_tracer().start_as_current_span("parent"):
        with get_tracer().start_as_current_span("child"):
            pass

    finished = by_name(spans)
    assert finished["child"].parent.span_id == finished["parent"].context.span_id
    assert finished["child"].context.trace_id == finished["parent"].context.trace_id
    assert finished["parent"].parent is None  # the root span has no parent


def test_exception_is_recorded_and_span_marked_error(spans):
    with pytest.raises(ValueError):
        with get_tracer().start_as_current_span("will.fail"):
            raise ValueError("boom")

    span = by_name(spans)["will.fail"]
    assert span.status.status_code == StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]
    assert span.events[0].attributes["exception.type"].endswith("ValueError")
    assert span.events[0].attributes["exception.message"] == "boom"


def test_trace_demo_endpoint_builds_a_span_tree(client, db, spans):
    response = client.get("/api/demo/trace")
    assert response.status_code == 200

    finished = by_name(spans)
    flow = finished["demo.business_flow"]
    assert flow.attributes["demo.fail_requested"] is False
    for step in ("demo.load_data", "demo.calculate"):
        assert finished[step].parent.span_id == flow.context.span_id
    assert finished["demo.calculate"].attributes["demo.items"] == 3
    assert response.json()["trace_id"] == format(flow.context.trace_id, "032x")


def test_trace_demo_failure_is_hidden_from_client_but_kept_in_trace(client, db, spans):
    response = client.get("/api/demo/trace", {"fail": "true"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert "demo failure" not in response.text

    flow = by_name(spans)["demo.business_flow"]
    assert flow.status.status_code == StatusCode.ERROR
    assert flow.events[0].attributes["exception.message"] == "demo failure"


def test_inject_and_extract_keep_the_same_trace(spans):
    with get_tracer().start_as_current_span("client.call") as client_span:
        carrier = inject_context()  # what a producer puts into a message / HTTP header
    assert "traceparent" in carrier

    # "another process": no current span, only the carrier
    with get_tracer().start_as_current_span("server.handle", context=extract_context(carrier)):
        pass

    finished = by_name(spans)
    assert finished["server.handle"].context.trace_id == client_span.get_span_context().trace_id
    assert finished["server.handle"].parent.span_id == client_span.get_span_context().span_id


def test_external_service_call_is_a_client_span(spans):
    result = call_external_service("mail-provider", "send")
    assert result["ok"] is True

    span = by_name(spans)["mail-provider.send"]
    assert span.kind == SpanKind.CLIENT
    assert span.attributes["peer.service"] == "mail-provider"


@pytest.mark.django_db
def test_one_trace_from_client_to_worker_to_external_service(spans, redis_server):
    """The full chain of the POC: client -> producer -> Redis -> worker -> DB work -> external service."""
    job = Job.objects.create(kind="email", payload={"to": "a@example.com"})

    with get_tracer().start_as_current_span("client.request") as client_span:
        enqueue_job(job)  # injects the trace context into the Redis message
    trace_id = client_span.get_span_context().trace_id

    Worker("worker-1", client=redis_server, retry_base_ms=0).run_once()  # the "other process"

    finished = by_name(spans)
    chain = ["client.request", "jobs.enqueue", "jobs.consume", "job.process", "mail-provider.send"]
    assert all(name in finished for name in chain), sorted(finished)
    assert {finished[name].context.trace_id for name in chain} == {trace_id}

    # and every span points to the one before it
    for parent, child in zip(chain, chain[1:]):
        assert finished[child].parent.span_id == finished[parent].context.span_id

    assert finished["jobs.enqueue"].kind == SpanKind.PRODUCER
    assert finished["jobs.consume"].kind == SpanKind.CONSUMER
    assert finished["job.process"].attributes["job.id"] == str(job.id)
    assert finished["jobs.consume"].attributes["worker.name"] == "worker-1"


@pytest.mark.django_db
def test_failed_attempt_is_visible_in_the_trace(spans, redis_server):
    job = Job.objects.create(kind="email", payload={"fail_times": 1})
    enqueue_job(job)
    Worker("worker-1", client=redis_server, retry_base_ms=0).run_once()

    consume = by_name(spans)["jobs.consume"]
    assert consume.status.status_code == StatusCode.ERROR
    assert consume.events[0].attributes["exception.type"].endswith("TransientError")
