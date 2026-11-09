# POC 1: Django 6 + Ninja + JWT + Idempotency + Transactions

**Status: ✅ Completed.** A tiny online shop whose "Place Order" button never double-charges and never leaves half an order behind.

**Stack:** Python 3.12 · Django 6 · Django Ninja · PostgreSQL 17 (Docker) · PyJWT · django-environ · uv · pytest

---

## 1. The Idea : 

**The shop:** a Keyboard, price ₹10, stock 5. Alice buys 2 through `POST /orders/place`.

**Every purchase touches 3 tables:**
- `Order`: a new row (`PENDING`, later `CONFIRMED`)
- `Product`: stock goes down
- `TransactionRecord`: a receipt row

**What can go wrong, and the concept that fixes it:**

| Chapter | What goes wrong | Concept | Where |
|---|---|---|---|
| 1 | Server crashes after stock is deducted: ghost order, lost stock | **Transaction**: all-or-nothing, rollback | `transactions/services.py` → `place_order` |
| 2 | The optional free sample fails and kills the whole order | **Savepoint** (nested `atomic`) | same function, step 4 |
| 3 | Confirmation email sent, then the order rolls back | **`on_commit`**: side effects only after a real commit | same function, step 6 |
| 4 | Two buyers grab the last keyboard | **Locking**: `select_for_update()` | same function, first line |
| 5 | Alice's reply is lost, her app retries: she is charged twice | **Idempotency key** | `idempotency/service.py` |
| 6 | Same key, but a different order | **Fingerprint** → 422 | `make_fingerprint` |
| 7 | Alice double-clicks: two requests at the same instant | **Unique constraint** decides the winner | `begin()` |
| 8 | The order fails, but the key stays stuck | **`abandon()`** frees the key so a retry works | `orders/api.py` |

**The one-line version:** transactions protect the data inside *one* request. Idempotency protects against the *same* request arriving twice. Together: exactly one purchase, never half a purchase.

---

## 2. Run it locally

