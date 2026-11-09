"""
Topic C: Redis Streams workers, tested against fakeredis (no real Redis needed).

Every test uses retry_base_ms=0 so a failed message can be retried at once, instead of
waiting for the real back-off (2 s, 4 s, ...).
"""
import pytest

from jobs import handlers, streams
from jobs.models import Job, JobEffect
from jobs.producer import enqueue_job
from jobs.worker import Worker

pytestmark = pytest.mark.django_db


def make_job(kind="email", **payload):
    job = Job.objects.create(kind=kind, payload=payload)
    enqueue_job(job)
    return job


def make_worker(redis, name="worker-1", **kwargs):
    return Worker(name, client=redis, retry_base_ms=0, **kwargs)


def pending_count(redis):
    return redis.xpending(streams.STREAM, streams.GROUP)["pending"]


def run_until_idle(workers, rounds=10):
    for _ in range(rounds):
        for worker in workers:
            worker.run_once()


# ------------------------------------------------------------------ success
def test_job_succeeds_and_message_is_acknowledged(redis_server):
    job = make_job(to="a@example.com")
    make_worker(redis_server).run_once()

    job.refresh_from_db()
    assert job.status == Job.Status.DONE
    assert job.attempts == 1
    assert job.processed_by == "worker-1"
    assert job.effect.description == "email sent to a@example.com"
    assert pending_count(redis_server) == 0  # XACK was sent


def test_every_job_kind_has_a_handler(redis_server):
    jobs = [make_job("email"), make_job("file", lines=100), make_job("report"),
            make_job("long", steps=2, step_seconds=0)]
    run_until_idle([make_worker(redis_server)], rounds=len(jobs))

    for job in jobs:
        job.refresh_from_db()
        assert job.status == Job.Status.DONE, job.kind


def test_message_is_not_acknowledged_before_the_job_finishes(redis_server):
    job = make_job(always_fail=True)
    make_worker(redis_server).run_once()  # one failed attempt

    assert pending_count(redis_server) == 1  # still waiting, NOT acked
    job.refresh_from_db()
    assert job.status == Job.Status.RETRYING
    assert "always_fail" in job.last_error


# ------------------------------------------------------------------ retry
def test_failed_job_is_retried_until_it_works(redis_server):
    job = make_job(fail_times=2)  # attempts 1 and 2 fail, attempt 3 works
    worker = make_worker(redis_server)

    worker.run_once()
    job.refresh_from_db()
    assert (job.status, job.attempts) == (Job.Status.RETRYING, 1)

    worker.run_once()
    job.refresh_from_db()
    assert (job.status, job.attempts) == (Job.Status.RETRYING, 2)

    worker.run_once()
    job.refresh_from_db()
    assert (job.status, job.attempts) == (Job.Status.DONE, 3)
    assert pending_count(redis_server) == 0
    assert redis_server.xlen(streams.DLQ_STREAM) == 0


def test_retry_waits_for_the_backoff_time(redis_server):
    job = make_job(fail_times=1)
    worker = Worker("worker-1", client=redis_server, retry_base_ms=60_000)  # one minute

    worker.run_once()  # attempt 1 fails
    worker.run_once()  # too early to retry: back-off not over
    job.refresh_from_db()
    assert job.attempts == 1
    assert pending_count(redis_server) == 1


# ------------------------------------------------------------------ dead-letter queue
def test_job_that_always_fails_ends_in_the_dead_letter_stream(redis_server):
    job = make_job(always_fail=True)
    run_until_idle([make_worker(redis_server)], rounds=6)

    job.refresh_from_db()
    assert job.status == Job.Status.DEAD
    assert job.attempts == streams.MAX_ATTEMPTS
    assert pending_count(redis_server) == 0  # removed from the pending list
    assert not JobEffect.objects.filter(job=job).exists()

    dead = redis_server.xrange(streams.DLQ_STREAM)
    assert len(dead) == 1
    entry = dead[0][1]
    assert entry["job_id"] == str(job.id)
    assert "always_fail" in entry["reason"]
    assert "original_id" in entry and "failed_at" in entry


def test_unknown_job_kind_is_dead_lettered(redis_server):
    job = Job.objects.create(kind="mystery")  # a kind no handler knows
    enqueue_job(job)
    run_until_idle([make_worker(redis_server)], rounds=6)

    job.refresh_from_db()
    assert job.status == Job.Status.DEAD
    assert "unknown job kind" in job.last_error


def test_message_for_a_missing_job_does_not_crash_the_worker(redis_server):
    redis_server.xadd(streams.STREAM, {"job_id": "00000000-0000-0000-0000-000000000001"})
    redis_server.xadd(streams.STREAM, {"job_id": "not-a-uuid"})
    run_until_idle([make_worker(redis_server)], rounds=8)

    assert pending_count(redis_server) == 0
    assert redis_server.xlen(streams.DLQ_STREAM) == 2


# ------------------------------------------------------------------ crash + recovery
class WorkerCrashed(BaseException):
    """Stands in for os._exit(): not an Exception, so nothing in the worker can catch it."""


