import pytest

from app.models.user_ai_config import UserAIConfig
from tests.fakes import TEST_USER_ID


async def test_get_returns_defaults_when_unconfigured(client, auth_headers):
    resp = await client.get("/config/ai", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["chat"]["api_key_set"] is False
    assert data["chat"]["base_url"] == ""
    assert data["embed"]["api_key_set"] is False


async def test_put_encrypts_and_get_masks(client, auth_headers, session_factory):
    payload = {
        "chat": {"base_url": "http://localhost:11434/v1", "api_key": "sk-real-secret", "model": "qwen3:8b"},
        "embed": {"base_url": "", "api_key": "", "model": ""},
        "vision": {"base_url": "", "api_key": "", "model": ""},
        "rerank": {"base_url": "", "api_key": "", "model": ""},
        "web_search": {"enabled": False, "api_key": "", "provider": ""},
    }
    resp = await client.put("/config/ai", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    # 落库的是密文
    async with session_factory() as session:
        row = await session.get(UserAIConfig, TEST_USER_ID)
        assert row is not None
        assert row.chat_api_key != "sk-real-secret"
        assert "sk-real-secret" not in row.chat_api_key
    # GET 不回明文
    resp2 = await client.get("/config/ai", headers=auth_headers)
    data = resp2.json()["data"]
    assert data["chat"]["api_key_set"] is True
    assert data["chat"]["api_key_masked"]
    assert "sk-real-secret" not in data["chat"]["api_key_masked"]


async def test_put_requiring_encryption_secret(client, auth_headers, monkeypatch):
    from app.core.settings import settings
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    payload = {"chat": {"base_url": "http://x", "api_key": "k", "model": "m"},
               "embed": {"base_url": "", "api_key": "", "model": ""},
               "vision": {"base_url": "", "api_key": "", "model": ""},
               "rerank": {"base_url": "", "api_key": "", "model": ""},
               "web_search": {"enabled": False, "api_key": "", "provider": ""}}
    resp = await client.put("/config/ai", json=payload, headers=auth_headers)
    assert resp.status_code == 500
