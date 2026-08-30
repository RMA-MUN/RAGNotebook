"""应用后台初始化：AI 模型 → Neo4j Schema/图谱 worker → NoteService → 知识库 → 云端重排序。

初始化顺序即依赖顺序；Neo4j 相关步骤失败仅告警不阻塞启动（图谱功能降级）。
"""
import asyncio
import time

from app.core.logger_handler import logger


class _BackgroundInitManager:
    """后台初始化管理器

    在 FastAPI 启动后通过 start() 在后台异步初始化所有重型资源，
    避免模块级导入阻塞 uvicorn 启动。
    每个组件初始化完成后设置对应的 Event。
    """

    def __init__(self):
        self._started = False
        self._start_time = 0.0

        # 各组件的初始化状态事件
        self.models_ready = asyncio.Event()
        self.note_service_ready = asyncio.Event()
        self.reranker_ready = asyncio.Event()

        # 图谱构建 worker（Neo4j 主路径；stop_event 置位后退出）
        self.graph_worker_stop = asyncio.Event()
        self._graph_worker_task: asyncio.Task | None = None

        # 初始化后的实例（初始化完成前为 None）
        self.chat_model = None
        self.embed_model = None
        self.vision_model = None
        self.note_service = None
        self.reorder_service = None

    async def start(self):
        """启动后台初始化（不阻塞主事件循环）"""
        if self._started:
            return
        self._started = True
        self._start_time = time.time()
        asyncio.create_task(self._initialize_all())

    async def _initialize_all(self):
        """后台执行所有重型初始化"""
        try:
            logger.info("🔄 开始后台初始化...")

            # 1. AI 模型（调用 factory 中的工厂类）
            await self._init_models()

            # 1.5 Neo4j 图谱连接与 Schema（失败仅告警，不阻塞启动）
            await self._init_graph()

            # 1.6 图谱构建 worker（任务表持久化，重启自动恢复消费）
            self._start_graph_worker()

            # 2. NoteService（业务单例，不依赖外部向量库）
            await self._init_note_service()

            # 2.5 预热向量库服务（线程内初始化，避免上传时切片线程与事件循环线程在 _init_lock 上互相阻塞）
            await self._init_vector_store()

            # 3. 云端重排序服务（轻量 HTTP 客户端，无本地模型）
            await self._init_reranker()

            elapsed = time.time() - self._start_time
            logger.info(f"✅ 后台初始化完成，耗时 {elapsed:.1f} 秒")

        except Exception as e:
            logger.error(f"❌ 后台初始化失败: {e}", exc_info=True)

    async def _init_models(self):
        """初始化 AI 模型"""
        from app.utils.factory import ChatModelFactory, EmbedModelFactory, VisionModelFactory

        self.chat_model = await asyncio.to_thread(
            lambda: ChatModelFactory().generator()
        )
        logger.info("✅ chat_model 初始化完成")

        self.embed_model = await asyncio.to_thread(
            lambda: EmbedModelFactory().generator()
        )
        logger.info("✅ embed_model 初始化完成")

        self.vision_model = await asyncio.to_thread(
            lambda: VisionModelFactory().generator()
        )
        logger.info("✅ vision_model 初始化完成")

        self.models_ready.set()

    async def _init_graph(self):
        """初始化 Neo4j 图谱 Schema（幂等；未配置或失败时仅告警，不阻塞启动）"""
        from app.graph.storage.neo4j_client import ensure_graph_schema, neo4j_configured

        if not neo4j_configured():
            logger.warning("⚠️ NEO4J_URI 未配置，图谱功能降级（API 返回 503）")
            return
        try:
            await ensure_graph_schema(self.embed_model)
        except Exception as e:
            logger.error(f"❌ Neo4j Schema 初始化失败: {e}", exc_info=True)

    def _start_graph_worker(self):
        """启动图谱构建 worker（幂等；仅 Neo4j 主路径需要消费任务表）。"""
        if self._graph_worker_task is not None and not self._graph_worker_task.done():
            return
        from app.graph.storage.neo4j_client import neo4j_configured

        if not neo4j_configured():
            logger.info("Neo4j 未配置，图谱构建 worker 不启动")
            return
        self.graph_worker_stop.clear()
        self._graph_worker_task = asyncio.create_task(
            _run_graph_worker(self.graph_worker_stop))
        logger.info("✅ 图谱构建 worker 已启动")

    async def stop_graph_worker(self):
        """停止图谱构建 worker（应用 shutdown 时调用）。"""
        self.graph_worker_stop.set()
        if self._graph_worker_task is not None:
            try:
                await asyncio.wait_for(self._graph_worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._graph_worker_task.cancel()
            self._graph_worker_task = None

    async def _init_note_service(self):
        """初始化 NoteService（向量能力已迁 Neo4j，此处仅构建业务单例）"""
        await self.models_ready.wait()

        from app.services.note_service import NoteService

        self.note_service = NoteService()
        logger.info("✅ NoteService 初始化完成")
        self.note_service_ready.set()

    async def _init_vector_store(self):
        """预热 VectorStoreService 单例（提前在线程内完成，避免运行时锁竞争阻塞事件循环）"""
        from app.rag.vector_store import VectorStoreService

        await asyncio.to_thread(lambda: VectorStoreService())
        logger.info("✅ VectorStoreService 预热完成")

    async def _init_reranker(self):
        """初始化云端重排序服务（读 RERANKER_* 配置；无本地模型加载，未配置时调用方降级）"""
        from app.rag.reorder_service import ReorderService

        self.reorder_service = ReorderService()
        logger.info("✅ ReorderService 初始化完成")
        self.reranker_ready.set()


async def _run_graph_worker(stop_event: asyncio.Event):
    """包装 worker 循环，异常不拖垮初始化管理器。"""
    from app.graph.services.graph_worker import run_worker_loop

    try:
        await run_worker_loop(stop_event)
    except Exception as e:
        logger.error(f"图谱构建 worker 异常退出: {e}", exc_info=True)


# 全局单例
init_manager = _BackgroundInitManager()
