from ninja import Schema


class RegisterSchema(Schema):
    username: str
    email: str
    password: str


class UserOut(Schema):
    id: int
    username: str
    email: str
    
class LoginSchema(Schema):
    username: str
    password: str


class TokenResponse(Schema):
    access: str
    refresh: str
    token_type: str


class ErrorSchema(Schema):
    detail: str

class LogoutSchema(Schema):
    refresh: str

class RefreshSchema(Schema):
    refresh: str