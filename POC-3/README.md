# POC 3: ASGI, OpenTelemetry, Redis Streams and OWASP

A small backend where one request travels **client → API → database → Redis stream → worker → external service**, and I can see the whole trip as a single trace in Jaeger. The same project also holds the IDOR demo and the other OWASP demos (each with a vulnerable and a secure version).

> **Warning:** the `/vulnerable` endpoints are broken on purpose. This project is for local use only. Never deploy it.

Extra file: [OWASP.md](OWASP.md) (the 6 points for each OWASP topic).

---

## 1. Objective

Understand and show, with working code:

- **A. ASGI**: how it differs from WSGI, sync vs async views, the app lifecycle, WebSockets, middleware, Uvicorn.
- **B. OpenTelemetry**: traces, spans, context propagation, custom spans, error capture, DB spans, export to Jaeger.
- **C. Redis Streams**: consumer groups, XACK, retries with back-off, recovery of crashed workers (XPENDING / XCLAIM), dead-letter stream, idempotent workers, several workers at once.
- **D. IDOR**: a vulnerable endpoint, the secure one, and tests for both.
- **E. OWASP demos**: IDOR, SQL injection, authentication / authorization, misconfiguration, sensitive data, error handling, security logging.

## 2. Technologies

Python 3.12 · Django 6 · Django Ninja 1.8 · Uvicorn · PostgreSQL 17 · Redis 7 (redis-py) · OpenTelemetry (SDK, OTLP/HTTP exporter, Django-ASGI and psycopg instrumentation) · Jaeger 1.62 · PyJWT · Docker Compose · uv · pytest, pytest-asyncio, fakeredis, httpx, websockets.

## 3. Prerequisites

- Docker with Compose (Postgres, Redis and Jaeger run as containers)
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself)
- Free ports: `5434` (Postgres), `6379` (Redis), `16686` / `4317` / `4318` (Jaeger), `8000` (API)

## 4. Local setup

```bash
cd POC-3
docker compose up -d                  # postgres, redis, jaeger
cp .env.example .env                  # placeholder secrets, fine for local use
uv sync
uv run python manage.py migrate
uv run uvicorn config.asgi:application --port 8000
```

In extra terminals (one worker is enough, two or three show the load being shared):

```bash
uv run python manage.py run_worker --name worker-1
uv run python manage.py run_worker --name worker-2
```

- Swagger: http://127.0.0.1:8000/api/docs
- Jaeger UI: http://localhost:16686 (services: `django-api`, `worker`, `demo-client`)

Environment variables (see `.env.example`): `SECRET_KEY`, `JWT_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DB_*`, `REDIS_URL`, `OTEL_EXPORTER` (`otlp` | `console` | `none`), `OTEL_EXPORTER_OTLP_ENDPOINT`. No secret is in the code. If Jaeger is not running, set `OTEL_EXPORTER=console` (prints spans) or `none`.

## 5. Architecture

```
                         ┌──────────────── uvicorn (one process, one event loop) ────────────────┐
 client ──HTTP / WS────▶ │ TimingMiddleware ▶ ASGI router ─▶ /ws/*   (echo, chat)                │
                         │                              └──▶ Django ─▶ Ninja API ──▶ PostgreSQL  │
                         └──────────────────────────────────────────────┬─────────────────────────┘
                                                                        │ XADD (+ traceparent)
                                                                        ▼
                                                              Redis stream "jobs"
                                                           consumer group "workers"
                                                       ┌────────────┴────────────┐
                                                   worker-1                  worker-2
                                                       │  process_job (DB, idempotent)
                                                       ▼  fake mail provider (CLIENT span)
                                            failed 3 times ─▶ stream "jobs:dlq"

 every hop creates spans ──▶ OpenTelemetry ──▶ Jaeger
```

`config/asgi.py` is a tiny hand-written ASGI router (lifespan, websocket, http) wrapped by one pure-ASGI middleware. Everything under `/api` is handed to Django.

## 6. Concepts

