import hashlib
import json
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import IdempotencyRecord

KEY_LIFETIME = timedelta(hours=24)


def make_fingerprint(request, body: dict) -> str:
    """Same method + path + body  ->  same fingerprint."""
    text = request.method + request.path + json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def begin(user, scope, key, fingerprint):
    """
    Try to claim the key. Returns (record, reply):

      (record, None)        -> we own the key, go run the business operation
      (None, (status, body)) -> don't run it, send this reply instead
    """
    # old keys don't count any more
    IdempotencyRecord.objects.filter(
        user=user, scope=scope, key=key, expires_at__lt=timezone.now()
    ).delete()

    try:
        # the unique (user, scope, key) constraint decides the winner when two
        # identical requests arrive together. savepoint (atomic) keeps the
        # connection usable if the insert loses.
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                user=user,
                scope=scope,
                key=key,
                request_hash=fingerprint,
                expires_at=timezone.now() + KEY_LIFETIME,
            )
        return record, None

    except IntegrityError:
        existing = IdempotencyRecord.objects.filter(user=user, scope=scope, key=key).first()

    if existing is None:
        # the other request failed and removed its key a moment ago
        return None, (409, {"detail": "Please retry the request."})

    if existing.request_hash != fingerprint:
        return None, (422, {"detail": "This Idempotency-Key was already used with a different request."})

    if existing.status == IdempotencyRecord.Status.PROCESSING:
        return None, (409, {"detail": "A request with this Idempotency-Key is still being processed."})

    # COMPLETED -> replay the saved answer, do not run the operation again
    return None, (existing.response_status, existing.response_body)


def complete(record, status_code, body):
    """Save the result. Call this INSIDE the same transaction as the business work."""
    record.status = IdempotencyRecord.Status.COMPLETED
    record.response_status = status_code
    record.response_body = body
    record.save(update_fields=["status", "response_status", "response_body"])


def abandon(record):
    """The operation failed and was rolled back: free the key so the client can retry."""
    record.delete()
