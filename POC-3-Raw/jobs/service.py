"""
process_job: what a worker does with ONE job. Safe to run twice for the same job.

Why it is idempotent:
  1. A job that is already DONE is skipped ("duplicate").
  2. The business effect (JobEffect) and status=DONE are written in ONE transaction,
     so either both exist or neither does. A crash before the commit = nothing happened,
     the retry starts clean. A crash after the commit but before XACK = the redelivered
     message finds status DONE and is skipped.
  3. select_for_update() makes a second worker wait for the first one instead of
     running the same job at the same time.
"""
from django.db import transaction
from django.db.models import F

from core.telemetry import get_tracer

from .handlers import run_handler
from .models import Job, JobEffect


def process_job(job_id, worker_name, heartbeat=None):
    """Returns "done" or "duplicate". Raises when the job fails (the worker then retries)."""
    heartbeat = heartbeat or (lambda: None)

    with get_tracer().start_as_current_span("job.process") as span:
        span.set_attribute("job.id", str(job_id))
        span.set_attribute("worker.name", worker_name)

        # Count the attempt in its own (auto-committed) statement, so the number survives
        # even when the work below fails and its transaction is rolled back.
        started = (
            Job.objects.filter(pk=job_id)
            .exclude(status=Job.Status.DONE)
            .update(attempts=F("attempts") + 1, status=Job.Status.PROCESSING,
                    processed_by=worker_name)
        )
        if not started:
            if not Job.objects.filter(pk=job_id).exists():
                raise Job.DoesNotExist(f"job {job_id} does not exist")
            span.set_attribute("job.duplicate", True)
            return "duplicate"

        with transaction.atomic():
            job = Job.objects.select_for_update().get(pk=job_id)
            if job.status == Job.Status.DONE:  # another worker finished it while we waited
                span.set_attribute("job.duplicate", True)
                return "duplicate"

            span.set_attribute("job.kind", job.kind)
            span.set_attribute("job.attempt", job.attempts)
            description = run_handler(job, heartbeat)

            JobEffect.objects.create(job=job, description=description)
            job.status = Job.Status.DONE
            job.last_error = ""
            job.save(update_fields=["status", "last_error", "updated_at"])

        span.set_attribute("job.effect", description)
        return "done"
