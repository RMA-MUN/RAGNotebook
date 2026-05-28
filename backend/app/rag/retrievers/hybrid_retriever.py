import asyncio

from langchain_chroma import Chroma
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.utils.config import chroma_config

from .empty_retriever import EmptyRetriever


class HybridRetriever:
    """混合检索器（BM25 + 向量检索）"""

    def __init__(self, vectors_store: Chroma):
        self.vectors_store = vectors_store

    async def get_bm25_retriever(self, user_id: str = None):
        """
        获取BM25检索器
        :param user_id: 用户ID，必须提供，否则返回None
        :return: BM25Retriever实例
        """
        if not user_id:
            return None

        all_docs_result = await asyncio.to_thread(
            self.vectors_store.get,
            include=['documents', 'metadatas'],
            where={'user_id': user_id}
        )
        documents = []
        for i, doc_content in enumerate(all_docs_result['documents']):
            metadata = all_docs_result['metadatas'][i] if i < len(all_docs_result['metadatas']) else {}
            documents.append(Document(page_content=doc_content, metadata=metadata))

        if documents:
            bm25_retriever = BM25Retriever.from_documents(
                documents=documents,
                k=chroma_config['k']
            )
            return bm25_retriever
        else:
            return None

    async def _get_all_documents(self) -> list[Document]:
        """
        获取向量库中的所有文档
        :return: 文档列表
        """
        all_docs = await asyncio.to_thread(
            self.vectors_store.get,
            include=['documents', 'metadatas']
        )
        documents = []
        for i, doc in enumerate(all_docs['documents']):
            metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
            documents.append(Document(page_content=doc, metadata=metadata))
        return documents

    async def get_retriever(self, query: str = None, user_id: str = None) -> BaseRetriever:
        """
        获取混合检索器（BM25 + 向量检索）
        :param query: 查询语句，用于动态调整权重
        :param user_id: 用户ID，用于过滤用户的文档，为空时不返回任何文档
        :return: EnsembleRetriever实例或单独的向量检索器
        """
        if not user_id:
            return EmptyRetriever()

        filter_dict = {'user_id': user_id}
        vector_retriever = self.vectors_store.as_retriever(
            search_type='similarity',
            search_kwargs={'k': chroma_config['k'], 'filter': filter_dict},
        )
        bm25_retriever = await self.get_bm25_retriever(user_id)

        if bm25_retriever:
            weights = await self.get_dynamic_weights(query)
            ensemble_retriever = EnsembleRetriever(
                retrievers=[vector_retriever, bm25_retriever],
                weights=weights
            )
            return ensemble_retriever
        else:
            return vector_retriever

    @staticmethod
    async def get_dynamic_weights(query: str = None):
        """
        根据查询动态调整权重
        :param query: 查询语句
        :return: 权重列表 [向量检索权重, BM25检索权重]
        """
        default_vector_weight = 0.5
        default_bm25_weight = 0.5

        if not query:
            return [default_vector_weight, default_bm25_weight]

        query_length = len(query)
        query_words = len(query.split())

        if query_length > 50:
            vector_weight = 0.7
            bm25_weight = 0.3
        elif query_length < 20:
            vector_weight = 0.3
            bm25_weight = 0.7
        else:
            vector_weight = default_vector_weight
            bm25_weight = default_bm25_weight

        if query_words > 0:
            word_density = query_words / query_length
            if word_density > 0.1:
                bm25_weight = min(bm25_weight + 0.1, 0.7)
                vector_weight = max(vector_weight - 0.1, 0.3)

        return [vector_weight, bm25_weight]
