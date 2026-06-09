import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware

from app.core.background_init import init_manager
from app.core.failed_response_register import register_exception_handlers
from app.core.logger_handler import logger
from app.db.db_config import init_db
from app.db.redis_config import close_redis, connect_redis
from app.router.chat import chat_router
from app.router.health import health_router
from app.router.knowledge_router import knowledge_router
from app.router.note_router import note_router
from app.router.review_router import review_router
from app.router.user import user_router
from app.services.database_session_manager import init_database_session_manager

# 加载环境变量
load_dotenv()

app = FastAPI()

# 集成限流中间件（暂时注释掉，以免在调试阶段干扰正常请求）
# RateLimitMiddleware 基于令牌桶实现，每 60 秒允许 100 个请求
# 正式部署时可根据接口负载调整限流策略
# 所有限流（包括路由上的 Depends(rate_limit(...))）通过 RATE_LIMIT_ENABLED=false 一键关闭
# app.add_middleware(RateLimitMiddleware, limit=100, window=60)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time, 4))
    return response

# 集成API路由
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(health_router)
app.include_router(user_router)
app.include_router(note_router)
app.include_router(review_router)




app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 允许访问的源
    allow_credentials=True, # 允许携带cookie
    allow_methods=["*"], # 允许的请求方法
    allow_headers=["*"], # 允许的请求头
)

# 注册异常处理函数
register_exception_handlers(app)

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化会话管理器"""
    # 初始化数据库表结构
    await init_db()
    logger.info("数据库表结构初始化完成")

    # 使用数据库版本的会话管理器
    await init_database_session_manager()
    logger.info("数据库会话管理器初始化完成")

    # 连接Redis
    await connect_redis()
    logger.info("Redis连接初始化完成")

    # 检查并重排序模型（在后台异步加载）
    await init_manager.start()
    logger.info("部分资源正在初始化（模型加载、ChromaDB初始化等将在后台继续加载）")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    # 关闭 Redis 连接
    await close_redis()
    logger.info("Redis连接已关闭")

    # 关闭 SQLAlchemy 引擎（释放 aiomysql 连接池，避免 GC 时事件循环已关闭）
    from app.db.db_config import async_engine
    await async_engine.dispose()
    logger.info("数据库引擎已关闭")
