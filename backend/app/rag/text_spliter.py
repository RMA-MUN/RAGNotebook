import asyncio
import math
from typing import Any

from langchain.embeddings.base import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.utils.config import chroma_config


class AsyncTextSplitter:
    """
    异步文本分割器类

    核心功能：
    - 将文本分割成多个片段
    - 保留标题、段落、列表层级等结构
    - 保证片段语义完整，避免把一个观点拆成多段
    - 使用嵌入模型结合余弦相似度判断语义完整性
    """

    def __init__(self,
                 chunk_size: int = 1000,
                 chunk_overlap: int = 200,
                 separators: list[str] | None = None,
                 embedding_model: Embeddings | None = None):
        """
        初始化文本分割器

        Args:
            chunk_size: 每个文本片段的最大长度
            chunk_overlap: 片段之间的重叠长度
            separators: 分割符列表，用于分割文本
            embedding_model: 嵌入模型，用于计算语义相似度
        """
        # 默认分割符，按优先级排序
        default_separators = chroma_config['separators']

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or default_separators
        self.embedding_model = embedding_model

        # 初始化递归字符分割器
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=self.separators
        )

    async def split_text(self, text: str) -> list[str]:
        """
        分割文本成多个片段

        Args:
            text: 要分割的文本

        Returns:
            List[str]: 分割后的文本片段列表
        """
        # 使用递归字符分割器分割文本（同步操作，用to_thread包装）
        chunks = await asyncio.to_thread(self.splitter.split_text, text)

        # 如果提供了嵌入模型，进一步优化分割结果
        if self.embedding_model:
            chunks = await self._optimize_chunks(chunks)

        return chunks

    async def split_documents(self, documents: list[Any]) -> list[Any]:
        """
        分割文档列表

        Args:
            documents: 文档对象列表

        Returns:
            List[Any]: 分割后的文档对象列表
        """
        # 使用递归字符分割器分割文档（同步操作，用to_thread包装）
        split_docs = await asyncio.to_thread(self.splitter.split_documents, documents)
        return split_docs

    def split_documents_sync(self, documents: list[Any]) -> list[Any]:
        """
        同步分割文档列表（用于多线程场景）

        Args:
            documents: 文档对象列表

        Returns:
            List[Any]: 分割后的文档对象列表
        """
        return self.splitter.split_documents(documents)

    def split_text_sync(self, text: str) -> list[str]:
        """
        同步分割文本（用于多线程场景）

        Args:
            text: 要分割的文本

        Returns:
            List[str]: 分割后的文本片段列表
        """
        chunks = self.splitter.split_text(text)

        if self.embedding_model:
            chunks = self._optimize_chunks_sync(chunks)

        return chunks

    def _optimize_chunks_sync(self, chunks: list[str]) -> list[str]:
        """
        同步优化分割结果

        Args:
            chunks: 初步分割的文本片段列表

        Returns:
            List[str]: 优化后的文本片段列表
        """
        optimized_chunks = []
        current_chunk = chunks[0]

        for i in range(1, len(chunks)):
            similarity = self._calculate_similarity_sync(current_chunk, chunks[i])

            if similarity > 0.7:
                current_chunk += " " + chunks[i]
            else:
                optimized_chunks.append(current_chunk)
                current_chunk = chunks[i]

        optimized_chunks.append(current_chunk)
        return optimized_chunks

    def _calculate_similarity_sync(self, text1: str, text2: str) -> float:
        """
        同步计算两个文本片段的语义相似度

        Args:
            text1: 第一个文本片段
            text2: 第二个文本片段

        Returns:
            float: 两个文本片段的相似度，范围0-1
        """
        if not self.embedding_model:
            return 0.0

        embedding1 = self.embedding_model.embed_query(text1)
        embedding2 = self.embedding_model.embed_query(text2)

        return self._cosine_similarity(embedding1, embedding2)

    async def _optimize_chunks(self, chunks: list[str]) -> list[str]:
        """
        使用嵌入模型优化分割结果，确保语义完整性

        Args:
            chunks: 初步分割的文本片段列表

        Returns:
            List[str]: 优化后的文本片段列表
        """
        optimized_chunks = []
        current_chunk = chunks[0]

        for i in range(1, len(chunks)):
            # 计算当前片段与下一个片段的语义相似度
            similarity = await self._calculate_similarity(current_chunk, chunks[i])

            # 如果相似度高于阈值，合并两个片段
            if similarity > 0.7:  # 相似度阈值
                current_chunk += " " + chunks[i]
            else:
                optimized_chunks.append(current_chunk)
                current_chunk = chunks[i]

        # 添加最后一个片段
        optimized_chunks.append(current_chunk)

        return optimized_chunks

    async def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两个文本片段的语义相似度

        Args:
            text1: 第一个文本片段
            text2: 第二个文本片段

        Returns:
            float: 两个文本片段的相似度，范围0-1
        """
        if not self.embedding_model:
            return 0.0

        embedding1 = self.embedding_model.embed_query(text1)
        embedding2 = self.embedding_model.embed_query(text2)

        # 计算余弦相似度
        similarity = self._cosine_similarity(embedding1, embedding2)

        return similarity

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """
        计算两个向量的余弦相似度

        Args:
            vec1: 第一个向量
            vec2: 第二个向量

        Returns:
            float: 两个向量的余弦相似度，范围0-1
        """

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(a * a for a in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)
