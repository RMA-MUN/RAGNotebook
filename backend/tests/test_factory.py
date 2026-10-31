"""Unit tests for app.utils.factory config resolution and factory.factories.

Only pure env-resolution functions and the no-network generator() branches
are tested.  ChatModelFactory/EmbedModelFactory.generator() are NOT called
because they construct real model clients.
"""
import pytest

from app.core.settings import settings
from app.utils.factory import (
    EmbedModelFactory,
    RerankerModelFactory,
    VisionModelFactory,
    _resolve_openai_config,
    create_planner_chat_openai,
    resolve_chat_config,
    resolve_embed_config,
    resolve_planner_config,
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
    "PLANNER_BASE_URL",
    "PLANNER_API_KEY",
    "PLANNER_MODEL_NAME",
]


def _clear_env(monkeypatch):
    """把 settings 中会影响解析的配置属性清空（配置源已收敛到 Settings，
    测试直接 patch 属性而非环境变量）。"""
    for key in [*ALL_KEYS, "CHAT_API_KEY"]:
        monkeypatch.setattr(settings, key, None, raising=False)


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
    monkeypatch.setattr(settings, "SOME_MODEL", "capability-model", raising=False)
    cfg = _resolve_openai_config("SOME_MODEL", default_model="default-model")
    assert cfg["model"] == "capability-model"


def test_resolve_fallback_disabled_never_falls_back(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
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
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-chat")
    monkeypatch.setattr(settings, "OPENAI_MODEL_NAME", "deepseek-chat")
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
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    cfg = resolve_vision_config()
    # atomic fallback: both unset -> whole OPENAI_* pair used, model default qwen-vl-max
    assert cfg == {
        "model": "qwen-vl-max",
        "api_key": "sk-openai",
        "base_url": "https://openai.example",
    }


def test_vision_config_atomic_fallback_no_partial_mixing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example")
    cfg = resolve_vision_config()
    # 原子回落:只有 base_url 而没有 api_key -> 绝不混搭 OPENAI_API_KEY
    assert cfg["base_url"] == "https://vision.example"
    assert cfg["api_key"] is None
    assert cfg["model"] == "qwen-vl-max"


def test_vision_config_full_own_credentials(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://vision.example")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk-vision")
    monkeypatch.setattr(settings, "VISION_MODEL_NAME", "qwen-vl-plus")
    cfg = resolve_vision_config()
    assert cfg == {
        "model": "qwen-vl-plus",
        "api_key": "sk-vision",
        "base_url": "https://vision.example",
    }


def test_vision_config_key_only_no_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "VISION_API_KEY", "sk-vision")
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
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    cfg = resolve_embed_config()
    assert cfg == {
        "model": "text-embedding-v3",
        "api_key": "sk-openai",
        "base_url": "https://openai.example",
    }


def test_embed_config_no_partial_mixing(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "http://localhost:11434/v1")
    cfg = resolve_embed_config()
    assert cfg["base_url"] == "http://localhost:11434/v1"
    assert cfg["api_key"] is None


def test_embed_config_full_own_credentials(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "EMBED_API_KEY", "ollama")
    monkeypatch.setattr(settings, "EMBED_MODEL_NAME", "bge-m3")
    cfg = resolve_embed_config()
    assert cfg == {
        "model": "bge-m3",
        "api_key": "ollama",
        "base_url": "http://localhost:11434/v1",
    }


# ---------------------------------------------------------------------------
# resolve_planner_config（规划小模型：PLANNER_* → 复用 EMBED 通道 → OPENAI_*）
# ---------------------------------------------------------------------------
def test_planner_config_reuses_embed_channel_by_default(monkeypatch):
    """不配 PLANNER_* 时自动复用 EMBED 硅基流动通道，零新增配置切小模型。"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "EMBED_API_KEY", "sk-embed")
    cfg = resolve_planner_config()
    assert cfg == {
        "model": "Qwen/Qwen3-8B",
        "api_key": "sk-embed",
        "base_url": "https://api.siliconflow.cn/v1",
    }


def test_planner_config_own_trio_wins(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "EMBED_API_KEY", "sk-embed")
    monkeypatch.setattr(settings, "PLANNER_BASE_URL", "https://planner.example/v1")
    monkeypatch.setattr(settings, "PLANNER_API_KEY", "sk-planner")
    monkeypatch.setattr(settings, "PLANNER_MODEL_NAME", "tiny-model")
    cfg = resolve_planner_config()
    assert cfg == {
        "model": "tiny-model",
        "api_key": "sk-planner",
        "base_url": "https://planner.example/v1",
    }


def test_planner_config_falls_back_to_openai(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    cfg = resolve_planner_config()
    assert cfg["base_url"] == "https://openai.example"
    assert cfg["api_key"] == "sk-openai"


def test_planner_config_partial_planner_never_mixes_vendors(monkeypatch):
    """只配 PLANNER_BASE_URL 不配 key：绝不混搭他家 key，退回 EMBED/OPENAI 完整通道。"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://openai.example")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(settings, "PLANNER_BASE_URL", "https://planner.example/v1")
    cfg = resolve_planner_config()
    assert cfg["base_url"] == "https://openai.example"
    assert cfg["api_key"] == "sk-openai"


