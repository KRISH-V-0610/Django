"""Start one worker process:  uv run python manage.py run_worker --name worker-1"""
from django.conf import settings
from django.core.management.base import BaseCommand

from core.telemetry import setup_telemetry, shutdown_telemetry
from jobs.worker import Worker


class Command(BaseCommand):
    help = "Run a Redis Streams worker (start several to see the load being shared)."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="worker-1", help="consumer name inside the group")
        parser.add_argument("--retry-base-ms", type=int, default=None,
                            help="back-off before the first retry (default from jobs/streams.py)")

    def handle(self, *args, **options):
        # the worker is its own "service" in the trace
        setup_telemetry(
            "worker",
            exporter=settings.OTEL_EXPORTER,
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        )
        try:
            Worker(options["name"], retry_base_ms=options["retry_base_ms"]).run_forever()
        finally:
            shutdown_telemetry()
