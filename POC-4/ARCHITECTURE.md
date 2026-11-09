# Architecture

Diagrams are Mermaid, so GitHub and VS Code render them.

## 1. The four parts

```mermaid
flowchart LR
    subgraph clients["Clients (start work, never run it)"]
        API["Django Ninja API<br/>(uvicorn, async)"]
        CLI["demos/*.py and<br/>schedules CLI"]
    end

    subgraph server["Temporal Server (docker: temporal, port 7233, UI 8233)"]
        HIST[("Event history<br/>one per workflow")]
        TQ["Task queues<br/>default / orders / email / files"]
        SCH["Schedules"]
    end

    subgraph workers["Workers (long-running processes, poll the server)"]
        W1["worker: orders-queue"]
        W2["worker: email-queue"]
        W3["worker: default-queue + files-queue"]
    end

    DB[("PostgreSQL<br/>products, orders,<br/>payments, reservations")]

    API -- "start / signal / query" --> server
    CLI -- "start / signal / query" --> server
    SCH -- "starts a workflow on a tick" --> TQ
    TQ <-- "workers POLL for tasks" --> W1
    TQ <-- "workers POLL for tasks" --> W2
    TQ <-- "workers POLL for tasks" --> W3
    W1 -- "activities read and write" --> DB
    W2 -- "read order, send mail" --> DB
    API -- "reads orders" --> DB
```

- The **client** only talks to the server (gRPC, port 7233). It does not know where a worker is.
- The **server** stores the event history and the task queues. It runs no application code.
- A **worker** polls a task queue, runs workflow code or an activity, and reports the result. It keeps nothing between tasks, so any number of workers can be started, stopped or crashed.
- **Activities** are the only place that touches PostgreSQL, the (fake) payment gateway and email.

## 2. One order, start to finish

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (curl)
    participant A as Django API
    participant T as Temporal Server
    participant O as Worker (orders-queue)
    participant E as Worker (email-queue)
    participant D as PostgreSQL

    C->>A: POST /api/orders
    A->>D: insert Order (PENDING)
    A->>T: start_workflow(OrderWorkflow, id="order-<uuid>", queue=orders-queue)
    A-->>C: 201 (answers right away)
    T->>O: workflow task
    O->>T: run OrderWorkflow, schedule validate_order
    T->>O: activity task: validate_order
    O->>D: read order
    Note over O,T: check_availability, then (total >= 1000) wait for the approve signal
    O->>D: reserve_inventory (unique Reservation row)
    O->>D: charge_payment (unique Payment row)
    O->>D: update_order_status CONFIRMED
    T->>E: activity task: send_order_confirmation (email-queue)
    E-->>T: done
    O->>D: update_order_status COMPLETED
    C->>A: GET /api/orders/{id}/status
    A->>T: query status + progress
    T-->>A: answer from the workflow object
    A-->>C: 200 with steps and percent
```

Every arrow between a worker and the server becomes an **event in the history** (`WorkflowTaskScheduled`, `ActivityTaskScheduled`, `ActivityTaskCompleted`, ...). That list is the state of the workflow.

## 3. The order workflow as a flow (saga)

```mermaid
flowchart TD
    S([start]) --> V[validate_order]
    V --> AV[check_availability]
    AV --> BIG{total >= 1000?}
    BIG -- yes --> WAIT["status AWAITING_APPROVAL<br/>wait_condition(approve or cancel, 24h)"]
    BIG -- no --> R
    WAIT -- approve --> R[reserve_inventory]
    WAIT -- "cancel / 24h timeout" --> COMP
    R --> P["charge_payment<br/>(retry: 1s, 2s, 4s... max 5 tries)"]
    P --> ST[update_order_status CONFIRMED]
    ST --> EM["send_order_confirmation<br/>(email-queue, 3 tries)"]
    EM -- ok --> DONE[update_order_status COMPLETED]
    EM -- "keeps failing" --> DONE2["COMPLETED with note<br/>(order stays valid)"]
    V -. "business error / cancel signal" .-> COMP
    AV -. "ProductUnavailable" .-> COMP
    R -. "cancel signal" .-> COMP
    P -. "PaymentDeclined / cancel signal" .-> COMP
    ST -. "cancel signal" .-> COMP
    COMP["COMPENSATING<br/>refund_payment if charged<br/>release_inventory if reserved"] --> END2[FAILED or CANCELLED]
```

- Solid arrows are the happy path. Dotted arrows are failures; the workflow undoes what it did, newest first.
- A **retryable** error (gateway down, timeout) never reaches the dotted path until the retry policy gives up.
- The `cancel` signal is checked after each step (`_check_cancel`) and while waiting for approval.

## 4. What happens when a worker dies

```mermaid
sequenceDiagram
    participant T as Temporal Server (history)
    participant W1 as Worker 1
    participant W2 as Worker 2 (started later)

    W1->>T: activity 1..3 completed (3 events saved)
    W1--xT: crash while running activity 4
    Note over T: nothing lost, the history still says "3 done, 4 started"
    Note over T: activity 4 times out (start-to-close / heartbeat) and is scheduled again
    W2->>T: poll the queue
    T->>W2: workflow task with the full history
    Note over W2: REPLAY: run the workflow code from the top,<br/>every await that is already in the history returns its<br/>saved result at once, no activity is run again
    W2->>T: continue at activity 4
