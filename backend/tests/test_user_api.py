"""用户认证 API 集成测试（真实 SQLite CRUD + 内存 Redis + 假模型）。

覆盖: login / register / reset-password / refresh-token / detail / update / logout。
"""
from tests.conftest import install_fake_redis
from tests.fakes import TEST_USER_ID

PASSWORD = "secret123"


async def seed_user(session_factory, username="alice", email="alice@example.com", password=PASSWORD, uuid=None):
    """向 SQLite 种入一个用户，返回 ORM 实例。"""
    from app.models.user_model import User, UserStatusChoice
    from app.utils.auth_utils import hash_password

    async with session_factory() as s:
        user = User(
            uuid=uuid or TEST_USER_ID,
            username=username,
            email=email,
            password=hash_password(password),
            status=UserStatusChoice.ACTIVE,
            is_active=True,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


def mint_token(user_uuid: str, username: str = "alice", email: str = "alice@example.com") -> str:
    from app.utils.auth_utils import generate_token
    token, _ = generate_token(user_uuid, username, email)
    return token


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
async def test_login_success(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post("/user/login/", json={"username": "alice", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert "登录成功" in body["message"]
    assert body["token"]
    assert body["user"]["username"] == "alice"
    assert body["user"]["uuid"] == TEST_USER_ID


async def test_login_by_email(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post("/user/login/", json={"email": "alice@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["token"]


async def test_login_unknown_user(client):
    resp = await client.post("/user/login/", json={"username": "nobody", "password": PASSWORD})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "用户名或邮箱不存在" in body["message"]


async def test_login_wrong_password(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post("/user/login/", json={"username": "alice", "password": "wrong123"})
    assert resp.status_code == 400
    assert "密码错误" in resp.json()["message"]


async def test_login_locked_user(client, session_factory):
    from app.models.user_model import User, UserStatusChoice
    from app.utils.auth_utils import hash_password

    async with session_factory() as s:
        user = User(username="bob", email="bob@example.com", password=hash_password(PASSWORD), status=UserStatusChoice.LOCKED)
        s.add(user)
        await s.commit()

    resp = await client.post("/user/login/", json={"username": "bob", "password": PASSWORD})
    assert resp.status_code == 400
    assert "用户状态异常" in resp.json()["message"]


async def test_login_validation_error(client):
    resp = await client.post("/user/login/", json={"username": "alice", "password": "123"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "password" in body["message"] or "字段" in body["message"]


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------
async def test_register_success(client):
    resp = await client.post("/user/register/", json={
        "username": "newuser",
        "email": "new@example.com",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == 201
    assert "注册成功" in body["message"]
    assert body["token"]
    assert body["user"]["email"] == "new@example.com"


async def test_register_password_mismatch(client):
    resp = await client.post("/user/register/", json={
        "username": "newuser",
        "email": "new@example.com",
        "password": PASSWORD,
        "confirm_password": "different1",
    })
    assert resp.status_code == 400
    assert "密码和确认密码不一致" in resp.json()["message"]["confirm_password"]


async def test_register_duplicate_email(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post("/user/register/", json={
        "username": "other",
        "email": "alice@example.com",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    })
    assert resp.status_code == 400
    assert "该邮箱已被注册" in resp.json()["message"]["email"]


async def test_register_invalid_email(client):
    resp = await client.post("/user/register/", json={
        "username": "x",
        "email": "not-an-email",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    })
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 重置密码
# ---------------------------------------------------------------------------
async def test_reset_password_success(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post(
        "/user/reset-password/",
        json={"old_password": PASSWORD, "new_password": "newpass123", "confirm_password": "newpass123"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "密码重置成功"
    assert body["token"]


async def test_reset_password_confirm_mismatch(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post(
        "/user/reset-password/",
        json={"old_password": PASSWORD, "new_password": "newpass123", "confirm_password": "other456"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 400
    assert "新密码和确认密码不一致" in resp.json()["message"]


async def test_reset_password_same_as_old(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post(
        "/user/reset-password/",
        json={"old_password": PASSWORD, "new_password": PASSWORD, "confirm_password": PASSWORD},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 400
    assert "新密码不能和旧密码相同" in resp.json()["message"]


async def test_reset_password_wrong_old(client, session_factory):
    await seed_user(session_factory)
    resp = await client.post(
        "/user/reset-password/",
        json={"old_password": "wrongold", "new_password": "newpass123", "confirm_password": "newpass123"},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 400
    assert "请检查旧密码是否正确" in resp.json()["message"]


# ---------------------------------------------------------------------------
# Token 刷新
# ---------------------------------------------------------------------------
async def test_refresh_token_success(client, session_factory):
    user = await seed_user(session_factory)
    token = mint_token(user.uuid)
    resp = await client.post("/user/refresh-token/", json={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Token刷新成功"
    assert body["token"] != token
    assert body["expire_time"] > 0


async def test_refresh_token_invalid(client):
    resp = await client.post("/user/refresh-token/", json={"token": "garbage.not.jwt"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


# ---------------------------------------------------------------------------
# 用户详情 / 更新 / 登出
# ---------------------------------------------------------------------------
async def test_get_user_detail(client, session_factory):
    await seed_user(session_factory)
    resp = await client.get("/user/detail/", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["uuid"] == TEST_USER_ID
    assert body["data"]["username"] == "alice"


async def test_update_user(client, session_factory):
    await seed_user(session_factory)
    resp = await client.put("/user/update/", json={"bio": "hello world", "gender": 1},
                            headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "用户信息更新成功"
    assert body["user"]["bio"] == "hello world"
    assert body["user"]["gender"] == 1
    assert body["token"]


async def test_update_user_telephone_conflict(client, session_factory):
    from app.models.user_model import User, UserStatusChoice
    from app.utils.auth_utils import hash_password

    async with session_factory() as s:
        other = User(uuid="other-user", username="other", email="other@example.com",
                     telephone="13800000000", password=hash_password(PASSWORD), status=UserStatusChoice.ACTIVE)
        s.add(other)
        await s.commit()

    await seed_user(session_factory)
    resp = await client.put("/user/update/", json={"telephone": "13800000000"},
                            headers={"Authorization": "Bearer x"})
    assert resp.status_code == 400
    assert "该电话号码已被注册" in resp.json()["message"]["telephone"]


async def test_logout(client):
    resp = await client.post("/user/logout/", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert resp.json()["message"] == "用户注销成功"


# ---------------------------------------------------------------------------
# 认证失败路径（真实 security / get_current_user_id）
# ---------------------------------------------------------------------------
async def test_protected_endpoint_without_token(raw_client):
    resp = await raw_client.get("/user/detail/")
    # HTTPBearer 默认 auto_error：缺少 Authorization 头 → 401（starlette 0.50 行为）
    assert resp.status_code == 401


async def test_protected_endpoint_invalid_token(raw_client):
    resp = await raw_client.get("/user/detail/", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


async def test_protected_endpoint_revoked_token(raw_client, monkeypatch, session_factory):
    """Token 已被加入黑名单 → 401。"""
    from tests.fakes import FakeRedis

    from app.utils.auth_utils import decode_django_jwt

    redis = FakeRedis()
    await install_fake_redis(monkeypatch, redis)

    user = await seed_user(session_factory)
    token = mint_token(user.uuid)
    payload = decode_django_jwt(token)
    await redis.set(f"blacklist:{payload['jti']}", "1")

    resp = await raw_client.get("/user/detail/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401