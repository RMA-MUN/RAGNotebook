import asyncio
import json
import re
from typing import Any

from app.core.logger_handler import logger
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
    """规划专用小模型（PLANNER_* → 复用 EMBED 通道 → 回落 OPENAI_*，Qwen 系自动关 thinking）。"""
    try:
        from app.utils.factory import create_planner_chat_openai

        return create_planner_chat_openai()
    except Exception:
        return None


# 规划小模型单例：避免每请求新建 client（丢失 HTTP keep-alive）。
_planner_model_cache: Any = None


def _resolve_planner_chat_model():
    """规划模型解析：显式注入 > 后台预热的 planner_model > 按配置新建（缓存复用）。

    注意：不再复用 init_manager.chat_model（那是主回答大模型，规划用它就是 39 秒的来源）。
    """
    global _planner_model_cache
    try:
        from app.core.background_init import init_manager
        if getattr(init_manager, "planner_model", None) is not None:
            return init_manager.planner_model
    except Exception:
        pass
    if _planner_model_cache is None:
        _planner_model_cache = _create_default_chat_model()
    return _planner_model_cache


def reset_planner_model_cache() -> None:
    """仅测试用：清空规划模型单例缓存。"""
    global _planner_model_cache
    _planner_model_cache = None


def _resolve_shared_chat_model():
    """优先复用后台已预热的 chat_model（连接已建立），避免每消息重新实例化。

    未预热完成时回落自建兜底，保证规划不阻塞。
    注：仅 evaluator 在用（主回答大模型）；planner 已切独立小模型，见上。
    """
    try:
        from app.core.background_init import init_manager
        if init_manager.chat_model is not None:
            return init_manager.chat_model
    except Exception:
        pass
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


class AgenticRagPlanner:
    def __init__(self, chat_model=None):
        self.chat_model = chat_model if chat_model is not None else _resolve_planner_chat_model()
        self.prompt_template = _load_prompt()

    async def plan(self, query: str) -> RetrievalPlan:
        if self.chat_model is not None:
            prompt = self.prompt_template.replace("{query}", query)
            timeout = float(getattr(settings, "PLANNER_TIMEOUT_S", 8.0) or 8.0)
            # 总预算制：文本和结构化分同一个 deadline，最坏情况整体仍 ≤ timeout
            deadline = asyncio.get_running_loop().time() + timeout
            # 1) 文本 JSON 解析（few-shot 下小模型又快又准，2~4 秒为主路径）
            text_plan = await self._text_plan(prompt, query, timeout)
            if text_plan is not None:
                return text_plan
            # 2) pydantic 结构化输出兜底（文本实在解析不出时再试；json_mode
            # 比 function-calling 轻，但本通道偶发超时，故放备胎位）
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0.5:
                structured = await self._structured_plan(prompt, remaining)
                if structured is not None:
                    return structured

        plan = self._fallback_plan(query)
        plan.metadata = {"source": "fallback"}
        return plan

    async def _text_plan(self, prompt: str, query: str, timeout: float) -> RetrievalPlan | None:
        """文本 JSON 解析路径（兼容不支持 tool_call 的通道与测试假模型）。"""
        content: Any = None
        try:
            response = await asyncio.wait_for(
                self.chat_model.ainvoke(prompt), timeout=timeout
            )
            content = getattr(response, "content", response)
            if isinstance(content, str):
                return self._clamp(RetrievalPlan.model_validate(_extract_json_object(content)))
            logger.warning(
                f"规划 LLM 返回非文本 content（{type(content).__name__}），"
                f"换结构化重试: {query[:50]!r}"
            )
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(f"规划 LLM 文本路径超时（>{timeout:.0f}s），换结构化重试: {query[:50]!r}")
        except Exception as e:
            logger.warning(
                f"规划文本解析失败，换结构化重试: {type(e).__name__} "
                f"query={query[:50]!r} content={str(content)[:300]!r}"
            )
        return None

    async def _structured_plan(self, prompt: str, timeout: float) -> RetrievalPlan | None:
        """pydantic 约束的结构化规划；不可用（无此方法/通道不支持/解析失败）返回 None。

        用 json_mode 而不用 function-calling：小模型做 tool-call 结构负担重
        （超时/乱写），纯 JSON + pydantic 校验是它的甜点区。
        """
        factory = getattr(self.chat_model, "with_structured_output", None)
        if not callable(factory):
            return None
        try:
            try:
                structured_model = factory(RetrievalPlan, method="json_mode")
            except TypeError:
                structured_model = factory(RetrievalPlan)
            result = await asyncio.wait_for(structured_model.ainvoke(prompt), timeout=timeout)
            if isinstance(result, RetrievalPlan):
                return self._clamp(result)
            if isinstance(result, dict):
                return self._clamp(RetrievalPlan.model_validate(result))
            logger.info(f"结构化规划返回意外类型（{type(result).__name__}），回落文本解析")
        except Exception as e:
            logger.info(f"结构化规划不可用，回落文本解析: {type(e).__name__} {str(e)[:150]}")
        return None

    @staticmethod
    def _clamp(plan: RetrievalPlan) -> RetrievalPlan:
        """小模型发散兜底：步数封顶、top_k 限幅。"""
        plan.steps = plan.steps[:2]
        for step in plan.steps:
            step.top_k = max(1, min(step.top_k, 5))
        plan.metadata = {"source": "llm"}
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
