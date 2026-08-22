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


def _resolve_openai_config(
    model_env: str,
    base_url_env: str = "OPENAI_BASE_URL",
    api_key_env: str = "OPENAI_API_KEY",
    fallback_to_openai: bool = True,
    default_model: str | None = None,
) -> dict:
    """解析某个能力的 (model, api_key, base_url)，全部走 OpenAI 兼容协议。

    - 每个能力可独立配置自己的 base_url / api_key / model（支持跨平台混搭，
      如 对话=DeepSeek、视觉=百炼、嵌入=Ollama/OpenRouter）。
    - 回落是「原子」的：仅当该能力的 base_url 与 api_key **两者都未设置**时，
      才整体回落 OPENAI_BASE_URL / OPENAI_API_KEY——绝不把不同平台的 url 与 key 混搭，
      避免部分配置时静默使用错误供应商的凭据。
    - model 取能力专属变量（如 VISION_MODEL_NAME），为空时用 default_model。
    - 返回 {"model": str, "api_key": str | None, "base_url": str | None}

    内部通用实现——各能力请通过 resolve_chat_config() / resolve_vision_config() /
    resolve_embed_config() 调用，保证每个能力只读自己那组环境变量。
    """
    base_url = os.getenv(base_url_env)
    api_key = os.getenv(api_key_env)
    if fallback_to_openai and not base_url and not api_key:
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv(model_env) or default_model
    return {"model": model, "api_key": api_key, "base_url": base_url}


def resolve_chat_config() -> dict:
    """对话模型的配置（只读 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL_NAME）"""
    return _resolve_openai_config("OPENAI_MODEL_NAME", default_model="gpt-4o-mini")


def resolve_vision_config() -> dict:
    """视觉模型的配置（读 VISION_*；仅当 url 与 key 都为空时整体回落 OPENAI_*）"""
    return _resolve_openai_config(
        "VISION_MODEL_NAME", "VISION_BASE_URL", "VISION_API_KEY",
        fallback_to_openai=True, default_model="qwen-vl-max",
    )


def resolve_embed_config() -> dict:
    """嵌入模型的配置（读 EMBED_*；仅当 url 与 key 都为空时整体回落 OPENAI_*）"""
    return _resolve_openai_config(
        "EMBED_MODEL_NAME", "EMBED_BASE_URL", "EMBED_API_KEY",
        fallback_to_openai=True, default_model="text-embedding-v3",
    )


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
        cfg = resolve_chat_config()
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
        cfg = resolve_embed_config()
        if not (cfg["base_url"] and cfg["api_key"]):
            raise ValueError(
                "嵌入模型配置不完整：请同时提供 EMBED_BASE_URL 与 EMBED_API_KEY；"
                "或二者都留空以整体回落 OPENAI_BASE_URL/OPENAI_API_KEY。"
                "避免跨供应商混用凭据（如只配了 EMBED_BASE_URL 却用对话的 OPENAI_API_KEY）。"
            )
        logger.info(f"📦 EmbedModel 使用OpenAI兼容嵌入模型: {cfg['model']}")
        return OpenAIEmbeddings(
            model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            check_embedding_ctx_length=False,  # 发送原始字符串数组；token 数组输入部分供应商（如 DashScope 兼容模式）不支持
            chunk_size=10,                     # DashScope text-embedding-v3/v4 单次请求最多 10 条文本
        )


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

        # VISION_ENABLED=true 或未设置：统一 OpenAI 兼容（VISION_* 全空时原子回落 OPENAI_*）
        cfg = resolve_vision_config()
        if not (cfg["base_url"] and cfg["api_key"]):
            logger.warning("🎨 视觉配置不完整（缺少 VISION_BASE_URL/VISION_API_KEY 且无完整 OPENAI_* 回落），视觉已关闭（降级纯文本）")
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
