"""
Retry policies. A retry policy only applies to ACTIVITIES (and optionally workflows).
Workflow code itself is never "retried": after a crash it is replayed from its history.
"""
from datetime import timedelta

from temporalio.common import RetryPolicy

# Calls to something outside our control (APIs): wait 1s, 2s, 4s, 8s... (max 10s), give up after 5 tries
EXTERNAL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)

# Our own database: failures are rare and short, so try more times
DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=10,
)

# Email: a few tries, then the workflow decides what to do (the order still completes)
EMAIL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=5),
    maximum_attempts=3,
)

# Business errors are raised by the activity as ApplicationError(non_retryable=True).
# This policy shows the other way: name the error types that must never be retried.
NO_RETRY_FOR_BUSINESS_ERRORS = RetryPolicy(
    maximum_attempts=5,
    non_retryable_error_types=["PaymentDeclined", "ProductUnavailable", "InvalidOrder"],
)
