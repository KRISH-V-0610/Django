import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from ninja.security import HttpBearer


class JWTAuth(HttpBearer):
    """`Authorization: Bearer <token>` -> the User, or None (Ninja then answers 401)."""

    def authenticate(self, request, token):
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return None

        User = get_user_model()
        try:
            return User.objects.get(pk=payload["user_id"], is_active=True)
        except (User.DoesNotExist, KeyError, TypeError, ValueError):
            return None


jwt_auth = JWTAuth()
