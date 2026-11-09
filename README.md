# Django Learning: POC 1 to 4

These are the four backend POCs I built during my internship. Each one is a small working project around one topic. Every folder has its own README with setup steps, architecture, tests, failure scenarios and what I learned. This page is only the map.

## The four POCs

| # | Topic | Folder | Tests |
|---|---|---|---|
| 1 | Django Ninja API, JWT auth, idempotency, transactions | [POC-1](POC-1) | 30 |
| 2 | Python 3.14 features, ORM constraints, F and Q objects | [POC-2](POC-2) | 48 |
| 3 | ASGI, OpenTelemetry, Redis Streams workers, IDOR and OWASP | [POC-3](POC-3) | 105 |
| 4 | Temporal instead of Celery | [POC-4](POC-4) | 106 |

## POC 1: Ninja API, JWT, idempotency, transactions

A tiny shop where "Place Order" never charges twice and never leaves half an order behind.

- Register, login, refresh and logout with JWT. Orders sit behind auth, and the API is versioned under `/api/v1/`.
- `POST /orders/place` runs in one transaction. There is a savepoint for an optional free sample, `on_commit` for the confirmation email, and `select_for_update` so two buyers can't take the last item.
- An `Idempotency-Key` header makes a retried request return the first answer instead of creating a second order.

Stack: Python 3.12, Django 6, Django Ninja, PostgreSQL 17, PyJWT, uv, pytest.

## POC 2: Python 3.14 and Django ORM constraints

A small shop where the database itself refuses bad data.

- Small scripts in `python_features/`: UUIDv7, frozen dataclass, plain Python datasets, the walrus operator, and `assert` versus real exceptions (run it with `python -O` to see why).
- The ORM side: unique constraints (single, multi-column, conditional), check constraints, F expressions for stock updates, and Q objects for a search where every filter is optional.

Stack: Python 3.14, Django 6, Django Ninja, PostgreSQL 17, uv, pytest. It reuses the Postgres container from POC 1.

## POC 3: ASGI, OpenTelemetry, Redis Streams, OWASP

One request travels client, API, database, Redis stream, worker and an external service, and I can see the whole trip as one trace in Jaeger.

- A raw ASGI app with middleware, next to the Django one, to see how ASGI differs from WSGI.
- OpenTelemetry: custom spans, errors on spans, context passed from the API into the worker.
- Redis Streams workers: consumer groups, retries with back-off, recovery of a crashed worker, a dead-letter stream, and idempotent job processing.
- IDOR and the other OWASP demos. Each has a vulnerable version and a secure version, with tests for both.

The `/vulnerable` endpoints are broken on purpose, so keep this project local.

Stack: Python 3.12, Django 6, Django Ninja, Uvicorn, PostgreSQL 17, Redis 7, Jaeger, uv, pytest.

## POC 4: Temporal instead of Celery

An order service where the API only starts a Temporal workflow, and the workflow does the real work.

- The order workflow validates, checks stock, optionally waits for an approval, reserves stock, takes payment and sends an email. If a later step fails, it refunds and releases the stock.
- Signals (approve, cancel, update email) and queries (status, progress) on a running order.
- Retry policies, timeouts and heartbeats, child workflows, several task queues and workers, and schedules instead of Celery Beat.
- A worker can be killed in the middle of a workflow and the workflow carries on at the same step when a worker comes back.

There is also [CELERY_VS_TEMPORAL.md](POC-4/CELERY_VS_TEMPORAL.md), which compares the two and shows how I would migrate a Celery task.

Stack: Python 3.12, Django 6, Django Ninja, Uvicorn, Temporal, PostgreSQL 17, uv, pytest.

## Running them

You need Docker and [uv](https://docs.astral.sh/uv/). The basic steps are the same everywhere:

```bash
cd POC-1                      # or POC-2, POC-3, POC-4
docker compose up -d          # POC 2 has no compose file, it uses POC 1's database
cp .env.example .env
uv sync
uv run python manage.py migrate
uv run pytest
```

POC 3 and 4 also need extra processes (uvicorn and workers). The exact commands are in the "Local setup" section of their READMEs.

| POC | Postgres port | Other ports |
|---|---|---|
| 1 | 5433 | |
| 2 | 5433 (same container as POC 1, database `django_poc2`) | |
| 3 | 5434 | Redis 6379, Jaeger UI 16686, OTLP 4317 and 4318 |
| 4 | 5435 | Temporal 7233, Temporal UI 8233 |

Every API listens on port 8000 by default, so run one POC at a time.

About the tests: POC 1 and 2 expect the Postgres container to be running. POC 3 and 4 can also run without Docker on SQLite, and the command for that is in their READMEs.

## Folder layout

```
Django-Learning/
├── POC-1/    accounts, orders, transactions, idempotency
├── POC-2/    shop app + python_features scripts
├── POC-3/    asgi-demo, jobs (Redis Streams), owasp, scripts, OWASP.md
└── POC-4/    temporal_app, orders, demos, CELERY_VS_TEMPORAL.md
```

## Notes

- `.env` files are not committed. `.env.example` in each folder has placeholder values that are fine for local use.
- The POC 3 and POC 4 READMEs follow the section list from the assignment, and both say clearly what has not been run yet.

## AI assistance

The test cases and the documentation were generated with the help of AI.