| Topic | What I show | Where |
|---|---|---|
| A. ASGI | WSGI vs ASGI side by side, sync vs async timing, lifespan, WebSocket, middleware | `asgi-demo/`, `config/asgi.py`, `core/asgi_*.py`, `core/websocket.py`, `core/api.py` |
| B. OpenTelemetry | custom spans, parent/child, exceptions recorded on a span, `traceparent` carried through Redis, DB spans | `core/telemetry.py`, `core/api.py`, `jobs/producer.py`, `jobs/worker.py`, `jobs/service.py` |
| C. Redis Streams | consumer group, XACK after success, retry with exponential back-off, XPENDING/XCLAIM recovery, DLQ, idempotent processing | `jobs/streams.py`, `jobs/worker.py`, `jobs/service.py`, `jobs/models.py` |
| D. IDOR | vulnerable and secure routers over one model | `documents/api.py` |
| E. OWASP | seven demos, vulnerable next to secure | `owasp/api.py`, `owasp/config_audit.py`, `core/security_log.py`, [OWASP.md](OWASP.md) |

Rules the worker follows:

1. A message is only **acknowledged (XACK) after the work succeeded**. A crash before that leaves it pending.
2. A pending message of a dead worker is taken over with **XCLAIM** after it has been idle for a while.
3. A failed job is **re-queued with a longer wait each time** (2 s, 4 s, ...). After 3 attempts it goes to `jobs:dlq`.
4. The same message can arrive twice, so processing is **idempotent**: a `JobEffect` row (one per job) is created inside a transaction with the job row locked.
5. A long job sends a **heartbeat** (XCLAIM `JUSTID` on its own message) so other workers do not steal it.

## 7. API endpoints and commands

All paths start with `/api`. Protected endpoints need `Authorization: Bearer <token>` from `/api/auth/login`.

| Group | Endpoints |
|---|---|
| Health, auth | `GET /health` · `POST /auth/register` · `POST /auth/login` |
| Demo (A, B) | `GET /demo/sync` · `/demo/async` · `/demo/async-blocking` · `/demo/db-sync` · `/demo/db-async` · `/demo/trace?fail=true` |
| Jobs (C) | `POST /jobs` (202, or 503 if Redis is down) · `GET /jobs` · `GET /jobs/stats` · `GET /jobs/dead-letters` · `GET /jobs/{job_id}` |
| Documents (D, secure) | `POST/GET /documents` · `GET/PUT/DELETE /documents/{id}` (only your own) |
| IDOR demo (D, vulnerable) | `GET/PUT/DELETE /vulnerable/documents/{id}` |
| OWASP (E) | `/owasp/sqli/{vulnerable,secure}` · `/owasp/auth/{vulnerable-login,secure-login}` · `/owasp/admin/users/{vulnerable,secure}` · `/owasp/misconfig/{vulnerable,secure}` · `/owasp/profile/{vulnerable,secure}` · `/owasp/errors/{vulnerable,secure}` |
| WebSocket (A) | `/ws/echo` · `/ws/chat` |

Every HTTP response carries `X-Process-Time-Ms` (from the middleware).

**Commands**

```bash
uv run python manage.py run_worker --name worker-1 [--retry-base-ms 500]
uv run python manage.py jobs_demo success|retry|dlq|crash|duplicate|many [--count 10] [--timeout 60]
uv run python manage.py audit_config           # checks the settings, exit code 1 if something is unsafe
```

| `jobs_demo` scenario | What happens |
|---|---|
| `success` | an email, a file and a report job all finish |
| `retry` | fails twice, succeeds on the 3rd delivery |
| `dlq` | always fails, ends in `jobs:dlq` after 3 attempts |
| `crash` | the worker that picks it up is killed (`os._exit`). Run two workers, or restart the dead one: after the idle time the message is claimed (XCLAIM) and finished |
| `duplicate` | the same job is published twice, the effect happens once |
| `many` | N jobs, shared between all running workers |

**Scripts** (server must be running; `BASE_URL` defaults to `http://127.0.0.1:8000`):

```bash
uv run python scripts/compare_sync_async.py   # 5 concurrent requests: sync vs async vs blocking-in-async
uv run python scripts/ws_demo.py              # echo + two-user chat
uv run python scripts/trace_demo.py           # one trace across client, API, DB, worker, external service
uv run python scripts/idor_demo.py            # bob attacks alice: vulnerable vs secure
uv run python scripts/owasp_demo.py           # every OWASP demo in one run
```

