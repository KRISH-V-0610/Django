import jwt

from django.conf import settings
from django.contrib.auth import get_user_model
from ninja.security import HttpBearer


class JWTAuth(HttpBearer):

    def authenticate(self, request, token):

        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=["HS256"],
            )

        except jwt.InvalidTokenError:
            return None

        if payload.get("token_type") != "access":
            return None

        User = get_user_model()

        try:
            user = User.objects.get(
                pk=payload["user_id"],
                is_active=True,
            )
        except (User.DoesNotExist, KeyError, TypeError, ValueError):
            return None

        return user


jwt_auth = JWTAuth()