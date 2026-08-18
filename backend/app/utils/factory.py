import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from langchain_community.chat_models.tongyi import ChatTongyi
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
    """
    视觉模型工厂 - 支持阿里云百炼和Ollama多模态模型。
    用于 PDF 多模态加载场景：将 PDF 页面渲染为图片，然后调用视觉模型进行图片理解，
    提取纯文本提取难以获取的图表、表格、流程图等视觉信息。

    之所以单独为一个视觉模型工厂而不是复用 ChatModelFactory，是因为：
    1. ChatModel 使用 streaming=True（流式输出），而视觉模型只能用 streaming=False
       （图片理解不适合流式）
    2. 视觉模型可能有独立的模型配置（如 VISION_OLLAMA_MODEL_NAME 区分于 OLLAMA_MODEL_NAME）
    3. 部分用户可能希望视觉模型使用更大的参数量或专门的多模态模型（如 qwen-vl 系列）
    """

    def generator(self) -> BaseChatModel | None:
        """根据VISION_MODEL_TYPE生成对应的视觉模型"""
        # 未设置 VISION_MODEL_TYPE 时，默认跟随 LLM_TYPE（保持向后兼容）
        vision_type = os.getenv("VISION_MODEL_TYPE", "").upper() or os.getenv("LLM_TYPE", "ALIYUN").upper()

        if vision_type == "OLLAMA":
            model_name = os.getenv("VISION_OLLAMA_MODEL_NAME") or os.getenv("OLLAMA_MODEL_NAME") or "qwen-vl:7b"
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

            logger.info(f"🎨 VisionModel 使用Ollama多模态模型: {model_name}, 地址: {base_url}")

            return ChatOllama(
                model=model_name,
                base_url=base_url,
                # 视觉模型禁用 streaming，因为图片理解需要在完整的上下文上做推理
                streaming=False,
                top_p=0.7,
            )

        elif vision_type == "ALIYUN":
            model_name = os.getenv("VISION_CHAT_MODEL_NAME") or os.getenv("CHAT_MODEL_NAME") or "qwen3-max"
            api_key = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            base_url = os.getenv("ALIYUN_BASE_URL")

            logger.info(f"🎨 VisionModel 使用阿里云百炼多模态模型: {model_name}")

            return ChatTongyi(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                streaming=False,
                top_p=0.7,
            )

        else:
            raise ValueError(f"不支持的VISION_MODEL_TYPE: {vision_type}，可选值: ALIYUN, OLLAMA")


class RerankerModelFactory(BaseModelFactory):
    """重排序模型工厂 - 已废弃，使用CrossEncoder模型"""
    def generator(self) -> Embeddings | BaseChatModel | None:
        """生成模型"""
        return None


chat_model = None
embed_model = None
reranker_model = None
vision_model = None