```

That is why workflow code must be **deterministic**: the replay must reach the same decisions as the first run, otherwise it cannot line up with the history.

## 5. Task queues and who listens

| Task queue | Workflows registered | Activities registered | Started with |
|---|---|---|---|
| `default-queue` | Hello, Notification, RetryDemo, LongRunning, Report, DataProcessing | demo activities, `record_notification`, `generate_report`, `list_inbox_files` | `--queue default` |
| `orders-queue` | OrderWorkflow | validate, availability, reserve, charge, update status, release, refund | `--queue orders` |
| `email-queue` | none (activity-only worker) | `send_email`, `send_order_confirmation` | `--queue email` |
| `files-queue` | ProcessFileWorkflow (child) | `process_file`, `archive_file` | `--queue files` |

`--queue all` (the default) starts one worker per queue inside a single process. A workflow asks for an activity on a queue (`task_queue=EMAIL_TASK_QUEUE`), and only a worker that listens there and has registered that activity will pick it up. If nobody listens, the task waits (`demos.notification` shows this) or hits a `schedule_to_start` timeout (`demos.retries schedule_to_start`).

## 6. Database

```mermaid
erDiagram
    PRODUCT ||--o{ ORDER : "ordered as"
    ORDER ||--o| RESERVATION : "has one"
    ORDER ||--o| PAYMENT : "has one"
    PRODUCT ||--o{ RESERVATION : "stock taken from"

    PRODUCT {
        string sku UK
        string name
        decimal price
        int stock
    }
    ORDER {
        uuid id PK
        int quantity
        string customer_email
        decimal total_amount
        string simulate
        string status
        text failure_reason
    }
    RESERVATION {
        int quantity
        bool released
    }
    PAYMENT {
        decimal amount
        string status
    }
    NOTIFICATIONLOG {
        string workflow_id UK
        string recipient
        string subject
    }
```

- `RESERVATION.order` and `PAYMENT.order` are one-to-one. That unique constraint is what makes the activities **idempotent**: a retry finds the existing row and does not take stock or money twice.
- `NOTIFICATIONLOG.workflow_id` is unique for the same reason.
- Order status values: `PENDING`, `AWAITING_APPROVAL`, `CONFIRMED`, `COMPLETED`, `FAILED`, `CANCELLED`.
- The Temporal server keeps its own history (`start-dev` stores it in a SQLite file on a Docker volume). That is separate from the Django PostgreSQL database.

## 7. Schedules (replacement for Celery Beat)

```mermaid
flowchart LR
    SC["Schedule (stored in the Temporal server)<br/>every N seconds or daily at HH:MM"] -- tick --> POL{"previous run<br/>still going?"}
    POL -- no --> START["start workflow<br/>(Report / Notification / DataProcessing)"]
    POL -- "yes, overlap = SKIP" --> DROP["tick dropped, counted as skipped"]
    POL -- "yes, overlap = BUFFER_ONE" --> BUF["one tick kept, runs when the current one ends"]
```

Created, paused, resumed, triggered and deleted with `python -m temporal_app.schedules ...`. Because a tick starts an ordinary workflow, scheduled runs get retries and history like any other.

## 8. Project map

```
POC-4/
├── config/                  Django settings (env based), urls, asgi
├── orders/                  Django app: models, services (DB logic), api (Ninja), seed_products command
├── temporal_app/
│   ├── constants.py         task queue names, timeouts (no Django imports, used inside workflows)
│   ├── policies.py          retry policies
│   ├── payloads.py          dataclasses passed between client, workflows and activities
│   ├── client.py            one cached Temporal client per process
│   ├── bootstrap.py         django.setup() for workers and CLIs
│   ├── schedules.py         schedules CLI (create / list / describe / pause / ...)
│   ├── workflows/           order.py (saga), demo.py (hello, retries, long running, report), files.py (parent + child)
│   ├── activities/          orders.py, demo.py, notifications.py, files.py, reports.py
│   └── workers/             registry.py (queue -> workflows + activities), run_worker.py
├── demos/                   runnable scenarios for each topic of the assignment
├── tests/                   activity, workflow (time-skipping server), service, API and worker tests
└── docker-compose.yml       PostgreSQL 17 + Temporal dev server with Web UI
```

Layering rule: **workflows decide, activities do, services hold the database logic**. A workflow never touches the database. An activity is a thin wrapper around a function in `orders/services.py`, so the same logic is tested without Temporal.
