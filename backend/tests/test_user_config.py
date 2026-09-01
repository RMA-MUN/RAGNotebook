import pytest

from app.models.user_ai_config import UserAIConfig
from app.utils.user_config import (
    USER_API_KEY_PLACEHOLDER,
    create_chat_model_for_user,
    get_user_ai_config,
    resolve_chat_config_for_user,
)
from tests.conftest import patch_session_factory


async def test_resolve_falls_back_to_env_when_unconfigured(monkeypatch, session_factory):
    patch_session_factory(monkeypatch, session_factory)
    from app.core.settings import settings
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-env", raising=False)
    monkeypatch.setattr(settings, "OPENAI_MODEL_NAME", "gpt-4o", raising=False)
    from app.utils.user_config import _CACHE
    _CACHE.clear()
    cfg = await resolve_chat_config_for_user("user-no-config")
    assert cfg["base_url"] == "https://api.openai.com/v1"
    assert cfg["api_key"] == "sk-env"
    assert cfg["model"] == "gpt-4o"


async def test_resolve_uses_user_config_and_placeholder(monkeypatch, session_factory):
    patch_session_factory(monkeypatch, session_factory)
    async with session_factory() as session:
        session.add(UserAIConfig(user_id="u-1", chat_base_url="http://localhost:11434/v1", chat_model="qwen3:8b"))
        await session.commit()
    from app.utils.user_config import _CACHE
    _CACHE.clear()
    cfg = await resolve_chat_config_for_user("u-1")
    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["api_key"] == USER_API_KEY_PLACEHOLDER
    assert cfg["model"] == "qwen3:8b"
