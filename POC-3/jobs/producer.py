"""Producer: puts a job on the Redis stream and attaches the current trace context."""
from opentelemetry.trace import SpanKind

from core.telemetry import get_tracer, inject_context

from . import streams


def enqueue_job(job):
    """XADD one message. Returns the stream message id (like '1712345678901-0')."""
    with get_tracer().start_as_current_span("jobs.enqueue", kind=SpanKind.PRODUCER) as span:
        span.set_attribute("messaging.system", "redis")
        span.set_attribute("messaging.destination.name", streams.STREAM)
        span.set_attribute("job.id", str(job.id))
        span.set_attribute("job.kind", job.kind)

        # The trace context travels INSIDE the message ("traceparent" field). Without this
        # the worker's spans would start a brand-new, unrelated trace.
        fields = {"job_id": str(job.id), "kind": job.kind, **inject_context()}
        message_id = streams.get_redis().xadd(streams.STREAM, fields)

        span.set_attribute("messaging.message.id", message_id)
        return message_id
