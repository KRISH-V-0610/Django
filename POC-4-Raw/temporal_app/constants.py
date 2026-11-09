"""
Names and timeouts shared by workflows, workers, the API and the demos.

No Django imports here on purpose: workflow code runs inside Temporal's sandbox and
must stay free of anything that touches the outside world.
"""
from datetime import timedelta

# ---- task queues: one per kind of workload, each can have its own dedicated workers
DEFAULT_TASK_QUEUE = "default-queue"  # demo workflows, reports, notifications
ORDERS_TASK_QUEUE = "orders-queue"  # OrderWorkflow + order activities
EMAIL_TASK_QUEUE = "email-queue"  # email activities only
FILES_TASK_QUEUE = "files-queue"  # heavy file processing

QUEUE_ALIASES = {
    "default": DEFAULT_TASK_QUEUE,
    "orders": ORDERS_TASK_QUEUE,
    "email": EMAIL_TASK_QUEUE,
    "files": FILES_TASK_QUEUE,
}

# ---- order workflow
ORDER_EXECUTION_TIMEOUT = timedelta(hours=1)  # whole workflow, including retries and waiting
APPROVAL_TIMEOUT = timedelta(hours=24)  # how long a large order may wait for "approve"
PAYMENT_START_TO_CLOSE = timedelta(seconds=5)  # one payment attempt must finish within this
DEFAULT_START_TO_CLOSE = timedelta(seconds=10)
EMAIL_START_TO_CLOSE = timedelta(seconds=10)

# ---- simulation of a flaky payment API: fails on attempts 1 and 2, works on attempt 3
PAYMENT_API_DOWN_ATTEMPTS = 2

# ---- schedule ids
REPORT_SCHEDULE_ID = "report-schedule"
NOTIFICATION_SCHEDULE_ID = "notification-schedule"
PROCESSING_SCHEDULE_ID = "processing-schedule"


def order_workflow_id(order_id) -> str:
    """Workflow ID = business ID. Temporal refuses a second running workflow with the same ID."""
    return f"order-{order_id}"
