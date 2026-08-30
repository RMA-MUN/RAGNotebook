"""云端重排序服务：调用 rerank API（SiliconFlow / Jina / Cohere 兼容协议）。

接口约定：POST {RERANKER_API_BASE_URL}/rerank，payload {"model", "query", "documents"}，
响应 {"results": [{"index": int, "relevance_score": float}, ...]}。
API 未配置或调用失败时返回 success=False，由调用方按原顺序 + similarity 0 降级。
"""
from typing import Any

import httpx

from app.core.logger_handler import logger
from app.core.settings import settings


class ReorderService:
    """文档重排序服务（云端 rerank API）"""

    def __init__(self, http_client_factory=None):
        self.api_base_url = (settings.RERANKER_API_BASE_URL or "").rstrip("/")
        self.api_key = settings.RERANKER_API_KEY
        self.model = settings.RERANKER_MODEL
        self.http_client_factory = http_client_factory or httpx.AsyncClient

    async def _rerank(self, query: str, documents: list[str]) -> list[float]:
        """调用 rerank API，按文档原顺序返回相关性分数。"""
        async with self.http_client_factory(timeout=10.0) as client:
            resp = await client.post(
                f"{self.api_base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "query": query, "documents": documents},
            )
            resp.raise_for_status()
            data = resp.json()
        results = sorted(data.get("results", []), key=lambda r: r.get("index", 0))
        return [float(r.get("relevance_score", 0.0)) for r in results]

    async def reorder_documents(self, query: str, documents: list[str], thinking_callback=None) -> dict[str, Any]:
        """
        对文档进行重排序
        :param query: 查询语句
        :param documents: 文档列表
        :param thinking_callback: 思考过程回调函数
        :return: 包含重排序结果的字典，格式为：
                 {"success": bool, "documents": List[Dict], "error": str}
        """
        try:
            if not documents:
                return {
                    "success": True,
                    "documents": [],
                    "error": ""
                }

            if thinking_callback:
                await thinking_callback({
                    "type": "thinking",
                    "stage": "reorder",
                    "content": f"正在计算 {len(documents)} 个文档的相关性分数..."
                })

            scores = await self._rerank(query, documents)

            # 构建结果列表
            scored_documents = []
            for doc, score in zip(documents, scores):
                scored_documents.append({
                    "document": doc,
                    "similarity": score
                })
                logger.info(f"【重排序服务】文档相似度分数: {score:.4f}")

            if thinking_callback:
                score_details = []
                for i, (doc, score) in enumerate(zip(documents, scores), 1):
                    score_details.append({
                        "index": i,
                        "score": round(score, 4),
                        "preview": doc[:100] + "..." if len(doc) > 100 else doc
                    })
                await thinking_callback({
                    "type": "thinking",
                    "stage": "reorder",
                    "content": f"已计算完成 {len(documents)} 个文档的相关性分数，按分数降序排序",
                    "details": {
                        "scores": score_details
                    }
                })

            # 按相似度分数降序排序
            sorted_docs = sorted(scored_documents, key=lambda x: x["similarity"], reverse=True)
            logger.info(f"【重排序服务】文档重排序成功，返回 {len(sorted_docs)} 个文档")

            return {
                "success": True,
                "documents": sorted_docs,
                "error": ""
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"【重排序服务】重排序失败: {error_msg}")
            return {
                "success": False,
                "documents": [],
                "error": error_msg
            }

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