**You need:** Docker, [uv](https://docs.astral.sh/uv/), Python 3.12.

1. **Create `.env`:**
   - Copy the template: `cp .env.example .env` (the DB values already match `docker-compose.yml`).
   - Put real random values in `SECRET_KEY` and `JWT_SECRET_KEY`. Use at least 32 characters:
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. **Start Postgres:** `docker compose up -d` (exposed on host port **5433**).
3. **Install and migrate:**
   ```bash
   uv sync
   uv run python manage.py migrate
   ```
4. **Run the server:** `uv run python manage.py runserver`
5. **Open Swagger:** `http://127.0.0.1:8000/api/v1/docs`

**Look inside the database (optional):** connect pgAdmin or DBeaver to host `127.0.0.1`, port `5433`, DB `django_poc`, user `postgres`, password `postgres`.

**If something is off:**
- "Failed to fetch" in Swagger: the Django server isn't running.
- Missing tables or columns: run `migrate` again.

---

## 3. Project map

```text
config/        settings, root URLs, API versioning (/api/v1/)
accounts/      register, login, JWT access and refresh tokens, logout
orders/        orders CRUD + the POST /orders/place endpoint
transactions/  Product, TransactionRecord, place_order() (the atomic workflow)
idempotency/   IdempotencyRecord model, begin/complete/abandon, cleanup command
tests/         pytest suite (auth, transactions, idempotency)
```

**Endpoints** (all protected except register and login; send `Authorization: Bearer <access>`):

| Method | URL | What it does |
|---|---|---|
| POST | `/api/v1/auth/register` | Create a user |
| POST | `/api/v1/auth/login` | Get `access` and `refresh` tokens |
| POST | `/api/v1/auth/refresh` | Rotate tokens |
| POST | `/api/v1/auth/logout` | Revoke the refresh token |
| GET | `/api/v1/auth/profile` | Who am I |
| POST / GET | `/api/v1/products/` | Create / list products |
| POST / GET | `/api/v1/orders/` | Create / list plain orders |
| GET / PATCH / DELETE | `/api/v1/orders/{id}` | Read / update / delete an order |
| **POST** | **`/api/v1/orders/place`** | **The star: transactional + idempotent purchase** |

---

## 4. Demo script (do it in Swagger, in this order)

**Setup**
1. `POST /auth/register`: username, email, and a strong password (for example `Str0ng-pass-123`).
2. `POST /auth/login`: copy `access`. Click **Authorize** and paste it.
3. `POST /products/`: `{"name": "Keyboard", "price": 10, "stock": 5}`

**For every `/orders/place` call**, add the header `Idempotency-Key` (any new string you invent) and use body `{"product_id": 1, "quantity": 2}`.

| Try | Key | Body change | You should see |
|---|---|---|---|
| ✅ Happy path | `k1` | none | **201**, `stock_left: 2`, `free_sample: true`, `status: CONFIRMED` |
| 🔁 Replay | `k1` | none | **Same 201**, same `order_id`, no new order or stock change |
| ⚠️ Same key, other order | `k1` | `quantity: 3` | **422** |
| 🆕 New purchase | `k2` | none | New order |
| 💥 Crash after stock | `k3` | add `"fail_at": "after_stock"` | **500**, nothing changed |
| 💥 Crash after receipt | `k4` | add `"fail_at": "after_record"` | **500**, nothing changed |
| 🔖 Savepoint | `k5` | add `"fail_at": "free_sample"` | **201**, `free_sample: false`, only the sample is undone |
| 📦 Too many | `k6` | `quantity: 99` | **400** not enough stock |
| 🚫 No header | none | none | **400** |

**Then prove it in the database** (pgAdmin or DBeaver):
```sql
SELECT * FROM transactions_product;
SELECT * FROM orders_order ORDER BY id;
SELECT * FROM transactions_transactionrecord;
SELECT * FROM idempotency_idempotencyrecord;
```
- After a crash (`k3`, `k4`): stock, orders and receipts are unchanged.
- The crashed key has **no row** in the idempotency table.
- `fail_at` is a fake crash button in the request body, because real crashes are hard to trigger on demand.

---

## 5. Transactions: the key points

- **Transaction** = a bundle of DB steps that is all-or-nothing.
- **`with transaction.atomic():`** commits on a clean exit and rolls back if an exception escapes.
- **ACID:**
  - **A**tomicity: no half-done work
  - **C**onsistency: rules always hold (`CheckConstraint`: stock ≥ 0)
  - **I**solation: concurrent requests don't interfere
  - **D**urability: committed data survives a crash
- **Savepoint** = a nested `atomic()`. If it fails, only that block is undone.
  - Catch its error *outside* the inner block.
  - Then call `refresh_from_db()`, because Python's copy of the row is stale.
- **`on_commit(...)`** runs only after the outermost transaction commits, and is dropped on rollback.
- **No external calls inside a transaction:**
  - They can't be rolled back.
  - They are slow and hold DB locks.
  - A timeout leaves you unsure whether it happened.
- **Never swallow an exception inside `atomic()`.** Django never sees it, so the half-done work is committed.
- **`select_for_update()`** locks one row until commit or rollback; the second buyer waits and then sees the updated stock.
- **Across systems** (payment gateways) there is no shared rollback. Use `PENDING` → call the gateway → `PAID`/`FAILED`, an idempotency key sent to the gateway, refunds as compensation, and webhooks plus reconciliation.

---

## 6. Idempotency: the key points

- **Idempotent** = doing it 10 times has the same effect as doing it once. `POST` is not, so we build it.
- **Why duplicates happen:** timeouts, double-clicks, automatic retries.
- **`Idempotency-Key` header:**
  - Generated by the **client**, once per purchase attempt, and reused on every retry.
  - A new purchase gets a new key.
  - Never generated by the server: a server ID would be new for every retry.
- **Fingerprint** (server-made, never sent): SHA-256 of method + URL + sorted JSON body. It catches "same key, different order" → **422**.
- **Stored in `IdempotencyRecord`:** `user, scope, key, request_hash, status, response_status, response_body, expires_at`.
  - Unique on **`(user, scope, key)`**.
  - Per user, so nobody can replay someone else's response.
- **Two states:** `PROCESSING` (claimed) → `COMPLETED` (reply saved).
- **Double click at the same instant:** both `INSERT`; the unique constraint lets one win. The loser gets **409** (still processing) or the saved reply.
- **Expiry:** 24 hours. `begin()` removes an expired row for the same key; `manage.py clean_idempotency_keys` removes all expired rows.
- **Idempotency vs unique constraint:**
  - A unique constraint rejects the duplicate with an error.
  - Idempotency **replays the original success**.
  - The constraint is the tool that makes it safe under concurrency.

**What `service.py` does:**

| Function | Job |
|---|---|
| `make_fingerprint` | Turn method + URL + body into a hash |
| `begin` | Try to claim the key. Returns `(record, None)` = "you own it, run it", or `(None, reply)` = "send this reply instead" |
| `complete` | Save the status and response on the record (inside the transaction) |
| `abandon` | Delete the record so a failed request can be retried |

---

## 7. How they work together: `POST /orders/place`

```text
begin()            claim the key (commits immediately, so a duplicate sees PROCESSING)
  │
  ├─ reply?  ──►  return it (replay / 409 / 422)
  │
  └─ with transaction.atomic():
        place_order()   lock product → order → deduct stock → receipt → sample → CONFIRMED
        complete()      save the reply
     ── commit ──►  on_commit: send_confirmation()
     
  on error: everything rolls back → abandon() deletes the key → client can retry
```

**Why `complete()` is inside the same transaction:** the order and the `COMPLETED` record are saved together or not at all. A gap between them would leave a stuck key or a duplicate order on retry.

---

## 8. Tests (`uv run pytest`, 30 tests)

- Runs against real Postgres in a temporary test database, so your data is safe.
- **Files:** `test_auth.py` (JWT, expiry, refresh, revoke), `test_transactions.py`, `test_idempotency.py`.
- **How to read one test:**
  1. Arrange: fixtures from `conftest.py` give you `client`, `auth`, `product`.
  2. Act: make the request.
  3. Assert: check the response and the database.
- **Covered:** commit, rollback, savepoint, `on_commit` run vs skipped, the swallowed-exception trap, the DB constraint, replay, 422, 409, expiry, cleanup, 5 simultaneous identical requests → 1 order, 6 simultaneous different orders → never oversold.

---

## 9. Known limitations (be upfront about these)

- A server **crash mid-request** leaves the key as `PROCESSING` until it expires (24h). Production fixes this with a lock timeout.
- The cleanup command is **not scheduled**; run it by hand or from cron.
- `send_confirmation` only **logs**; no real email or payment gateway is called.
- Tests show two warnings: a short JWT key (use 32+ characters) and Ninja deprecating `return status, body` tuples.

---

## 10. Quick answers for the 12 viva questions

1. **DRF vs Ninja:** Ninja is type-hint and Pydantic based, lighter and faster, with automatic OpenAPI docs. DRF uses serializers and viewsets.
2. **JWT internally:** `header.payload.signature`. The server signs the payload with a secret and verifies the signature on each request. No session lookup is needed.
3. **Why a short access lifetime:** a stolen access token stops working soon. The long-lived refresh token is used rarely and can be revoked.
4. **Access token expires:** the API returns 401. The client calls `/auth/refresh` and gets a new pair (rotation).
5. **Prevent duplicate orders:** an `Idempotency-Key` header, a stored key plus fingerprint, and replay of the saved response.
6. **Two identical requests at once:** both try to insert the key. The unique constraint lets one win; the other gets 409 or the replay.
7. **Idempotency vs unique constraint:** a constraint rejects with an error; idempotency returns the original result for the whole operation.
8. **ACID:** atomicity, consistency, isolation, durability (see section 5).
9. **`atomic()` meets an exception:** it rolls back all changes since the block began and re-raises. A nested block rolls back to its savepoint only.
10. **Savepoint:** a marker inside a transaction. A failure rolls back only to the marker.
11. **`on_commit`:** for side effects (email, events) that must happen only if the data was really saved.
12. **No external calls in a transaction:** they can't be rolled back, they hold locks while slow, and a timeout leaves the outcome unknown.

---

## AI assistance

The test cases (`tests/`) and the documentation (this README and my revision notes) were generated with the help of AI.
