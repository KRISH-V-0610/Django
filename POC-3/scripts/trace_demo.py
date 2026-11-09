"""
Topic B: one request that travels  Client -> Django API -> DB -> Redis -> Worker -> external service,
all in ONE trace. Needs: Jaeger, the API (uvicorn) and a worker running (see README).

    uv run python scripts/trace_demo.py

This script is the "Client" hop: it starts the trace and sends the trace id along in the
`traceparent` HTTP header. Then open the printed Jaeger link.
"""
import os
import time

from common import JAEGER_UI, heading, new_client

from core.telemetry import get_tracer, inject_context, setup_telemetry, shutdown_telemetry

setup_telemetry(
    "demo-client",
    exporter=os.environ.get("OTEL_EXPORTER", "otlp"),
    endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
)


def main():
    http = new_client()
    with get_tracer().start_as_current_span("client.send-email-job") as span:
        trace_id = format(span.get_span_context().trace_id, "032x")

        headers = inject_context()  # {"traceparent": "00-<trace id>-<span id>-01"}
        heading("1. client -> API: create a job")
        print("traceparent header:", headers["traceparent"])
        response = http.post("/api/jobs", headers=headers,
                             json={"kind": "email", "payload": {"to": "trace@example.com"}})
        response.raise_for_status()
        job_id = response.json()["id"]
        print("job created:", job_id)

        heading("2. wait for the worker")
        for _ in range(30):
            job = http.get(f"/api/jobs/{job_id}", headers=headers).json()
            if job["status"] in ("done", "dead"):
                break
            time.sleep(0.5)
        print(f"status={job['status']} attempts={job['attempts']} worker={job['processed_by']}")
        print("effect:", job["effect"])

    shutdown_telemetry()  # flush the client's own spans to Jaeger
    heading("3. look at the trace")
    print("trace id:", trace_id)
    print(f"open: {JAEGER_UI}/trace/{trace_id}")
    print("expected spans: client.send-email-job > POST /api/jobs > jobs.enqueue > jobs.consume")
    print("                > job.process > (SQL queries) and mail-provider.send")


if __name__ == "__main__":
    main()
