"""The HTTP side of the job system, with a fake Redis behind it."""
import redis
import pytest

from jobs import streams
from jobs.models import Job
from jobs.worker import Worker

pytestmark = pytest.mark.django_db


def post_job(client, **body):
    return client.post("/api/jobs", data=body, content_type="application/json")


def test_creating_a_job_saves_it_and_publishes_a_message(client, redis_server):
    response = post_job(client, kind="email", payload={"to": "a@example.com"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    job = Job.objects.get(pk=body["id"])
    assert job.payload == {"to": "a@example.com"}

    messages = redis_server.xrange(streams.STREAM)
    assert len(messages) == 1
    fields = messages[0][1]
    assert fields["job_id"] == body["id"]
    assert "traceparent" in fields  # the trace context travels inside the message


def test_unknown_kind_is_rejected(client, redis_server):
    response = post_job(client, kind="launch-rocket")
    assert response.status_code == 422
    assert Job.objects.count() == 0
    assert redis_server.xlen(streams.STREAM) == 0


def test_redis_down_gives_503_and_marks_the_job_dead(client, monkeypatch):
    def broken(job):
        raise redis.ConnectionError("redis is down")

    monkeypatch.setattr("jobs.api.enqueue_job", broken)
    response = post_job(client, kind="email")

    assert response.status_code == 503
    job = Job.objects.get(pk=response.json()["job_id"])
    assert job.status == Job.Status.DEAD
    assert job.last_error == "queue unavailable"


def test_job_status_follows_the_worker(client, redis_server):
    job_id = post_job(client, kind="report", payload={"name": "weekly"}).json()["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"

    Worker("worker-1", client=redis_server, retry_base_ms=0).run_once()

    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "done"
    assert body["processed_by"] == "worker-1"
    assert body["effect"].startswith("report 'weekly' generated")


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/6f9619ff-8b86-d011-b42d-00c04fc964ff").status_code == 404


def test_stats_show_queue_state(client, redis_server):
    post_job(client, kind="email")
    post_job(client, kind="email", payload={"always_fail": True})
    worker = Worker("worker-1", client=redis_server, retry_base_ms=0)

    worker.run_once()  # first job succeeds
    worker.run_once()  # second job fails once and stays pending

    stats = client.get("/api/jobs/stats").json()
    assert stats["stream_length"] == 2
    assert stats["pending"] == 1
    assert stats["consumers"] == {"worker-1": 1}
    assert stats["dead_letters"] == 0


def test_dead_letters_endpoint_lists_failed_jobs(client, redis_server):
    job_id = post_job(client, kind="email", payload={"always_fail": True}).json()["id"]
    worker = Worker("worker-1", client=redis_server, retry_base_ms=0)
    for _ in range(5):
        worker.run_once()

    dead = client.get("/api/jobs/dead-letters").json()
    assert [entry["job_id"] for entry in dead] == [job_id]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "dead"
