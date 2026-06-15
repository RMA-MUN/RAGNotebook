from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class UserRegisterRequest(BaseModel):
    username: str
    email: str
    telephone: Optional[str] = None
    password: str
    confirm_password: str

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        if len(v) < 6 or len(v) > 20:
            raise ValueError("密码长度必须在6-20位之间")
        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm_password(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("密码和确认密码不一致")
        return v


class UserLoginRequest(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str


class UserResponse(BaseModel):
    uuid: str
    username: str
    email: str
    telephone: Optional[str] = None
    gender: Optional[int] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None
    status: int
    date_joined: Optional[datetime] = None
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    username: Optional[str] = None
    telephone: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[int] = None
    bio: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
    confirm_password: Optional[str] = None

    @field_validator("new_password")
    @classmethod
    def validate_new_password_length(cls, v: str) -> str:
        if len(v) < 6 or len(v) > 20:
            raise ValueError("密码长度必须在6-20位之间")
        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_confirm_password(cls, v: Optional[str], info) -> Optional[str]:
        if v is not None and "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("新密码和确认密码不一致")
        return v


class TokenRefreshRequest(BaseModel):
    token: str


class TokenResponse(BaseModel):
    token: str
    expire_time: int
    user: Optional[UserResponse] = None
