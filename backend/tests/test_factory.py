"""Unit tests for app.utils.factory config resolution and factory.factories.

Only pure env-resolution functions and the no-network generator() branches
are tested.  ChatModelFactory/EmbedModelFactory.generator() are NOT called
because they construct real model clients.
"""
import pytest

from app.utils.factory import (
    EmbedModelFactory,
    RerankerModelFactory,
    VisionModelFactory,
    _resolve_openai_config,
    resolve_chat_config,
    resolve_embed_config,
    resolve_vision_config,
)

# every capability env var that can affect resolution
ALL_KEYS = [
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_NAME",
    "VISION_BASE_URL",
    "VISION_API_KEY",
    "VISION_MODEL_NAME",
    "VISION_ENABLED",
    "EMBED_BASE_URL",
    "EMBED_API_KEY",
    "EMBED_MODEL_NAME",
]


def _clear_env(monkeypatch):
    for key in ALL_KEYS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# _resolve_openai_config (core atomic-fallback logic)
# ---------------------------------------------------------------------------
def test_resolve_all_unset_returns_nones(monkeypatch):
    _clear_env(monkeypatch)
    cfg = _resolve_openai_config("SOME_MODEL")
    assert cfg == {"model": None, "api_key": None, "base_url": None}


def test_resolve_model_falls_back_to_default(monkeypatch):
    _clear_env(monkeypatch)
    cfg = _resolve_openai_config("SOME_MODEL", default_model="default-model")
    assert cfg["model"] == "default-model"


def test_resolve_uses_capability_model_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SOME_MODEL", "capability-model")
    cfg = _resolve_openai_config("SOME_MODEL", default_model="default-model")
    assert cfg["model"] == "capability-model"


def test_resolve_fallback_disabled_never_falls_back(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = _resolve_openai_config(
        "EMBED_MODEL_NAME",
        "EMBED_BASE_URL",
        "EMBED_API_KEY",
        fallback_to_openai=False,
        default_model="m",
    )
    assert cfg == {"model": "m", "api_key": None, "base_url": None}


# ---------------------------------------------------------------------------
# resolve_chat_config
# ---------------------------------------------------------------------------
def test_chat_config_defaults(monkeypatch):
    _clear_env(monkeypatch)
    cfg = resolve_chat_config()
    assert cfg == {"model": "gpt-4o-mini", "api_key": None, "base_url": None}


def test_chat_config_uses_openai_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-chat")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "deepseek-chat")
    cfg = resolve_chat_config()
    assert cfg == {
        "model": "deepseek-chat",
        "api_key": "sk-chat",
        "base_url": "https://api.example.com/v1",
    }


# ---------------------------------------------------------------------------
# resolve_vision_config
# ---------------------------------------------------------------------------
def test_vision_config_default_model(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = resolve_vision_config()
    # atomic fallback: both unset -> whole OPENAI_* pair used, model default qwen-vl-max
    assert cfg == {
        "model": "qwen-vl-max",
        "api_key": "sk-openai",
        "base_url": "https://openai.example",
    }


def test_vision_config_atomic_fallback_no_partial_mixing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example")
    cfg = resolve_vision_config()
    # 原子回落:只有 base_url 而没有 api_key -> 绝不混搭 OPENAI_API_KEY
    assert cfg["base_url"] == "https://vision.example"
    assert cfg["api_key"] is None
    assert cfg["model"] == "qwen-vl-max"


def test_vision_config_full_own_credentials(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example")
    monkeypatch.setenv("VISION_API_KEY", "sk-vision")
    monkeypatch.setenv("VISION_MODEL_NAME", "qwen-vl-plus")
    cfg = resolve_vision_config()
    assert cfg == {
        "model": "qwen-vl-plus",
        "api_key": "sk-vision",
        "base_url": "https://vision.example",
    }


def test_vision_config_key_only_no_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("VISION_API_KEY", "sk-vision")
    cfg = resolve_vision_config()
    assert cfg["api_key"] == "sk-vision"
    assert cfg["base_url"] is None  # 不回落到 OPENAI_BASE_URL
    assert cfg["model"] == "qwen-vl-max"


# ---------------------------------------------------------------------------
# resolve_embed_config
# ---------------------------------------------------------------------------
def test_embed_config_default_model(monkeypatch):
    _clear_env(monkeypatch)
    cfg = resolve_embed_config()
    assert cfg["model"] == "text-embedding-v3"
    assert cfg["api_key"] is None
    assert cfg["base_url"] is None


def test_embed_config_atomic_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = resolve_embed_config()
    assert cfg == {
        "model": "text-embedding-v3",
        "api_key": "sk-openai",
        "base_url": "https://openai.example",
    }


def test_embed_config_no_partial_mixing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("EMBED_BASE_URL", "http://localhost:11434/v1")
    cfg = resolve_embed_config()
    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["api_key"] is None


def test_embed_config_full_own_credentials(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("EMBED_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("EMBED_API_KEY", "ollama")
    monkeypatch.setenv("EMBED_MODEL_NAME", "bge-m3")
    cfg = resolve_embed_config()
    assert cfg == {
        "model": "bge-m3",
        "api_key": "ollama",
        "base_url": "http://localhost:11434/v1",
    }


# ---------------------------------------------------------------------------
# factories (network-free branches only)
# ---------------------------------------------------------------------------
def test_vision_factory_disabled_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VISION_ENABLED", "false")
    assert VisionModelFactory().generator() is None


def test_vision_factory_fail_soft_without_config(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VISION_ENABLED", "true")
    assert VisionModelFactory().generator() is None


def test_vision_factory_default_when_unset_and_no_config(monkeypatch):
    _clear_env(monkeypatch)
    assert VisionModelFactory().generator() is None


def test_reranker_factory_always_returns_none():
    assert RerankerModelFactory().generator() is None


# ---------------------------------------------------------------------------
# EmbedModelFactory.generator()（构造 OpenAIEmbeddings 不发起网络请求）
# ---------------------------------------------------------------------------
def test_embed_factory_sends_raw_strings_for_dashscope(monkeypatch):
    """DashScope 兼容模式不支持 token 数组输入，必须发送原始字符串数组"""
    _clear_env(monkeypatch)
    monkeypatch.setenv("EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("EMBED_API_KEY", "sk-embed")
    monkeypatch.setenv("EMBED_MODEL_NAME", "text-embedding-v3")
    embed = EmbedModelFactory().generator()
    assert embed.model == "text-embedding-v3"
    assert embed.check_embedding_ctx_length is False
    assert embed.chunk_size == 10


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
