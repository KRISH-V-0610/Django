"""
Login logic used by /api/auth/login and /api/owasp/auth/secure-login (OWASP topic 3).

What makes it safe:
  * one generic failure for "no such user" and "wrong password" (no user enumeration)
  * after MAX_FAILED wrong attempts the account is locked for LOCK_SECONDS (brute force)
  * every failure and every lock is written to the security log
Django's authenticate() also hashes a dummy password for unknown users, so the response
time does not reveal whether the user exists.
"""
from django.contrib.auth import authenticate
from django.core.cache import cache

from core.security_log import log_event

from .tokens import create_access_token

MAX_FAILED = 5
LOCK_SECONDS = 15 * 60


class LoginLocked(Exception):
    """Too many failed attempts for this username."""


def attempt_login(request, username, password):
    """Return an access token, or None for bad credentials. Raises LoginLocked when locked."""
    key = f"login-fail:{username.lower()}"

    if cache.get(key, 0) >= MAX_FAILED:
        log_event("login_locked", request, username=username)
        raise LoginLocked

    user = authenticate(username=username, password=password)
    if user is None:
        cache.add(key, 0, LOCK_SECONDS)  # create the counter once, with an expiry
        cache.incr(key)
        log_event("login_failed", request, username=username)
        return None

    cache.delete(key)
    return create_access_token(user.id)
