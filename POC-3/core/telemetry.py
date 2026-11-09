"""
OpenTelemetry setup (topic B).

    setup_telemetry("django-api")   -> every process calls this once at start-up
    inject_context() / extract_context()  -> carry the trace across a process boundary

No Django import at module level, so plain scripts (the demo client) can use it too.
"""
import logging
import os

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def setup_telemetry(service_name, exporter=None, endpoint=None, django=False):
    """
    Create the tracer provider, attach an exporter and switch on auto-instrumentation.

    exporter: "otlp" (send to Jaeger), "console" (print spans) or "none" (do nothing).
    django:   True only in the web process (instruments incoming HTTP requests).
    """
    global _provider
    exporter = exporter or os.environ.get("OTEL_EXPORTER", "otlp")
    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    if exporter == "none" or _provider is not None:
        return _provider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    if exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        # imported here so "none" / "console" work even if the exporter package is missing
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        otlp = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(otlp))

    trace.set_tracer_provider(provider)
    _provider = provider

    # Auto-instrumentation: libraries get spans without us touching their code.
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    PsycopgInstrumentor().instrument()  # one span per SQL query (PostgreSQL)

    if django:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()  # one SERVER span per incoming HTTP request

    logger.info("telemetry on: service=%s exporter=%s", service_name, exporter)
    return provider


def shutdown_telemetry():
    """Send spans that are still waiting in the batch buffer, then stop."""
    if _provider is not None:
        _provider.force_flush()
        _provider.shutdown()


def get_tracer():
    return trace.get_tracer("poc3")


def inject_context():
    """Current trace context -> small dict (traceparent, tracestate) to put in a message/header."""
    carrier = {}
    propagate.inject(carrier)
    return carrier


def extract_context(carrier):
    """The reverse: dict -> context, to use as the parent of a new span."""
    return propagate.extract(carrier)
