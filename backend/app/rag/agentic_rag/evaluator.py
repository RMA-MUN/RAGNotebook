"""可答性评估：判断本地证据能否回答问题，决定是否走 web 兜底搜索。

- 规则快路径（不调 LLM）：无证据 → 不可答；查询含新鲜词 → 不可答（需最新信息）；
- 其余交给 LLM 判证据与问题的相关性：向量检索恒返回 top-k，"存在证据"不等于
  "证据相关"（如笔记未收录的人物会拿到噪声切片），LLM 判不可答即触发 web 兜底；
- LLM 不可用或解析失败时回落规则（有证据即视为可答）。
"""
from typing import Any

from app.core.logger_handler import logger
from app.rag.agentic_rag.planner import (
    _extract_json_object,
    _resolve_shared_chat_model,
    has_freshness_term,
)
from app.rag.agentic_rag.schemas import AnswerabilityResult, Evidence

_EVIDENCE_PREVIEW_CHARS = 300
_MAX_EVIDENCE_ITEMS = 8

_FALLBACK_PROMPT = (
    "You are an Agentic RAG answerability evaluator. "
    "Return only JSON with keys: answerable, confidence, reason, web_queries.\n\n"
    "User query: {query}\n\nEvidence:\n{evidence}"
)


def _load_prompt() -> str:
    try:
        from app.utils.prompt_loader import load_prompt

        return load_prompt("agentic_rag_answerability_prompt")
    except Exception:
        return _FALLBACK_PROMPT


def _format_evidence(evidences: list[Evidence]) -> str:
    lines = []
    for i, evidence in enumerate(evidences[:_MAX_EVIDENCE_ITEMS], 1):
        preview = evidence.content[:_EVIDENCE_PREVIEW_CHARS].replace("\n", " ")
        score = f"{evidence.score:.3f}" if evidence.score is not None else "n/a"
        lines.append(f"[{i}] {evidence.title}（{evidence.source}，score={score}）{preview}")
    return "\n".join(lines)


def _parse_answerability(data: dict[str, Any], query: str) -> AnswerabilityResult:
    raw_answerable = data.get("answerable")
    if isinstance(raw_answerable, str):
        answerable = raw_answerable.strip().lower() in {"true", "1", "yes"}
    else:
        answerable = bool(raw_answerable)

    try:
        confidence = max(0.0, min(float(data.get("confidence", 0.5)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.5

    reason = str(data.get("reason") or "").strip() or (
        "Local evidence is relevant to the query."
        if answerable
        else "Local evidence is insufficient to answer the query."
    )

    web_queries = [str(q).strip() for q in (data.get("web_queries") or []) if str(q).strip()]
    if not answerable and not web_queries:
        web_queries = [query]
    return AnswerabilityResult(
        answerable=answerable, confidence=confidence, reason=reason, web_queries=web_queries
    )


class AnswerabilityEvaluator:
    def __init__(self, chat_model=None, prompt_template: str | None = None):
        self.chat_model = chat_model
        self.prompt_template = prompt_template

    async def evaluate(self, query: str, evidences: list[Evidence]) -> AnswerabilityResult:
        # 规则快路径：结论确定，无需 LLM
        if not evidences:
            return AnswerabilityResult(
                answerable=False,
                confidence=0.0,
                reason="No evidence is available to answer the query.",
                web_queries=[query],
            )
        if has_freshness_term(query):
            return AnswerabilityResult(
                answerable=False,
                confidence=0.35,
                reason="Query asks for fresh information, so web evidence is required.",
                web_queries=[query],
            )

        model = self.chat_model if self.chat_model is not None else _resolve_shared_chat_model()
        if model is None:
            return self._rule_fallback()
        try:
            return await self._llm_evaluate(query, evidences, model)
        except Exception as e:
            logger.warning(f"可答性 LLM 判定失败，回落规则: {query}: {e}")
            return self._rule_fallback()

    def _rule_fallback(self) -> AnswerabilityResult:
        return AnswerabilityResult(
            answerable=True,
            confidence=0.75,
            reason="Local evidence is available and query does not require freshness.",
            web_queries=[],
        )

    def _template(self) -> str:
        if self.prompt_template is None:
            self.prompt_template = _load_prompt()
        return self.prompt_template

    async def _llm_evaluate(self, query: str, evidences: list[Evidence], model) -> AnswerabilityResult:
        prompt = (
            self._template()
            .replace("{query}", query)
            .replace("{evidence}", _format_evidence(evidences))
        )
        response = await model.ainvoke(prompt)
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            raise ValueError("Answerability LLM response is not text")
        return _parse_answerability(_extract_json_object(content), query)
