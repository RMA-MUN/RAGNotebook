from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str = Field(..., min_length=6, max_length=20)


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=20)
    confirm_password: str = Field(..., min_length=6, max_length=20)
    telephone: str | None = None


class ResetPasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=6, max_length=20)
    new_password: str = Field(..., min_length=6, max_length=20)
    confirm_password: str = Field(..., min_length=6, max_length=20)


class UserUpdateRequest(BaseModel):
    username: str | None = None
    telephone: str | None = None
    avatar: str | None = None
    gender: int | None = None
    bio: str | None = None


class TokenRefreshRequest(BaseModel):
    token: str


class UserResponse(BaseModel):
    uuid: str | None = None
    user_id: str | None = None
    id: str | None = None
    username: str
    email: str
    telephone: str | None = None
    gender: int | None = None
    bio: str | None = None
    avatar: str | None = None
    status: int | None = None
    date_joined: datetime | None = None
    last_login: datetime | None = None
    is_active: bool | None = None


class LoginResponse(BaseModel):
    message: str
    user: UserResponse
    token: str


class RegisterResponse(BaseModel):
    status: int
    message: str
    user: UserResponse
    token: str


class ActionResponse(BaseModel):
    message: str
    user: UserResponse | None = None
    token: str | None = None


class UserDetailResponse(BaseModel):
    success: bool
    message: str
    data: UserResponse