def test_crashed_worker_leaves_the_message_pending_and_another_worker_recovers_it(
    redis_server, monkeypatch
):
    def fake_crash():
        raise WorkerCrashed

    monkeypatch.setattr(handlers, "crash_worker", fake_crash)
    job = make_job("report", crash_on_attempt=1)

    with pytest.raises(WorkerCrashed):
        make_worker(redis_server, "worker-1").run_once()  # dies in the middle of the job

    # no XACK was sent, so Redis still lists the message as pending for worker-1
    assert pending_count(redis_server) == 1
    job.refresh_from_db()
    assert job.status == Job.Status.PROCESSING
    assert not JobEffect.objects.filter(job=job).exists()  # the work was rolled back

    make_worker(redis_server, "worker-2").run_once()  # XPENDING + XCLAIM

    job.refresh_from_db()
    assert job.status == Job.Status.DONE
    assert job.processed_by == "worker-2"
    assert pending_count(redis_server) == 0
    assert JobEffect.objects.filter(job=job).count() == 1


def test_worker_that_dies_right_after_reading_loses_nothing(redis_server):
    job = make_job()
    streams.ensure_group(redis_server)
    # worker-1 reads the message and dies before doing anything
    redis_server.xreadgroup(streams.GROUP, "worker-1", {streams.STREAM: ">"}, count=1)
    assert pending_count(redis_server) == 1

    make_worker(redis_server, "worker-2").run_once()

    job.refresh_from_db()
    assert job.status == Job.Status.DONE
    assert pending_count(redis_server) == 0


def test_message_that_exhausted_all_deliveries_is_dead_lettered(redis_server):
    """Every delivery killed its worker: after MAX_ATTEMPTS the message must stop circulating."""
    job = make_job()
    streams.ensure_group(redis_server)
    redis_server.xreadgroup(streams.GROUP, "worker-1", {streams.STREAM: ">"}, count=1)
    message_id = redis_server.xpending_range(streams.STREAM, streams.GROUP, "-", "+", 10)[0]["message_id"]
    for _ in range(streams.MAX_ATTEMPTS - 1):  # two more deliveries, each "crashed"
        redis_server.xclaim(streams.STREAM, streams.GROUP, "worker-1", 0, [message_id])

    make_worker(redis_server, "worker-2").run_once()

    job.refresh_from_db()
    assert job.status == Job.Status.DEAD
    assert redis_server.xlen(streams.DLQ_STREAM) == 1
    assert pending_count(redis_server) == 0


# ------------------------------------------------------------------ idempotency
def test_duplicate_message_does_not_repeat_the_work(redis_server):
    job = make_job()
    enqueue_job(job)  # the SAME job published a second time
    worker = make_worker(redis_server)

    results = [worker.handle(*_first(redis_server, worker.name), delivery_count=1) for _ in range(2)]

    assert results == ["done", "duplicate"]
    assert JobEffect.objects.filter(job=job).count() == 1
    job.refresh_from_db()
    assert job.attempts == 1  # the duplicate did not count as a new attempt
    assert pending_count(redis_server) == 0  # both messages were acknowledged


def _first(redis, consumer):
    """Read the next new message the way the worker does: returns (message_id, fields)."""
    streams.ensure_group(redis)
    (_stream, messages), = redis.xreadgroup(streams.GROUP, consumer, {streams.STREAM: ">"}, count=1)
    return messages[0]


def test_redelivery_after_a_lost_ack_is_skipped(redis_server):
    """The job committed, but the worker died before XACK: the redelivery must be a no-op."""
    job = make_job()
    streams.ensure_group(redis_server)
    redis_server.xreadgroup(streams.GROUP, "worker-1", {streams.STREAM: ">"}, count=1)
    from jobs.service import process_job

    process_job(str(job.id), "worker-1")  # done and committed, but no XACK sent

    make_worker(redis_server, "worker-2").run_once()  # recovers the un-acked message

    assert JobEffect.objects.filter(job=job).count() == 1
    assert pending_count(redis_server) == 0


def test_database_refuses_a_second_effect_for_the_same_job():
    from django.db import IntegrityError, transaction

    job = Job.objects.create(kind="email")
    JobEffect.objects.create(job=job, description="first")
    with pytest.raises(IntegrityError), transaction.atomic():
        JobEffect.objects.create(job=job, description="second")


# ------------------------------------------------------------------ several workers
def test_two_workers_share_the_jobs_without_duplicates(redis_server):
    jobs = [make_job(to=f"user{i}@example.com") for i in range(6)]
    workers = [make_worker(redis_server, "worker-1"), make_worker(redis_server, "worker-2")]

    run_until_idle(workers, rounds=4)

    for job in jobs:
        job.refresh_from_db()
        assert job.status == Job.Status.DONE
        assert job.attempts == 1
    assert JobEffect.objects.count() == 6  # each job produced its effect exactly once
    assert {job.processed_by for job in jobs} == {"worker-1", "worker-2"}  # both did work
    assert pending_count(redis_server) == 0


def test_heartbeat_keeps_the_delivery_counter(redis_server):
    make_job("long", steps=1, step_seconds=0)
    worker = make_worker(redis_server)
    streams.ensure_group(redis_server)
    message_id, _fields = _first(redis_server, worker.name)

    worker.heartbeat(message_id)
    worker.heartbeat(message_id)

    entry = redis_server.xpending_range(streams.STREAM, streams.GROUP, "-", "+", 10)[0]
    assert entry["times_delivered"] == 1  # XCLAIM ... JUSTID does not count as a new delivery
