"""请求级用户模型配置解析：按用户取配置建模型，未配置回落全局 .env。

规则（全局约定，勿与 factory 原子回落冲突）：
- 该能力用户填了 base_url → 视为已配置：api_key=用户值或占位符，model=用户值或默认。
- 未配置 → 回落 app.utils.factory 的全局解析（原子回落）。
"""
import time

from sqlalchemy import select

from app.core.settings import settings
from app.db.db_config import AsyncSessionLocal
from app.models.user_ai_config import UserAIConfig
from app.utils.encryption import decrypt_secret
from app.utils.factory import create_chat_openai, resolve_embed_config, resolve_vision_config

USER_API_KEY_PLACEHOLDER = "ollama"

_CACHE_TTL_SECONDS = 30.0
_CACHE: dict[str, tuple[float, UserAIConfig | None]] = {}

_DEFAULTS = {"chat": "gpt-4o-mini", "embed": "text-embedding-v3", "vision": "qwen-vl-max"}


async def get_user_ai_config(user_id: str) -> UserAIConfig | None:
    now = time.time()
    hit = _CACHE.get(user_id)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserAIConfig).where(UserAIConfig.user_id == user_id))
        row = result.scalar_one_or_none()
    _CACHE[user_id] = (now, row)
    return row


def invalidate_user_config(user_id: str) -> None:
    _CACHE.pop(user_id, None)


async def resolve_chat_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.chat_base_url:
        return {
            "model": row.chat_model or _DEFAULTS["chat"],
            "base_url": row.chat_base_url,
            "api_key": decrypt_secret(row.chat_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    from app.utils.factory import resolve_chat_config

    return resolve_chat_config()


async def resolve_embed_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.embed_base_url:
        return {
            "model": row.embed_model or _DEFAULTS["embed"],
            "base_url": row.embed_base_url,
            "api_key": decrypt_secret(row.embed_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    return resolve_embed_config()


async def resolve_vision_config_for_user(user_id: str) -> dict:
    row = await get_user_ai_config(user_id)
    if row is not None and row.vision_base_url:
        return {
            "model": row.vision_model or _DEFAULTS["vision"],
            "base_url": row.vision_base_url,
            "api_key": decrypt_secret(row.vision_api_key) or USER_API_KEY_PLACEHOLDER,
        }
    return resolve_vision_config()


async def create_chat_model_for_user(user_id: str, streaming: bool = True):
    cfg = await resolve_chat_config_for_user(user_id)
    return create_chat_openai(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        streaming=streaming, top_p=0.7,
    )


async def create_embed_model_for_user(user_id: str):
    from langchain_openai import OpenAIEmbeddings

    cfg = await resolve_embed_config_for_user(user_id)
    if not (cfg["base_url"] and cfg["api_key"]):
        raise ValueError("嵌入模型配置不完整：请同时提供 base_url 与 api_key，或留空以回落 OPENAI_*")
    return OpenAIEmbeddings(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        check_embedding_ctx_length=False, chunk_size=10, timeout=30,
    )


async def create_vision_model_for_user(user_id: str):
    cfg = await resolve_vision_config_for_user(user_id)
    if not (cfg["base_url"] and cfg["api_key"]):
        return None
    return create_chat_openai(
        model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
        streaming=False, top_p=0.7,
    )
