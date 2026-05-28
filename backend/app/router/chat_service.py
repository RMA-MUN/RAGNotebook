import uuid
from typing import Any

from fastapi import HTTPException

from app.agent.agent import get_agent_response
from app.core.logger_handler import logger
from app.rag.rag_service import RagService
from app.rag.reorder_service import reorder_service
from app.services import session_manager as sm


class ChatService:
    """路由服务层，处理业务逻辑"""

    async def handle_agent_query(self, query: str, session_id: str | None, user_id: str) -> tuple[str, dict, str]:
        """处理智能代理查询逻辑"""
        session_id = session_id or str(uuid.uuid4())

        history = await sm.session_manager.get_history(session_id, user_id)

        result = await get_agent_response(query, history)
        response = result.get("response")
        steps = result.get("steps", [])

        await sm.session_manager.add_message(session_id, user_id, query, response)

        return session_id, response, steps

    async def handle_rag_query(self, query: str, user_id: str) -> str:
        """处理 RAG 查询逻辑"""
        rag_service = RagService(user_id)
        response = await rag_service.rag_summary(query)
        return response

    async def handle_get_session(self, session_id: str, user_id: str) -> list[tuple[str, str]]:
        """处理获取会话逻辑"""
        history = await sm.session_manager.get_history(session_id, user_id)
        return history

    async def handle_delete_session(self, session_id: str, user_id: str) -> None:
        """处理删除会话逻辑"""
        await sm.session_manager.clear_session(session_id, user_id)

    async def handle_get_all_sessions(self) -> list[str]:
        """处理获取所有会话逻辑"""
        session_ids = await sm.session_manager.get_all_session_ids()
        return session_ids

    async def handle_get_user_sessions(self, user_id: str, current_user_id: str) -> list[dict]:
        """处理获取用户会话逻辑"""
        if user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        sessions = await sm.session_manager.get_user_sessions(user_id)
        return sessions

    async def handle_reorder(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        """
        使用本地Ollama重排序模型对文档进行中文重排序
        :param query: 查询语句
        :param documents: 文档列表
        :return: 排序后的文档列表，包含文档内容和相似度
        """
        try:
            result = await reorder_service.reorder_documents(query, documents)

            if result["success"]:
                logger.info(
                    f"【重排序结果】查询: {query} 排序结果: "
                    f"{[f'文档 {doc['document']}: {doc['similarity']:.4f}' for doc in result['documents']]}"
                )
                return result["documents"]
            else:
                logger.warning(f"【重排序失败】{result['error']}")
                return [{"document": doc, "similarity": 0.0} for doc in documents]
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"重排序过程中出错: {str(e)}")


def get_router_service() -> ChatService:
    """获取路由服务实例（用于依赖注入）"""
    return ChatService()