`asgi-demo/` has three standalone files: `01_wsgi.py` (plain WSGI app: `uv run python asgi-demo/01_wsgi.py`), `02_sync_vs_async.py` (timings, no server: `uv run python asgi-demo/02_sync_vs_async.py`), `asgi_app.py` (raw ASGI app that streams a response in chunks: `uv run uvicorn --app-dir asgi-demo asgi_app:app`, then `curl -N http://127.0.0.1:8000`).

## 8. Execution evidence

No screenshots are committed. Everything below is plain terminal output, so it can be reproduced with the commands in section 7. The numbers come from a local run on SQLite with one uvicorn process and the console exporter. Timings vary from run to run.

**Sync vs async** (`scripts/compare_sync_async.py`):

```
5 concurrent requests, each one waits 0.5s
one after another it would take 2.5s

async  (await asyncio.sleep)     total  0.72s
sync   (def + time.sleep)        total  0.56s
async  (BLOCKING time.sleep)     total  2.57s
```

Sync `def` views also overlap, because Django runs them in worker threads under ASGI. The one that serializes everything is blocking code inside `async def`.

**IDOR** (`scripts/idor_demo.py`):

```
bob reads alice's document        -> 200  {"id": 1, "title": "Alice salary", ...}     (vulnerable)
bob overwrites it                 -> 200  {"title": "hacked by bob", ...}             (vulnerable)
bob reads alice's document        -> 404  {"detail": "Not Found"}                      (secure)
bob asks for an id that is not there -> 404  {"detail": "Not Found"}                   (same answer)
```

**OWASP** (`scripts/owasp_demo.py`, shortened):

```
SQL injection ' OR '1'='1       vulnerable -> every row            secure -> []
vulnerable-login: unknown user  -> 401 "No such user."             (leaks which names exist)
vulnerable-login: bad password  -> 401 "Wrong password."
secure-login: both cases        -> 401 "Invalid credentials."
7 wrong guesses, then the right password:  vulnerable -> 200       secure -> 429 (locked)
admin user list as a normal user:          vulnerable -> 200       secure -> 403
profile:  vulnerable -> includes "password": "pbkdf2_sha256$..."   secure -> {"id", "username"} only
error:    vulnerable -> 500 with traceback                         secure -> 500 {"detail": "Internal server error", "error_id": "..."}
```

**Security log** (uvicorn terminal after the demos):

```
<time> WARNING security: security_event event='login_failed' ip='127.0.0.1' path='/api/owasp/auth/secure-login' username='...' trace_id='...'
```

**Trace** (`scripts/trace_demo.py`, then open the printed Jaeger link). Expected span tree:

```
client.send-email-job
└── POST /api/jobs                      (django-api)
    ├── (DB spans of the INSERT)        (PostgreSQL only)
    └── jobs.enqueue                    (PRODUCER)
        └── jobs.consume                (worker)
            └── job.process
                ├── (DB spans of the locking SELECT and the INSERT)
                └── mail-provider.send  (CLIENT)
```

## 9. Tests and results

```bash
uv run pytest            # needs the Postgres container (docker compose up -d)
```

Quick run without Docker, using SQLite (the tests use fakeredis and an in-memory span exporter, so they need no Redis or Jaeger):

```bash
DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: uv run pytest      # Git Bash
```

Result of the last run: **105 passed** (SQLite, fakeredis, in-memory spans).

| File | Covers |
|---|---|
| `test_asgi.py` | lifespan, routing to Django, timing header, security headers, JSON 404 (no stack trace), Swagger page and OpenAPI schema, async ORM |
| `test_sync_async.py` | the demo endpoints and the overlap behaviour |
| `test_websocket.py` | echo and chat (join, message, leave) |
| `test_telemetry.py` | spans, parent/child, exceptions, `traceparent` through Redis, one trace client → worker → external service |
| `test_jobs_streams.py` | success, XACK, retry with back-off, DLQ, crash recovery with XCLAIM, duplicates, several workers |
| `test_jobs_api.py` | job endpoints, 503 when Redis is down |
| `test_auth.py` | register, login, lock-out, token checks |
| `test_idor.py` | vulnerable and secure endpoints, security log |
| `test_owasp.py` | each OWASP demo, the configuration audit, log masking |

