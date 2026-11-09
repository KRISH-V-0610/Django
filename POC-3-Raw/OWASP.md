# POC 3: OWASP demos

Seven topics. Each one has the same six points: **what**, **why it happens**, **how I exploit it locally**, **impact**, **prevention**, **how I test the fix**.

Every weak endpoint sits next to a safe one (`/vulnerable` and `/secure`). The weak ones are broken on purpose. This app is for local learning only: never deploy it.

Before trying anything: start the server (`uv run uvicorn config.asgi:application --port 8000`). Then either run the script (`uv run python scripts/owasp_demo.py`, `uv run python scripts/idor_demo.py`) or use the Swagger page at http://127.0.0.1:8000/api/docs. The tests for everything below are in `tests/test_owasp.py` and `tests/test_idor.py`.

---

## 1. Broken access control: IDOR

Code: `documents/api.py`. Endpoints: `/api/vulnerable/documents/{id}` and `/api/documents/{id}`.

| | |
|---|---|
| **What** | Insecure Direct Object Reference. The API takes an id from the URL and returns that object without checking that it belongs to the caller. |
| **Why** | The vulnerable view only checks "is the user logged in?" (authentication). It never asks "is this document *theirs*?" (authorization). `get_object_or_404(Document, pk=id)` returns any row. |
| **Exploit locally** | Register alice and bob. Alice creates a document (id 1). Bob logs in and sends `GET /api/vulnerable/documents/1` with his own token. He gets Alice's data, and `PUT` / `DELETE` work too. `scripts/idor_demo.py` does exactly this. |
| **Impact** | Anyone with an account reads, changes or deletes everyone else's data. Ids are small sequential numbers, so a loop over 1..N dumps the whole table. |
| **Prevention** | Filter every query by the owner (`Document.objects.filter(pk=id, owner=request.auth)`), in one shared helper (`get_owned_document`) used by read, update and delete. Answer 404 (not 403) for someone else's object, so an attacker cannot even tell which ids exist. List endpoints are filtered too, and the owner of a new object is always the caller (never taken from the request body). |
| **Test the fix** | `uv run pytest tests/test_idor.py`. The vulnerable tests show bob succeeding. The secure tests show bob getting 404 for read, change and delete, the 404 being identical to a missing id, the list showing only own documents, and an `idor_blocked` line in the security log. |

---

## 2. SQL injection

Code: `owasp/api.py`. Endpoints: `/api/owasp/sqli/vulnerable?title=...` and `/api/owasp/sqli/secure?title=...`.

| | |
|---|---|
| **What** | User input becomes part of the SQL text, so the user can change what the query does. |
| **Why** | The vulnerable view builds the query with an f-string: `... WHERE title = '{title}'`. A quote in the input ends the string and what follows is read as SQL. |
| **Exploit locally** | `title=' OR '1'='1` makes the condition always true, so every row comes back. `title=x' UNION SELECT id, username, id FROM auth_user --` returns rows from another table (usernames). `scripts/owasp_demo.py` sends both. |
| **Impact** | Reading data the user must never see (other tables, password hashes), changing or deleting data, sometimes running commands on the database server. |
| **Prevention** | Parameterised queries: `cursor.execute("... WHERE title = %s", [title])`. The driver sends the value separately from the SQL, so quotes and keywords inside it are just text. Even better, use the ORM (`Document.objects.filter(title=title)`), which always parameterises. Never build SQL with f-strings or `%` on a string. |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k "sqli or normal_search"`. The same payloads return every row on `/vulnerable` and an empty list on `/secure`. A normal search returns the same result on both. |

---

## 3. Authentication and authorization

Code: `owasp/api.py` (demo endpoints), `accounts/services.py` (the safe login), `accounts/tokens.py` and `accounts/auth.py` (JWT).

| | |
|---|---|
| **What** | Authentication is "who are you?", authorization is "are you allowed to do this?". Three typical mistakes: telling an attacker which usernames exist, allowing unlimited password guesses, and checking the login but not the role. |
| **Why** | `vulnerable-login` answers "No such user." or "Wrong password." (two different messages) and has no limit. `/admin/users/vulnerable` only needs a valid token, it never looks at `is_staff`. |
| **Exploit locally** | (a) Send a login for a made-up name and for a real name with a wrong password: the two answers differ, so the names that exist can be listed. (b) Send 7 wrong guesses, then the right password: it still works, so a script can try thousands of passwords. (c) Log in as a normal user and call `/api/owasp/admin/users/vulnerable`: the full user list comes back. |
| **Impact** | Account takeover by brute force, a clean list of targets, and normal users doing admin things. |
| **Prevention** | One generic message for both failures ("Invalid credentials."). Lock a username after 5 failed attempts for 15 minutes (counter in the cache, answer 429). Django's `authenticate()` also hashes a dummy password for unknown users, so the response time does not give the answer away either. Check the role after the login (`request.auth.is_staff`, answer 403). JWT checks: signature, expiry, algorithm pinned to HS256 (so `alg: none` is refused), user must still be active, secret separate from `SECRET_KEY`. Passwords are stored hashed (PBKDF2). |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k "login or admin"` and `uv run pytest tests/test_auth.py`. They cover the two messages being identical, the lock-out (429), the counter being per username, a forbidden call for a normal user, and rejected tokens (missing, garbage, expired, wrong secret, `alg: none`, deactivated user). |

