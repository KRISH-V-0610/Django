# POC 2: Python 3.14 + Django ORM Data Integrity

**Status: ✅ Completed.** A tiny shop backend where the **database itself** refuses bad data, and stock and counters stay correct even when many requests hit at once.

**Stack:** Python 3.14 (`3.14.0rc1`, the build `uv` provides) · Django 6 · Django Ninja · PostgreSQL 17 (Docker) · pydantic 2.12.0 (pinned) · django-environ · uv · pytest / pytest-django

---

## 1. The story

**The shop:** customers, and products with a SKU, price and stock.

| Topic | Real problem | Concept | Where |
|---|---|---|---|
| A | Random IDs make the index slow as the table grows | **UUIDv7** (timestamp first, so new rows sort last) | `shop/models.py`, `python_features/uuid_demo.py` |
| B | A settings object gets changed by accident | **Frozen dataclass** | `python_features/dataclass_demo.py` |
| C | Messy rows: missing, invalid, negative values | **Plain Python datasets**: clean, filter, aggregate | `python_features/dataset_demo.py` |
| D | The same value is computed twice | **Walrus `:=`** | `python_features/walrus_demo.py` |
| E | A business rule hidden in `assert` vanishes under `python -O` | **`assert` vs exceptions** | `python_features/assert_demo.py` |
| F | Two customers with the same email, or two active products with the same SKU | **Unique constraints**: single, multi-column, conditional | `shop/models.py` |
| G | Negative stock, rating of 9, min above max | **Check constraints** | `shop/models.py` |
| H | Two buyers take the last item (lost update) | **F expressions**: the database does the maths | `shop/services.py` |
| I | A search where any mix of filters is optional | **Q objects**: `\|`, `&`, `~`, built dynamically | `shop/filters.py` |

**The one-line version:** Python checks protect your code, but **database constraints** protect your data, and **F expressions** keep concurrent updates correct.

---

## 2. Endpoints (all under `/api`)

| Method | Path | What it does |
|---|---|---|
| POST | `/customers` | Create a customer. Duplicate email returns `409`. |
| POST | `/products` | Create a product. A broken rule returns `400`. |
| GET | `/products/search` | Flexible search using Q objects. |
| GET | `/products/{id}` | Get one product. Adds 1 to `views` using F. |
| POST | `/products/{id}/add-stock` | Add stock using F. |
| POST | `/products/{id}/remove-stock` | Remove stock safely. Too little stock returns `400`. |

`/products/search` is declared **before** `/products/{id}`, otherwise "search" would be read as an ID.

---

## 3. Run it locally

**You need:** Docker, [uv](https://docs.astral.sh/uv/), Python 3.14 (`uuid.uuid7()` exists only from 3.14).

1. **Start Postgres on port 5433.** The container from `POC-1` works: `docker compose up -d` there.
2. **Create the POC-2 database:**
   ```bash
   docker exec django_poc_db psql -U postgres -c "CREATE DATABASE django_poc2;"
   ```
3. **Create `.env`:** `cp .env.example .env` (the values already match the container).
4. **Install and migrate:**
   ```bash
   uv sync
   uv run python manage.py migrate
   ```
5. **Run the server:** `uv run python manage.py runserver`
6. **Open Swagger:** `http://127.0.0.1:8000/api/docs`
7. **Run the tests (48):** `uv run pytest`

**Try the Python demos:**
```bash
uv run python -m python_features.uuid_demo
uv run python -m python_features.assert_demo        # asserts ON
uv run python -O -m python_features.assert_demo     # asserts OFF: the withdraw bug appears
```

---

## 4. Project map

```
POC-2/
├── config/              # settings and URLs
├── shop/
│   ├── models.py        # UUIDv7 keys, unique and check constraints
│   ├── services.py      # F expressions, try_save (catches IntegrityError)
│   ├── filters.py       # Q objects: build_product_filter
│   └── api.py           # the endpoints above
├── python_features/     # runnable demos for topics A to E
├── tests/               # pytest suite
└── docs/                # revision notes (git-ignored)
```

---

## 5. AI assistance

The test cases (`tests/`) and the documentation were generated with the help of AI.
