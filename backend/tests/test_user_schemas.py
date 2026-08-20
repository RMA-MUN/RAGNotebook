"""app/schemas/user_schemas.py 的 Pydantic 模型校验测试。"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.user_schemas import (
    ActionResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenRefreshRequest,
    UserDetailResponse,
    UserResponse,
    UserUpdateRequest,
)


def _has_error_for(excinfo: pytest.ExceptionInfo, field: str):
    return any(field in e["loc"] for e in excinfo.value.errors())


def _assert_raises_for(model, payload, field: str):
    with pytest.raises(ValidationError) as excinfo:
        model(**payload)
    assert _has_error_for(excinfo, field), f"期望字段 {field} 校验失败，实际错误: {excinfo.value.errors()}"


VALID_PASSWORD = "secret123"


class TestLoginRequest:
    def test_valid_with_username(self):
        model = LoginRequest(username="alice", password=VALID_PASSWORD)
        assert model.username == "alice"
        assert model.email is None

    def test_valid_with_email(self):
        model = LoginRequest(email="alice@example.com", password=VALID_PASSWORD)
        assert model.email == "alice@example.com"

    def test_username_and_email_optional(self):
        # 模式上两者均可选（语义上至少填一个由业务层校验）
        model = LoginRequest(password=VALID_PASSWORD)
        assert model.username is None
        assert model.email is None

    def test_password_required(self):
        _assert_raises_for(LoginRequest, {"username": "alice"}, "password")

    def test_password_too_short(self):
        _assert_raises_for(LoginRequest, {"username": "alice", "password": "12345"}, "password")

    def test_password_too_long(self):
        _assert_raises_for(LoginRequest, {"username": "alice", "password": "a" * 21}, "password")

    def test_password_exactly_min_length_ok(self):
        model = LoginRequest(username="alice", password="123456")
        assert model.password == "123456"

    def test_password_exactly_max_length_ok(self):
        model = LoginRequest(username="alice", password="a" * 20)
        assert len(model.password) == 20

    def test_email_type_not_checked_in_login(self):
        # LoginRequest 的 email 是普通 str，不做 EmailStr 校验
        model = LoginRequest(email="not-an-email", password=VALID_PASSWORD)
        assert model.email == "not-an-email"


class TestRegisterRequest:
    VALID = {
        "username": "alice",
        "email": "alice@example.com",
        "password": VALID_PASSWORD,
        "confirm_password": VALID_PASSWORD,
    }

    def test_valid(self):
        model = RegisterRequest(**self.VALID)
        assert model.telephone is None

    def test_valid_with_telephone(self):
        model = RegisterRequest(**self.VALID, telephone="13800138000")
        assert model.telephone == "13800138000"

    def test_username_required(self):
        payload = {k: v for k, v in self.VALID.items() if k != "username"}
        _assert_raises_for(RegisterRequest, payload, "username")

    def test_email_required(self):
        payload = {k: v for k, v in self.VALID.items() if k != "email"}
        _assert_raises_for(RegisterRequest, payload, "email")

    def test_password_required(self):
        payload = {k: v for k, v in self.VALID.items() if k != "password"}
        _assert_raises_for(RegisterRequest, payload, "password")

    def test_confirm_password_required(self):
        payload = {k: v for k, v in self.VALID.items() if k != "confirm_password"}
        _assert_raises_for(RegisterRequest, payload, "confirm_password")

    def test_invalid_email_rejected(self):
        _assert_raises_for(RegisterRequest, {**self.VALID, "email": "not-an-email"}, "email")

    def test_empty_email_rejected(self):
        _assert_raises_for(RegisterRequest, {**self.VALID, "email": ""}, "email")

    def test_password_too_short(self):
        _assert_raises_for(RegisterRequest, {**self.VALID, "password": "12345"}, "password")

    def test_password_too_long(self):
        _assert_raises_for(RegisterRequest, {**self.VALID, "password": "a" * 21}, "password")

    def test_confirm_password_too_short(self):
        _assert_raises_for(RegisterRequest, {**self.VALID, "confirm_password": "123"}, "confirm_password")

    def test_confirm_password_mismatch_not_enforced_by_schema(self):
        # 两次密码是否一致属于业务校验，模式层不拦截
        payload = {k: v for k, v in self.VALID.items() if k != "confirm_password"}
        model = RegisterRequest(**payload, confirm_password="different1")
        assert model.confirm_password == "different1"


class TestResetPasswordRequest:
    def test_valid(self):
        model = ResetPasswordRequest(
            old_password="oldpass1", new_password=VALID_PASSWORD, confirm_password=VALID_PASSWORD
        )
        assert model.old_password == "oldpass1"

    def test_old_password_required(self):
        with pytest.raises(ValidationError) as excinfo:
            ResetPasswordRequest(new_password=VALID_PASSWORD, confirm_password=VALID_PASSWORD)
        assert _has_error_for(excinfo, "old_password")

    def test_new_password_too_short(self):
        with pytest.raises(ValidationError) as excinfo:
            ResetPasswordRequest(old_password="oldpass1", new_password="12345", confirm_password=VALID_PASSWORD)
        assert _has_error_for(excinfo, "new_password")

    def test_confirm_password_too_long(self):
        with pytest.raises(ValidationError) as excinfo:
            ResetPasswordRequest(old_password="oldpass1", new_password=VALID_PASSWORD, confirm_password="a" * 21)
        assert _has_error_for(excinfo, "confirm_password")


class TestUserUpdateRequest:
    def test_all_optional(self):
        model = UserUpdateRequest()
        assert model.username is None
        assert model.telephone is None
        assert model.avatar is None
        assert model.gender is None
        assert model.bio is None

    def test_valid_full(self):
        model = UserUpdateRequest(username="bob", telephone="139", avatar="/a.png", gender=1, bio="hi")
        assert model.gender == 1

    def test_gender_must_be_int(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest(gender="male")


class TestTokenRefreshRequest:
    def test_valid(self):
        assert TokenRefreshRequest(token="abc").token == "abc"

    def test_token_required(self):
        _assert_raises_for(TokenRefreshRequest, {}, "token")


class TestUserResponse:
    def test_valid(self):
        model = UserResponse(username="alice", email="alice@example.com")
        assert model.uuid is None
        assert model.user_id is None
        assert model.id is None
        assert model.telephone is None
        assert model.status is None
        assert model.date_joined is None
        assert model.is_active is None

    def test_username_required(self):
        _assert_raises_for(UserResponse, {"email": "a@b.com"}, "username")

    def test_email_required(self):
        _assert_raises_for(UserResponse, {"username": "alice"}, "email")

    def test_datetime_accepted(self):
        model = UserResponse(
            username="alice", email="a@b.com", date_joined=datetime(2024, 1, 1), is_active=True
        )
        assert model.is_active is True
        assert model.date_joined.year == 2024


class TestUserResponseModels:
    def _user(self) -> dict:
        return {"username": "alice", "email": "alice@example.com"}

    def test_login_response(self):
        model = LoginResponse(message="ok", user=UserResponse(**self._user()), token="tok")
        assert model.token == "tok"

    def test_login_response_requires_user(self):
        with pytest.raises(ValidationError) as excinfo:
            LoginResponse(message="ok", user={}, token="tok")
        assert _has_error_for(excinfo, "user")

    def test_register_response(self):
        model = RegisterResponse(status=1, message="ok", user=UserResponse(**self._user()), token="tok")
        assert model.status == 1

    def test_register_response_requires_status(self):
        with pytest.raises(ValidationError) as excinfo:
            RegisterResponse(message="ok", user=UserResponse(**self._user()), token="tok")
        assert _has_error_for(excinfo, "status")

    def test_action_response_optionals(self):
        model = ActionResponse(message="ok")
        assert model.user is None
        assert model.token is None

    def test_user_detail_response(self):
        model = UserDetailResponse(success=True, message="ok", data=UserResponse(**self._user()))
        assert model.success is True

    def test_user_detail_response_requires_data(self):
        with pytest.raises(ValidationError) as excinfo:
            UserDetailResponse(success=True, message="ok", data={})
        assert _has_error_for(excinfo, "data")