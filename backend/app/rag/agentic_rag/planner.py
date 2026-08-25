import json
import os
import re
from typing import Any

from pydantic import ValidationError

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
            model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            streaming=False,
            top_p=0.7,
        )
    except Exception:
        return None


class AgenticRagPlanner:
    def __init__(self, chat_model=None):
        self.chat_model = chat_model if chat_model is not None else _create_default_chat_model()
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

        return RetrievalPlan(
            need_retrieval=True,
            steps=[RetrievalStep(tool="hybrid_search", query=query)],
            allow_web_fallback=has_freshness_term(query),
            reason="Deterministic fallback retrieval plan.",
        )
