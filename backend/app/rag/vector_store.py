import asyncio
import os
import shutil
import threading

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.logger_handler import logger
from app.utils.config import chroma_config
from app.utils.image_extractor import delete_image_directory, delete_user_all_images
from app.utils.path_tool import get_abstract_path

from .document_handler import DocumentProcessor
from .md5_manager import MD5Store
from .retrievers.hybrid_retriever import HybridRetriever


def _clear_chroma_cache():
    """
    清除 ChromaDB SharedSystemClient 内部单例缓存，避免 KeyError。
    ChromaDB 在 0.5.x+ 引入了 SharedSystemClient，它内部维护了一个全局 _instance 字典。
    当同一个进程反复创建/删除 Chroma 实例时，会抛出 KeyError（因为缓存中的 client 已被销毁）。
    在初始化前主动清除缓存，可以避免此问题。
    """
    try:
        from chromadb.api.shared_system_client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def _reset_chroma_db(persist_dir: str):
    """删除 Chroma 数据库目录（文件系统），同时清除内存中的缓存，达到完全重置的效果"""
    _clear_chroma_cache()
    if os.path.exists(persist_dir):
        shutil.rmtree(persist_dir)
        logger.info(f"已删除 Chroma 数据库目录并重置缓存: {persist_dir}")


class _LazyEmbedding(Embeddings):
    """延迟加载的嵌入模型包装器

    VectorStoreService 是单例，在后台初始化完成前就可能被创建。
    直接用 init_manager.embed_model 传入 Chroma 会得到 None，
    等 Chroma 真正调 embed_documents 时就会崩溃。
    这个包装器把模型解析推迟到 embed 调用时，确保模型已就绪。
    """

    def _get_model(self):
        from app.core.background_init import init_manager
        model = init_manager.embed_model
        if model is None:
            raise RuntimeError("嵌入模型尚未初始化完成，请稍后重试")
        return model

    def embed_documents(self, texts):
        return self._get_model().embed_documents(texts)

    def embed_query(self, text):
        return self._get_model().embed_query(text)


