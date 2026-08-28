"""从用户问句抽取图谱实体候选词：优先 LLM 解析，失败回落规则分词。

图谱实体匹配依赖「实体名」，而 RAG 传入的是整句用户问题，直接对整句做
search_entities 的 %...% 模糊匹配几乎命中不了（如「DeepSeek 是什么」匹配不到
「DeepSeek」）。本模块先用 LLM 从问句抽实体候选词，再用规则兜底。
"""
import json
import os
import re
from typing import Any

from app.core.logger_handler import logger

_JSON_ARRAY_RE = re.compile(r"\[.*\]", flags=re.DOTALL)

# 问句常见噪声词：这些词不该被当作实体名，规则兜底时剔除
_NOISE = {
    "是什么", "有哪些", "相关", "关系", "什么", "怎么", "如何", "为什么", "区别",
    "关于", "请问", "介绍", "讲下", "介绍下", "介绍介绍", "介绍一下", "讲一下",
    "讲一讲", "说说", "说一下", "科普一下", "who", "what", "is", "are",
    "which", "的", "和", "与", "或", "呢", "吗", "啊",
}


def _extract_json_array(raw: str) -> list[str]:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped).strip()
    match = _JSON_ARRAY_RE.search(stripped)
    if not match:
        raise ValueError("未找到 JSON 数组")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("实体候选必须是字符串数组")
    return [x for x in parsed if isinstance(x, str) and x.strip()]


def _fallback_candidates(query: str) -> list[str]:
    """无 LLM 时用规则拆词：按常见分隔符拆分后，剔噪声、去重、截断。"""
    parts = re.split(r"[\s,，。！？!?、;；:：/（）()]+", query)
    candidates: list[str] = []
    for part in parts:
        word = part.strip()
        if not word or word.lower() in _NOISE:
            continue
        if word.lower() == word and len(word) > 20:
            continue
        if word not in candidates:
            candidates.append(word)
    return candidates[:5]


class QueryEntityExtractor:
    def __init__(self, chat_model: Any | None = None, prompt_template: str | None = None):
        self.chat_model = chat_model
        self.prompt_template = prompt_template or self._load_prompt()

    @staticmethod
    def _load_prompt() -> str:
        try:
            from app.utils.prompt_loader import load_prompt
            return load_prompt("query_entity_extraction_prompt")
        except Exception:
            return "Return only a JSON array of entity names from the question. Question: {query}"

    @classmethod
    def _create_default_chat_model(cls):
        from app.utils.factory import create_chat_openai
        return create_chat_openai(
            model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            streaming=False,
            top_p=0.7,
        )

    async def extract(self, query: str) -> list[str]:
        candidates: list[str] = []
        model = self._resolve_chat_model()
        if model is None:
            candidates = _fallback_candidates(query)
            return candidates
        try:
            from langchain_core.messages import HumanMessage
            prompt = self.prompt_template.replace("{query}", query)
            response = await model.ainvoke(prompt)
            content = getattr(response, "content", response)
            if isinstance(content, str):
                candidates = _extract_json_array(content)
        except Exception as e:
            logger.warning(f"LLM 查询实体抽取失败，回落规则: {query}: {e}")
            candidates = []

        if not candidates:
            candidates = _fallback_candidates(query)
        return candidates

    def _resolve_chat_model(self):
        """优先复用后台已预热的 chat_model，未就绪时回落自建兜底。"""
        if self.chat_model is not None:
            return self.chat_model
        try:
            from app.core.background_init import init_manager
            if init_manager.chat_model is not None:
                return init_manager.chat_model
        except Exception:
            pass
        return self._create_default_chat_model()
