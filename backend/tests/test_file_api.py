"""文件上传 API 测试（/file/upload/）。"""

import os

from tests.conftest import install_fake_redis
from tests.fakes import TEST_USER_ID


async def test_upload_file(client, session_factory, monkeypatch, tmp_path):
    """上传成功：文件写入临时目录、avatar 更新、redis 缓存被清。"""
    from app.router import user as user_module
    from app.models.user_model import User, UserStatusChoice
    from app.utils.auth_utils import hash_password

    # 把媒体目录重定向到临时目录，避免污染仓库
    monkeypatch.setattr(user_module, "MEDIA_DIR", str(tmp_path))

    async with session_factory() as s:
        user = User(uuid=TEST_USER_ID, username="alice", email="alice@example.com",
                    password=hash_password("secret123"), status=UserStatusChoice.ACTIVE, is_active=True)
        s.add(user)
        await s.commit()

    from tests.fakes import FakeRedis
    redis = FakeRedis()
    await install_fake_redis(monkeypatch, redis)
    await redis.set(f"user:{TEST_USER_ID}", "stale")

    resp = await client.post(
        "/file/upload/",
        files={"file": ("avatar.png", b"\x89PNG fake-image-bytes", "image/png")},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["url"].startswith("/media/img/")
    assert body["data"]["alt"]

    # 文件确实写到了临时目录
    files = os.listdir(os.path.join(str(tmp_path), "img"))
    assert len(files) == 1 and files[0].endswith(".png")

    # avatar 已更新
    async with session_factory() as s:
        from sqlalchemy import select
        from app.models.user_model import User
        result = await s.execute(select(User).where(User.uuid == TEST_USER_ID))
        updated = result.scalar_one()
        assert updated.avatar == body["data"]["url"]

    # 用户缓存已被清除
    assert await redis.get(f"user:{TEST_USER_ID}") is None


async def test_upload_file_requires_auth(raw_client):
    resp = await raw_client.post("/file/upload/", files={"file": ("a.png", b"x", "image/png")})
    assert resp.status_code == 401