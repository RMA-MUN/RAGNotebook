"""Chat 路由：Agent 对话（流式/非流式）与 Agentic RAG 前置检索编排。

流式端点时序：先发占位 thinking 帧（前端折叠框即时出现）→ 转发 RAG 真实思考事件 →
Agent 流式回答 → done；RAG 失败不阻塞回答（rag_context 置空继续走 Agent）。
"""
import asyncio
import json
import time
import uuid

from fastapi import Depends
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel, Field

from app.agent.agent import (
    get_agent_resume_stream_response,
    get_agent_stream_response,
    read_approval_snapshot,
    read_pending_interrupt,
)
from app.agent.agent_rag_tool import build_pre_searched_queries
from app.core.logger_handler import logger
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.rag.agentic_rag.service import AgenticRagService
from app.schemas.models import QueryRequest, ReorderRequest, ReorderResponse, SessionResponse
from app.services import session_manager as sm
from app.utils.auth_utils import get_current_user_id

chat_router = APIRouter(prefix="/chat", tags=["chat"])


def get_router_service():
    from app.router.chat_service import get_router_service as _get_router_service

    return _get_router_service()


@chat_router.post("/agent/query/stream")
async def query_stream(
        request: QueryRequest,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """查询Agent流式响应"""
    session_id = request.session_id or str(uuid.uuid4())

    async def stream_with_rag_thinking():
        """实时转发 Agentic RAG 思考事件，再转发 Agent 流式响应。"""
        from app.core.logger_handler import logger

        t_req = time.perf_counter()
        rag_context = ""
        thinking_queue = asyncio.Queue()
        rag_done = object()

        async def thinking_callback(data: dict):
            await thinking_queue.put(data)

        async def run_rag():
            try:
                return await AgenticRagService().run(
                    request.query, user_id, thinking_callback=thinking_callback
                )
            except Exception as e:
                logger.error(f"【Agentic RAG】管线执行失败: {e}", exc_info=True)
                return None
            finally:
                await thinking_queue.put(rag_done)

        rag_task = asyncio.create_task(run_rag())
        try:
            # 先发占位：让前端「正在规划」折叠框立即出现，而不是干等一整轮 LLM
            yield "data: " + json.dumps({
                "type": "thinking",
                "stage": "agentic_plan",
                "content": "正在规划检索策略…",
                "details": {"placeholder": True},
            }, ensure_ascii=False) + "\n\n"

            while True:
                event = await thinking_queue.get()
                if event is rag_done:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            result = await rag_task
            rag_ms = (time.perf_counter() - t_req) * 1000
            if result is not None:
                rag_context = result.context or ""
            logger.info(f"【端到端耗时】RAG阶段完成 rag={rag_ms:.0f}ms context_chars={len(rag_context)} query={request.query[:50]!r}")

            searched_queries = build_pre_searched_queries(request.query, result)
            # 转发 Agent 流式响应
            first_token_logged = False
            t_agent = time.perf_counter()
            async for chunk in get_agent_stream_response(
                request.query,
                session_id,
                user_id,
                rag_context=rag_context,
                rag_searched_queries=searched_queries,
            ):
                if not first_token_logged:
                    first_token_logged = True
                    ttfb_ms = (time.perf_counter() - t_req) * 1000
                    agent_start_ms = (time.perf_counter() - t_agent) * 1000
                    logger.info(f"【端到端耗时】Agent首chunk TTFB={ttfb_ms:.0f}ms (RAG={rag_ms:.0f}ms + Agent启动={agent_start_ms:.0f}ms)")
                yield chunk
            total_ms = (time.perf_counter() - t_req) * 1000
            logger.info(f"【端到端耗时】请求完成 total={total_ms:.0f}ms RAG={rag_ms:.0f}ms")
        finally:
            if not rag_task.done():
                rag_task.cancel()

    return StreamingResponse(
        stream_with_rag_thinking(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


class ResumeRequest(BaseModel):
    """恢复审批请求模型"""
    session_id: str
    decisions: list[dict] = Field(default_factory=list)


@chat_router.post("/agent/resume")
async def resume_agent(
        request: ResumeRequest,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60)),
):
    """恢复被中断的 Agent run（decisions: [{"type": "approve"|"reject", ...}]）"""
    # run_id 从会话 pending_run_id 取（更稳，不信任客户端）
    pending_run_id = await sm.session_manager.get_pending_run_id(request.session_id, user_id)
    if not pending_run_id:
        async def stream_error():
            yield f"data: {json.dumps({'type': 'error', 'content': 'RESUME_MISMATCH'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(stream_error(), media_type="text/event-stream")

    async def stream_resume():
        async for chunk in get_agent_resume_stream_response(
            session_id=request.session_id, user_id=user_id,
            run_id=pending_run_id, decisions=request.decisions,
        ):
            yield chunk

    return StreamingResponse(
        stream_resume(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@chat_router.get("/session/{session_id}/pending")
async def get_pending(session_id: str, user_id: str = Depends(get_current_user_id)):
    """返回待审批中断元数据（含原始 query）；无 pending 返回 null。"""
    pending_run_id = await sm.session_manager.get_pending_run_id(session_id, user_id)
    if not pending_run_id:
        return success_response(data=None)
    info = await read_pending_interrupt(pending_run_id)
    return success_response(data=info)


@chat_router.get("/session/{session_id}/approval")
async def get_approval(session_id: str, user_id: str = Depends(get_current_user_id)):
    """返回该会话上次审批快照（含已审批完成，active=False）；从无审批返回 null。"""
    pending_run_id = await sm.session_manager.get_pending_run_id(session_id, user_id)
    if not pending_run_id:
        return success_response(data=None)
    info = await read_approval_snapshot(pending_run_id)
    return success_response(data=info)


@chat_router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service=Depends(get_router_service)):
    """获取会话信息，使用user_id验证"""
    history = await router_service.handle_get_session(session_id, user_id)
    pending_run_id = await router_service.handle_get_pending_run_id(session_id, user_id)
    return success_response(data=SessionResponse(
        session_id=session_id, history=history, pending_run_id=pending_run_id,
    ))


@chat_router.delete("/session/{session_id}")
async def delete_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service=Depends(get_router_service)):
    """删除会话（若有待审批 run 一并清理 checkpoint thread）"""
    from app.agent.agent import get_checkpointer

    pending_run_id = await sm.session_manager.get_pending_run_id(session_id, user_id)
    if pending_run_id:
        try:
            await get_checkpointer().adelete_thread(pending_run_id)
        except Exception as e:
            logger.warning(f"清理 pending thread 失败（可忽略）: {e}")
    await router_service.handle_delete_session(session_id, user_id)
    return success_response(message=f"Session {session_id} deleted successfully")


@chat_router.get("/sessions")
async def get_all_sessions(router_service=Depends(get_router_service)):
    """获取所有会话ID"""
    session_ids = await router_service.handle_get_all_sessions()
    return success_response(data={"sessions": session_ids})


@chat_router.get("/sessions/{user_id}")
async def get_user_sessions(
    user_id: str,
    current_user_id: str = Depends(get_current_user_id),
    router_service=Depends(get_router_service),
):
    """获取用户所有会话ID"""
    session_ids = await router_service.handle_get_user_sessions(user_id, current_user_id)
    return success_response(data={"sessions": session_ids})


@chat_router.post("/reorder", response_model=ReorderResponse)
async def reorder_documents(
        request: ReorderRequest,
        router_service=Depends(get_router_service),
        _: None = Depends(rate_limit(limit=20, window=60))
):
    """使用Ollama本地的嵌入模型对文档进行中文重排序"""
    sorted_docs = await router_service.handle_reorder(request.query, request.documents)
    return success_response(data=ReorderResponse(documents=sorted_docs))
