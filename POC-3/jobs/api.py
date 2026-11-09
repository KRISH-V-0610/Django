"""HTTP side of the job system: create a job, look at it, look at the queue."""
import logging
from typing import Literal
from uuid import UUID

import redis
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Status

from . import streams
from .models import Job
from .producer import enqueue_job

logger = logging.getLogger(__name__)
router = Router(tags=["jobs"])


class JobIn(Schema):
    kind: Literal["email", "file", "report", "long"]
    payload: dict = {}


class JobOut(Schema):
    id: UUID
    kind: str
    status: str
    attempts: int
    last_error: str
    processed_by: str
    effect: str | None = None

    @staticmethod
    def resolve_effect(obj):
        effect = getattr(obj, "effect", None)  # reverse OneToOne: missing -> None
        return effect.description if effect else None


class QueueUnavailable(Schema):
    detail: str
    job_id: UUID


@router.post("", response={202: JobOut, 503: QueueUnavailable})
def create_job(request, data: JobIn):
    # 1) the row is saved (and committed) first ...
    job = Job.objects.create(kind=data.kind, payload=data.payload)
    # 2) ... and only then the message is published, so a worker can never receive a
    #    job id that is not in the database yet.
    try:
        enqueue_job(job)
    except redis.RedisError as exc:
        logger.error("could not enqueue job %s: %s", job.id, exc)
        Job.objects.filter(pk=job.pk).update(status=Job.Status.DEAD, last_error="queue unavailable")
        return Status(503, {"detail": "Queue unavailable, job was not queued.", "job_id": job.id})
    return Status(202, job)


@router.get("", response=list[JobOut])
def list_jobs(request):
    return Job.objects.select_related("effect").order_by("-created_at")[:50]


@router.get("/stats")
def queue_stats(request):
    """What Redis knows: stream length, un-acked (pending) messages per worker, DLQ size."""
    client = streams.get_redis()
    streams.ensure_group(client)
    pending = client.xpending(streams.STREAM, streams.GROUP)
    return {
        "stream_length": client.xlen(streams.STREAM),
        "pending": pending["pending"],
        "consumers": {c["name"]: c["pending"] for c in pending["consumers"]},
        "dead_letters": client.xlen(streams.DLQ_STREAM),
    }


@router.get("/dead-letters")
def dead_letters(request):
    client = streams.get_redis()
    return [{"id": entry_id, **fields} for entry_id, fields in client.xrange(streams.DLQ_STREAM)]


@router.get("/{job_id}", response=JobOut)
def get_job(request, job_id: UUID):
    return get_object_or_404(Job.objects.select_related("effect"), pk=job_id)
