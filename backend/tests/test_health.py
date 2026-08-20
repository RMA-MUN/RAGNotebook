"""健康检查 API 测试。"""


async def test_health_live(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"] == {"status": "ok"}


async def test_health_ready_ok_when_connections_ok(client, monkeypatch):
    """MySQL/Redis 都正常时，/health/ready 返回 ok。"""
    async def _ok(*args, **kwargs):
        return True

    monkeypatch.setattr("app.router.health.check_mysql_connection", _ok)
    monkeypatch.setattr("app.router.health.check_redis_connection", _ok)

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


async def test_health_ready_503_when_connection_fails(client, monkeypatch):
    """任一连接失败时，/health/ready 返回 503。"""
    async def _fail(*args, **kwargs):
        return False

    monkeypatch.setattr("app.router.health.check_mysql_connection", _fail)
    monkeypatch.setattr("app.router.health.check_redis_connection", _fail)

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == 503
    assert "连接失败" in body["message"]


async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello World"}


async def test_hello_name(client):
    resp = await client.get("/hello/world")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello world"}