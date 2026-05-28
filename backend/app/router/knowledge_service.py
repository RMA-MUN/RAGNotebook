import asyncio
import base64
import os
import tempfile
import time
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import magic
from fastapi import HTTPException, UploadFile

from app.core.logger_handler import logger
from app.rag.sse_models import SliceResult, SSEEvent
from app.rag.task_queue import TaskQueue
from app.rag.vector_store import VectorStoreService
from app.utils.file_handler import get_file_md5_hex_sync

ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.md', '.pptx', '.docx'}
ALLOWED_MIME_TYPES = {
    'application/pdf', 'text/plain', 'text/markdown',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
}
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_FOLDER_SIZE = 200 * 1024 * 1024


@dataclass
class ProcessingState:
    total_files: int = 0
    total_valid: int = 0
    sliced_count: int = 0
    written_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    slice_success_count: int = 0

    def current_progress(self) -> int:
        if self.total_valid == 0:
            return 0
        slice_progress = (self.sliced_count / self.total_valid) * 60
        write_progress = (self.written_count / self.total_valid) * 40
        return int(min(99, slice_progress + write_progress))


def _sync_slice_file(file_content: bytes, filename: str, file_index: int, user_id: str, queue: TaskQueue):
    """在 ThreadPoolExecutor 中执行的同步切片函数"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name

        try:
            # 在加载文档之前计算 md5，因为多模态PDF加载器需要 md5 来确定图片的存储路径。
            # 如果后移（等切片完再算），多模态加载器就无法将图片保存到正确的位置。
            md5_hex = get_file_md5_hex_sync(temp_file_path)
            store = VectorStoreService()
            documents = store.get_file_document_sync(temp_file_path, md5=md5_hex, user_id=user_id)
            if not documents:
                queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error="文件加载为空"))
                return

            split_docs = store.split_documents_sync(documents)
            if not split_docs:
                queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error="切片结果为空"))
                return

            for doc in split_docs:
                doc.metadata['user_id'] = user_id
                doc.metadata['original_filename'] = filename
                doc.metadata['md5'] = md5_hex

            queue.put(SliceResult.success_result(
                file_index=file_index, filename=filename, documents=split_docs, md5=md5_hex
            ))
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
    except Exception as e:
        logger.error(f"【SSE上传】切片文件 {filename} 时出错: {e}")
        queue.put(SliceResult.error_result(file_index=file_index, filename=filename, error=str(e)))


class KnowledgeService:
    """知识库管理服务"""

    async def handle_add_vector_single(self, file: UploadFile, user_id: str) -> str:
        """处理添加单个向量逻辑"""
        store = VectorStoreService()

        if file.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="文件大小不能超过20MB")

        content = await file.read()
        await file.seek(0)

        mime = magic.Magic(mime=True)
        file_type = mime.from_buffer(content)

        file_extension = os.path.splitext(file.filename)[1].lower()

        if file_type not in ALLOWED_MIME_TYPES and file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"文件类型不支持，目前支持PDF、TXT、Markdown、PPTX、DOCX文件类型。检测到的文件类型: {file_type}，扩展名: {file_extension}"
            )

        await store.get_document(files=[file], user_id=user_id)
        return file.filename

    async def handle_add_vector_multiple(self, files: list[UploadFile], user_id: str) -> list[str]:
        """处理添加多个向量逻辑"""
        total_size = 0
        for file in files:
            total_size += file.size or 0

        if total_size > MAX_FOLDER_SIZE:
            raise HTTPException(status_code=400, detail="文件总大小不能超过200MB")

        start_time = time.time()
        results = []
        for file in files:
            try:
                await self.handle_add_vector_single(file, user_id)
                results.append(file.filename)
            except Exception as e:
                logger.error(f"【添加向量】处理文件 {file.filename} 时出错: {e}")
                raise

        end_time = time.time()
        logger.info(f"【添加向量】耗时: {end_time - start_time:.2f}秒，处理文件数: {len(results)}")

        return results

    def _yield_start_event(self, total_files: int) -> str:
        """SSE 事件：开始处理，通知前端文件总数"""
        return SSEEvent(
            event_type='start', total_files=total_files, message='开始处理文件...', progress=0
        ).to_sse()

    def _yield_size_error_event(self) -> str:
        """SSE 事件：文件总大小超限错误"""
        return SSEEvent(
            event_type='error', message='文件总大小不能超过200MB',
            error_message='文件总大小不能超过200MB'
        ).to_sse()

    def _yield_validation_error_event(
        self, current_index: int, total_files: int, filename: str,
        file_type: str, file_extension: str, failed_count: int
    ) -> str:
        """SSE 事件：单个文件 MIME 类型验证失败"""
        return SSEEvent(
            event_type='error', file_index=current_index, total_files=total_files,
            filename=filename, step='validation',
            message=f'文件 {filename} 类型不支持',
            error_message=f'文件类型: {file_type}，扩展名: {file_extension}',
            progress=int(current_index / total_files * 100),
            failed_count=failed_count
        ).to_sse()

    def _yield_slicing_completed_event(self, result: SliceResult, state: ProcessingState) -> str:
        """SSE 事件：单个文件多线程切片完成，准备写入向量库"""
        return SSEEvent(
            event_type='slicing_completed', file_index=result.file_index,
            total_files=state.total_files, filename=result.filename,
            chunk_count=result.chunk_count, step='slicing',
            message=f'文件 {result.filename} 切片完成，共 {result.chunk_count} 个切片',
            progress=state.current_progress(),
            success_count=state.success_count, failed_count=state.failed_count,
            slice_success_count=state.slice_success_count
        ).to_sse()

    def _yield_writing_event(self, result: SliceResult, state: ProcessingState) -> str:
        """SSE 事件：开始将切片结果写入向量数据库"""
        return SSEEvent(
            event_type='writing', file_index=result.file_index,
            total_files=state.total_files, filename=result.filename,
            step='writing', message=f'正在写入向量 {result.filename}...',
            progress=state.current_progress(),
            success_count=state.success_count, failed_count=state.failed_count,
            slice_success_count=state.slice_success_count
        ).to_sse()

    def _yield_completed_event(self, result: SliceResult, state: ProcessingState) -> str:
        """SSE 事件：单个文件全部处理完成（切片+写入成功）"""
        return SSEEvent(
            event_type='completed', file_index=result.file_index,
            total_files=state.total_files, filename=result.filename,
            step='completed', message=f'文件 {result.filename} 处理完成',
            progress=state.current_progress(),
            success_count=state.success_count, failed_count=state.failed_count,
            slice_success_count=state.slice_success_count
        ).to_sse()

    def _yield_write_error_event(self, result: SliceResult, state: ProcessingState, error: str) -> str:
        """SSE 事件：切片结果写入向量数据库时发生异常"""
        return SSEEvent(
            event_type='error', file_index=result.file_index,
            total_files=state.total_files, filename=result.filename,
            step='writing', message=f'文件 {result.filename} 写入失败',
            error_message=error,
            progress=state.current_progress(),
            success_count=state.success_count, failed_count=state.failed_count,
            slice_success_count=state.slice_success_count
        ).to_sse()

    def _yield_slice_error_event(self, result: SliceResult, state: ProcessingState) -> str:
        """SSE 事件：单个文件切片阶段失败（文件损坏/格式不支持等）"""
        return SSEEvent(
            event_type='error', file_index=result.file_index,
            total_files=state.total_files, filename=result.filename,
            step='slicing', message=f'文件 {result.filename} 切片失败',
            error_message=result.error,
            progress=state.current_progress(),
            success_count=state.success_count, failed_count=state.failed_count,
            slice_success_count=state.slice_success_count
        ).to_sse()

    def _yield_finish_event(self, start_time: float, total_files: int, success_count: int, failed_count: int) -> str:
        """SSE 事件：所有文件处理结束，汇总统计信息"""
        total_time = round(time.time() - start_time, 2)
        return SSEEvent(
            event_type='finish', total_files=total_files,
            success_count=success_count, failed_count=failed_count,
            message=f'处理完成，耗时 {total_time} 秒', progress=100
        ).to_sse()

    async def _validate_and_read_files(
        self, files: list[UploadFile]
    ) -> tuple[list[dict], list[str], int]:
        """
        阶段1: 读取文件内容并验证总大小
        阶段2: 逐一验证文件 MIME 类型
        返回 (有效文件列表, SSE错误事件列表, 总文件数)
        """
        total_files = len(files)
        total_size = 0
        files_content = []
        error_events: list[str] = []

        for file in files:
            content = await file.read()
            files_content.append({'file': file, 'content': content})
            total_size += len(content)
            await file.seek(0)

        if total_size > MAX_FOLDER_SIZE:
            logger.error(f"【SSE上传】文件总大小超过限制，总大小: {total_size / (1024 * 1024):.2f}MB，限制: 200MB")
            return [], [self._yield_size_error_event()], total_files

        mime = magic.Magic(mime=True)
        valid_files = []
        current_index = 1
        failed_count = 0

        for file_info in files_content:
            file = file_info['file']
            content = file_info['content']
            file_type = mime.from_buffer(content)
            file_extension = os.path.splitext(file.filename)[1].lower()

            if file_type not in ALLOWED_MIME_TYPES and file_extension not in ALLOWED_EXTENSIONS:
                failed_count += 1
                error_events.append(self._yield_validation_error_event(
                    current_index, total_files, file.filename,
                    file_type, file_extension, failed_count
                ))
                logger.warning(f"【SSE上传】文件类型验证失败: {file.filename}，检测到类型: {file_type}，扩展名: {file_extension}")
            else:
                valid_files.append({
                    'content': content,
                    'filename': file.filename,
                    'file_index': current_index
                })
                logger.debug(f"【SSE上传】文件类型验证通过: {file.filename}")
            current_index += 1

        return valid_files, error_events, total_files

    def _start_slicing(
        self, valid_files: list[dict], user_id: str
    ) -> tuple[TaskQueue, ThreadPoolExecutor, list]:
        """启动多线程切片，返回 (队列, 执行器, future列表)"""
        queue = TaskQueue(maxsize=10)
        queue.set_total_count(len(valid_files))

        slice_tasks = [
            (info['content'], info['filename'], info['file_index'], user_id)
            for info in valid_files
        ]

        max_workers = min(len(slice_tasks), max(1, os.cpu_count() or 1))
        logger.info(f"【SSE上传】切片阶段使用 {max_workers} 个线程")

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = [executor.submit(_sync_slice_file, *args, queue) for args in slice_tasks]

        return queue, executor, futures

    async def _process_slice_results(
        self, queue: TaskQueue, valid_count: int, store: VectorStoreService,
        state: ProcessingState, user_id: str
    ) -> AsyncGenerator[str, None]:
        """消费切片队列 → 写入向量库 → yield SSE 进度事件"""
        while state.written_count < valid_count:
            try:
                result = queue.get(block=True, timeout=0.1)

                state.sliced_count += 1

                if result.success:
                    state.slice_success_count += 1

                    yield self._yield_slicing_completed_event(result, state)

                    try:
                        yield self._yield_writing_event(result, state)

                        await asyncio.to_thread(store.vectors_store.add_documents, result.documents)
                        await store.save_md5_hex(result.md5, result.filename, result.filename, user_id)

                        state.success_count += 1
                        state.written_count += 1

                        yield self._yield_completed_event(result, state)
                        logger.info(f"【SSE上传】文件 {result.filename} 写入完成")

                    except Exception as e:
                        state.written_count += 1
                        state.failed_count += 1
                        logger.error(f"【SSE上传】写入文件 {result.filename} 时出错: {e}")
                        yield self._yield_write_error_event(result, state, str(e))

                else:
                    state.written_count += 1
                    state.failed_count += 1
                    logger.error(f"【SSE上传】切片文件 {result.filename} 失败: {result.error}")
                    yield self._yield_slice_error_event(result, state)

                queue.task_done()

            except Exception:
                continue

    async def handle_add_vector_multiple_stream(
        self,
        files: list[UploadFile],
        user_id: str
    ) -> AsyncGenerator[str, None]:
        """
        处理多个文件上传并返回流式进度（多线程切片 + 单线程串行写入）
        """
        total_files = len(files)
        logger.info(f"【SSE上传】开始处理文件上传，文件数量: {total_files}，用户ID: {user_id}")

        yield self._yield_start_event(total_files)

        # 文件验证
        valid_files, error_events, _ = await self._validate_and_read_files(files)
        for event in error_events:
            yield event

        if not valid_files:
            logger.info("【SSE上传】无有效文件可处理")
            return

        start_time = time.time()
        state = ProcessingState(
            total_files=total_files,
            total_valid=len(valid_files)
        )

        # 多线程切片
        queue, executor, _ = self._start_slicing(valid_files, user_id)

        # 串行消费 + 写入
        store = VectorStoreService()
        async for sse in self._process_slice_results(queue, len(valid_files), store, state, user_id):
            yield sse

        executor.shutdown(wait=True)

        logger.info(
            f"【SSE上传】文件处理完成，总数: {total_files}，"
            f"成功: {state.success_count}，失败: {state.failed_count}，"
            f"耗时: {round(time.time() - start_time, 2)}秒"
        )

        yield self._yield_finish_event(start_time, total_files, state.success_count, state.failed_count)

    def _calculate_progress(self, sliced_count: int, written_count: int, total: int) -> int:
        if total == 0:
            return 0
        slice_progress = (sliced_count / total) * 60
        write_progress = (written_count / total) * 40
        return int(min(99, slice_progress + write_progress))

    async def clean_user_upload(self, user_id: str) -> None:
        """处理删除用户上传的所有向量逻辑"""
        store = VectorStoreService()
        await store.delete_user_documents(user_id)

    async def handle_clear_user_md5(self, user_id: str, delete_documents: bool = True) -> None:
        store = VectorStoreService()
        await store.delete_user_md5(user_id, delete_documents)
        if delete_documents:
            logger.info(f"【知识库】清空用户 {user_id} 的MD5记录和文档")
        else:
            logger.info(f"【知识库】清空用户 {user_id} 的MD5记录（保留知识库文档）")

    async def handle_delete_single_md5(self, user_id: str, md5_value: str, delete_documents: bool = True) -> bool:
        store = VectorStoreService()
        success = await store.delete_single_md5(user_id, md5_value, delete_documents)
        if success:
            logger.info(f"【知识库】删除用户 {user_id} 的MD5记录: {md5_value}")
        else:
            logger.warning(f"【知识库】删除用户 {user_id} 的MD5记录失败: {md5_value}")
        return success

    async def handle_delete_by_filename(self, user_id: str, filename: str, delete_documents: bool = True) -> bool:
        store = VectorStoreService()
        success = await store.delete_by_filename(user_id, filename, delete_documents)
        if success:
            logger.info(f"【知识库】删除用户 {user_id} 的文件: {filename}")
        else:
            logger.warning(f"【知识库】删除用户 {user_id} 的文件失败: {filename}")
        return success

    async def handle_get_md5_info(self, user_id: str, md5_value: str):
        store = VectorStoreService()
        return await store.get_md5_info(user_id, md5_value)

    async def handle_get_all_md5_records(self, user_id: str):
        store = VectorStoreService()
        return await store.get_all_md5_records(user_id)

    async def handle_get_user_knowledge(self, user_id: str) -> list:
        store = VectorStoreService()
        documents = await store.get_user_documents(user_id)
        logger.info(f"【知识库】获取用户 {user_id} 的知识库文档，共 {len(documents)} 个文件")
        return documents

    async def handle_get_document_detail(self, user_id: str, filename: str) -> dict:
        store = VectorStoreService()
        document = await store.get_document_detail(user_id, filename)
        if not document:
            raise HTTPException(status_code=404, detail=f"文档 {filename} 不存在")
        logger.info(f"【知识库】获取文档详情: {filename}")
        return document

    async def handle_get_document_chunks(self, user_id: str, filename: str) -> dict:
        store = VectorStoreService()
        chunks = await store.get_document_chunks(user_id, filename)
        if chunks['total_chunks'] == 0:
            raise HTTPException(status_code=404, detail=f"文档 {filename} 不存在或没有切片")
        logger.info(f"【知识库】获取文档切片: {filename}，共 {chunks['total_chunks']} 个切片")
        return chunks

    async def handle_get_batch_images(self, user_id: str, md5: str) -> dict:
        """
        一次性读取某个文档的所有提取图片，以 base64 data URL 的形式返回。
        这样前端可以一次请求拿到所有图片，然后根据 chunk 中的 image_paths 按需渲染，
        避免了每个图片单独发 HTTP 请求的性能开销（尤其适合移动端或图片较多的场景）。
        """
        from app.utils.path_tool import get_data_path
        image_dir = os.path.join(get_data_path(), 'extracted_images', user_id, md5)
        if not os.path.isdir(image_dir):
            logger.warning(f"【知识库】图片目录不存在: {image_dir}")
            return {"md5": md5, "images": {}}

        images = {}
        try:
            for filename in sorted(os.listdir(image_dir)):
                filepath = os.path.join(image_dir, filename)
                if not os.path.isfile(filepath):
                    continue
                _, ext = os.path.splitext(filename)
                mime_map = {
                    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.tiff': 'image/tiff', '.tif': 'image/tiff',
                    '.bmp': 'image/bmp', '.gif': 'image/gif', '.webp': 'image/webp',
                }
                mime = mime_map.get(ext.lower(), 'application/octet-stream')
                with open(filepath, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                images[filename] = f"data:{mime};base64,{b64}"
        except Exception as e:
            logger.error(f"【知识库】读取批量图片失败: {e}")
            raise HTTPException(status_code=500, detail=f"读取图片失败: {e}")

        logger.info(f"【知识库】读取批量图片: {md5}，共 {len(images)} 张")
        return {"md5": md5, "images": images}


def get_knowledge_service() -> KnowledgeService:
    """获取知识库服务实例（用于依赖注入）"""
    return KnowledgeService()
