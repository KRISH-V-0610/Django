# Celery vs Temporal

How a Celery-style background job maps to Temporal, and what changes when we move. Every "Temporal side" below points to code in this project.

## 1. The one idea that differs

- **Celery** sends a *message* ("run task X with these arguments") to a broker. A worker picks it up and runs it. Celery does not remember what the job is doing. If the job has 5 steps and the worker dies after step 3, Celery only knows "a message was delivered".
- **Temporal** stores the *execution* of a job. Every step that finished is written to an **event history** on the Temporal server. If a worker dies after step 3, another worker **replays** the history, skips what already happened and carries on at step 4.

So with Celery I write tasks and glue them together myself (retries, state in a DB, "what step are we on?"). With Temporal the glue is the workflow, and the server keeps the state.

## 2. Vocabulary map

| Celery | Temporal | Where in this project |
|---|---|---|
| `@shared_task` function | **Activity** (`@activity.defn`) | `temporal_app/activities/` |
| A chain of tasks (`a.s() \| b.s()`) | A **Workflow** that awaits activities one after another | `workflows/order.py` (`_process`) |
| `group(...)` | `asyncio.gather` over **child workflows** or activities | `workflows/files.py` (`DataProcessingWorkflow`) |
| `chord` (group, then a callback) | `gather`, then the next `await` | `DataProcessingWorkflow` collects all child results |
| Broker (Redis / RabbitMQ) | The Temporal server (its **task queues** are inside it) | `docker-compose.yml` service `temporal` |
| Queue name / `-Q email` | **Task queue** + `--queue email` on the worker | `temporal_app/constants.py`, `workers/run_worker.py` |
| `autoretry_for`, `retry_backoff`, `max_retries` | **RetryPolicy** (initial interval, backoff, max interval, max attempts) | `temporal_app/policies.py` |
| `Reject` / do not retry | `ApplicationError(non_retryable=True)` or `non_retryable_error_types` | `activities/orders.py`, `policies.py` |
| `soft_time_limit` / `time_limit` | `start_to_close_timeout` (+ `schedule_to_start`, `heartbeat_timeout`) | `workflows/demo.py` (`RetryDemoWorkflow`) |
| `countdown` / `eta` | `await workflow.sleep(...)` (durable timer, survives restarts) | `workflows/demo.py` (`ReportWorkflow`; `LongRunningWorkflow` pauses with `wait_condition(timeout=...)`) |
| `AsyncResult.get()` | `handle.result()` (the workflow result is stored in history) | `demos/*.py` |
| `AsyncResult.state` / custom progress | **Query** (`@workflow.query`) | `OrderWorkflow.status`, `.progress` |
| `revoke(task_id)` | **Signal** (`cancel`) or `handle.cancel()` | `OrderWorkflow.cancel` |
| Waiting for a human (poll a DB flag) | `workflow.wait_condition` + **Signal** (`approve`) | `OrderWorkflow` (orders over 1000) |
| Celery Beat | **Schedules** (live inside the server) | `temporal_app/schedules.py` |
| Flower (monitoring) | **Temporal Web UI** (history, pending activities, schedules) | http://localhost:8233 |
| `acks_late` + idempotent task | Retries are built in, **activities must still be idempotent** | `Reservation` / `Payment` unique rows |

## 3. Things Temporal gives me that I would have to build with Celery

| Need | Celery | Temporal |
|---|---|---|
| Resume after a crash in the middle of a job | Not built in. Store progress yourself, or lose / repeat work | Automatic: replay the history |
| See exactly what a job did and when | Logs, maybe a results table | Event history per workflow in the UI |
| Undo steps when a later step fails (saga) | Hand-written, often forgotten | A normal `try / except` in workflow code (`_compensate_and_finish`) |
| Wait hours or days for an event | `eta` tasks sit in the broker, easy to lose | `wait_condition` with a timeout, state kept on the server |
| Talk to a running job (approve, cancel, read state) | Extra DB table + polling | Signals and queries |
| Schedule that never runs twice and can be paused | Beat is a single process (two beats = double runs) | Schedule is a server object with an overlap policy |
| One job exactly once per business id | Own dedup key | Workflow ID is unique while running (`order-<uuid>`) |

## 4. What Temporal costs

- **A server to run.** Temporal needs its own service and database. In production that is more to operate than a Redis broker (this POC only uses `start-dev`).
- **Workflow code must be deterministic.** No `random`, no `datetime.now()`, no network, no DB, no `time.sleep` inside a workflow, because the code is replayed and must make the same decisions. All of that goes into activities (`workflows/demo.py` header has the rules).
- **Changing a workflow that is already running needs care.** Old executions replay with the new code. A change that alters the order of commands breaks replay unless it is versioned (`workflow.patched`) or the old runs are drained first. I did not implement versioning, only noted it.
- **A learning curve.** Workflow / activity / worker / task queue / history is more to learn than "a function with a decorator".
- **Payload limits.** Arguments and results go into the history, so they must stay small (IDs, not whole files). That is why activities here pass `order_id` and read the row themselves.

## 5. Retries do not make things safe

A retry runs the activity again, and the first attempt may already have done part of the work (the charge went through but the reply was lost). So each activity that changes something is written to be **idempotent**:

- `reserve_inventory` and `charge_payment` use a unique row per order (`Reservation`, `Payment`, both one-to-one with the order) plus `get_or_create`. A second attempt finds the row and does nothing.
- `NotificationLog.workflow_id` is unique, so a retried notification never creates two log rows.
- Compensation (`refund_payment`, `release_inventory`) is also safe to run twice: the second call finds nothing left to undo and returns `False`. It is harmless if the step never happened.

`tests/test_activities.py` calls `reserve_inventory`, `charge_payment` and `record_notification` twice and checks that the database changed only once.

## 6. How I would migrate an existing Celery task

1. Pick one task that has pain (steps, retries, waiting). Leave the simple fire-and-forget ones for later.
2. Turn each *side-effecting* piece into an **activity** (a function with `@activity.defn`). Keep it idempotent. Put a timeout and a retry policy on the call.
3. Write the **workflow** that calls those activities in order. Move the state that used to live in a DB "status" flag into workflow fields and expose it with a query.
4. Replace `task.delay(...)` in the Django view with `client.start_workflow(..., id=<business id>, task_queue=...)` (see `orders/api.py`).
5. Replace Beat entries with a **Schedule**.
6. Run a worker for the new task queue. Run old Celery and new Temporal side by side until the queue of old tasks is empty, then remove the Celery task.

## 7. Short version for the conversation

> Celery moves a message, Temporal keeps the execution. If the worker dies, Celery needs me to have saved the progress, Temporal replays the history and continues. I pay for that with a server to run and the rule that workflow code must be deterministic.
