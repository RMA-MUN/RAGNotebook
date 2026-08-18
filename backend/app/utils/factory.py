import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.logger_handler import logger

# 加载环境变量
load_dotenv()


def create_chat_openai(model: str, api_key: str | None, base_url: str | None,
                       streaming: bool = True, top_p: float = 0.7) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        streaming=streaming,
        top_p=top_p,
    )


def resolve_openai_config(
    model_env: str,
    base_url_env: str = "OPENAI_BASE_URL",
    api_key_env: str = "OPENAI_API_KEY",
    fallback_to_openai: bool = True,
    default_model: str | None = None,
) -> dict:
    """解析某个能力的 (model, api_key, base_url)，全部走 OpenAI 兼容协议。

    - base_url/api_key 优先取能力专属变量（如 VISION_BASE_URL / VISION_API_KEY），
      为空时回落 OPENAI_BASE_URL / OPENAI_API_KEY（若 fallback_to_openai=True）。
    - model 优先取能力专属变量（如 VISION_MODEL_NAME），为空时用 default_model。
    - 返回 {"model": str, "api_key": str | None, "base_url": str | None}
    """
    base_url = os.getenv(base_url_env) or (os.getenv("OPENAI_BASE_URL") if fallback_to_openai else None)
    api_key = os.getenv(api_key_env) or (os.getenv("OPENAI_API_KEY") if fallback_to_openai else None)
    model = os.getenv(model_env) or default_model
    return {"model": model, "api_key": api_key, "base_url": base_url}


class BaseModelFactory(ABC):
    """基础模型工厂"""

    @abstractmethod
    def generator(self) -> Embeddings | BaseChatModel | None:
        """生成模型"""
        pass


class ChatModelFactory(BaseModelFactory):
    """聊天模型工厂 - 统一 OpenAI 兼容协议"""

    def generator(self) -> Embeddings | BaseChatModel | None:
        """根据 OPENAI_* 环境变量生成聊天模型（统一 OpenAI 兼容协议）"""
        cfg = resolve_openai_config("OPENAI_MODEL_NAME", default_model="gpt-4o-mini")
        logger.info(f"📦 ChatModel 使用OpenAI兼容模型: {cfg['model']}")
        return create_chat_openai(
            model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            streaming=True, top_p=0.7,
        )


class EmbedModelFactory(BaseModelFactory):
    """嵌入模型工厂 - 统一 OpenAI 兼容 /v1/embeddings"""
    def generator(self) -> Embeddings | BaseChatModel | None:
        """根据 EMBED_* 环境变量生成嵌入模型（统一 OpenAI 兼容 /v1/embeddings）"""
        from langchain_openai import OpenAIEmbeddings
        cfg = resolve_openai_config(
            "EMBED_MODEL_NAME", "EMBED_BASE_URL", "EMBED_API_KEY",
            fallback_to_openai=True, default_model="text-embedding-v3",
        )
        logger.info(f"📦 EmbedModel 使用OpenAI兼容嵌入模型: {cfg['model']}")
        return OpenAIEmbeddings(model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"])


class VisionModelFactory(BaseModelFactory):
    """视觉模型工厂 - 可选模块，统一 OpenAI 兼容协议。

    VISION_ENABLED 三态：
    - 未设置（None）: 默认启用 OpenAI 兼容（VISION_* 空则回落 OPENAI_*）
    - "false"       : 彻底关闭，返回 None（PDF 走纯文本，无需任何视觉配置）
    - "true"        : 强制启用；缺少 VISION_BASE_URL/VISION_API_KEY 且无 OPENAI_* 回落时
                      告警并返回 None（fail-soft 降级）
    """

    def generator(self) -> BaseChatModel | None:
        vision_enabled = os.getenv("VISION_ENABLED")

        if vision_enabled is not None and vision_enabled.lower() == "false":
            logger.info("🎨 视觉模型未启用（VISION_ENABLED=false），PDF 走纯文本")
            return None

        # VISION_ENABLED=true 或未设置：统一 OpenAI 兼容（VISION_* 空则回落 OPENAI_*）
        cfg = resolve_openai_config(
            "VISION_MODEL_NAME", "VISION_BASE_URL", "VISION_API_KEY",
            fallback_to_openai=True, default_model="qwen-vl-max",
        )
        if vision_enabled is not None and vision_enabled.lower() == "true" and not (cfg["base_url"] and cfg["api_key"]):
            logger.warning("🎨 VISION_ENABLED=true 但缺少 VISION_BASE_URL/VISION_API_KEY（且无 OPENAI_* 回落），视觉已关闭（降级纯文本）")
            return None
        logger.info(f"🎨 VisionModel 使用OpenAI兼容多模态模型: {cfg['model']}")
        return create_chat_openai(
            model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            streaming=False, top_p=0.7,
        )


class RerankerModelFactory(BaseModelFactory):
    """重排序模型工厂 - 已废弃，使用CrossEncoder模型"""
    def generator(self) -> Embeddings | BaseChatModel | None:
        """生成模型"""
        return None


chat_model = None
embed_model = None
reranker_model = None
vision_model = None
