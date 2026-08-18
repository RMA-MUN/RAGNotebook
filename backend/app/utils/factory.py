import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

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


def resolve_chat_config(custom_model: str | None = None) -> dict:
    """按 LLM_TYPE 解析出 (llm_type, model, api_key, base_url)。agent.py 与 factory 共用。

    返回 dict 含键: llm_type / model / api_key / base_url
    llm_type: "OLLAMA" | "ALIYUN" | "OPENAI_COMPAT"（OPENAI 视为 OPENAI_COMPAT 的别名）
    """
    llm_type = os.getenv("LLM_TYPE", "ALIYUN").upper()
    if llm_type == "OPENAI":
        llm_type = "OPENAI_COMPAT"
    if llm_type == "OPENAI_COMPAT":
        return {
            "llm_type": llm_type,
            "model": custom_model or os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "api_key": os.getenv("OPENAI_API_KEY"),
            "base_url": os.getenv("OPENAI_BASE_URL"),
        }
    if llm_type == "OLLAMA":
        return {
            "llm_type": llm_type,
            "model": custom_model or os.getenv("OLLAMA_MODEL_NAME", "qwen3:7b"),
            "api_key": None,
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        }
    # ALIYUN（默认，legacy）：ALIYUN_BASE_URL 本身就是 OpenAI 兼容(compatible-mode)地址
    return {
        "llm_type": "ALIYUN",
        "model": custom_model
                 or os.getenv("ALIYUN_MODEL_NAME")
                 or os.getenv("CHAT_MODEL_NAME", "qwen3-max"),
        "api_key": os.getenv("ALIYUN_ACCESS_KEY_SECRET"),
        "base_url": os.getenv("ALIYUN_BASE_URL"),
    }


class BaseModelFactory(ABC):
    """基础模型工厂"""

    @abstractmethod
    def generator(self) -> Embeddings | BaseChatModel | None:
        """生成模型"""
        pass


class ChatModelFactory(BaseModelFactory):
    """聊天模型工厂 - 支持Ollama和OpenAI兼容模型（含阿里云百炼 compatible-mode）"""

    def generator(self) -> Embeddings | BaseChatModel | None:
        """根据LLM_TYPE生成对应的聊天模型"""
        cfg = resolve_chat_config()
        if cfg["llm_type"] == "OLLAMA":
            logger.info(f"📦 ChatModel 使用Ollama模型: {cfg['model']}, 地址: {cfg['base_url']}")
            return ChatOllama(
                model=cfg["model"],
                base_url=cfg["base_url"],
                streaming=True,
                top_p=0.7,
            )
        logger.info(f"📦 ChatModel 使用OpenAI兼容模型: {cfg['model']}")
        return create_chat_openai(
            model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            streaming=True, top_p=0.7,
        )


class EmbedModelFactory(BaseModelFactory):
    """嵌入模型工厂 - 支持Ollama和OpenAI兼容模型"""
    def generator(self) -> Embeddings | BaseChatModel | None:
        """根据EMBED_MODEL_TYPE生成对应的嵌入模型"""
        embed_type = os.getenv("EMBED_MODEL_TYPE", "OLLAMA").upper()

        if embed_type == "OLLAMA":
            model_name = os.getenv("TEXT_EMBEDDING_MODEL_NAME", "qwen3-embedding:0.6b")
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            logger.info(f"📦 EmbedModel 使用Ollama嵌入模型: {model_name}, 地址: {base_url}")
            return OllamaEmbeddings(model=model_name, base_url=base_url)

        # OPENAI_COMPAT / OPENAI / ALIYUN(legacy)：统一走 OpenAI 兼容 /v1/embeddings
        from langchain_openai import OpenAIEmbeddings
        if embed_type in ("ALIYUN", "OPENAI", "OPENAI_COMPAT"):
            base_url = (os.getenv("EMBED_BASE_URL") if embed_type != "ALIYUN"
                        else os.getenv("ALIYUN_BASE_URL"))
            api_key = (os.getenv("EMBED_API_KEY") if embed_type != "ALIYUN"
                       else os.getenv("ALIYUN_ACCESS_KEY_SECRET"))
            model = (os.getenv("EMBED_MODEL_NAME") if embed_type != "ALIYUN"
                     else os.getenv("ALIYUN_EMBED_MODEL_NAME", "text-embedding-v3"))
            logger.info(f"📦 EmbedModel 使用OpenAI兼容嵌入模型: {model}, 地址: {base_url}")
            return OpenAIEmbeddings(model=model, api_key=api_key, base_url=base_url)

        raise ValueError(f"不支持的EMBED_MODEL_TYPE: {embed_type}，可选值: OLLAMA, OPENAI_COMPAT, ALIYUN")


