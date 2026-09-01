"""云端重排序服务：调用 rerank API（SiliconFlow / Jina / Cohere 兼容协议）。

接口约定：POST {RERANKER_API_BASE_URL}/rerank，payload {"model", "query", "documents"}，
响应 {"results": [{"index": int, "relevance_score": float}, ...]}。
API 未配置或调用失败时返回 success=False，由调用方按原顺序 + similarity 0 降级。
"""

from typing import Any

import httpx

from app.core.logger_handler import logger
from app.core.settings import settings


async def get_reorder_config_for_user(user_id: str) -> dict[str, str]:
    """按用户解析云端 rerank 配置（base_url/api_key/model），未配置回落全局 RERANKER_*。

    用户配置了基础地址或模型即视为已配置重排序；api_key 解密失败时回落全局配置。
    DB 查询异常或 SECRET_KEY 缺失等任意错误一律回落全局 RERANKER_*（fail-soft），
    保证默认链路不因用户配置解析失败而阻塞请求。
    """
    from app.utils.encryption import decrypt_secret
    from app.utils.user_config import get_user_ai_config

    try:
        row = await get_user_ai_config(user_id)
        if row is None:
            return {
                "base_url": (settings.RERANKER_API_BASE_URL or "").rstrip("/"),
                "api_key": settings.RERANKER_API_KEY,
                "model": settings.RERANKER_MODEL,
            }
        return {
            "base_url": (row.rerank_base_url or settings.RERANKER_API_BASE_URL or "").rstrip("/"),
            "api_key": decrypt_secret(row.rerank_api_key) or settings.RERANKER_API_KEY,
            "model": row.rerank_model or settings.RERANKER_MODEL,
        }
    except Exception as e:
        logger.warning(
            "per-user rerank config resolution failed, using global RERANKER_* for user_id=%s: %s",
            user_id, e, exc_info=True,
        )
        return {
            "base_url": (settings.RERANKER_API_BASE_URL or "").rstrip("/"),
            "api_key": settings.RERANKER_API_KEY,
            "model": settings.RERANKER_MODEL,
        }


class ReorderService:
    """文档重排序服务（云端 rerank API）"""

    def __init__(self, http_client_factory=None):
        self.api_base_url = (settings.RERANKER_API_BASE_URL or "").rstrip("/")
        self.api_key = settings.RERANKER_API_KEY
        self.model = settings.RERANKER_MODEL
        self.http_client_factory = http_client_factory or httpx.AsyncClient

    async def _rerank(
        self,
        query: str,
        documents: list[str],
        api_base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> list[float]:
        """调用 rerank API，按文档原顺序返回相关性分数。"""
        api_base_url = api_base_url or self.api_base_url
        api_key = api_key or self.api_key
        model = model or self.model
        async with self.http_client_factory(timeout=10.0) as client:
            resp = await client.post(
                f"{api_base_url}/rerank",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "query": query, "documents": documents},
            )
            resp.raise_for_status()
            data = resp.json()
        results = sorted(data.get("results", []), key=lambda r: r.get("index", 0))
        return [float(r.get("relevance_score", 0.0)) for r in results]

    async def reorder_documents(
        self,
        query: str,
        documents: list[str],
        thinking_callback=None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        对文档进行重排序
        :param query: 查询语句
        :param documents: 文档列表
        :param thinking_callback: 思考过程回调函数
        :param config: 可选 per-user 配置 {base_url, api_key, model}；为空时沿用实例级/全局配置
        :return: 包含重排序结果的字典，格式为：
                 {"success": bool, "documents": List[Dict], "error": str}
        """
        try:
            if not documents:
                return {"success": True, "documents": [], "error": ""}

            if thinking_callback:
                await thinking_callback({"type": "thinking", "stage": "reorder", "content": f"正在计算 {len(documents)} 个文档的相关性分数..."})

            base_url = (config.get("base_url") if config else None) or self.api_base_url
            api_key = (config.get("api_key") if config else None) or self.api_key
            model = (config.get("model") if config else None) or self.model
            scores = await self._rerank(query, documents, api_base_url=base_url, api_key=api_key, model=model)

            # 构建结果列表
            scored_documents = []
            for doc, score in zip(documents, scores):
                scored_documents.append({"document": doc, "similarity": score})
                logger.info(f"【重排序服务】文档相似度分数: {score:.4f}")

            if thinking_callback:
                score_details = []
                for i, (doc, score) in enumerate(zip(documents, scores), 1):
                    score_details.append({"index": i, "score": round(score, 4), "preview": doc[:100] + "..." if len(doc) > 100 else doc})
                await thinking_callback(
                    {"type": "thinking", "stage": "reorder", "content": f"已计算完成 {len(documents)} 个文档的相关性分数，按分数降序排序", "details": {"scores": score_details}}
                )

            # 按相似度分数降序排序
            sorted_docs = sorted(scored_documents, key=lambda x: x["similarity"], reverse=True)
            logger.info(f"【重排序服务】文档重排序成功，返回 {len(sorted_docs)} 个文档")

            return {"success": True, "documents": sorted_docs, "error": ""}
        except Exception as e:
            error_msg = str(e)
            logger.error(f"【重排序服务】重排序失败: {error_msg}")
            return {"success": False, "documents": [], "error": error_msg}

    @staticmethod
    async def format_reorder_result(sorted_docs: list[dict]) -> str:
        """
        格式化重排序结果
        :param sorted_docs: 重排序后的文档列表
        :return: 格式化后的字符串
        """
        formatted_result = "重排序后的文档列表：\n"
        for i, doc in enumerate(sorted_docs, 1):
            formatted_result += f"{i}. 相似度: {doc.get('similarity', 0):.4f}\n"
            formatted_result += f"   内容: {doc.get('document', '')}\n\n"
        return formatted_result


# 全局重排序服务实例
reorder_service = ReorderService()
