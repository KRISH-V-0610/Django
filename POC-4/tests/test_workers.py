"""
Worker setup: which queue runs what, and the command line options.
No Temporal server is needed, we only look at the configuration.
"""
import inspect
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from temporal_app.activities import demo, files, notifications, orders, reports
from temporal_app.constants import (
    DEFAULT_TASK_QUEUE,
    EMAIL_TASK_QUEUE,
    FILES_TASK_QUEUE,
    ORDERS_TASK_QUEUE,
    QUEUE_ALIASES,
)
from temporal_app.workers import run_worker
from temporal_app.workers.registry import get_queue_configs
from temporal_app.workflows import demo as demo_workflows
from temporal_app.workflows import files as file_workflows
from temporal_app.workflows import order as order_workflows


def defined_in(*modules, marker: str) -> list:
    """Everything a module defines that carries Temporal's @activity.defn / @workflow.defn mark."""
    found = []
    for module in modules:
        for _, obj in inspect.getmembers(module):
            if getattr(obj, marker, None) is not None and getattr(obj, "__module__", None) == module.__name__:
                found.append(obj)
    return found


def names(items) -> set[str]:
    return {item.__name__ for item in items}


@pytest.fixture(scope="module")
def configs():
    return get_queue_configs()


# ------------------------------------------------------------------ routing
def test_every_alias_has_a_config(configs):
    assert set(QUEUE_ALIASES.values()) == set(configs)


def test_order_workflow_lives_only_on_the_orders_queue(configs):
    for queue, config in configs.items():
        assert ("OrderWorkflow" in names(config.workflows)) == (queue == ORDERS_TASK_QUEUE)


def test_email_activities_live_only_on_the_email_queue(configs):
    for queue, config in configs.items():
        registered = names(config.activities)
        for email_activity in ("send_email", "send_order_confirmation"):
            assert (email_activity in registered) == (queue == EMAIL_TASK_QUEUE)


def test_email_worker_is_activity_only(configs):
    assert configs[EMAIL_TASK_QUEUE].workflows == []
    assert configs[EMAIL_TASK_QUEUE].activities


def test_child_workflow_runs_on_the_files_queue(configs):
    assert names(configs[FILES_TASK_QUEUE].workflows) == {"ProcessFileWorkflow"}
    assert names(configs[FILES_TASK_QUEUE].activities) == {"process_file", "archive_file"}


def test_every_workflow_and_activity_is_registered_somewhere(configs):
    """Forgetting to register something only shows up at runtime as a task nobody picks up."""
    registered_workflows = {w for c in configs.values() for w in names(c.workflows)}
    registered_activities = {a for c in configs.values() for a in names(c.activities)}

    defined_workflows = names(
        defined_in(demo_workflows, file_workflows, order_workflows, marker="__temporal_workflow_definition")
    )
    defined_activities = names(
        defined_in(demo, files, notifications, orders, reports, marker="__temporal_activity_definition")
    )

    assert defined_workflows and defined_activities  # the helper really found something
    assert defined_workflows == registered_workflows
    assert defined_activities == registered_activities


def test_activities_asked_for_by_a_workflow_are_registered_on_that_queue(configs):
    # the order workflow runs these on its own queue, the confirmation on the email queue
    on_orders = names(configs[ORDERS_TASK_QUEUE].activities)
    assert {"validate_order", "reserve_inventory", "charge_payment", "release_inventory", "refund_payment"} <= on_orders
    assert "send_order_confirmation" in names(configs[EMAIL_TASK_QUEUE].activities)
    assert "record_notification" in names(configs[DEFAULT_TASK_QUEUE].activities)


# ------------------------------------------------------------------ command line
def test_default_is_all_queues():
    args = run_worker.parse_args([])
    assert args.queue == ["all"]
    assert args.max_activities == 10


def test_queue_and_limits_can_be_chosen():
    args = run_worker.parse_args(["--queue", "email", "orders", "--max-activities", "3", "--max-workflow-tasks", "4"])
    assert args.queue == ["email", "orders"]
    assert args.max_activities == 3
    assert args.max_workflow_tasks == 4


def test_unknown_queue_is_refused():
    with pytest.raises(SystemExit):
        run_worker.parse_args(["--queue", "nonsense"])


def test_build_workers_makes_one_worker_per_queue(monkeypatch):
    created = []

    class FakeWorker:
        def __init__(self, client, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(run_worker, "Worker", FakeWorker)
    with ThreadPoolExecutor(max_workers=1) as executor:
        workers = run_worker.build_workers(MagicMock(), ["orders", "email"], 7, 5, executor)

    assert len(workers) == 2
    assert [kw["task_queue"] for kw in created] == [ORDERS_TASK_QUEUE, EMAIL_TASK_QUEUE]
    assert all(kw["max_concurrent_activities"] == 7 for kw in created)
    assert all(kw["max_concurrent_workflow_tasks"] == 5 for kw in created)
    assert created[0]["identity"].startswith(f"{ORDERS_TASK_QUEUE}@pid")