**Not run yet:** `docker compose up` itself, a real PostgreSQL (`select_for_update`, DB spans), a real Redis with real worker processes, and the Jaeger UI. The DB spans in the trace only appear with PostgreSQL, not SQLite.

## 10. Failure scenarios

| Scenario | What happens |
|---|---|
| Worker fails a job | not acknowledged, re-queued with back-off (2 s, 4 s), 3 attempts, then `jobs:dlq` |
| Worker crashes mid-job | message stays in XPENDING; another worker takes it with XCLAIM after the idle time |
| Same message delivered twice | the second run finds the effect row and does nothing (`jobs_demo duplicate`) |
| Worker crashes after the work but before XACK | message is delivered again, idempotency stops a second effect |
| Redis is down | `POST /jobs` returns 503 and the job row is marked `dead` (`queue unavailable`), it is not left pending |
| External service fails | span is marked ERROR with the exception, the job is retried |
| Blocking call in `async def` | the event loop stops, all requests wait (`/demo/async-blocking`) |
| Unhandled exception in an endpoint | client gets a generic 500 with an `error_id`, the stack trace goes to the log and the span |
| Brute-force login | the username is locked for 15 min after 5 failures (429) |

## 11. Technical decisions

- **Raw ASGI router + one pure-ASGI middleware** instead of a framework, to see the three scope types (`lifespan`, `websocket`, `http`).
- **Django Ninja** for the HTTP API: type-checked input and automatic Swagger.
- **Redis Streams with consumer groups** (not a list or pub/sub): messages stay stored until acknowledged, so a crash loses nothing.
- **Retry by re-publishing with a delay** and an attempt counter in the database, so the stream does not fill with stuck messages and the attempts survive a crash.
- **DLQ move is XADD + XACK in one MULTI/EXEC**, so a message is never in both places or in neither.
- **Idempotency in the database** (`JobEffect` OneToOne with the job, row locked while processing) because Redis delivers at least once, never exactly once.
- **`traceparent` stored in the Redis message**, because Redis has no headers; this is what joins the worker's spans to the API's trace.
- **Telemetry is started before Django loads**, so the instrumentation can patch it.
- **Simple JWT (PyJWT, HS256)** with its own secret, the algorithm pinned on decode, short life time.
- **Return 404 for someone else's document** instead of 403, so ids cannot be probed.
- **Sync `def` views stay sync** (Django runs them in threads). Only truly awaiting code is `async def`.

## 12. Challenges

- Sync views are **not** serialized under ASGI; I first assumed they were. Measuring showed the real problem is blocking code inside `async def`.
- Ninja's `/api/docs` returned 500 until `"ninja"` was in `INSTALLED_APPS` and a Django templates backend was configured. A test now covers it.
- Redis is not auto-instrumented, so I propagate the trace context by hand through the message.
- Making a crash testable: the demo job can tell the worker to exit hard (`crash_on_attempt`), and the tests leave a message pending so a second worker has to recover it with XCLAIM.
- Retries must not break ordering of attempts: the attempt count is updated in the database before the work starts.

## 13. Key learnings

- ASGI is a plain async function `app(scope, receive, send)`, a request is just a sequence of messages.
- One blocking call in `async def` freezes every request on that event loop.
- A trace is a tree of spans linked by a trace id; crossing a process boundary needs the context passed along explicitly.
- Streams give *at-least-once* delivery, so the consumer has to be idempotent.
- Authentication says who you are, authorization says what you may do; most IDOR bugs are a missing owner filter.
- Parameterised queries, allow-list responses and generic errors remove whole bug classes at once.

## 14. Future improvements

- Run the full compose stack and Jaeger in CI and add screenshots of the traces.
- Run the worker tests against a real Redis in CI, not only fakeredis.
- Metrics (OpenTelemetry metrics) next to traces, and alerts on `security_event` spikes.
- Rate limiting at the proxy for all endpoints, not only login.
- Refresh tokens and token revocation.
- Dead-letter replay command and a small dashboard for `jobs:dlq`.
- Gunicorn with Uvicorn workers behind a reverse proxy for a production-like setup.

---

## AI assistance

The test cases (`tests/`) and the documentation were generated with the help of AI.
