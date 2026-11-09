from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ninja import Field, Router, Schema, Status

from .services import LoginLocked, attempt_login

router = Router(tags=["auth"])
User = get_user_model()


class RegisterIn(Schema):
    username: str = Field(..., min_length=3, max_length=50)
    password: str


class LoginIn(Schema):
    username: str
    password: str


class MessageOut(Schema):
    detail: str


class UserOut(Schema):
    id: int
    username: str


class TokenOut(Schema):
    access: str
    token_type: str = "bearer"


@router.post("/register", response={201: UserOut, 400: MessageOut, 409: MessageOut})
def register(request, data: RegisterIn):
    try:
        validate_password(data.password)
    except ValidationError as exc:
        return Status(400, {"detail": " ".join(exc.messages)})
    try:
        user = User.objects.create_user(username=data.username, password=data.password)
    except IntegrityError:
        return Status(409, {"detail": "Username already taken."})
    return Status(201, user)


@router.post("/login", response={200: TokenOut, 401: MessageOut, 429: MessageOut})
def login(request, data: LoginIn):
    try:
        token = attempt_login(request, data.username, data.password)
    except LoginLocked:
        return Status(429, {"detail": "Too many failed attempts. Try again later."})
    if token is None:
        return Status(401, {"detail": "Invalid credentials."})  # same text for every failure
    return Status(200, {"access": token})
