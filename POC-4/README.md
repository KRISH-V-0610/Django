# POC 4: Temporal instead of Celery

An order-processing service where the API only **starts** a Temporal workflow and the workflow does the real work (validate, check stock, wait for approval, reserve stock, pay, send mail) with retries, compensation and signals/queries. The same project has demos for every topic of the assignment: retries and timeouts, heartbeats, child workflows, several workers, several task queues, schedules, and a worker that is killed and restarted in the middle of a workflow.

Extra file: [CELERY_VS_TEMPORAL.md](CELERY_VS_TEMPORAL.md) (comparison and how to migrate a Celery task).

---

## 1. Objective

Understand and show, with working code:

- **A/B. Fundamentals and setup**: server, client, worker, task queue, event history, replay; a first workflow run from a worker and watched in the Web UI.
- **C. Workflows**: simple, multi-step, multi-activity, failure handling (saga with compensation), long-running, child workflows, resume after a worker restart.
- **D. Activities**: email, database update, external API simulation, file processing, retries, timeouts, heartbeats, idempotency.
- **E/F. Workers and task queues**: several workers on one queue, dedicated workers per queue, concurrency settings, routing of workflows and activities.
- **G. Retries and timeouts**: retry policy, exponential backoff, max attempts, non-retryable errors, start-to-close, schedule-to-start and heartbeat timeouts.
- **H. Signals and queries**: approve, cancel and update an order while it runs, read its live state.
- **I. Schedules** instead of Celery Beat, and a Celery to Temporal migration note.

## 2. Technologies

Python 3.12 · Django 6 · Django Ninja 1.8 (async views) · Uvicorn · Temporal (dev server) · Temporal Python SDK (`temporalio`) · PostgreSQL 17 · Docker Compose · uv · pytest, pytest-asyncio, pytest-django.

## 3. Prerequisites

- Docker with Compose (PostgreSQL and the Temporal server with its Web UI run as containers)
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself)
- Free ports: `5435` (Postgres), `7233` (Temporal gRPC), `8233` (Temporal UI), `8000` (API)
- Internet on the first `uv run pytest`: the SDK downloads the Temporal test server binary

## 4. Local setup

```bash
cd POC-4
docker compose up -d                  # postgres + temporal (UI: http://localhost:8233)
cp .env.example .env                  # placeholder secrets, fine for local use
uv sync
uv run python manage.py migrate
uv run python manage.py seed_products
uv run uvicorn config.asgi:application --port 8000
```

Workers, each in its own terminal (a worker is just a long-running process that polls a queue):

```bash
uv run python -m temporal_app.workers.run_worker                  # every queue in one process
# or one process per queue:
uv run python -m temporal_app.workers.run_worker --queue orders
uv run python -m temporal_app.workers.run_worker --queue email
uv run python -m temporal_app.workers.run_worker --queue default files
# concurrency limits:  --max-activities 10 --max-workflow-tasks 10
```

- Swagger: http://127.0.0.1:8000/api/docs
- Temporal Web UI: http://localhost:8233

