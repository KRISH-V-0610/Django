"""
Which workflows and activities does a worker on each task queue run?

A task queue is just a name. A worker listens on ONE queue and can only run what it has
registered. That is how work is routed: the workflow asks for an activity on a queue, and
only a worker listening there (and knowing that activity) will pick it up.
"""
from dataclasses import dataclass, field
from typing import Callable

from temporal_app.constants import (
    DEFAULT_TASK_QUEUE,
    EMAIL_TASK_QUEUE,
    FILES_TASK_QUEUE,
    ORDERS_TASK_QUEUE,
)


@dataclass
class QueueConfig:
    workflows: list[type] = field(default_factory=list)
    activities: list[Callable] = field(default_factory=list)


def get_queue_configs() -> dict[str, QueueConfig]:
    # imported here so Django is already set up (activities import models)
    from temporal_app.activities import demo, files, notifications, orders, reports
    from temporal_app.workflows.demo import (
        HelloWorkflow,
        LongRunningWorkflow,
        NotificationWorkflow,
        ReportWorkflow,
        RetryDemoWorkflow,
    )
    from temporal_app.workflows.files import DataProcessingWorkflow, ProcessFileWorkflow
    from temporal_app.workflows.order import OrderWorkflow

    return {
        DEFAULT_TASK_QUEUE: QueueConfig(
            workflows=[
                HelloWorkflow,
                NotificationWorkflow,
                RetryDemoWorkflow,
                LongRunningWorkflow,
                ReportWorkflow,
                DataProcessingWorkflow,
            ],
            activities=[
                demo.say_hello,
                demo.call_external_api,
                demo.reject_request,
                demo.decline_by_type,
                demo.slow_call,
                demo.stalled_call,
                demo.long_running_step,
                notifications.record_notification,
                reports.generate_report,
                files.list_inbox_files,
            ],
        ),
        ORDERS_TASK_QUEUE: QueueConfig(
            workflows=[OrderWorkflow],
            activities=[
                orders.validate_order,
                orders.check_availability,
                orders.reserve_inventory,
                orders.charge_payment,
                orders.update_order_status,
                orders.release_inventory,
                orders.refund_payment,
            ],
        ),
        # activity-only worker: no workflows, just email activities
        EMAIL_TASK_QUEUE: QueueConfig(
            activities=[notifications.send_email, orders.send_order_confirmation],
        ),
        FILES_TASK_QUEUE: QueueConfig(
            workflows=[ProcessFileWorkflow],
            activities=[files.process_file, files.archive_file],
        ),
    }
