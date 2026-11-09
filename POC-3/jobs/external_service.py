"""A fake external service (mail provider, storage, BI tool...) so the trace has a last hop."""
import time

from django.conf import settings
from opentelemetry.trace import SpanKind

from core.telemetry import get_tracer


def call_external_service(service, operation):
    """Pretend to call another system. It shows up in the trace as a CLIENT span."""
    with get_tracer().start_as_current_span(f"{service}.{operation}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("peer.service", service)
        span.set_attribute("external.operation", operation)
        time.sleep(settings.EXTERNAL_SERVICE_LATENCY)
        return {"service": service, "operation": operation, "ok": True}
