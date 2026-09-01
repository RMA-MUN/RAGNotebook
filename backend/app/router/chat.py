"""Chat 路由：Agent 对话（流式/非流式）与 Agentic RAG 前置检索编排。

流式端点时序：先发占位 thinking 帧（前端折叠框即时出现）→ 转发 RAG 真实思考事件 →
Agent 流式回答 → done；RAG 失败不阻塞回答（rag_context 置空继续走 Agent）。
"""
import asyncio
import json
import uuid

from fastapi import Depends
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter

from app.agent.agent import get_agent_stream_response
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.rag.agentic_rag.service import AgenticRagService
from app.schemas.models import QueryRequest, ReorderRequest, ReorderResponse, SessionResponse
from app.utils.auth_utils import get_current_user_id
from app.utils.user_config import create_chat_model_for_user

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

        # 每用户对话模型：配置可解析时注入，否则回落工厂默认（不阻塞对话）
        try:
            user_chat = await create_chat_model_for_user(user_id)
        except Exception:
            user_chat = None

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
            if result is not None:
                rag_context = result.context or ""

            # 转发 Agent 流式响应
            async for chunk in get_agent_stream_response(
                request.query, session_id, user_id, rag_context=rag_context, chat_model=user_chat
            ):
                yield chunk
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


@chat_router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service=Depends(get_router_service)):
    """获取会话信息，使用user_id验证"""
    history = await router_service.handle_get_session(session_id, user_id)
    return success_response(data=SessionResponse(session_id=session_id, history=history))


@chat_router.delete("/session/{session_id}")
async def delete_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service=Depends(get_router_service)):
    """删除会话"""
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
