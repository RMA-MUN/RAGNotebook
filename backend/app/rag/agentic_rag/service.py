import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from app.core.logger_handler import logger
from app.rag.agentic_rag.evaluator import AnswerabilityEvaluator
from app.rag.agentic_rag.evidence import format_evidence_context, merge_evidence
from app.rag.agentic_rag.local_retriever import LocalRetriever
from app.rag.agentic_rag.planner import AgenticRagPlanner
from app.rag.agentic_rag.query_entity_extractor import QueryEntityExtractor
from app.rag.agentic_rag.schemas import AgenticRagResult, AnswerabilityResult, Evidence
from app.rag.agentic_rag.web_search import WebSearchClient
from app.utils.user_config import create_chat_model_for_user, create_embed_model_for_user


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
        # 每用户模型：配置可解析时注入 per-user 组件，否则回落注入默认（调用方注入 / 全局 fallback）
        try:
            user_chat = await create_chat_model_for_user(user_id)
        except Exception:
            user_chat = None
        if user_chat is not None:
            planner = AgenticRagPlanner(chat_model=user_chat)
            evaluator = AnswerabilityEvaluator(chat_model=user_chat)
        else:
            planner = self.planner
            evaluator = self.evaluator
        try:
            user_embed = await create_embed_model_for_user(user_id)
        except Exception:
            user_embed = None
        retriever = self.local_retriever if user_embed is None else LocalRetriever(
            note_service=self.local_retriever.note_service,
            session_factory=self.local_retriever.session_factory,
            query_entity_extractor=QueryEntityExtractor(chat_model=user_chat),
            embed_model=user_embed,
        )
        plan = await planner.plan(query)
        await self._emit(
            thinking_callback,
            "agentic_plan",
            "Planned retrieval strategy.",
            {
                "query": query,
                "source": plan.metadata.get("source", "unknown"),
                "steps": [step.model_dump() for step in plan.steps],
                "need_retrieval": plan.need_retrieval,
                "allow_web_fallback": plan.allow_web_fallback,
                "step_count": len(plan.steps),
                "reason": plan.reason,
            },
        )

        if not plan.need_retrieval:
            return AgenticRagResult(context="", evidences=[], plan=plan, answerability=None, used_web=False)

        graph_steps = [step for step in plan.steps if step.tool == "search_graph"]
        text_steps = [step for step in plan.steps if step.tool != "search_graph"]

        # 图检索（含 LLM 实体抽取）与文本检索并行，避免 LLM 抽取拉长整体延迟
        graph_task = asyncio.create_task(self._search_graph_only(user_id, graph_steps, retriever))
        text_task = asyncio.create_task(retriever.search(user_id, text_steps))
        local_evidences = []
        graph_evidences = []
        if graph_steps:
            # 图检索本身内部会再次调 LLM 抽取实体；失败不阻塞文本检索
            try:
                graph_evidences = await graph_task
            except Exception as e:
                logger.warning(f"图谱检索失败: {e}")
        if text_steps:
            local_evidences = await text_task
        await asyncio.gather(graph_task, text_task, return_exceptions=True)
        local_evidences = [*local_evidences, *graph_evidences]

        await self._emit(
            thinking_callback,
            "local_retrieval",
            "Retrieved local evidence.",
            {
                "queries": [step.model_dump() for step in plan.steps],
                "evidence_count": len(local_evidences),
                "results": [self._evidence_preview(evidence) for evidence in local_evidences],
            },
        )
        await asyncio.sleep(0)  # 分帧：让各阶段落入不同事件循环 tick，避免一次性涌入前端

        answerability = await evaluator.evaluate(query, local_evidences)
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
        await asyncio.sleep(0)

        web_evidences = await self._maybe_search_web(query, answerability, thinking_callback)
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
        await asyncio.sleep(0)

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
        answerability: AnswerabilityResult,
        thinking_callback: ThinkingCallback | None,
    ) -> list[Evidence]:
        """web 兜底：answerability 判不可答（或显式给出 web_queries）即触发。

        planner 的 allow_web_fallback 仅作规划提示、不再一票否决——
        本地知识缺口（检索回来的证据与问题不相关）同样需要搜网；
        硬开关收敛到 WebSearchClient 自身（WEB_SEARCH_ENABLED/provider/key）。
        """
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
            {
                "queries": queries,
                "evidence_count": len(web_evidences),
                "results": [self._evidence_preview(evidence) for evidence in web_evidences],
            },
        )
        return web_evidences

    async def _search_graph_only(self, user_id: str, steps: list, retriever: LocalRetriever) -> list[Evidence]:
        """仅执行 search_graph 步骤（无其他文本步骤时也不抛错）。"""
        if not steps:
            return []
        return await retriever.search(user_id, steps)

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

    @staticmethod
    def _evidence_preview(evidence: Evidence) -> dict[str, Any]:
        return {
            "id": evidence.id,
            "source": evidence.source,
            "title": evidence.title,
            "score": evidence.score,
            "url": evidence.url,
            "preview": evidence.content[:500],
        }
