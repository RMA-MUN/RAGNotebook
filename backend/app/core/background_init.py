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

            # 2. ChromaDB（NoteService，依赖 embed_model）
            await self._init_note_service()

            # 3. 重排序模型（引入 torch、sentence_transformers 等重型框架）
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

    async def _init_note_service(self):
        """初始化 NoteService（ChromaDB，依赖 embed_model）"""
        await self.models_ready.wait()

        from app.services.note_service import NoteService

        self.note_service = await asyncio.to_thread(
            lambda: NoteService(embed_model=self.embed_model)
        )
        logger.info("✅ NoteService（ChromaDB）初始化完成")
        self.note_service_ready.set()

    async def _init_reranker(self):
        """检查并初始化重排序模型（触发 torch 等重型框架加载）"""
        from app.rag.reorder_service import ReorderService, check_and_download_reranker_model

        await asyncio.to_thread(check_and_download_reranker_model)
        logger.info("✅ 重排序模型检查完成")

        self.reorder_service = ReorderService()
        logger.info("✅ ReorderService 初始化完成")
        self.reranker_ready.set()


# 全局单例
init_manager = _BackgroundInitManager()
