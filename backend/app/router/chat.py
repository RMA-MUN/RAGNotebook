import asyncio
import json
import uuid

from fastapi import Depends
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter

from app.agent.agent import get_agent_stream_response
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.router.chat_service import ChatService, get_router_service
from app.schemas.models import QueryRequest, RAGRequest, RAGResponse, ReorderRequest, ReorderResponse, SessionResponse
from app.utils.auth_utils import get_current_user_id

chat_router = APIRouter(prefix="/chat", tags=["chat"])


@chat_router.post("/agent/query/stream")
async def query_stream(
        request: QueryRequest,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """查询Agent流式响应"""
    session_id = request.session_id or str(uuid.uuid4())

    from app.core.logger_handler import logger
    from app.rag.vector_store import VectorStoreService

    vector_store = VectorStoreService()

    # ---- 路由判断（快速，~50ms）----
    score = await vector_store.compute_route_score(
        request.query, user_id
    )

    # 查询 Top-1 文档详情，用于日志输出
    top1_docs = await asyncio.to_thread(
        vector_store.vectors_store.similarity_search_with_score,
        request.query, k=1, filter={"user_id": user_id}
    )
    if top1_docs:
        top1_doc, top1_distance = top1_docs[0]
        source_type = "笔记库" if top1_doc.metadata.get("source_type") == "note" else "知识库"
        source_name = top1_doc.metadata.get("title") or top1_doc.metadata.get("original_filename", "未知")
        preview = top1_doc.page_content[:80].replace("\n", " ")
        logger.info(
            f"【路由决策】查询: 「{request.query}」 | "
            f"score: {score:.4f} (距离: {top1_distance:.4f}) | "
            f"Top-1来源: {source_type}《{source_name}》 | "
            f"预览: {preview}... | "
            f"决策: {'→ RAG 前置管线' if score > 0.5 else '→ 跳过 RAG'}"
        )
    else:
        logger.info(
            f"【路由决策】查询: 「{request.query}」 | "
            f"score: {score:.4f} | "
            f"Top-1: 无文档 | "
            f"决策: → 跳过 RAG"
        )

    async def stream_with_rag_thinking():
        """包装生成器：RAG 管线在内部实时推送思考事件，再转发 Agent 流式响应"""
        rag_context = ""

        if score > 0.5:
            from app.rag.rag_service import RagService

            # RAG 管线与 SSE 推送共用的队列
            thinking_queue = asyncio.Queue()
            rag_done = asyncio.Event()

            async def thinking_callback(data: dict):
                await thinking_queue.put(data)

            async def run_rag_pipeline():
                """在后台执行 RAG 管线，thinking 事件通过队列实时推送"""
                try:
                    rag_service = RagService(user_id, thinking_callback=thinking_callback)
                    documents = await rag_service.retrieve_document(request.query)

                    def _format_doc(doc):
                        if doc.metadata.get("source_type") == "note":
                            title = doc.metadata.get("title", "无标题")
                            return f"[来源：笔记《{title}》]\n{doc.page_content}"
                        else:
                            filename = doc.metadata.get("original_filename", "知识库文档")
                            return f"[来源：知识库《{filename}》]\n{doc.page_content}"

                    doc_contents = [_format_doc(doc) for doc in documents]
                    reordered = await rag_service.reorder_documents(request.query, doc_contents)
                    nonlocal rag_context
                    rag_context = "\n\n".join(reordered[:3])
                    logger.info(f"【RAG前置】检索到 {len(documents)} 个文档，重排序后取前 {min(3, len(reordered))} 个注入 Agent")
                except Exception as e:
                    logger.error(f"【RAG前置】管线执行失败: {e}", exc_info=True)
                finally:
                    rag_done.set()

            # 启动 RAG 管线（后台任务）
            rag_task = asyncio.create_task(run_rag_pipeline())

            # 实时推送 RAG 思考事件：边跑边推，不等管线结束
            while not rag_done.is_set() or not thinking_queue.empty():
                try:
                    event = thinking_queue.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.QueueEmpty:
                    # 队列暂时为空，等 RAG 管线产出新事件
                    try:
                        event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except (asyncio.TimeoutError, asyncio.QueueEmpty):
                        continue

            # 确保 RAG 任务完成，再 drain 一次队列防止竞态丢失事件
            await rag_task
            while not thinking_queue.empty():
                event = thinking_queue.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 转发 Agent 流式响应
        async for chunk in get_agent_stream_response(
            request.query, session_id, user_id, rag_context=rag_context
        ):
            yield chunk

    return StreamingResponse(
        stream_with_rag_thinking(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


@chat_router.post("/rag/query", response_model=RAGResponse)
async def query_rag(
        request: RAGRequest,
        user_id: str = Depends(get_current_user_id),
        router_service: ChatService = Depends(get_router_service),
        _: None = Depends(rate_limit(limit=15, window=60))
):
    """RAG检索"""
    response = await router_service.handle_rag_query(request.query, user_id)
    return success_response(data=RAGResponse(response=response))


@chat_router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service: ChatService = Depends(get_router_service)):
    """获取会话信息，使用user_id验证"""
    history = await router_service.handle_get_session(session_id, user_id)
    return success_response(data=SessionResponse(session_id=session_id, history=history))


@chat_router.delete("/session/{session_id}")
async def delete_session(session_id: str, user_id: str = Depends(get_current_user_id), router_service: ChatService = Depends(get_router_service)):
    """删除会话"""
    await router_service.handle_delete_session(session_id, user_id)
    return success_response(message=f"Session {session_id} deleted successfully")


@chat_router.get("/sessions")
async def get_all_sessions(router_service: ChatService = Depends(get_router_service)):
    """获取所有会话ID"""
    session_ids = await router_service.handle_get_all_sessions()
    return success_response(data={"sessions": session_ids})


@chat_router.get("/sessions/{user_id}")
async def get_user_sessions(
    user_id: str,
    current_user_id: str = Depends(get_current_user_id),
    router_service: ChatService = Depends(get_router_service),
):
    """获取用户所有会话ID"""
    session_ids = await router_service.handle_get_user_sessions(user_id, current_user_id)
    return success_response(data={"sessions": session_ids})


@chat_router.post("/reorder", response_model=ReorderResponse)
async def reorder_documents(
        request: ReorderRequest,
        router_service: ChatService = Depends(get_router_service),
        _: None = Depends(rate_limit(limit=20, window=60))
):
    """使用Ollama本地的嵌入模型对文档进行中文重排序"""
    sorted_docs = await router_service.handle_reorder(request.query, request.documents)
    return success_response(data=ReorderResponse(documents=sorted_docs))
