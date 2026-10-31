from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.logger_handler import logger
from app.core.settings import settings


def create_chat_openai(model: str, api_key: str | None, base_url: str | None,
                       streaming: bool = True, top_p: float = 0.7,
                       model_kwargs: dict | None = None,
                       extra_body: dict | None = None) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "streaming": streaming,
        "top_p": top_p,
        "model_kwargs": model_kwargs or {},
    }
    # extra_body 仅在 SDK 支持时透传（老版本 ChatOpenAI 无此参数则忽略）
    import inspect as _inspect

    if extra_body and "extra_body" in _inspect.signature(ChatOpenAI).parameters:
        kwargs["extra_body"] = extra_body
    return ChatOpenAI(**kwargs)


def create_planner_chat_openai() -> BaseChatModel | None:
    """规划专用小模型 client。

    关 thinking：规划只是吐小 JSON，
    思考链只会拖慢首 token；OpenAI 官方通道不带该参数（会 400）。
    配置缺失返回 None，调用方回落确定性计划。
    """
    try:
        cfg = resolve_planner_config()
        if not (cfg["base_url"] and cfg["api_key"]):
            return None
        host = (cfg["base_url"] or "").lower()
        extra = {"enable_thinking": False} if ("siliconflow" in host or "dashscope" in host) else None
        return create_chat_openai(
            model=cfg["model"],
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            streaming=False,
            top_p=0.7,
            extra_body=extra,
        )
    except Exception:
        return None


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
    base_url = getattr(settings, base_url_env, None) or None
    api_key = getattr(settings, api_key_env, None) or None
    if fallback_to_openai and not base_url and not api_key:
        base_url = settings.OPENAI_BASE_URL or None
        api_key = settings.OPENAI_API_KEY or None
    model = getattr(settings, model_env, None) or default_model
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


def resolve_planner_config() -> dict:
    """检索规划小模型的配置。

    候选通道按序尝试（每候选内部原子：url 与 key 必须成对，绝不跨供应商混搭）：
    1. PLANNER_*（独立配置，模型为空时用 Qwen/Qwen2.5-7B-Instruct）；
    2. EMBED_*（复用嵌入通道——同为硅基流动时 key 通用，零新增配置即可切小模型）；
    3. OPENAI_*（最终回落，与主回答同通道）。
    """
    planner_base = getattr(settings, "PLANNER_BASE_URL", None) or None
    planner_key = getattr(settings, "PLANNER_API_KEY", None) or None
    default_planner_model = (
        getattr(settings, "PLANNER_MODEL_NAME", None) or "Qwen/Qwen3-8B"
    )
    if planner_base and planner_key:
        return {
            "model": default_planner_model,
            "api_key": planner_key,
            "base_url": planner_base,
        }
    embed_base = getattr(settings, "EMBED_BASE_URL", None) or None
    embed_key = getattr(settings, "EMBED_API_KEY", None) or None
    if embed_base and embed_key:
        return {
            "model": default_planner_model,
            "api_key": embed_key,
            "base_url": embed_base,
        }
    cfg = _resolve_openai_config("OPENAI_MODEL_NAME", default_model="gpt-4o-mini")
    # OPENAI_* 回落时模型名仍优先用 PLANNER_MODEL_NAME（用户只想换规划模型、不换通道时）
    if getattr(settings, "PLANNER_MODEL_NAME", None):
        cfg["model"] = settings.PLANNER_MODEL_NAME
    return cfg


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
        cfg = resolve_embed_config()
        if not (cfg["base_url"] and cfg["api_key"]):
            logger.warning(
                "嵌入配置不完整（缺少 EMBED_BASE_URL/EMBED_API_KEY 且无完整 OPENAI_* 回落），"
                "嵌入已关闭（降级无向量能力）"
            )
            return None
        from langchain_openai import OpenAIEmbeddings
        logger.info(f"📦 EmbedModel 使用OpenAI兼容嵌入模型: {cfg['model']}")
        return OpenAIEmbeddings(
            model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            check_embedding_ctx_length=False,  # 发送原始字符串数组；token 数组输入部分供应商（如 DashScope 兼容模式）不支持
            chunk_size=10,                     # DashScope text-embedding-v3/v4 单次请求最多 10 条文本
            timeout=30,                        # 供应商挂起时快速失败，避免上传/检索请求无限期悬挂
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
        vision_enabled = settings.VISION_ENABLED

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