Environment variables (see `.env.example`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_ENGINE` (optional), `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `ORDER_APPROVAL_THRESHOLD`. No secret is in the code.

The API must run under **uvicorn**, not `manage.py runserver` (see decision 7).

Seed data (`seed_products`): `WIDGET` 25.00 × 10, `GADGET` 40.00 × 2, `PRICEY` 1500.00 × 5, `OUT-OF-STOCK` 10.00 × 0.

## 5. Architecture

```
  curl / Swagger ──▶ Django Ninja API (uvicorn)           demos/*.py, schedules CLI
                        │  start / signal / query                │
                        ▼                                        ▼
              ┌──────────────────────── Temporal Server (:7233, UI :8233) ───────────────────────┐
              │  event history per workflow      task queues: orders · email · default · files   │
              │  schedules (tick starts a workflow)                                              │
              └───────▲──────────────────────────────▲──────────────────────────▲───────────────┘
                      │ poll                         │ poll                     │ poll
              worker (orders-queue)          worker (email-queue)      worker (default + files)
                      │ activities                   │
                      ▼                              ▼
                 PostgreSQL                    mail backend
```

- The API and the demos are **clients**: they never run workflow code.
- Workflow code only decides. Activities do the side effects (database, payment, email) through `orders/services.py`.

## 6. Concepts

| Concept | In one line | Where |
|---|---|---|
| Temporal Server | stores the event history and the task queues, runs no app code | `docker-compose.yml` |
| Client | starts workflows, sends signals, runs queries | `temporal_app/client.py` |
| Worker | polls a queue, runs workflow or activity code, keeps no state | `temporal_app/workers/` |
| Task queue | just a name used to route tasks to workers | `temporal_app/constants.py` |
| Workflow | deterministic code that orchestrates activities; its state is rebuilt from history | `temporal_app/workflows/` |
| Activity | normal code with side effects, retried by the server | `temporal_app/activities/` |
| Event history, replay, durable execution | every step is saved; after a crash another worker replays the history and continues | `demos/long_running.py` |
| Determinism | replay must make the same decisions, so no `random`, `now()`, network or DB in workflow code | header of `workflows/demo.py` |
| Workflow ID / Run ID | ID = business key (`order-<uuid>`, unique while running); run ID = one execution | `order_workflow_id()` |
| Retry policy | initial interval, backoff, max interval, max attempts, non-retryable errors | `temporal_app/policies.py` |
| Timeouts used | start-to-close, schedule-to-start, heartbeat, workflow execution timeout | `workflows/demo.py`, `workflows/order.py` |
| Heartbeat | "I am alive" + saved progress, a retry resumes from it | `activities/files.py` |
| Signals / queries | change a running workflow / read its state without changing it | `OrderWorkflow` |
| Child workflows | a parent starts one child per file, each with its own history | `workflows/files.py` |
| Schedules | the server starts a workflow on a timer (replaces Celery Beat) | `temporal_app/schedules.py` |
| Idempotency | a retried activity must not repeat its side effect | unique `Reservation` / `Payment` rows |
| Saga (compensation) | when a late step fails, undo the earlier ones in reverse order | `OrderWorkflow._compensate_and_finish` |

Explained in the docs only, **not coded**: workflow versioning (`workflow.patched`), worker versioning, schedule-to-close timeout, workflow run timeout and workflow task timeout.

## 7. API endpoints and commands

### HTTP API (`/api`)

| Method and path | What it does |
|---|---|
| `POST /orders` | create the order row and start `OrderWorkflow`; answers `201` right away. Body: `sku`, `quantity` (> 0), `customer_email`, optional `simulate` |
| `GET /orders/{id}` | the order as stored in the database |
| `GET /orders/{id}/status` | live state from the running workflow (2 queries): `status`, `approved`, `cancel_requested`, `confirmation_email`, `workflow_status`, `progress` (`completed_steps`, `percent`) |
| `POST /orders/{id}/approve` | signal: approve a large order that is waiting |
| `POST /orders/{id}/cancel?reason=...` | signal: cancel; the workflow refunds and releases stock if needed |
| `POST /orders/{id}/update-email` | signal: change the confirmation address. Body: `email` |

Error mapping: unknown SKU or order `404`, bad body `422`, workflow not running `409`, Temporal unreachable or no worker answering `503`.

`simulate` values (failure injection for demos and tests): `payment_declined`, `payment_api_down`, `payment_timeout`, `email_down`.

```bash
curl -X POST localhost:8000/api/orders -H "Content-Type: application/json" \
     -d '{"sku":"PRICEY","quantity":1,"customer_email":"me@example.com"}'
curl localhost:8000/api/orders/<id>/status
curl -X POST localhost:8000/api/orders/<id>/approve
curl -X POST "localhost:8000/api/orders/<id>/cancel?reason=changed%20mind"
```

### Demos (`uv run python -m demos.<name>`)

| Command | Topic | Needs a worker on |
|---|---|---|
| `demos.hello Krish` | B: simplest workflow | default |
| `demos.orders <scenario>` | C, G, H: `success` `large` `large_cancel` `unavailable` `declined` `api_down` `timeout` `email_down` `cancel` `duplicate` or `all` | orders + email |
| `demos.retries [scenario]` | G: `flaky` `non_retryable` `non_retryable_type` `max_attempts` `start_to_close` `heartbeat` `schedule_to_start` | default |
| `demos.notification --api-fails N` | C, D, F: multi-step workflow across two queues (waits until an email worker exists) | default, then email |
| `demos.children [--resume-demo]` | C, D: parent and child workflows; heartbeat resume | default + files |
| `demos.long_running [--steps N --pause S]` | C, E: kill the worker in the middle, start it again | default |
| `demos.multi_worker --count N` | E, F: several workers on one queue | default (run 2+) |
| `demos.schedule_overlap` | I: SKIP versus BUFFER_ONE (about 50 seconds) | default |

### Schedules (`uv run python -m temporal_app.schedules <command>`)

```
create report|notification|processing [--every SECONDS | --daily-at HH:MM --weekdays-only] [--overlap skip|buffer_one]
list · describe <id> · pause <id> · resume <id> · trigger <id> · backfill <id> --minutes N · delete <id>
```

## 8. Execution evidence

The output below was captured in a local run on Windows: SQLite instead of PostgreSQL, and the Temporal dev server started by the Python SDK (`WorkflowEnvironment.start_local`) instead of Docker. Worker processes and uvicorn were real. No screenshots are committed.

**Order scenarios** (`demos.orders all`, shortened):

```
=== success: happy path: all steps, order COMPLETED ===
  database now   : {'db_status': 'COMPLETED', 'stock_now': 8, 'payments': 1, 'payment_status': 'CHARGED', ...}
  progress query : {'completed_steps': ['validate', 'check_availability', 'reserve_inventory', 'payment', 'update_status', 'email', 'complete'], 'percent': 100, 'total_steps': 7}

=== large_cancel: cancel signal while waiting for approval ===
  workflow result: status='CANCELLED', reason='customer changed their mind', steps=['validate', 'check_availability']
  database now   : {'db_status': 'CANCELLED', 'stock_now': 5, 'payments': 0, ...}

=== declined: card declined: non-retryable, stock is released again ===
  workflow result: status='FAILED', reason='PaymentDeclined: card was declined'
  database now   : {'db_status': 'FAILED', 'stock_now': 10, 'payments': 0, 'reservation_released': True}

=== timeout: attempt 1 charges but times out; the retry must not charge twice ===
  database now   : {'db_status': 'COMPLETED', 'stock_now': 9, 'payments': 1, 'payment_status': 'CHARGED', ...}

=== cancel: cancel while processing: refund + release stock ===
  database now   : {'db_status': 'CANCELLED', 'stock_now': 10, 'payments': 1, 'payment_status': 'REFUNDED', 'reservation_released': True}

=== duplicate: a second start with the same workflow id is refused ===
  second start with the same workflow id was refused by Temporal (good)
```

**Retries** (`demos.retries flaky non_retryable`):

```
=== flaky: fails twice, 1s then 2s backoff, third attempt works ===
took 4.1s -> {'outcome': 'succeeded', 'result': {'api': 'flaky-api', 'attempt': 3, 'data': 'ok'}, ...}
=== non_retryable: ApplicationError(non_retryable=True): stops at once ===
took 0.1s -> {'error_type': 'BusinessRuleViolation', 'outcome': 'failed', 'retry_state': 'NON_RETRYABLE_FAILURE', ...}
```

**Worker killed in the middle of a workflow** (`demos.long_running --steps 8 --pause 3`). The worker was killed at 02:04:26 and a new one started at 02:04:39:

```
02:04:25 progress: {'done': 2, 'stopped': False, 'total': 8}
02:04:27 progress: {'done': 3, 'stopped': False, 'total': 8}
02:04:32 no worker answered the query (stopped? start it again, the workflow keeps its place)
02:04:37 no worker answered the query (stopped? start it again, the workflow keeps its place)
02:04:42 no worker answered the query (stopped? start it again, the workflow keeps its place)
02:04:45 progress: {'done': 3, 'stopped': False, 'total': 8}      <- new worker, same step
02:04:57 result: finished 8 of 8 steps
```

**Two workers on the same queue** (`demos.multi_worker --count 40`):

```
activities handled per worker process: {'70796': 21, '33216': 19}
```

**Child workflows** (`demos.children`): `{'files_failed': 0, 'files_found': 3, 'files_processed': 3, 'lines': 180, 'words': 1440}`

**HTTP API against the real server** (uvicorn + worker, `/api/docs` and `/api/openapi.json` both `200`):

| Request | Result |
|---|---|
| `POST /orders` WIDGET × 2 | `201`, order ends `COMPLETED` |
| `POST /orders` PRICEY × 1 | `201`; `GET /status` shows `AWAITING_APPROVAL` |
| `update-email` then `approve` | `200` both; order ends `COMPLETED` |
| second PRICEY order, `cancel?reason=changed mind` | `200`; order ends `CANCELLED`, reason `changed mind` |
| `simulate: payment_declined` | order ends `FAILED`, `PaymentDeclined: card was declined` |
| unknown SKU / unknown order / signal on unknown order | `404` |
| `quantity: 0` / `simulate: boom` | `422` |

Database afterwards: `WIDGET` stock 10 − 1 − 2 = 7 (two completed orders; the declined one reserved 1 and released it again), `PRICEY` stock 5 − 1 = 4 (the cancelled order never reserved anything), 3 payments, all `CHARGED`.

**Schedule** (`schedules create report --every 5`, then `trigger`, `describe`, `pause`): `overlap policy: SKIP`, `actions started: 9`, `skipped (overlap): 5`.

**Temporal server not reachable** (it was not running during one of my API runs): `POST /orders` answered `503 {"detail": "cannot reach the Temporal server"}` and the order was stored as `FAILED` with reason `could not start workflow`.

## 9. Tests and results

```bash
uv run pytest                                   # with the compose Postgres running
# quick run without Docker (this is how the results below were produced):
DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: uv run pytest
```

Result: **106 passed** in about 2 minutes (SQLite).

| File | Tests | What it checks | How |
|---|---|---|---|
| `test_services.py` | 17 | order logic: totals, stock, reserve/charge/refund/release, each safe to repeat | real database |
| `test_activities.py` | 23 | every activity alone: attempts, non-retryable errors, heartbeats, resume, file handling | `ActivityEnvironment`, no server |
| `test_order_workflow.py` | 13 | `OrderWorkflow`: happy path, each failure, compensation, approval, cancel, update-email, approval timeout, workflow cancel | Temporal **time-skipping** test server, **mock activities** with the real names |
| `test_demo_workflows.py` | 17 | hello, notification (two queues), every retry/timeout scenario, long-running, report with durable sleep, child workflows | time-skipping server, DB-touching activities mocked |
| `test_api.py` | 25 | what the API sends to Temporal and how errors become `404/409/422/503` | Temporal client **mocked** |
| `test_workers.py` | 11 | which queue runs which workflows/activities, CLI options | configuration only |

Time skipping means a 24 hour approval timeout finishes in milliseconds.

**Not covered by automated tests:** the real activities running inside a real worker, and the real API talking to a real server. I checked those by hand (section 8).

**Not run yet:** `docker compose up` itself, a real PostgreSQL (so `select_for_update` was only exercised on SQLite), the Temporal Web UI, and these live runs: `demos.retries` scenarios other than `flaky` and `non_retryable`, `demos.children --resume-demo`, the `stop` signal of `demos.long_running`, `demos.schedule_overlap`, and `schedules` `resume`, `backfill`, `delete`. The ones with a test (all retry scenarios, resume from heartbeat, stop signal) pass on the time-skipping server.

## 10. Failure scenarios

| Scenario | What happens |
|---|---|
| Card declined | `PaymentDeclined` is non-retryable. Stock is released, order `FAILED` |
| Not enough stock | `ProductUnavailable` is non-retryable. Nothing to undo, order `FAILED` |
| Payment gateway down | retried with backoff (1s, 2s, 4s... up to 5 attempts), then works on the 3rd attempt in the demo |
| Payment charged but the answer is lost (timeout) | the retry finds the existing `Payment` row and does not charge again |
| Email keeps failing | 3 attempts, then the order still completes with the note `confirmation email could not be sent` |
| Cancel while waiting for approval | order `CANCELLED`, nothing was reserved |
| Cancel during payment | refund payment, release stock, order `CANCELLED` |
| Nobody approves for 24 hours | the workflow cancels the order (checked on the time-skipping server) |
| Same order started twice | Temporal refuses the second workflow with the same ID |
| Worker dies or restarts mid-workflow | the workflow waits, another worker replays the history and continues at the same step |
| No worker on a queue | the task waits; with a schedule-to-start timeout it fails after that time; the API status query answers `503` after 5 seconds |
| Temporal server unreachable | API answers `503` and marks the order `FAILED: could not start workflow` |
| Signal to a finished workflow | API answers `409` (checked with the mocked client) |

## 11. Technical decisions

1. **Workflow ID = business ID** (`order-<uuid>`). It gives free de-duplication.
2. **Workflows decide, activities do.** Activities are thin wrappers over `orders/services.py`, so the logic can be tested without Temporal.
3. **Idempotent activities.** One-to-one `Reservation` and `Payment` rows plus `get_or_create`. A retry never repeats a side effect.
4. **Compensation inside the workflow** (a plain `try / except`), not a database rollback, because the steps span several activities and a payment.
5. **Small payloads.** Activities receive the `order_id` and load the row, so the history stays small.
6. **Email failure does not undo the order.** The customer already paid, so it is logged and the order completes.
7. **Async API under uvicorn only.** The cached Temporal client is bound to the event loop it was created on, and `runserver` can use a new loop per request.
8. **Status in two places.** Activities keep the `Order.status` column up to date (so `GET /orders/{id}` works without Temporal); `GET /orders/{id}/status` asks the live workflow with queries.
9. **Separate task queues** (orders, email, files, default) so each workload can have its own workers and limits.
10. **Failure injection with a `simulate` field** on the order, so every scenario of the assignment can be triggered on purpose.
11. **Time-skipping test server with mock activities** for workflow tests, real database for service tests.

## 12. Challenges

- Understanding why workflow code must be deterministic and what is allowed inside it. Django models cannot be imported in the workflow sandbox the normal way (`imports_passed_through`), and `constants.py` has no Django imports for the same reason.
- A cancel can arrive while an activity is half done. The flags `_reserved` and `_charged` are set **before** the activity runs, so the compensation always covers it and is harmless if the step never happened.
- Making the payment-timeout case safe. The first attempt really charges, so a naive retry would charge twice. The unique `Payment` row fixed it.
- Async tests with the Django ORM need `transaction=True`, and the time-skipping server is started once per session to keep the tests fast.
- Getting a Temporal server to run reliably on my Windows machine for the live checks.

## 13. Key learnings

- Celery moves a message, Temporal keeps the **execution**. The event history is the state, and replay is how a new worker catches up.
- A worker keeps nothing. Killing it did not lose the workflow, it only paused it (section 8).
- Retries are not a safety net on their own. Anything with a side effect needs idempotency.
- Signals change a running workflow, queries only read it.
- A task queue is a routing name. Whoever listens there and has registered the code gets the task.
- A schedule living in the server fixes Celery Beat's "two beat processes run the job twice".
- Business errors and temporary errors must be told apart (non-retryable versus retried).

## 14. Future improvements

- Workflow versioning (`workflow.patched`) and replay tests (`Replayer`) so a deployed change cannot break running workflows.
- A production Temporal setup (its own PostgreSQL, not `start-dev`), plus the live checks listed under "Not run yet".
- Idempotency keys sent to a real payment and email provider.
- Search attributes to list orders by status in the Web UI.
- OpenTelemetry tracing through the Temporal interceptors (ties back to POC 3).
- Authentication on the API and an alert when a workflow fails.
- CI that runs the tests against PostgreSQL.

---

## AI assistance

The test cases (`tests/`) and the documentation were generated with the help of AI.