# ---------------------------------------------------------------------------
# create_planner_chat_openai（Qwen 系通道关 thinking，OpenAI 官方不带）
# ---------------------------------------------------------------------------
def test_planner_client_disables_thinking_on_siliconflow(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(settings, "EMBED_API_KEY", "sk-embed")
    client = create_planner_chat_openai()
    assert client is not None
    assert getattr(client, "extra_body", None) == {"enable_thinking": False}


def test_planner_client_no_extra_params_on_openai(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-openai")
    client = create_planner_chat_openai()
    assert client is not None
    assert getattr(client, "extra_body", None) in (None, {})


def test_planner_client_none_without_config(monkeypatch):
    _clear_env(monkeypatch)
    assert create_planner_chat_openai() is None


# ---------------------------------------------------------------------------
# factories (network-free branches only)
# ---------------------------------------------------------------------------
def test_vision_factory_disabled_returns_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "VISION_ENABLED", "false")
    assert VisionModelFactory().generator() is None


def test_vision_factory_fail_soft_without_config(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "VISION_ENABLED", "true")
    assert VisionModelFactory().generator() is None


def test_vision_factory_default_when_unset_and_no_config(monkeypatch):
    _clear_env(monkeypatch)
    assert VisionModelFactory().generator() is None


def test_embed_factory_fail_soft_without_config(monkeypatch, caplog):
    _clear_env(monkeypatch)

    assert EmbedModelFactory().generator() is None
    assert "嵌入配置不完整" in caplog.text


@pytest.mark.parametrize(
    ("embed_base_url", "embed_api_key"),
    [
        ("https://embed.example/v1", None),
        (None, "sk-embed"),
    ],
    ids=["base-url-only", "api-key-only"],
)
def test_embed_factory_fail_soft_with_partial_config(
    monkeypatch, caplog, embed_base_url, embed_api_key,
):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "EMBED_BASE_URL", embed_base_url)
    monkeypatch.setattr(settings, "EMBED_API_KEY", embed_api_key)

    assert EmbedModelFactory().generator() is None
    assert "嵌入配置不完整" in caplog.text


def test_reranker_factory_always_returns_none():
    assert RerankerModelFactory().generator() is None


# ---------------------------------------------------------------------------
# EmbedModelFactory.generator()（构造 OpenAIEmbeddings 不发起网络请求）
# ---------------------------------------------------------------------------
def test_embed_factory_sends_raw_strings_for_dashscope(monkeypatch):
    """DashScope 兼容模式不支持 token 数组输入，必须发送原始字符串数组"""
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "EMBED_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "EMBED_API_KEY", "sk-embed")
    monkeypatch.setattr(settings, "EMBED_MODEL_NAME", "text-embedding-v3")
    embed = EmbedModelFactory().generator()
    assert embed.model == "text-embedding-v3"
    assert embed.check_embedding_ctx_length is False
    assert embed.chunk_size == 10


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
