import json
import re
from typing import Any

from pydantic import ValidationError

from app.core.settings import settings
from app.rag.agentic_rag.schemas import RetrievalPlan, RetrievalStep


FRESHNESS_TERMS = (
    "最新",
    "现在",
    "今天",
    "今年",
    "版本",
    "价格",
    "新闻",
    "latest",
    "current",
    "today",
    "price",
    "version",
    "news",
)

_ENTITY_PROMPTS = (
    "关系",
    "关联",
    "实体",
    "概念",
    "是什么",
    "有哪些相关",
    "who is",
    "what is",
    "relationship",
    "entity",
)

_CASUAL_GREETINGS = {
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "嗨",
    "哈喽",
}


def has_freshness_term(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in FRESHNESS_TERMS)


def _is_casual_greeting(query: str) -> bool:
    normalized = re.sub(r"[\s!！?？。,.，]+", "", query).lower()
    return normalized in _CASUAL_GREETINGS


def _is_entity_query(query: str) -> bool:
    """判断问题是否偏向实体/概念关系，命中则优先走知识图谱检索。"""
    lowered = query.lower()
    return any(term in lowered for term in _ENTITY_PROMPTS)


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("Planner JSON must be an object")
    return parsed


def _load_prompt() -> str:
    try:
        from app.utils.prompt_loader import load_prompt

        return load_prompt("agentic_rag_planner_prompt")
    except Exception:
        return (
            "Return only JSON for an Agentic RAG retrieval plan with keys: "
            "need_retrieval, steps, allow_web_fallback, reason. Query: {query}"
        )


def _create_default_chat_model():
    try:
        from app.utils.factory import create_chat_openai

        return create_chat_openai(
            model=settings.OPENAI_MODEL_NAME or "gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY or None,
            base_url=settings.OPENAI_BASE_URL or None,
            streaming=False,
            top_p=0.7,
        )
    except Exception:
        return None


def _resolve_shared_chat_model():
    """优先复用后台已预热的 chat_model（连接已建立），避免每消息重新实例化。

    未预热完成时回落自建兜底，保证规划不阻塞。
    """
    try:
        from app.core.background_init import init_manager
        if init_manager.chat_model is not None:
            return init_manager.chat_model
    except Exception:
        pass
    return _create_default_chat_model()


class AgenticRagPlanner:
    def __init__(self, chat_model=None):
        self.chat_model = chat_model if chat_model is not None else _resolve_shared_chat_model()
        self.prompt_template = _load_prompt()

    async def plan(self, query: str) -> RetrievalPlan:
        if self.chat_model is not None:
            try:
                prompt = self.prompt_template.replace("{query}", query)
                response = await self.chat_model.ainvoke(prompt)
                content = getattr(response, "content", response)
                if isinstance(content, str):
                    plan = RetrievalPlan.model_validate(_extract_json_object(content))
                    plan.metadata = {"source": "llm"}
                    return plan
            except Exception:
                pass

        plan = self._fallback_plan(query)
        plan.metadata = {"source": "fallback"}
        return plan

    def _fallback_plan(self, query: str) -> RetrievalPlan:
        if _is_casual_greeting(query):
            return RetrievalPlan(
                need_retrieval=False,
                steps=[],
                allow_web_fallback=False,
                reason="Casual greeting does not require retrieval.",
            )

        if _is_entity_query(query):
            return RetrievalPlan(
                need_retrieval=True,
                steps=[RetrievalStep(tool="search_graph", query=query),
                       RetrievalStep(tool="hybrid_search", query=query)],
                allow_web_fallback=has_freshness_term(query),
                reason="Entity-oriented query, prefer knowledge graph plus local retrieval.",
            )

        return RetrievalPlan(
            need_retrieval=True,
            steps=[RetrievalStep(tool="hybrid_search", query=query)],
            allow_web_fallback=has_freshness_term(query),
            reason="Deterministic fallback retrieval plan.",
        )