class VectorStoreService:
    """
    向量数据库服务（单例，线程安全初始化，自动恢复 ChromaDB 缓存冲突）。

    使用双重检查锁定（Double-Checked Locking）实现线程安全的单例模式。
    之所以需要单例，是因为 ChromaDB 客户端维护了内部的连接池和缓存，
    多个实例会导致资源冲突和不可预期的 KeyError。
    """
    _instance = None
    _initialized = False
    _init_lock = threading.Lock()

    def __new__(cls):
        # 第一重检查（无锁，性能优先）
        if cls._instance is None:
            with cls._init_lock:
                # 第二重检查（加锁后，确保线程安全）
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if VectorStoreService._initialized:
            return

        with VectorStoreService._init_lock:
            if VectorStoreService._initialized:
                return

            persist_dir = get_abstract_path(chroma_config['persist_directory'])
            # 在创建 Chroma 实例前清除缓存，避免残留的单例 client 导致 KeyError
            _clear_chroma_cache()

            try:
                self._init_chroma(persist_dir)
            except Exception as e:
                # Chroma 初始化失败时（如数据库文件损坏），自动删除并重建，
                # 实现优雅的自我修复，避免服务完全不可用
                logger.error(f"Chroma 初始化失败，即将重置数据库: {e}")
                _reset_chroma_db(persist_dir)
                self._init_chroma(persist_dir)

            VectorStoreService._initialized = True

    def _init_chroma(self, persist_dir: str):
        self.vectors_store = Chroma(
            collection_name=chroma_config['collection_name'],
            embedding_function=self._get_embed_model(),
            persist_directory=persist_dir,
        )
        self.md5_store = MD5Store()
        self.hybrid_retriever = HybridRetriever(self.vectors_store)
        self.document_processor = DocumentProcessor(self.vectors_store, self.md5_store, self._get_embed_model())

    @staticmethod
    def _get_embed_model():
        """获取嵌入模型（延迟加载包装器，模型在首次调用时解析）"""
        return _LazyEmbedding()

    async def get_bm25_retriever(self, user_id: str = None):
        return await self.hybrid_retriever.get_bm25_retriever(user_id)

    async def _get_all_documents(self) -> list[Document]:
        return await self.hybrid_retriever._get_all_documents()

    async def get_retriever(self, query: str = None, user_id: str = None):
        return await self.hybrid_retriever.get_retriever(query, user_id)

    @staticmethod
    async def get_dynamic_weights(query: str = None):
        return await HybridRetriever.get_dynamic_weights(query)

    async def check_md5_hex(self, md5_for_check: str, user_id: str = None) -> bool:
        return await self.md5_store.check_md5_hex(md5_for_check, user_id)

    async def save_md5_hex(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        await self.md5_store.save_md5_hex(md5_hex, filename, original_filename, user_id)

    def save_md5_hex_sync(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        self.md5_store.save_md5_hex_sync(md5_hex, filename, original_filename, user_id)

    async def delete_user_documents(self, user_id: str):
        """
        删除指定用户的所有文档（包括MD5记录）
        :param user_id: 用户ID
        """
        try:
            await self.delete_user_md5(user_id, delete_documents=True)
        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的文档时出错: {e}")
            raise

    async def delete_user_md5(self, user_id: str, delete_documents: bool = True):
        """
        删除指定用户的MD5记录
        :param user_id: 用户ID
        :param delete_documents: 是否同时删除向量数据库中的文档（默认True）
        """
        try:
            if delete_documents:
                await asyncio.to_thread(
                    self.vectors_store.delete,
                    where={"user_id": user_id}
                )
                logger.info(f"【向量数据库】已删除用户 {user_id} 的所有文档")

            await self.md5_store.delete_user_md5(user_id)
            # 同步清理该用户在磁盘上存储的所有 PDF 提取图片
            # 删除文档时必须连带删除对应的图片资源，否则会留下无法被引用的"脏"文件
            delete_user_all_images(user_id)
        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的MD5记录时出错: {e}")

    async def delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True):
        """
        通过文件名删除MD5记录及其对应的知识库内容
        :param user_id: 用户ID
        :param filename: 要删除的文件名
        :param delete_documents: 是否同时删除向量数据库中的对应文档（默认True）
        :return: 是否成功删除
        """
        try:
            md5_to_delete = await self.md5_store.delete_by_filename(user_id, filename)
            if md5_to_delete is None:
                logger.warning(f"【向量数据库】文件 {filename} 不存在于用户 {user_id} 的MD5记录中")
                return False

            logger.info(f"【向量数据库】已删除用户 {user_id} 的文件 {filename} 的MD5记录")

            if delete_documents:
                where_clause = {"$and": [{"user_id": user_id}, {"md5": md5_to_delete}]}
                await asyncio.to_thread(
                    self.vectors_store.delete,
                    where=where_clause
                )
                logger.info(f"【向量数据库】已删除用户 {user_id} 中文件 {filename} 对应的文档")

            # 删除该文档对应的 PDF 提取图片目录
            delete_image_directory(user_id, md5_to_delete)

            return True

        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的文件 {filename} 时出错: {e}")
            return False

    async def delete_single_md5(self, user_id: str, md5_to_delete: str, delete_documents: bool = True):
        """
        删除单个MD5记录及其对应的知识库内容
        :param user_id: 用户ID
        :param md5_to_delete: 要删除的MD5值
        :param delete_documents: 是否同时删除向量数据库中的对应文档（默认True）
        :return: 是否成功删除
        """
        try:
            success = await self.md5_store.delete_single_md5(user_id, md5_to_delete)
            if not success:
                logger.warning(f"【向量数据库】MD5记录 {md5_to_delete} 不存在")
                return False

            logger.info(f"【向量数据库】已删除用户 {user_id} 的MD5记录: {md5_to_delete}")

            if delete_documents:
                where_clause = {"$and": [{"user_id": user_id}, {"md5": md5_to_delete}]}
                await asyncio.to_thread(
                    self.vectors_store.delete,
                    where=where_clause
                )
                logger.info(f"【向量数据库】已删除用户 {user_id} 中MD5为 {md5_to_delete} 的文档")

            # 清理磁盘上该用户的 PDF 提取图片
            delete_image_directory(user_id, md5_to_delete)

            return True

        except Exception as e:
            logger.error(f"【向量数据库】删除用户 {user_id} 的MD5记录 {md5_to_delete} 时出错: {e}")
            return False

    async def get_md5_info(self, user_id: str, md5_value: str):
        """
        获取MD5对应的文档信息
        :param user_id: 用户ID
        :param md5_value: MD5值
        :return: MD5信息字典，不存在返回None
        """
        try:
            return await self.md5_store.get_md5_info(user_id, md5_value)
        except Exception as e:
            logger.error(f"【向量数据库】获取MD5信息 {md5_value} 时出错: {e}")
            return None

    async def get_all_md5_records(self, user_id: str):
        """
        获取用户的所有MD5记录
        :param user_id: 用户ID
        :return: MD5记录列表
        """
        try:
            records = await self.md5_store.get_all_md5_records(user_id)
            logger.info(f"【向量数据库】获取用户 {user_id} 的MD5记录，共 {len(records)} 条")
            return records
        except Exception as e:
            logger.error(f"【向量数据库】获取用户 {user_id} 的MD5记录时出错: {e}")
            return []

    async def get_user_documents(self, user_id: str = None):
        """
        获取用户的知识库文档列表
        :param user_id: 用户ID，如果为None则获取所有文档
        :return: 文档信息列表，包含文件名、文档数量、预览等信息
        """
        try:
            where_clause = {"user_id": user_id} if user_id else None
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause
            )

            docs_info = {}

            for i, doc_id in enumerate(all_docs['ids']):
                metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
                content = all_docs['documents'][i] if i < len(all_docs['documents']) else ""

                # 优先使用 metadata 中保存的 original_filename（用户上传时的原始文件名）
                # 因为 source 可能存的是临时文件的完整路径（如 C:\Users\...\tmp123.pdf），
                # 而 original_filename 才是用户看到的文件名
                source = metadata.get('source', metadata.get('filename', 'unknown'))
                if isinstance(source, str) and '\\' in source:
                    source = os.path.basename(source)
                filename = metadata.get('original_filename', source)

                original_filename = metadata.get('original_filename', filename)
                if filename not in docs_info:
                    docs_info[filename] = {
                        'id': doc_id,
                        'filename': filename,
                        'original_filename': original_filename,
                        'user_id': metadata.get('user_id'),
                        'chunk_count': 0,
                        'preview': "",
                        'created_at': metadata.get('created_at')
                    }

                docs_info[filename]['chunk_count'] += 1

                if not docs_info[filename]['preview'] and content:
                    preview_length = 100
                    docs_info[filename]['preview'] = content[:preview_length] + ("..." if len(content) > preview_length else "")

            result = list(docs_info.values())
            logger.info(f"【向量数据库】获取用户 {user_id} 的知识库文档，共 {len(result)} 个文件")
            return result

        except Exception as e:
            logger.error(f"【向量数据库】获取用户 {user_id} 的知识库文档时出错: {e}")
            raise

    async def get_document_detail(self, user_id: str, filename: str):
        """
        获取文档的详细内容
        :param user_id: 用户ID
        :param filename: 文件名
        :return: 文档详情信息，包含完整内容、图片列表和每段文本与图片的对应关系
        """
        try:
            where_clause = {"user_id": user_id}
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause
            )

            doc_info = None
            full_content = []
            chunk_count = 0
            all_images = set()
            doc_md5 = None
            chunks = []

            for i, doc_id in enumerate(all_docs['ids']):
                metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
                content = all_docs['documents'][i] if i < len(all_docs['documents']) else ""

                source = metadata.get('source', metadata.get('filename', ''))
                if isinstance(source, str):
                    source_name = os.path.basename(source)
                else:
                    source_name = str(source)
                original_filename = metadata.get('original_filename', '')

                # 同时匹配 source 和 original_filename，兼容不同切片方式写入的 metadata
                if source_name == filename or original_filename == filename:
                    if not doc_info:
                        doc_info = {
                            'id': doc_id,
                            'filename': filename,
                            'user_id': metadata.get('user_id'),
                            'chunk_count': 0,
                            'content': "",
                            'images': [],
                            'md5': metadata.get('md5'),
                            'created_at': metadata.get('created_at')
                        }
                        doc_md5 = metadata.get('md5')
                    chunk_count += 1
                    full_content.append(content)

                    # 从 metadata 中取出该 chunk 关联的图片文件名列表，
                    # 拼接成可供前端直接请求的 URL 路径（由 knowledge_router 中的图片路由处理）
                    image_paths = metadata.get('image_paths', [])
                    chunk_images = []
                    if isinstance(image_paths, list):
                        for img_name in image_paths:
                            img_url = f"/knowledge/image/{doc_md5}/{img_name}"
                            all_images.add(img_url)
                            chunk_images.append(img_url)

                    chunks.append({
                        'chunk_id': doc_id,
                        'index': len(chunks),
                        'content': content,
                        'page': metadata.get('page'),
                        'images': chunk_images,
                    })

            if doc_info:
                doc_info['chunk_count'] = chunk_count
                doc_info['content'] = '\n'.join(full_content)
                doc_info['images'] = sorted(all_images)
                doc_info['chunks'] = chunks

            logger.info(f"【向量数据库】获取文档详情: {filename}，chunk数量: {chunk_count}，图片数量: {len(all_images)}")
            return doc_info

        except Exception as e:
            logger.error(f"【向量数据库】获取文档详情 {filename} 时出错: {e}")
            raise

    async def get_document_chunks(self, user_id: str, filename: str):
        """
        获取文档的所有切片信息
        :param user_id: 用户ID
        :param filename: 文件名
        :return: 切片列表信息，包含图片列表
        """
        try:
            where_clause = {"user_id": user_id}
            all_docs = await asyncio.to_thread(
                self.vectors_store.get,
                include=['documents', 'metadatas'],
                where=where_clause
            )

            chunks = []
            chunk_index = 0

            for i, doc_id in enumerate(all_docs['ids']):
                metadata = all_docs['metadatas'][i] if i < len(all_docs['metadatas']) else {}
                content = all_docs['documents'][i] if i < len(all_docs['documents']) else ""

                source = metadata.get('source', metadata.get('filename', ''))
                if isinstance(source, str):
                    source_name = os.path.basename(source)
                else:
                    source_name = str(source)
                original_filename = metadata.get('original_filename', '')

                if source_name == filename or original_filename == filename:
                    doc_md5 = metadata.get('md5', '')
                    # 解析图片路径：从 metadata 中拿到图片文件名列表，拼接为前端可用的API URL
                    image_paths = metadata.get('image_paths', [])
                    if isinstance(image_paths, list):
                        images = [f"/knowledge/image/{doc_md5}/{img}" for img in image_paths]
                    else:
                        images = []

                    chunks.append({
                        'chunk_id': doc_id,
                        'index': chunk_index,
                        'content': content,
                        'metadata': metadata,
                        'images': images,
                    })
                    chunk_index += 1

            result = {
                'filename': filename,
                'total_chunks': len(chunks),
                'chunks': chunks
            }

            logger.info(f"【向量数据库】获取文档切片: {filename}，共 {len(chunks)} 个切片")
            return result

        except Exception as e:
            logger.error(f"【向量数据库】获取文档切片 {filename} 时出错: {e}")
            raise

    # 以下方法将参数透传给 DocumentProcessor，使其能获取 md5 和 user_id 用于多模态PDF加载
    async def get_file_document(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        return await self.document_processor.get_file_document(read_path, md5, user_id)

    def get_file_document_sync(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        return self.document_processor.get_file_document_sync(read_path, md5, user_id)

    def split_documents_sync(self, documents: list[Document]) -> list[Document]:
        return self.document_processor.split_documents_sync(documents)

    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None):
        await self.document_processor.get_document(files, user_id, progress_callback)


if __name__ == '__main__':
    async def main():
        store = VectorStoreService()
        await store.get_document()

        retriever = await store.get_retriever()
        results = await retriever.ainvoke('扫地')
        print(f"检索结果数量: {len(results)}")
        for result in results:
            print(result)

    asyncio.run(main())
