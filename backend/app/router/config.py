"""AI 配置端点：按用户读写 /config/ai。
约定：
- GET 只回打码（api_key_masked），绝不回明文；用 api_key_set 标记是否已配置。
- PUT upsert；api_key 为空=清空；非空则 encrypt_secret 后落库；成功后 invalidate_user_config。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.core.logger_handler import logger
from app.core.success_response import success_response
from app.db.db_config import AsyncSessionLocal
from app.models.user_ai_config import UserAIConfig
from app.schemas.ai_config_schemas import AIConfigIn, AIConfigOut, CapabilityOut, WebSearchOut
from app.utils.auth_utils import get_current_user_id
from app.utils.encryption import decrypt_secret, encrypt_secret
from app.utils.user_config import invalidate_user_config

config_router = APIRouter(tags=["config"], prefix="/config")


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return "****" + key[-4:]


def _capability_out(base_url, model, api_key_cipher) -> CapabilityOut:
    return CapabilityOut(
        base_url=base_url or "",
        model=model or "",
        api_key_set=bool(api_key_cipher),
        api_key_masked=_mask(decrypt_secret(api_key_cipher)),
    )


def _apply_key(value, existing_cipher):
    """按字段语义应用 api_key：字段缺失(None)保留现有密文；显式空串则清空。"""
    if value is None:
        return existing_cipher
    if value == "":
        return None
    return encrypt_secret(value) or None


@config_router.get("/ai")
async def get_ai_config(user_id: str = Depends(get_current_user_id)):
    async with AsyncSessionLocal() as session:
        row = await session.get(UserAIConfig, user_id)
    if row is None:
        empty_cap = CapabilityOut()
        return success_response(data=AIConfigOut(
            chat=empty_cap, embed=empty_cap, vision=empty_cap, rerank=empty_cap,
            web_search=WebSearchOut(), updated_at=None,
        ).model_dump())
    return success_response(data=AIConfigOut(
        chat=_capability_out(row.chat_base_url, row.chat_model, row.chat_api_key),
        embed=_capability_out(row.embed_base_url, row.embed_model, row.embed_api_key),
        vision=_capability_out(row.vision_base_url, row.vision_model, row.vision_api_key),
        rerank=_capability_out(row.rerank_base_url, row.rerank_model, row.rerank_api_key),
        web_search=WebSearchOut(
            enabled=bool(row.web_search_enabled),
            provider=row.web_search_provider or "",
            api_key_set=bool(row.web_search_api_key),
            api_key_masked=_mask(decrypt_secret(row.web_search_api_key)),
        ),
        updated_at=row.updated_at,
    ).model_dump())


@config_router.put("/ai")
async def put_ai_config(req: AIConfigIn, user_id: str = Depends(get_current_user_id)):
    async with AsyncSessionLocal() as session:
        row = await session.get(UserAIConfig, user_id)
        if row is None:
            row = UserAIConfig(user_id=user_id)
            session.add(row)
        row.chat_base_url = req.chat.base_url or None
        row.chat_api_key = _apply_key(req.chat.api_key, row.chat_api_key)
        row.chat_model = req.chat.model or None
        row.embed_base_url = req.embed.base_url or None
        row.embed_api_key = _apply_key(req.embed.api_key, row.embed_api_key)
        row.embed_model = req.embed.model or None
        row.vision_base_url = req.vision.base_url or None
        row.vision_api_key = _apply_key(req.vision.api_key, row.vision_api_key)
        row.vision_model = req.vision.model or None
        row.rerank_base_url = req.rerank.base_url or None
        row.rerank_api_key = _apply_key(req.rerank.api_key, row.rerank_api_key)
        row.rerank_model = req.rerank.model or None
        row.web_search_enabled = req.web_search.enabled
        row.web_search_api_key = _apply_key(req.web_search.api_key, row.web_search_api_key)
        row.web_search_provider = req.web_search.provider or None
        await session.commit()
        await session.refresh(row)
    invalidate_user_config(user_id)
    logger.info(f"用户 {user_id} 已更新 AI 配置")
    return success_response(message="AI 配置已保存")
