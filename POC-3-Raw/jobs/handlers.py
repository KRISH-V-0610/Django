"""
The business operations a worker can run (topic C use cases).
Each handler gets the job and a heartbeat() callback and returns a short description
of what it did. All the "external" work is simulated.
"""
import logging
import os
import time

from django.contrib.auth import get_user_model

from documents.models import Document

from .external_service import call_external_service

logger = logging.getLogger(__name__)


class TransientError(Exception):
    """A temporary problem: trying again later may work."""


class PermanentError(Exception):
    """This job will never succeed. It still goes through the retries and ends in the DLQ."""


def crash_worker():
    """Kill the whole process at once, like a power cut. No cleanup, no XACK."""
    logger.error("DEMO: simulating a worker crash now")
    os._exit(1)


def apply_demo_flags(job):
    """
    Failure injection for the demos. A job can ask for a failure through its payload:
        {"fail_times": 2}         fail the first 2 attempts, then succeed
        {"always_fail": true}     fail every attempt (ends in the dead-letter stream)
        {"crash_on_attempt": 1}   kill the worker process on that attempt
    Normal jobs never contain these keys.
    """
    payload = job.payload
    if payload.get("crash_on_attempt") == job.attempts:
        crash_worker()
    if payload.get("always_fail"):
        raise PermanentError("always_fail flag is set")
    if job.attempts <= payload.get("fail_times", 0):
        raise TransientError(f"simulated temporary failure on attempt {job.attempts}")


def send_email(job, heartbeat):
    to = job.payload.get("to", "user@example.com")
    call_external_service("mail-provider", "send")
    return f"email sent to {to}"


def process_file(job, heartbeat):
    lines = int(job.payload.get("lines", 1000))
    call_external_service("object-storage", "download")
    checksum = sum(range(lines)) % 9973
    return f"processed {lines} lines (checksum {checksum})"


def generate_report(job, heartbeat):
    name = job.payload.get("name", "daily-summary")
    users = get_user_model().objects.count()
    documents = Document.objects.count()
    call_external_service("bi-service", "publish")
    return f"report '{name}' generated: {users} users, {documents} documents"


def long_operation(job, heartbeat):
    """Several slow steps. heartbeat() after each one tells Redis 'I am still alive'."""
    steps = int(job.payload.get("steps", 3))
    step_seconds = float(job.payload.get("step_seconds", 1))
    for _ in range(steps):
        time.sleep(step_seconds)
        heartbeat()
    return f"long operation finished ({steps} steps)"


HANDLERS = {
    "email": send_email,
    "file": process_file,
    "report": generate_report,
    "long": long_operation,
}


def run_handler(job, heartbeat):
    apply_demo_flags(job)
    handler = HANDLERS.get(job.kind)
    if handler is None:
        raise PermanentError(f"unknown job kind: {job.kind}")
    return handler(job, heartbeat)
