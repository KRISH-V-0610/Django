import uuid

from django.db import models


class Job(models.Model):
    """The source of truth for a background job. The Redis message only carries the job id."""

    class Kind(models.TextChoices):
        EMAIL = "email", "Email notification"
        FILE = "file", "File processing"
        REPORT = "report", "Report generation"
        LONG = "long", "Long-running operation"

    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        RETRYING = "retrying"  # an attempt failed, the message will be delivered again
        DONE = "done"
        DEAD = "dead"  # gave up, the message is in the dead-letter stream

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    processed_by = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.kind} {self.id} [{self.status}]"


class JobEffect(models.Model):
    """
    The business result of a job (an email that was 'sent', a report that was 'generated').
    OneToOne = the DATABASE guarantees a job can produce its effect only once, even if
    the same message is delivered twice (worker-level idempotency, topic C).
    """

    job = models.OneToOneField(Job, on_delete=models.CASCADE, related_name="effect")
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
