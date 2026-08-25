import inspect
from collections.abc import Callable
from typing import Any

from app.rag.agentic_rag.evaluator import AnswerabilityEvaluator
from app.rag.agentic_rag.evidence import format_evidence_context, merge_evidence
from app.rag.agentic_rag.local_retriever import LocalRetriever
from app.rag.agentic_rag.planner import AgenticRagPlanner
from app.rag.agentic_rag.schemas import AgenticRagResult, AnswerabilityResult, Evidence, RetrievalPlan
from app.rag.agentic_rag.web_search import WebSearchClient


ThinkingCallback = Callable[[dict[str, Any]], Any]


class AgenticRagService:
    def __init__(
        self,
        planner: AgenticRagPlanner | None = None,
        local_retriever: LocalRetriever | None = None,
        evaluator: AnswerabilityEvaluator | None = None,
        web_search_client: WebSearchClient | None = None,
    ):
        self.planner = planner or AgenticRagPlanner()
        self.local_retriever = local_retriever or LocalRetriever()
        self.evaluator = evaluator or AnswerabilityEvaluator()
        self.web_search_client = web_search_client or WebSearchClient()

    async def run(
        self,
        query: str,
        user_id: str,
        thinking_callback: ThinkingCallback | None = None,
    ) -> AgenticRagResult:
        plan = await self.planner.plan(query)
        await self._emit(
            thinking_callback,
            "agentic_plan",
            "Planned retrieval strategy.",
            {
                "need_retrieval": plan.need_retrieval,
                "allow_web_fallback": plan.allow_web_fallback,
                "step_count": len(plan.steps),
                "reason": plan.reason,
            },
        )

        if not plan.need_retrieval:
            return AgenticRagResult(context="", evidences=[], plan=plan, answerability=None, used_web=False)

        local_evidences = await self.local_retriever.search(user_id, plan.steps)
        await self._emit(
            thinking_callback,
            "local_retrieval",
            "Retrieved local evidence.",
            {"evidence_count": len(local_evidences)},
        )

        answerability = self.evaluator.evaluate(query, local_evidences)
        await self._emit(
            thinking_callback,
            "answerability",
            "Evaluated local answerability.",
            {
                "answerable": answerability.answerable,
                "confidence": answerability.confidence,
                "reason": answerability.reason,
                "web_queries": answerability.web_queries,
            },
        )

        web_evidences = await self._maybe_search_web(query, plan, answerability, thinking_callback)
        fused_evidences = merge_evidence([*local_evidences, *web_evidences])
        await self._emit(
            thinking_callback,
            "evidence_fusion",
            "Merged local and web evidence.",
            {
                "local_count": len(local_evidences),
                "web_count": len(web_evidences),
                "fused_count": len(fused_evidences),
            },
        )

        context = format_evidence_context(fused_evidences)
        await self._emit(
            thinking_callback,
            "context_ready",
            "Prepared evidence context.",
            {"context_chars": len(context), "evidence_count": len(fused_evidences)},
        )

        return AgenticRagResult(
            context=context,
            evidences=fused_evidences,
            plan=plan,
            answerability=answerability,
            used_web=bool(web_evidences),
        )

    async def _maybe_search_web(
        self,
        query: str,
        plan: RetrievalPlan,
        answerability: AnswerabilityResult,
        thinking_callback: ThinkingCallback | None,
    ) -> list[Evidence]:
        if not plan.allow_web_fallback:
            return []
        if answerability.answerable and not answerability.web_queries:
            return []

        queries = answerability.web_queries or [query]
        web_evidences: list[Evidence] = []
        for web_query in queries:
            web_evidences.extend(await self.web_search_client.search(web_query, max_results=5))

        await self._emit(
            thinking_callback,
            "web_search",
            "Searched web fallback evidence.",
            {"queries": queries, "evidence_count": len(web_evidences)},
        )
        return web_evidences

    async def _emit(
        self,
        thinking_callback: ThinkingCallback | None,
        stage: str,
        content: str,
        details: dict[str, Any],
    ) -> None:
        if thinking_callback is None:
            return

        event = {"type": "thinking", "stage": stage, "content": content, "details": details}
        result = thinking_callback(event)
        if inspect.isawaitable(result):
            await result
