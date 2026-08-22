"""app/cache/redis_decorator.py 测试：缓存写入/命中、key 生成、删除与模式删除。"""
import pytest

from app.cache.redis_decorator import RedisCache, cache_with_redis
from tests.fakes import install_fake_redis


class TestGetOrSet:
    async def test_caches_result_and_returns_cached_on_second_call(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        async def compute(x):
            calls["n"] += 1
            return {"value": x}

        first = await RedisCache.get_or_set("k1", compute, 42)
        second = await RedisCache.get_or_set("k1", compute, 42)

        assert first == {"value": 42}
        assert second == {"value": 42}
        assert calls["n"] == 1  # 第二次命中缓存，函数不再执行

    async def test_caches_string_result(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        async def compute():
            calls["n"] += 1
            return "hello-cached"

        first = await RedisCache.get_or_set("k-str", compute)
        second = await RedisCache.get_or_set("k-str", compute)

        assert first == "hello-cached"
        assert second == "hello-cached"
        assert calls["n"] == 1

    async def test_caches_int_result_round_trip(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        async def compute():
            calls["n"] += 1
            return 7

        first = await RedisCache.get_or_set("k-int", compute)
        second = await RedisCache.get_or_set("k-int", compute)

        assert first == 7
        assert second == 7
        assert calls["n"] == 1

    async def test_different_keys_do_not_share_cache(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        async def compute(x):
            calls["n"] += 1
            return {"x": x}

        await RedisCache.get_or_set("a", compute, 1)
        await RedisCache.get_or_set("b", compute, 2)

        assert calls["n"] == 2

    async def test_expire_forwarded_to_redis(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)

        async def compute():
            return {"v": 1}

        await RedisCache.get_or_set("k-ex", compute, expire=60)
        assert redis._expire["k-ex"] == 60

    async def test_default_expire(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)

        async def compute():
            return {"v": 1}

        await RedisCache.get_or_set("k-def", compute)
        assert redis._expire["k-def"] == 3600

    async def test_any_key_type_converted_to_str(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        async def compute():
            calls["n"] += 1
            return "ok"

        await RedisCache.get_or_set(12345, compute)
        await RedisCache.get_or_set(12345, compute)
        assert calls["n"] == 1


class TestCacheKey:
    def test_basic_format(self):
        assert RedisCache.cache_key("notes", "u1", "q") == "notes:u1:q"

    def test_none_args_skipped(self):
        assert RedisCache.cache_key("prefix", None, "a", None) == "prefix:a"

    def test_db_session_args_skipped(self):
        class FakeSession:
            def execute(self):
                pass

        assert RedisCache.cache_key("prefix", FakeSession(), "a") == "prefix:a"

    def test_kwargs_sorted_and_formatted(self):
        key = RedisCache.cache_key("prefix", "u1", b="x", a="y")
        assert key == "prefix:u1:a:y:b:x"

    def test_none_kwargs_skipped(self):
        assert RedisCache.cache_key("prefix", a=None, b="x") == "prefix:b:x"

    def test_db_kwarg_skipped(self):
        assert RedisCache.cache_key("prefix", db=object(), a="x") == "prefix:a:x"

    def test_empty_parts(self):
        assert RedisCache.cache_key("prefix") == "prefix"


class TestDelete:
    async def test_delete_returns_true_and_removes(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)

        async def compute():
            return {"v": 1}

        await RedisCache.get_or_set("del-1", compute)
        assert await redis.get("del-1") is not None

        assert await RedisCache.delete("del-1") is True
        assert await redis.get("del-1") is None

    async def test_delete_non_existent_key_returns_true(self, monkeypatch):
        await install_fake_redis(monkeypatch)
        assert await RedisCache.delete("no-such-key") is True


class TestDeletePattern:
    async def test_delete_pattern_removes_matching_only(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)

        async def compute(x):
            return {"x": x}

        await RedisCache.get_or_set("pat:1", compute, 1)
        await RedisCache.get_or_set("pat:2", compute, 2)
        await RedisCache.get_or_set("other", compute, 3)

        removed = await RedisCache.delete_pattern("pat:*")

        assert removed == 2
        assert await redis.get("pat:1") is None
        assert await redis.get("pat:2") is None
        assert await redis.get("other") is not None

    async def test_delete_pattern_no_match_returns_zero(self, monkeypatch):
        await install_fake_redis(monkeypatch)
        assert await RedisCache.delete_pattern("nothing:*") == 0


class TestCacheWithRedisDecorator:
    async def test_decorator_caches_across_calls(self, monkeypatch):
        await install_fake_redis(monkeypatch)

        calls = {"n": 0}

        @cache_with_redis("deco", expire=60)
        async def load(user_id):
            calls["n"] += 1
            return {"user_id": user_id}

        first = await load("u1")
        second = await load("u1")

        assert first == {"user_id": "u1"}
        assert second == {"user_id": "u1"}
        assert calls["n"] == 1

    async def test_decorator_generates_key_from_args(self, monkeypatch):
        redis = await install_fake_redis(monkeypatch)

        @cache_with_redis("deco")
        async def load(user_id, page=1):
            return {"user_id": user_id, "page": page}

        await load("u1", page=1)
        assert redis._data["deco:u1:page:1"] is not None