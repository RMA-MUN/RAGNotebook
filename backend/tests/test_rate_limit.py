"""rate_limit 测试：全局开关关闭时放行；开启时按 IP 计数并抛出 429。"""
import pytest
from fastapi import HTTPException, Request

import app.core.rate_limit as rate_limit_module
from tests.fakes import install_fake_redis


def _make_scope(client=("127.0.0.1", 12345)):
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": client,
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
    }


def _make_request(client=("127.0.0.1", 12345)) -> Request:
    return Request(_make_scope(client))


class _StubASGIApp:
    """记录被调用的次数，模拟下游应用。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1


class TestRateLimitDependency:
    # -- 全局开关关闭（conftest 已设 RATE_LIMIT_ENABLED=false） --
    def test_module_flag_disabled_by_conftest_env(self):
        # 证明 conftest 的环境变量确实让模块常量变为 False
        assert rate_limit_module._RATE_LIMIT_ENABLED is False

    async def test_dependency_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", False)
        dep = rate_limit_module.rate_limit(limit=1, window=60)
        assert await dep(_make_request()) is None

    async def test_dependency_does_not_touch_redis_when_disabled(self, monkeypatch):
        # 关闭时不应创建任何限流 key（不触碰 redis）
        redis = await install_fake_redis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", False)
        dep = rate_limit_module.rate_limit(limit=5, window=60)
        for _ in range(10):
            assert await dep(_make_request()) is None
        assert redis._data == {}

    # -- 全局开关开启 --
    async def test_dependency_raises_429_when_count_exceeds_limit(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", True)
        dep = rate_limit_module.rate_limit(limit=1, window=60)

        request = _make_request()
        assert await dep(request) is None  # 第一次：计数 1

        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "请求过于频繁，请稍后再试"
        # 计数写入 redis，key 含客户端 IP
        assert redis._data["rate_limit:aichat:127.0.0.1"] == 1

    async def test_dependency_allows_requests_within_limit(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", True)
        dep = rate_limit_module.rate_limit(limit=3, window=60)

        request = _make_request()
        for _ in range(3):
            assert await dep(request) is None
        # 计数累加到 3
        assert redis._data["rate_limit:aichat:127.0.0.1"] == 3
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 429

    async def test_dependency_keys_are_per_ip(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", True)
        dep = rate_limit_module.rate_limit(limit=1, window=60)

        await dep(_make_request(client=("1.1.1.1", 1)))
        await dep(_make_request(client=("2.2.2.2", 2)))
        # 不同 IP 互不影响
        assert redis._data["rate_limit:aichat:1.1.1.1"] == 1
        assert redis._data["rate_limit:aichat:2.2.2.2"] == 1
        # 1.1.1.1 第二次请求触发 429
        with pytest.raises(HTTPException) as exc_info:
            await dep(_make_request(client=("1.1.1.1", 1)))
        assert exc_info.value.status_code == 429


class TestRateLimitMiddleware:
    async def test_passes_through_when_disabled(self, monkeypatch):
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", False)
        app = _StubASGIApp()
        middleware = rate_limit_module.RateLimitMiddleware(app, limit=1, window=60)

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message):
            pass

        await middleware(_make_scope(), _receive, _send)
        assert app.calls == 1  # 下游应用被调用，未做任何限流

    async def test_passes_through_when_disabled_even_repeated(self, monkeypatch):
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", False)
        app = _StubASGIApp()
        middleware = rate_limit_module.RateLimitMiddleware(app, limit=1, window=60)
        for _ in range(3):
            await middleware(_make_scope(), None, None)
        assert app.calls == 3

    async def test_enforces_limit_and_sends_429(self, monkeypatch):
        await install_fake_redis(monkeypatch)
        monkeypatch.setattr(rate_limit_module, "_RATE_LIMIT_ENABLED", True)
        app = _StubASGIApp()
        middleware = rate_limit_module.RateLimitMiddleware(app, limit=1, window=60)

        sent = []

        async def _receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def _send(message):
            sent.append(message)

        # 第一次：计数并放行到下游
        await middleware(_make_scope(), _receive, _send)
        assert app.calls == 1
        assert sent == []

        # 第二次：命中上限，由中间件直接返回 429，不再进入下游
        await middleware(_make_scope(), _receive, _send)
        assert app.calls == 1
        start = [m for m in sent if m["type"] == "http.response.start"]
        assert start and start[0]["status"] == 429