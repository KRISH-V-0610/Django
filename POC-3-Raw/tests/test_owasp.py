"""Topic E: every OWASP demo is attacked on the vulnerable endpoint and on the secure one."""
import logging
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.services import MAX_FAILED
from core.security_log import mask_sensitive
from documents.models import Document
from owasp.config_audit import audit_settings

pytestmark = pytest.mark.django_db

JSON = "application/json"
PASSWORD = "a-long-test-password"


def post(client, url, **body):
    return client.post(url, data=body, content_type=JSON)


# ===================================================================== 2. SQL injection
@pytest.fixture
def some_documents(alice, bob):
    Document.objects.create(owner=alice, title="alice-report")
    Document.objects.create(owner=bob, title="bob-report")


def test_normal_search_works_on_both_versions(client, some_documents):
    for version in ("vulnerable", "secure"):
        rows = client.get(f"/api/owasp/sqli/{version}", {"title": "alice-report"}).json()
        assert [row["title"] for row in rows] == ["alice-report"]


def test_sqli_vulnerable_returns_every_row(client, some_documents):
    rows = client.get("/api/owasp/sqli/vulnerable", {"title": "' OR '1'='1"}).json()
    assert len(rows) == 2  # the WHERE condition was rewritten by the attacker


def test_sqli_vulnerable_leaks_another_table(client, some_documents, alice):
    attack = "x' UNION SELECT id, password, id FROM auth_user --"
    rows = client.get("/api/owasp/sqli/vulnerable", {"title": attack}).json()
    assert any(row["title"] == alice.password for row in rows)  # password hashes came out


def test_sqli_secure_treats_the_payload_as_plain_text(client, some_documents):
    for attack in ("' OR '1'='1", "x' UNION SELECT id, password, id FROM auth_user --"):
        assert client.get("/api/owasp/sqli/secure", {"title": attack}).json() == []


# ============================================================ 3. authentication / authorization
def test_vulnerable_login_reveals_which_usernames_exist(client, alice):
    unknown = post(client, "/api/owasp/auth/vulnerable-login", username="nobody", password="x" * 10)
    wrong = post(client, "/api/owasp/auth/vulnerable-login", username="alice", password="x" * 10)
    assert unknown.json() != wrong.json()  # attacker learns that "alice" exists


def test_vulnerable_login_allows_unlimited_guessing(client, alice):
    for _ in range(MAX_FAILED * 3):
        post(client, "/api/owasp/auth/vulnerable-login", username="alice", password="guess-guess")
    ok = post(client, "/api/owasp/auth/vulnerable-login", username="alice", password=PASSWORD)
    assert ok.status_code == 200  # never locked


def test_secure_login_hides_usernames_and_locks_after_failures(client, alice):
    unknown = post(client, "/api/owasp/auth/secure-login", username="nobody", password="x" * 10)
    wrong = post(client, "/api/owasp/auth/secure-login", username="alice", password="x" * 10)
    assert unknown.json() == wrong.json()

    for _ in range(MAX_FAILED):
        post(client, "/api/owasp/auth/secure-login", username="alice", password="guess-guess")
    assert post(client, "/api/owasp/auth/secure-login", username="alice", password=PASSWORD).status_code == 429


def test_vulnerable_admin_list_is_open_to_any_logged_in_user(client, alice, auth):
    response = client.get("/api/owasp/admin/users/vulnerable", **auth(alice))
    assert response.status_code == 200
    assert response.json()[0]["email"] == alice.email  # a normal user sees the user directory


