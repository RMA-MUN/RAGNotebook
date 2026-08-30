"""项目配置中心：唯一的 Settings 读取点（pydantic-settings）。

其余模块一律 `from app.core.settings import settings` 读取配置，不再各自 os.getenv；
app/core/failed_response.py 从本模块 re-export settings，兼容既有导入路径。

环境变量加载时机：本模块被首次导入时实例化 Settings（读取 .env 与进程环境变量），
测试若需运行时覆盖，直接 monkeypatch settings 属性或实例化 Settings(_env_file=None, **overrides)。
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """项目配置类，自动从环境变量读取"""
    # 环境标识：dev(开发) / test(测试) / prod(生产)
    ENV: str = "dev"
    # DEBUG模式：开发环境默认True，生产环境强制False
    DEBUG_MODE: bool = True
    # 日志级别
    LOG_LEVEL: str = "INFO"

    # 安全
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"

    # LLM（OpenAI 兼容协议；各能力专属变量为空时按原子回落语义回落到 OPENAI_*）
    OPENAI_BASE_URL: str = ""
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL_NAME: str = ""
    # Agent 直接调用时的独立 API Key（为空回落 OPENAI_API_KEY 语义由调用方实现）
    CHAT_API_KEY: str = ""

    # 视觉模型（VISION_ENABLED 三态：None=默认启用 / "false"=关闭 / "true"=强制启用）
    VISION_BASE_URL: str | None = None
    VISION_API_KEY: str | None = None
    VISION_MODEL_NAME: str | None = None
    VISION_ENABLED: str | None = None
    VISION_BATCH_SIZE: int = 5
    VISION_DEDUP_ENABLED: bool = True
    VISION_DEDUP_THRESHOLD: int = 10
    VISION_BATCH_LOW_RES: bool = True

    # 嵌入模型
    EMBED_BASE_URL: str | None = None
    EMBED_API_KEY: str | None = None
    EMBED_MODEL_NAME: str | None = None

    # 联网搜索
    WEB_SEARCH_ENABLED: bool = False
    WEB_SEARCH_PROVIDER: str = ""
    WEB_SEARCH_API_KEY: str = ""

    # MySQL
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: str = "chat_history"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 3

    # Neo4j 知识图谱数据库（neo4j_uri 为空时图谱功能降级：API 返回 503）
    NEO4J_URI: str = ""
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""

    # 云端重排序（rerank API）
    RERANKER_API_BASE_URL: str = ""
    RERANKER_API_KEY: str = ""
    RERANKER_MODEL: str = ""

    # 限流全局开关
    RATE_LIMIT_ENABLED: bool = True

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