---

## 4. Security misconfiguration

Code: `owasp/api.py` (`/misconfig/*`), `owasp/config_audit.py`, `manage.py audit_config`, `config/settings.py`.

| | |
|---|---|
| **What** | The code is fine but the setup is unsafe: debug mode on, default or short secrets, wide-open host list, missing security headers, a forgotten debug endpoint. |
| **Why** | Defaults are made for convenience, and "temporary" things stay. `/misconfig/vulnerable` is a leftover debug endpoint that prints `SECRET_KEY` and the database settings. |
| **Exploit locally** | `GET /api/owasp/misconfig/vulnerable` returns the secret key and database credentials to anyone, no login. With the key an attacker can forge signed data. Run `uv run python manage.py audit_config` with the placeholder values from `.env.example`: it fails and says why. |
| **Impact** | Full compromise without exploiting a single bug: the secrets are simply handed out. `DEBUG=True` additionally shows stack traces, settings and SQL on every error page. |
| **Prevention** | All secrets come from environment variables (`.env` is git-ignored, only `.env.example` with placeholders is committed). `DEBUG=False` outside local work. A real `ALLOWED_HOSTS`. Security headers on (`SECURE_CONTENT_TYPE_NOSNIFF = True` through `SecurityMiddleware`, `X_FRAME_OPTIONS = "DENY"` through `XFrameOptionsMiddleware`). A health endpoint that says only `ok`. `audit_config` checks these automatically (it exits with code 1 on a failure, so it can run in CI). |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k "misconfig or audit or leaks"` and `uv run pytest tests/test_asgi.py -k security_headers`. A good configuration passes all checks. Each bad setting (debug on, placeholder secret, shared JWT secret, `*` host, ...) fails exactly its own check. The secure endpoint leaks nothing. |

---

## 5. Sensitive information exposure

Code: `owasp/api.py` (`/profile/*`), `core/security_log.py`.

| | |
|---|---|
| **What** | The API returns, or the logs contain, data that must stay private. |
| **Why** | `profile/vulnerable` returns `model_to_dict(user)`: every column, including the password hash. New columns added later leak automatically. |
| **Exploit locally** | Log in and call `GET /api/owasp/profile/vulnerable`: the response contains `password: pbkdf2_sha256$...`. An offline cracking tool can then guess the password without touching the server. |
| **Impact** | Stolen password hashes get cracked offline. Leaked emails, tokens or internal ids help every other attack. |
| **Prevention** | A response schema is an allow-list: `ProfileOut` has only `id` and `username`, so nothing else can leave, even if the model grows. Secret-looking keys are masked in log lines (`mask_sensitive` replaces `password`, `token`, `access`, `authorization`, `secret`, `api_key`, `cookie` with `***`). Passwords are never stored or logged in clear text. |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k "profile or masked"`. The vulnerable test finds `password` in the response, the secure one gets exactly `id` and `username`, and the masking test checks that secret fields become `***`. |

---

## 6. Secure error handling

Code: `owasp/api.py` (`/errors/*`), `config/api.py` (global handler).

| | |
|---|---|
| **What** | When something breaks, the client must learn nothing about the inside of the system, but the developer must still be able to find the cause. |
| **Why** | `errors/vulnerable` catches the exception and returns its text, its type and the whole stack trace. That shows file paths, library versions, SQL and variable names. |
| **Exploit locally** | `GET /api/owasp/errors/vulnerable?doc_id=abc`. The 500 response contains the traceback with the full path of the project on disk. The error text also tells the attacker that the input is passed to a number field of the database. |
| **Impact** | Free reconnaissance for the next attack (framework, versions, file layout, query shape). |
| **Prevention** | One global exception handler (`@api.exception_handler(Exception)`). The client gets `{"detail": "Internal server error", "error_id": "..."}` only. The stack trace goes to the server log together with the same `error_id`, and is recorded on the trace span, so a developer finds it from the id the user quotes. The secure endpoint has no `try/except` of its own on purpose: the unexpected error simply reaches the global handler. Unknown URLs get a short JSON 404, not a debug page. |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k error`, `uv run pytest tests/test_telemetry.py -k hidden_from_client` (failure hidden from the client but kept in the trace) and `uv run pytest tests/test_asgi.py -k unknown_url` (JSON 404). The vulnerable test finds `Traceback` in the body, the secure one does not, and the log has a record with the same `error_id`. |

---

## 7. Security logging and monitoring

Code: `core/security_log.py`. Used by login, authorization and IDOR paths.

| | |
|---|---|
| **What** | Security-relevant events (failed logins, lock-outs, forbidden calls, blocked IDOR attempts) are written to a log in a fixed format, so an attack can be noticed and investigated. |
| **Why** | Without it, brute forcing or probing of other people's ids leaves no trace: the responses look normal and nobody knows it happened. |
| **Exploit locally** | Run `scripts/owasp_demo.py` or fail a login a few times. The uvicorn terminal prints lines like `security_event event='login_failed' ip='127.0.0.1' path='/api/auth/login' username='alice' trace_id='...'`. Bob's blocked read of Alice's document prints `event='idor_blocked'`. |
| **Impact** | Missing logs mean attacks go unnoticed for months and cannot be reconstructed afterwards. Logs with secrets or forged IPs are a vulnerability of their own. |
| **Prevention** | One function, `log_event()`, so every line has the same shape: event name, IP, path, user, `trace_id`. The IP is `REMOTE_ADDR` (not the `X-Forwarded-For` header, which the client can forge). Secret values are masked. The `trace_id` links the line to the full trace in Jaeger. Events logged: `login_failed`, `login_locked`, `forbidden`, `idor_blocked`. A real setup would send these lines to a central place and alert on spikes. |
| **Test the fix** | `uv run pytest tests/test_owasp.py -k "logged or masked"` and `tests/test_idor.py -k logged`. They check that a failed login, a lock-out and a blocked IDOR attempt each produce a record with the right fields, and that secrets are masked. |

---

## Quick map

| # | Topic | Vulnerable | Secure | Tests |
|---|---|---|---|---|
| 1 | IDOR | `/api/vulnerable/documents/{id}` | `/api/documents/{id}` | `tests/test_idor.py` |
| 2 | SQL injection | `/api/owasp/sqli/vulnerable` | `/api/owasp/sqli/secure` | `tests/test_owasp.py` |
| 3 | Authentication / authorization | `/api/owasp/auth/vulnerable-login`, `/api/owasp/admin/users/vulnerable` | `/api/owasp/auth/secure-login`, `/api/owasp/admin/users/secure`, `/api/auth/login` | `tests/test_owasp.py`, `tests/test_auth.py` |
| 4 | Misconfiguration | `/api/owasp/misconfig/vulnerable` | `/api/owasp/misconfig/secure`, `manage.py audit_config` | `tests/test_owasp.py` |
| 5 | Sensitive data | `/api/owasp/profile/vulnerable` | `/api/owasp/profile/secure` | `tests/test_owasp.py` |
| 6 | Error handling | `/api/owasp/errors/vulnerable` | `/api/owasp/errors/secure` + global handler | `tests/test_owasp.py` |
| 7 | Security logging | (nothing logged) | `core/security_log.py` | `tests/test_owasp.py`, `tests/test_idor.py` |