def test_secure_admin_list_is_forbidden_for_normal_users(client, alice, auth, caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        response = client.get("/api/owasp/admin/users/secure", **auth(alice))
    assert response.status_code == 403
    assert "event='forbidden'" in caplog.text


def test_secure_admin_list_works_for_staff(client, make_user, auth):
    admin = make_user("boss", is_staff=True)
    assert client.get("/api/owasp/admin/users/secure", **auth(admin)).status_code == 200


def test_admin_endpoints_need_a_login_at_all(client):
    for version in ("vulnerable", "secure"):
        assert client.get(f"/api/owasp/admin/users/{version}").status_code == 401


# ============================================================ 4. security misconfiguration
def test_vulnerable_debug_endpoint_leaks_the_secret_key(client, settings):
    body = client.get("/api/owasp/misconfig/vulnerable").json()
    assert body["SECRET_KEY"] == settings.SECRET_KEY


def test_secure_endpoint_leaks_nothing(client, settings):
    response = client.get("/api/owasp/misconfig/secure")
    assert response.json() == {"status": "ok"}
    assert settings.SECRET_KEY not in response.text


def good_settings(**changes):
    values = dict(
        DEBUG=False,
        SECRET_KEY="s" * 50,
        JWT_SECRET_KEY="j" * 50,
        ALLOWED_HOSTS=["example.com"],
        SECURE_CONTENT_TYPE_NOSNIFF=True,
        X_FRAME_OPTIONS="DENY",
        MIDDLEWARE=["django.middleware.clickjacking.XFrameOptionsMiddleware"],
    )
    values.update(changes)
    return SimpleNamespace(**values)


def failed_checks(settings):
    return {name for name, passed, _advice in audit_settings(settings) if not passed}


def test_audit_passes_for_a_good_configuration():
    assert failed_checks(good_settings()) == set()


@pytest.mark.parametrize(
    "bad_setting, failing_check",
    [
        ({"DEBUG": True}, "DEBUG is off"),
        ({"SECRET_KEY": "change-me"}, "SECRET_KEY is not a placeholder"),
        ({"JWT_SECRET_KEY": "short"}, "JWT_SECRET_KEY is strong and separate from SECRET_KEY"),
        ({"JWT_SECRET_KEY": "s" * 50}, "JWT_SECRET_KEY is strong and separate from SECRET_KEY"),
        ({"ALLOWED_HOSTS": ["*"]}, "ALLOWED_HOSTS is explicit"),
        ({"SECURE_CONTENT_TYPE_NOSNIFF": False}, "X-Content-Type-Options: nosniff"),
        ({"MIDDLEWARE": []}, "Clickjacking protection (X-Frame-Options)"),
    ],
)
def test_audit_catches_each_misconfiguration(bad_setting, failing_check):
    assert failed_checks(good_settings(**bad_setting)) == {failing_check}


def test_audit_command_fails_with_a_bad_configuration(settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="check"):
        call_command("audit_config")


def test_audit_command_passes_with_a_good_configuration(settings, capsys):
    settings.DEBUG = False
    settings.SECRET_KEY = "s" * 50
    settings.JWT_SECRET_KEY = "j" * 50
    settings.ALLOWED_HOSTS = ["example.com"]
    call_command("audit_config")
    assert "All checks passed" in capsys.readouterr().out


# =========================================================== 5. sensitive information exposure
def test_vulnerable_profile_dumps_the_password_hash(client, alice, auth):
    body = client.get("/api/owasp/profile/vulnerable", **auth(alice)).json()
    assert body["password"] == alice.password


def test_secure_profile_returns_only_allowed_fields(client, alice, auth):
    response = client.get("/api/owasp/profile/secure", **auth(alice))
    assert response.json() == {"id": alice.id, "username": "alice"}
    assert alice.password not in response.text


# ================================================================ 6. secure error handling
def test_vulnerable_error_shows_internals_to_the_client(client):
    response = client.get("/api/owasp/errors/vulnerable", {"doc_id": "abc"})
    body = response.json()
    assert response.status_code == 500
    assert "Traceback" in body["traceback"]
    assert body["type"] == "ValueError"


def test_secure_error_hides_internals_but_logs_them(client, caplog):
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/owasp/errors/secure", {"doc_id": "abc"})

    body = response.json()
    assert response.status_code == 500
    assert set(body) == {"detail", "error_id"}
    assert body["detail"] == "Internal server error"
    assert "Traceback" not in response.text and "ValueError" not in response.text

    # a developer can find the full details with the error id
    record = next(r for r in caplog.records if body["error_id"] in r.getMessage())
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError


# ================================================================ 7. security logging
def test_failed_login_is_logged_with_user_and_ip(client, alice, caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        post(client, "/api/auth/login", username="alice", password="wrong-wrong")

    assert "event='login_failed'" in caplog.text
    assert "username='alice'" in caplog.text
    assert "ip='127.0.0.1'" in caplog.text
    assert "wrong-wrong" not in caplog.text  # the password itself is never logged


def test_lockout_is_logged(client, alice, caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        for _ in range(MAX_FAILED + 1):
            post(client, "/api/auth/login", username="alice", password="wrong-wrong")
    assert "event='login_locked'" in caplog.text


def test_secrets_are_masked_in_log_fields():
    masked = mask_sensitive({"username": "alice", "password": "hunter2", "Token": "abc"})
    assert masked == {"username": "alice", "password": "***", "Token": "***"}
