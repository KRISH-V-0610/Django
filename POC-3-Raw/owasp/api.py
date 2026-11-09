"""
OWASP demos (topic E). Every topic has a /vulnerable and a /secure version side by side.
The weak versions are deliberately broken for LOCAL learning only: never deploy this app.

    1. Broken access control / IDOR ........ documents/api.py  (/api/vulnerable/documents)
    2. SQL injection ....................... /sqli/*
    3. Authentication / authorization ...... /auth/*, /admin/*
    4. Security misconfiguration ........... /misconfig/*  and  manage.py audit_config
    5. Sensitive information exposure ...... /profile/*
    6. Secure error handling ............... /errors/*  (+ the global handler in config/api.py)
    7. Security logging .................... core/security_log.py (used by all the secure paths)
"""
import traceback

from django.contrib.auth import get_user_model
from django.db import connection
from django.forms.models import model_to_dict
from ninja import Router, Schema, Status

from accounts.auth import jwt_auth
from accounts.services import LoginLocked, attempt_login
from accounts.tokens import create_access_token
from core.security_log import log_event
from documents.models import Document

router = Router(tags=["owasp"])
User = get_user_model()


class LoginIn(Schema):
    username: str
    password: str


class MessageOut(Schema):
    detail: str


# ===================================================================== 2. SQL injection
def _rows(cursor):
    return [{"id": row[0], "title": row[1], "owner_id": row[2]} for row in cursor.fetchall()]


@router.get("/sqli/vulnerable")
def sqli_vulnerable(request, title: str):
    # BUG: user input is pasted into the SQL text, so it can change the query itself.
    sql = f"SELECT id, title, owner_id FROM documents_document WHERE title = '{title}'"
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return _rows(cursor)


@router.get("/sqli/secure")
def sqli_secure(request, title: str):
    # FIX: the value travels separately from the SQL text. The driver treats it as data
    # only, so quotes and keywords inside it have no special meaning.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, title, owner_id FROM documents_document WHERE title = %s", [title]
        )
        return _rows(cursor)


# ================================================== 3. authentication / authorization
@router.post("/auth/vulnerable-login", response={200: dict, 401: MessageOut})
def vulnerable_login(request, data: LoginIn):
    user = User.objects.filter(username=data.username).first()
    if user is None:
        return Status(401, {"detail": "No such user."})  # BUG 1: tells an attacker which names exist
    if not user.check_password(data.password):
        return Status(401, {"detail": "Wrong password."})  # BUG 2: and no limit on guesses
    return Status(200, {"access": create_access_token(user.id)})


@router.post("/auth/secure-login", response={200: dict, 401: MessageOut, 429: MessageOut})
def secure_login(request, data: LoginIn):
    try:
        token = attempt_login(request, data.username, data.password)
    except LoginLocked:
        return Status(429, {"detail": "Too many failed attempts. Try again later."})
    if token is None:
        return Status(401, {"detail": "Invalid credentials."})
    return Status(200, {"access": token})


def _user_list():
    return [
        {"id": u.id, "username": u.username, "email": u.email, "is_staff": u.is_staff}
        for u in User.objects.order_by("id")
    ]


@router.get("/admin/users/vulnerable", auth=jwt_auth)
def admin_users_vulnerable(request):
    return _user_list()  # BUG: logged in is enough, the role is never checked


@router.get("/admin/users/secure", auth=jwt_auth, response={200: list[dict], 403: MessageOut})
def admin_users_secure(request):
    if not request.auth.is_staff:  # authorization = "are you ALLOWED?", after authentication
        log_event("forbidden", request, required="is_staff")
        return Status(403, {"detail": "Admins only."})
    return Status(200, _user_list())


# ============================================================ 4. security misconfiguration
@router.get("/misconfig/vulnerable")
def misconfig_vulnerable(request):
    """A 'temporary' debug endpoint that was never removed."""
    from django.conf import settings

    return {
        "DEBUG": settings.DEBUG,
        "ALLOWED_HOSTS": settings.ALLOWED_HOSTS,
        "SECRET_KEY": settings.SECRET_KEY,
        "DATABASE": dict(settings.DATABASES["default"]),
    }


@router.get("/misconfig/secure")
def misconfig_secure(request):
    return {"status": "ok"}  # a health check says "ok" and nothing else


# =========================================================== 5. sensitive information
@router.get("/profile/vulnerable", auth=jwt_auth)
def profile_vulnerable(request):
    return model_to_dict(request.auth)  # BUG: dumps every column, including the password hash


class ProfileOut(Schema):
    id: int
    username: str


@router.get("/profile/secure", auth=jwt_auth, response=ProfileOut)
def profile_secure(request):
    return request.auth  # the response schema is an allow-list: only id and username leave


# ================================================================ 6. error handling
@router.get("/errors/vulnerable", response={200: dict, 500: dict})
def errors_vulnerable(request, doc_id: str):
    try:
        return Status(200, {"title": Document.objects.get(pk=doc_id).title})
    except Exception as exc:
        # BUG: the exception text and the stack trace are sent to the client
        return Status(500, {"error": str(exc), "type": type(exc).__name__,
                            "traceback": traceback.format_exc()})


@router.get("/errors/secure", response={200: dict})
def errors_secure(request, doc_id: str):
    # no try/except: an unexpected error reaches the global handler in config/api.py,
    # which logs the details and returns only a generic message plus an error id
    return Status(200, {"title": Document.objects.get(pk=doc_id).title})
