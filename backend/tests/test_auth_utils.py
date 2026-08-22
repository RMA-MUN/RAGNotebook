"""Unit tests for app.utils.auth_utils.

Redis-dependent paths use tests.fakes.FakeRedis via install_fake_redis.
DB-dependent paths use the SQLite db_engine/session_factory fixtures plus
patch_session_factory (which replaces app.utils.auth_utils.AsyncSessionLocal).
"""
import json
import time

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt

from app.models.user_model import User
from app.utils.auth_utils import (
    ALGORITHM,
    SECRET_KEY,
    blacklist_token,
    decode_django_jwt,
    generate_token,
    get_current_user_id,
    get_user_info_from_db,
    get_user_info_from_redis,
    hash_password,
    verify_password,
)

from tests.conftest import patch_session_factory
from tests.fakes import install_fake_redis


@pytest_asyncio.fixture
async def fake_redis(monkeypatch):
    """Install in-memory redis and return the FakeRedis instance."""
    return await install_fake_redis(monkeypatch)


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------
def test_hash_verify_password_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert isinstance(hashed, str)
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_different_passwords_hash_differently():
    assert hash_password("aaa") != hash_password("bbb")


def test_verify_password_wrong_password_returns_false():
    hashed = hash_password("correct")
    assert verify_password("wrong", hashed) is False


def test_verify_password_malformed_hash_raises():
    # verify_password does not swallow parse errors of malformed hashes
    with pytest.raises(ValueError):
        verify_password("x", "$2b$12$invalidhash")


# ---------------------------------------------------------------------------
# token generation / decoding
# ---------------------------------------------------------------------------
def test_generate_token_returns_token_and_expire_time():
    token, expire_time = generate_token("u1", "alice", "alice@example.com")
    assert isinstance(token, str)
    assert token.count(".") == 2  # JWT format
    assert isinstance(expire_time, int)
    # expire_time is ~24h in the future
    assert abs((expire_time - time.time()) - 86400) < 10


def test_generated_token_decodes_with_expected_claims():
    token, expire_time = generate_token("u1", "alice", "alice@example.com")
    payload = decode_django_jwt(token)
    assert payload is not None
    assert payload["user_id"] == "u1"
    assert payload["username"] == "alice"
    assert payload["email"] == "alice@example.com"
    assert payload["exp"] == expire_time
    assert "iat" in payload
    assert "jti" in payload and isinstance(payload["jti"], str)


def test_tokens_are_unique_per_call():
    t1, _ = generate_token("u1", "alice", "a@b.com")
    t2, _ = generate_token("u1", "alice", "a@b.com")
    assert t1 != t2


def test_decode_django_jwt_garbage_token_returns_none():
    assert decode_django_jwt("garbage") is None
    assert decode_django_jwt("a.b.c") is None
    assert decode_django_jwt("") is None


def test_decode_django_jwt_wrong_signature_returns_none():
    payload = {"user_id": "u1", "exp": int(time.time()) + 3600, "jti": "x"}
    forged = jose_jwt.encode(payload, "different-secret", algorithm=ALGORITHM)
    assert decode_django_jwt(forged) is None


# ---------------------------------------------------------------------------
# get_current_user_id
# ---------------------------------------------------------------------------
async def test_get_current_user_id_valid_token_returns_user_id(fake_redis):
    token, _ = generate_token("u-123", "bob", "bob@example.com")
    user_id = await get_current_user_id(_creds(token))
    assert user_id == "u-123"


async def test_get_current_user_id_invalid_token_raises_401(fake_redis):
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(_creds("not-a-jwt"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Could not validate credentials"


async def test_get_current_user_id_revoked_token_raises_401(fake_redis):
    token, _ = generate_token("u-123", "bob", "bob@example.com")
    await blacklist_token(token)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(_creds(token))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token has been revoked"


async def test_get_current_user_id_missing_user_id_claims_raises_401(fake_redis):
    payload = {"sub": "x", "jti": "unique-jti", "exp": int(time.time()) + 3600}
    token = jose_jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(_creds(token))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Could not find user ID in token"


# ---------------------------------------------------------------------------
# get_user_info_from_db
# ---------------------------------------------------------------------------
async def _insert_user(session_factory, **overrides):
    fields = dict(
        uuid="user-db-1",
        username="dbuser",
        email="dbuser@example.com",
        telephone="13800000000",
        password="hashed-pw",
        is_active=True,
        status=1,
        gender=1,
        bio="hello from db",
        avatar=None,
        last_login=None,
    )
    fields.update(overrides)
    async with session_factory() as session:
        session.add(User(**fields))
        await session.commit()


async def test_get_user_info_from_db_returns_dict_shape(
    db_engine, session_factory, monkeypatch, fake_redis
):
    patch_session_factory(monkeypatch, session_factory)
    await _insert_user(session_factory)

    info = await get_user_info_from_db("user-db-1")
    assert info is not None
    assert info["uuid"] == "user-db-1"
    assert info["user_id"] == "user-db-1"
    assert info["id"] == "user-db-1"
    assert info["username"] == "dbuser"
    assert info["email"] == "dbuser@example.com"
    assert info["telephone"] == "13800000000"
    assert info["gender"] == 1
    assert info["bio"] == "hello from db"
    assert info["avatar"] is None
    assert info["status"] == 1
    assert info["is_active"] is True
    assert "date_joined" in info and (info["date_joined"] is None or isinstance(info["date_joined"], str))
    assert info["last_login"] is None


async def test_get_user_info_from_db_missing_returns_none(
    db_engine, session_factory, monkeypatch, fake_redis
):
    patch_session_factory(monkeypatch, session_factory)
    assert await get_user_info_from_db("no-such-user") is None


# ---------------------------------------------------------------------------
# get_user_info_from_redis
# ---------------------------------------------------------------------------
async def test_get_user_info_from_redis_miss_then_db_then_cache(
    db_engine, session_factory, monkeypatch, fake_redis
):
    patch_session_factory(monkeypatch, session_factory)
    await _insert_user(session_factory)

    # cache miss -> falls back to DB and populates redis
    info = await get_user_info_from_redis("user-db-1")
    assert info is not None
    assert info["username"] == "dbuser"
    assert info["user_id"] == "user-db-1"

    # cached value now stored as JSON under key user:user-db-1
    raw = await fake_redis.get("user:user-db-1")
    assert raw is not None
    assert json.loads(raw)["username"] == "dbuser"

    # second call must come from redis even after the DB row is gone
    async with session_factory() as session:
        user = await session.get(User, "user-db-1")
        await session.delete(user)
        await session.commit()

    info2 = await get_user_info_from_redis("user-db-1")
    assert info2 == info


async def test_get_user_info_from_redis_cache_hit_skips_db(
    db_engine, session_factory, monkeypatch, fake_redis
):
    patch_session_factory(monkeypatch, session_factory)
    cached = {"uuid": "cached-1", "user_id": "cached-1", "id": "cached-1",
              "username": "cached", "email": "c@example.com"}
    await fake_redis.set("user:cached-1", json.dumps(cached))

    info = await get_user_info_from_redis("cached-1")
    assert info == cached


async def test_get_user_info_from_redis_missing_user_returns_none(
    db_engine, session_factory, monkeypatch, fake_redis
):
    patch_session_factory(monkeypatch, session_factory)
    assert await get_user_info_from_redis("missing-user") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))