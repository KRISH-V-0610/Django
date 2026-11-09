"""
The worker: reads jobs from the Redis stream as a member of a consumer group.

Delivery rules (this is what makes it "at least once"):
  * A message is delivered to ONE worker of the group (XREADGROUP).
  * It stays in the group's pending list (PEL) until the worker confirms it with XACK.
  * XACK is sent ONLY after the job succeeded.
  * Failed or abandoned messages stay pending. Any worker re-claims them later (XPENDING
    to find them, XCLAIM to take them) - that covers retries AND crashed workers.
  * After MAX_ATTEMPTS deliveries the message is moved to the dead-letter stream.
"""
import logging
import uuid
from datetime import datetime, timezone

from opentelemetry.trace import SpanKind, Status, StatusCode

from core.telemetry import extract_context, get_tracer

from . import streams
from .models import Job
from .service import process_job

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, name, client=None, retry_base_ms=None, max_attempts=None):
        self.name = name
        self.redis = client or streams.get_redis()
        self.retry_base_ms = streams.RETRY_BASE_MS if retry_base_ms is None else retry_base_ms
        self.max_attempts = streams.MAX_ATTEMPTS if max_attempts is None else max_attempts

    # ------------------------------------------------------------------ main loop
    def run_forever(self, block_ms=2000):
        streams.ensure_group(self.redis)
        logger.info("worker %s started (group=%s stream=%s)", self.name, streams.GROUP, streams.STREAM)
        try:
            while True:
                self.run_once(block_ms=block_ms)
        except KeyboardInterrupt:
            logger.info("worker %s stopped", self.name)

    def run_once(self, block_ms=0):
        """One round: first look for messages to retry / recover, then read new ones."""
        streams.ensure_group(self.redis)
        return self.recover_pending() + self.read_new(block_ms)

    # ------------------------------------------------------------------ new messages
    def read_new(self, block_ms=0):
        """XREADGROUP ... ">" = 'give me messages nobody in the group has been given yet'."""
        response = self.redis.xreadgroup(
            streams.GROUP, self.name, {streams.STREAM: ">"},
            count=1, block=block_ms or None,
        )
        handled = 0
        for _stream, messages in response or []:
            for message_id, fields in messages:
                self.handle(message_id, fields, delivery_count=1)
                handled += 1
        return handled

    # ------------------------------------------------------------------ retries + recovery
    def recover_pending(self):
        """
        XPENDING lists messages that were delivered but never acknowledged: either the
        attempt failed, or the worker holding it died. If a message has been idle longer
        than its back-off time we take it over with XCLAIM and run it again.
        """
        handled = 0
        pending = self.redis.xpending_range(streams.STREAM, streams.GROUP, "-", "+", count=20)
        for entry in pending:
            message_id = entry["message_id"]
            deliveries = entry["times_delivered"]
            wait_ms = self.retry_base_ms * 2 ** (deliveries - 1)  # exponential back-off
            if entry["time_since_delivered"] < wait_ms:
                continue

            # XCLAIM re-checks the idle time atomically, so if two workers try at once only
            # one of them gets the message.
            claimed = self.redis.xclaim(
                streams.STREAM, streams.GROUP, self.name,
                min_idle_time=wait_ms, message_ids=[message_id],
            )
            for claimed_id, fields in claimed:
                handled += 1
                if deliveries >= self.max_attempts:
                    # every attempt was used up (the last one probably killed its worker)
                    self.dead_letter(claimed_id, fields, "max attempts reached without success")
                else:
                    logger.info("worker %s took over %s (delivery #%s)",
                                self.name, claimed_id, deliveries + 1)
                    self.handle(claimed_id, fields, delivery_count=deliveries + 1)
        return handled

    # ------------------------------------------------------------------ one message
    def handle(self, message_id, fields, delivery_count):
        # The producer put the trace context in the message: continue that trace here.
        parent = extract_context(fields)
        with get_tracer().start_as_current_span(
            "jobs.consume", context=parent, kind=SpanKind.CONSUMER
        ) as span:
            span.set_attribute("messaging.system", "redis")
            span.set_attribute("messaging.destination.name", streams.STREAM)
            span.set_attribute("messaging.message.id", message_id)
            span.set_attribute("messaging.consumer.group.name", streams.GROUP)
            span.set_attribute("worker.name", self.name)
            span.set_attribute("delivery.count", delivery_count)

            job_id = fields.get("job_id", "")
            try:
                result = process_job(job_id, self.name, heartbeat=lambda: self.heartbeat(message_id))
            except Exception as exc:  # any failure = do NOT ack, the message stays pending
                logger.warning("worker %s: job %s failed on delivery #%s: %s",
                               self.name, job_id, delivery_count, exc)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self.on_failure(message_id, fields, delivery_count, exc)
                return "failed"

            self.redis.xack(streams.STREAM, streams.GROUP, message_id)  # success -> confirm
            span.set_attribute("job.result", result)
            logger.info("worker %s: job %s %s (acked %s)", self.name, job_id, result, message_id)
            return result

    def on_failure(self, message_id, fields, delivery_count, exc):
        job_id = fields.get("job_id", "")
        error = f"{type(exc).__name__}: {exc}"[:500]
        Job.objects.filter(pk=_valid_uuid(job_id)).exclude(status=Job.Status.DONE).update(
            status=Job.Status.RETRYING, last_error=error
        )
        if delivery_count >= self.max_attempts:
            self.dead_letter(message_id, fields, error)

    def heartbeat(self, message_id):
        """
        'I am still working on it.' XCLAIM with JUSTID resets the idle timer of our own
        message WITHOUT raising its delivery counter, so other workers do not steal it.
        """
        self.redis.xclaim(
            streams.STREAM, streams.GROUP, self.name,
            min_idle_time=0, message_ids=[message_id], justid=True,
        )

    def dead_letter(self, message_id, fields, reason):
        """Move the message to the dead-letter stream and acknowledge it so it stops retrying."""
        job_id = fields.get("job_id", "")
        if Job.objects.filter(pk=_valid_uuid(job_id), status=Job.Status.DONE).exists():
            # it actually finished earlier; only the XACK was missing
            self.redis.xack(streams.STREAM, streams.GROUP, message_id)
            return

        entry = {
            **fields,
            "original_id": message_id,
            "reason": reason[:300],
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.redis.pipeline(transaction=True) as pipe:  # both commands, or neither
            pipe.xadd(streams.DLQ_STREAM, entry)
            pipe.xack(streams.STREAM, streams.GROUP, message_id)
            pipe.execute()
        Job.objects.filter(pk=_valid_uuid(job_id)).exclude(status=Job.Status.DONE).update(
            status=Job.Status.DEAD, last_error=reason[:500]
        )
        logger.error("worker %s: job %s moved to dead-letter stream (%s)", self.name, job_id, reason)


def _valid_uuid(value):
    """A message with a missing/garbled job_id must not crash the worker's error handling."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.UUID(int=0)
