"""知识库文档服务（文档列表/详情/切片/MD5/上传管线）。

- chunk/向量统一存 Neo4j（(:Chunk) 节点，由图谱构建 worker 写入，见 graph_service）
- 本服务保留：文档列表/详情/切片查询（读 Neo4j）、md5 记录（上传去重）、文档删除联动
- 文档加载与切片（DocumentProcessor）保留：上传管线用它产出任务载荷
"""
import asyncio
import threading

from langchain_core.documents import Document

from app.core.logger_handler import logger
from app.utils.image_extractor import delete_image_directory, delete_user_all_images

from .document_handler import DocumentProcessor
from .md5_manager import MD5Store


class VectorStoreService:
    """知识库文档服务（单例，线程安全初始化）。"""
    _instance = None
    _initialized = False
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if VectorStoreService._initialized:
            return

        with VectorStoreService._init_lock:
            if VectorStoreService._initialized:
                return

            self.md5_store = MD5Store()
            self.document_processor = DocumentProcessor(self.md5_store)
            VectorStoreService._initialized = True

    # ---- 文档列表/详情/切片（读 Neo4j） ----
    @staticmethod
    def _neo4j_driver():
        # 独立访问点：测试可替换
        from app.graph.storage.neo4j_client import get_neo4j_driver

        return get_neo4j_driver()

    @classmethod
    async def _query_neo4j(cls, query: str, params: dict | None = None):
        """执行 Neo4j 查询并取回 records（本服务读文档数据的统一入口）。"""
        driver = cls._neo4j_driver()
        result = await driver.execute_query(query, params or {})
        return list(result.records)

    @staticmethod
    def _chunk_image_urls(md5: str, image_paths) -> list[str]:
        """chunk 图片相对路径 → 前端可访问的 /knowledge/image URL 列表。"""
        if isinstance(image_paths, list) and image_paths:
            return [f"/knowledge/image/{md5}/{img}" for img in image_paths]
        return []

    async def get_user_documents(self, user_id: str = None):
        """获取用户的知识库文档列表（MySQL graph_docs 注册行 ∪ Neo4j Doc/Chunk 聚合）。

        graph_docs 在上传入队时即登记，是文档存在性的即时基准——新上传的文档无需等待
        图谱抽取完成即可出现在列表中；chunk 数与预览来自 Neo4j，抽取未完成时为 0。
        created_at 优先取注册行的真实上传时间。
        """
        # 1. MySQL 注册行（上传即写入）
        registered: dict[str, dict] = {}
        try:
            from sqlalchemy import select

            from app.db.db_config import AsyncSessionLocal
            from app.models.graph import GraphDoc

            async with AsyncSessionLocal() as db:
                stmt = select(GraphDoc.id, GraphDoc.filename, GraphDoc.created_at)
                if user_id is not None:
                    stmt = stmt.where(GraphDoc.user_id == user_id)
                rows = (await db.execute(stmt.order_by(GraphDoc.created_at))).all()
            for rid, filename, created_at in rows:
                registered[rid] = {"filename": filename, "created_at": created_at}
        except Exception as e:
            logger.error(f"【知识库】读取文档注册行失败: {e}")

        # 2. Neo4j Doc/Chunk 聚合（抽取完成后才有；失败不阻塞注册行展示）
        try:
            records = await self._query_neo4j(
                "MATCH (d:Doc) WHERE $uid IS NULL OR d.user_id = $uid "
                "OPTIONAL MATCH (c:Chunk {kind: 'doc'}) "
                "WHERE c.source_id = d.id AND c.user_id = d.user_id "
                "WITH d, count(c) AS chunk_count, head(collect(c.text)) AS first_text "
                "RETURN d.id AS id, d.filename AS filename, d.user_id AS user_id, "
                "chunk_count, first_text",
                {"uid": user_id},
            )
        except Exception as e:
            logger.error(f"【知识库】获取用户文档列表失败: {e}")
            records = []

        preview_length = 100

        def _entry(doc_id: str, filename: str, user, chunk_count: int, preview: str, created_at) -> dict:
            return {
                "id": doc_id,
                "filename": filename,
                "original_filename": filename,
                "user_id": user,
                "chunk_count": chunk_count,
                "preview": (preview or "")[:preview_length] + ("..." if len(preview or "") > preview_length else ""),
                "created_at": created_at.isoformat() if created_at else None,
            }

        docs_info: list[dict] = []
        seen_ids: set[str] = set()
        for row in records:
            doc_id = row.get("id")
            seen_ids.add(doc_id)
            reg = registered.get(doc_id, {})
            docs_info.append(_entry(
                doc_id, reg.get("filename") or row.get("filename") or "unknown",
                row.get("user_id"), row.get("chunk_count") or 0,
                row.get("first_text") or "", reg.get("created_at")))
        # 注册了但尚未抽取完成的文档（Neo4j 还没有 Doc 节点）
        for rid, meta in registered.items():
            if rid in seen_ids:
                continue
            docs_info.append(_entry(rid, meta["filename"], user_id, 0, "", meta["created_at"]))

        docs_info.sort(key=lambda d: (d["created_at"] is None, d["created_at"] or ""))
        logger.info(f"【知识库】获取用户 {user_id} 的知识库文档，共 {len(docs_info)} 个文件")
        return docs_info

    async def get_document_detail(self, user_id: str, filename: str):
        """获取文档详情：完整内容、图片列表与切片明细（Neo4j）。"""
        try:
            records = await self._query_neo4j(
                "MATCH (d:Doc) WHERE d.user_id = $uid AND d.filename = $filename "
                "OPTIONAL MATCH (c:Chunk {kind: 'doc'}) "
                "WHERE c.source_id = d.id AND c.user_id = d.user_id "
                "RETURN d, c ORDER BY c.chunk_index",
                {"uid": user_id, "filename": filename},
            )
        except Exception as e:
            logger.error(f"【知识库】获取文档详情失败 {filename}: {e}")
            raise

        if not records:
            return None

        doc_node = records[0]["d"]
        doc_md5 = doc_node["id"]
        chunks = []
        all_images: set[str] = set()
        full_content: list[str] = []
        for index, row in enumerate(records):
            chunk = row["c"]
            if chunk is None:
                continue
            text = chunk.get("text") or ""
            full_content.append(text)
            chunk_images = self._chunk_image_urls(doc_md5, chunk.get("image_paths"))
            all_images.update(chunk_images)
            chunks.append({
                "chunk_id": chunk["id"],
                "index": index,
                "content": text,
                "page": chunk.get("page"),
                "images": chunk_images,
            })

        return {
            "id": doc_md5,
            "filename": filename,
            "user_id": doc_node.get("user_id"),
            "chunk_count": len(chunks),
            "content": "\n".join(full_content),
            "images": sorted(all_images),
            "md5": doc_md5,
            "created_at": None,
            "chunks": chunks,
        }

    async def get_document_chunks(self, user_id: str, filename: str):
        """获取文档的所有切片信息（Neo4j）。"""
        try:
            records = await self._query_neo4j(
                "MATCH (d:Doc) WHERE d.user_id = $uid AND d.filename = $filename "
                "OPTIONAL MATCH (c:Chunk {kind: 'doc'}) "
                "WHERE c.source_id = d.id AND c.user_id = d.user_id "
                "RETURN d, c ORDER BY c.chunk_index",
                {"uid": user_id, "filename": filename},
            )
        except Exception as e:
            logger.error(f"【知识库】获取文档切片失败 {filename}: {e}")
            raise

        chunks = []
        for index, row in enumerate(records):
            chunk = row["c"]
            if chunk is None:
                continue
            doc_md5 = row["d"]["id"]
            chunks.append({
                "chunk_id": chunk["id"],
                "index": index,
                "content": chunk.get("text") or "",
                "metadata": {"md5": doc_md5, "page": chunk.get("page"),
                             "user_id": row["d"].get("user_id")},
                "images": self._chunk_image_urls(doc_md5, chunk.get("image_paths")),
            })

        return {
            "filename": filename,
            "total_chunks": len(chunks),
            "chunks": chunks,
        }

    # ---- 删除（md5 记录 + 图片 + Neo4j 图谱联动） ----
    async def delete_user_documents(self, user_id: str):
        """删除指定用户的所有文档（包括MD5记录）"""
        try:
            await self.delete_user_md5(user_id, delete_documents=True)
        except Exception as e:
            logger.error(f"【知识库】删除用户 {user_id} 的文档时出错: {e}")
            raise

    async def delete_user_md5(self, user_id: str, delete_documents: bool = True):
        """删除指定用户的MD5记录（delete_documents 时联动清理 Neo4j 文档图谱）。"""
        try:
            if delete_documents:
                logger.info(f"【知识库】删除用户 {user_id} 的所有文档")
                await self.md5_store.delete_user_md5(user_id)
                # 同步清理该用户在磁盘上存储的所有 PDF 提取图片
                delete_user_all_images(user_id)
                # 图谱联动：清空该用户的文档节点/Chunk/关联/抽取日志（失败不影响删除主流程）
                try:
                    from app.graph.services.graph_service import cleanup_all_docs_graph
                    await cleanup_all_docs_graph(user_id)
                except Exception as e:
                    logger.error(f"【图谱】清空用户 {user_id} 文档图谱数据失败: {e}")
        except Exception as e:
            logger.error(f"【知识库】删除用户 {user_id} 的MD5记录时出错: {e}")

    async def delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True):
        """通过文件名删除MD5记录及其对应的知识库内容（Neo4j 图谱联动）。"""
        try:
            md5_to_delete = await self.md5_store.delete_by_filename(user_id, filename)
            if md5_to_delete is None:
                logger.warning(f"【知识库】文件 {filename} 不存在于用户 {user_id} 的MD5记录中")
                # 兜底：md5 记录缺失（历史残留/记录文件丢失）时仍按文件名清理图谱，防残留
                if delete_documents:
                    try:
                        from app.graph.services.graph_service import cleanup_doc_graph_by_filename
                        await cleanup_doc_graph_by_filename(user_id, filename)
                    except Exception as e:
                        logger.error(f"【图谱】按文件名清理文档图谱失败 {filename}: {e}")
                return False

            logger.info(f"【知识库】已删除用户 {user_id} 的文件 {filename} 的MD5记录")

            if delete_documents:
                # 删除该文档对应的 PDF 提取图片目录
                delete_image_directory(user_id, md5_to_delete)
                # 图谱联动：清理该文档的节点/Chunk/关联/抽取日志（失败不影响删除主流程）
                try:
                    from app.graph.services.graph_service import cleanup_doc_graph
                    await cleanup_doc_graph(user_id, md5_to_delete)
                except Exception as e:
                    logger.error(f"【图谱】清理文档 {filename} 图谱数据失败: {e}")

            return True

        except Exception as e:
            logger.error(f"【知识库】删除用户 {user_id} 的文件 {filename} 时出错: {e}")
            return False

    async def delete_single_md5(self, user_id: str, md5_to_delete: str, delete_documents: bool = True):
        """删除单个MD5记录及其对应的知识库内容（Neo4j 图谱联动）。"""
        try:
            success = await self.md5_store.delete_single_md5(user_id, md5_to_delete)
            if not success:
                logger.warning(f"【知识库】MD5记录 {md5_to_delete} 不存在")
                # 兜底：md5 记录缺失但图谱可能仍有该文档 → 仍清理，防残留
                if delete_documents:
                    try:
                        from app.graph.services.graph_service import cleanup_doc_graph
                        await cleanup_doc_graph(user_id, md5_to_delete)
                    except Exception as e:
                        logger.error(f"【图谱】清理文档 {md5_to_delete} 图谱数据失败: {e}")
                return False

            logger.info(f"【知识库】已删除用户 {user_id} 的MD5记录: {md5_to_delete}")

            if delete_documents:
                # 清理磁盘上该用户的 PDF 提取图片
                delete_image_directory(user_id, md5_to_delete)
                # 图谱联动：清理该文档的节点/Chunk/关联/抽取日志（失败不影响删除主流程）
                try:
                    from app.graph.services.graph_service import cleanup_doc_graph
                    await cleanup_doc_graph(user_id, md5_to_delete)
                except Exception as e:
                    logger.error(f"【图谱】清理文档 {md5_to_delete} 图谱数据失败: {e}")

            return True

        except Exception as e:
            logger.error(f"【知识库】删除用户 {user_id} 的MD5记录 {md5_to_delete} 时出错: {e}")
            return False

    # ---- MD5 记录（上传去重） ----
    async def check_md5_hex(self, md5_for_check: str, user_id: str = None) -> bool:
        return await self.md5_store.check_md5_hex(md5_for_check, user_id)

    async def save_md5_hex(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        await self.md5_store.save_md5_hex(md5_hex, filename, original_filename, user_id)

    def save_md5_hex_sync(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        self.md5_store.save_md5_hex_sync(md5_hex, filename, original_filename, user_id)

    async def get_md5_info(self, user_id: str, md5_value: str):
        """获取MD5对应的文档信息，不存在返回None。"""
        try:
            return await self.md5_store.get_md5_info(user_id, md5_value)
        except Exception as e:
            logger.error(f"【知识库】获取MD5信息 {md5_value} 时出错: {e}")
            return None

    async def get_all_md5_records(self, user_id: str):
        """获取用户的所有MD5记录。"""
        try:
            records = await self.md5_store.get_all_md5_records(user_id)
            logger.info(f"【知识库】获取用户 {user_id} 的MD5记录，共 {len(records)} 条")
            return records
        except Exception as e:
            logger.error(f"【知识库】获取用户 {user_id} 的MD5记录时出错: {e}")
            return []

    # ---- 文档加载与切片（上传管线） ----
    async def get_file_document(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        return await self.document_processor.get_file_document(read_path, md5, user_id)

    def get_file_document_sync(self, read_path: str, md5: str = None, user_id: str = None) -> list[Document]:
        return self.document_processor.get_file_document_sync(read_path, md5, user_id)

    def split_documents_sync(self, documents: list[Document]) -> list[Document]:
        return self.document_processor.split_documents_sync(documents)

    async def get_document(self, files: list = None, user_id: str = None, progress_callback=None):
        """上传管线：加载 → 切片 → 保存 md5 → 入队图谱构建任务（chunk/向量由 worker 写 Neo4j）。"""
        await self.document_processor.get_document(files, user_id, progress_callback)


if __name__ == '__main__':
    async def main():
        store = VectorStoreService()
        docs = await store.get_user_documents()
        logger.debug(f"知识库文档数量: {len(docs)}")

    asyncio.run(main())