class VisionModelFactory(BaseModelFactory):
    """视觉模型工厂 - 可选模块。

    VISION_ENABLED 三态：
    - 未设置（None）: 保持旧行为（跟随 VISION_MODEL_TYPE 或 LLM_TYPE）
    - "false"       : 彻底关闭，返回 None（PDF 走纯文本，无需任何视觉配置）
    - "true"        : 强制使用 VISION_MODEL_TYPE（OPENAI_COMPAT / OLLAMA），
                      配置缺失时告警并返回 None（fail-soft 降级）

    统一 OpenAI 兼容协议：OPENAI_COMPAT / legacy ALIYUN 分支均使用 ChatOpenAI
    （streaming=False，图片理解不适合流式），OLLAMA 分支保留 ChatOllama。
    """

    def generator(self) -> BaseChatModel | None:
        vision_enabled = os.getenv("VISION_ENABLED")

        if vision_enabled is not None and vision_enabled.lower() == "false":
            logger.info("🎨 视觉模型未启用（VISION_ENABLED=false），PDF 走纯文本")
            return None

        if vision_enabled is not None and vision_enabled.lower() == "true":
            vtype = os.getenv("VISION_MODEL_TYPE", "").upper()
            if vtype == "OLLAMA":
                model_name = os.getenv("VISION_OLLAMA_MODEL_NAME", "qwen-vl:7b")
                base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
                logger.info(f"🎨 VisionModel 使用Ollama多模态模型: {model_name}, 地址: {base_url}")
                return ChatOllama(
                    model=model_name,
                    base_url=base_url,
                    streaming=False,
                    top_p=0.7,
                )
            base_url = os.getenv("VISION_BASE_URL") or os.getenv("ALIYUN_BASE_URL")
            api_key = os.getenv("VISION_API_KEY") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            model = (os.getenv("VISION_MODEL_NAME")
                     or os.getenv("VISION_CHAT_MODEL_NAME")
                     or os.getenv("CHAT_MODEL_NAME") or "qwen-vl-max")
            if not (base_url and api_key):
                logger.warning("🎨 VISION_ENABLED=true 但缺少 VISION_BASE_URL/VISION_API_KEY，视觉已关闭（降级纯文本）")
                return None
            logger.info(f"🎨 VisionModel 使用OpenAI兼容多模态模型: {model}")
            return create_chat_openai(
                model=model, api_key=api_key, base_url=base_url,
                streaming=False, top_p=0.7,
            )

        # 未设置 VISION_ENABLED：保持旧行为（跟随 VISION_MODEL_TYPE 或 LLM_TYPE）
        vtype = os.getenv("VISION_MODEL_TYPE", "").upper() or os.getenv("LLM_TYPE", "ALIYUN").upper()
        if vtype in ("OPENAI", "OPENAI_COMPAT", "ALIYUN"):
            base_url = os.getenv("VISION_BASE_URL") or os.getenv("ALIYUN_BASE_URL")
            api_key = os.getenv("VISION_API_KEY") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            model = (os.getenv("VISION_MODEL_NAME")
                     or os.getenv("VISION_CHAT_MODEL_NAME")
                     or os.getenv("CHAT_MODEL_NAME") or "qwen-vl-max")
            logger.info(f"🎨 VisionModel 使用OpenAI兼容多模态模型: {model}")
            return create_chat_openai(
                model=model, api_key=api_key, base_url=base_url,
                streaming=False, top_p=0.7,
            )
        # OLLAMA
        model_name = (os.getenv("VISION_OLLAMA_MODEL_NAME")
                      or os.getenv("OLLAMA_MODEL_NAME") or "qwen-vl:7b")
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        logger.info(f"🎨 VisionModel 使用Ollama多模态模型: {model_name}, 地址: {base_url}")
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            streaming=False,
            top_p=0.7,
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
