"""Neo4j 连接层：驱动惰性单例 + 图 Schema 幂等初始化。

- get_neo4j_driver()：按 settings.neo4j_* 创建 AsyncDriver 单例（线程安全）。
- ensure_graph_schema()：幂等创建约束与索引；向量索引维度由 embed 模型探针自动测定，
  已存在索引维度不一致时报错提示清库重建，避免写入期才发现维度漂移。
"""
import asyncio
import threading

from app.core.failed_response import settings
from app.core.logger_handler import logger

_driver = None
_driver_loop_id: int | None = None
_driver_lock = threading.Lock()

# 向量索引维度在进程内只需探测一次
_probed_dims: int | None = None

# 中文全文检索用 cjk 二元分词；若建索引阶段发现该 analyzer 不可用，降级 standard
_FULLTEXT_ANALYZERS = ("cjk", "standard")

_PROBE_TEXT = "Neo4j 向量索引维度探测 probe"


def neo4j_configured() -> bool:
    """是否配置了 Neo4j（未配置时图谱存储回落 MySQL）。"""
    return bool(settings.NEO4J_URI)


def get_neo4j_driver():
    """返回 AsyncDriver 单例；未配置 NEO4J_URI 时抛 RuntimeError。

    AsyncDriver 的连接池绑定创建时的事件循环；检测到循环切换（测试环境每个用例
    一个新 loop）时丢弃旧实例重建，避免 "Future attached to a different loop"。
    """
    global _driver, _driver_loop_id
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _driver is not None and current_loop is not None and _driver_loop_id is not None \
            and id(current_loop) != _driver_loop_id:
        logger.warning("Neo4j 驱动检测到事件循环变更，重建驱动实例")
        _driver = None

    if _driver is None:
        with _driver_lock:
            if _driver is None:
                if not neo4j_configured():
                    raise RuntimeError("NEO4J_URI 未配置，无法创建 Neo4j 驱动")
                from neo4j import AsyncGraphDatabase

                _driver = AsyncGraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                )
                _driver_loop_id = id(current_loop) if current_loop is not None else None
                logger.info(f"✅ Neo4j 驱动已创建: {settings.NEO4J_URI}")
    return _driver


async def close_neo4j_driver():
    """关闭并清空驱动单例（应用 shutdown 时调用）。"""
    global _driver, _probed_dims
    with _driver_lock:
        if _driver is not None:
            await _driver.close()
            _driver = None
            _probed_dims = None
            logger.info("Neo4j 驱动已关闭")


def probe_embedding_dims(embed_model) -> int:
    """用 embed 模型对探针文本求向量，返回维度（同步，调用方按需 to_thread）。"""
    global _probed_dims
    if _probed_dims is None:
        if embed_model is None:
            raise RuntimeError("嵌入模型尚未初始化，无法探测向量维度")
        _probed_dims = len(embed_model.embed_query(_PROBE_TEXT))
        logger.info(f"✅ 嵌入向量维度探测完成: {_probed_dims}")
    return _probed_dims


_SCHEMA_DDL = (
    # Chunk/Doc 不设单列 id 唯一约束：id 是业务键（md5/来源:序号）跨用户可重复，
    # 隔离由 (id, user_id) 复合 MERGE 键保证，查询一律叠加 user_id 过滤
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT entity_user_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE (e.user_id, e.name) IS UNIQUE",
    "CREATE CONSTRAINT entity_type_id_unique IF NOT EXISTS FOR (t:EntityType) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT note_id_unique IF NOT EXISTS FOR (n:Note) REQUIRE n.id IS UNIQUE",
    "CREATE INDEX chunk_user_index IF NOT EXISTS FOR (c:Chunk) ON (c.user_id)",
    "CREATE INDEX chunk_source_user_index IF NOT EXISTS FOR (c:Chunk) ON (c.user_id, c.kind, c.source_id)",
    "CREATE INDEX entity_user_index IF NOT EXISTS FOR (e:Entity) ON (e.user_id)",
    "CREATE INDEX doc_user_index IF NOT EXISTS FOR (d:Doc) ON (d.user_id)",
)


async def _existing_vector_dims(driver) -> int | None:
    """读现有 chunk_embedding_index 的维度配置；索引不存在返回 None。"""
    result = await driver.execute_query(
        "SHOW VECTOR INDEXES YIELD name, options WHERE name = 'chunk_embedding_index' RETURN options"
    )
    for record in result.records:
        config = (record.get("options") or {}).get("indexConfig") or {}
        dims = config.get("vector.dimensions")
        if dims is not None:
            return int(dims)
    return None


async def _create_fulltext_index(driver) -> None:
    """按优先级尝试 analyzer 建 Chunk 全文索引；全部失败则抛出最后异常。"""
    last_error: Exception | None = None
    for analyzer in _FULLTEXT_ANALYZERS:
        try:
            await driver.execute_query(
                "CREATE FULLTEXT INDEX chunk_text_index IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text] "
                f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{analyzer}', `fulltext.eventually_consistent`: false}}}}"
            )
            if analyzer != _FULLTEXT_ANALYZERS[0]:
                logger.warning(f"⚠️ fulltext analyzer '{_FULLTEXT_ANALYZERS[0]}' 不可用，已降级 '{analyzer}'")
            return
        except Exception as e:
            last_error = e
            logger.warning(f"fulltext analyzer '{analyzer}' 建索引失败: {e}")
    raise RuntimeError(f"Chunk 全文索引创建失败: {last_error}")


async def ensure_graph_schema(embed_model=None) -> None:
    """幂等初始化图约束与索引；embed_model 就绪时同时确保向量索引维度匹配。"""
    driver = get_neo4j_driver()

    for ddl in _SCHEMA_DDL:
        await driver.execute_query(ddl)
    await _create_fulltext_index(driver)

    if embed_model is None:
        logger.warning("⚠️ embed 模型未就绪，跳过向量索引创建（检索前需完成 Schema 初始化）")
        return

    dims = await asyncio.to_thread(probe_embedding_dims, embed_model)
    existing = await _existing_vector_dims(driver)
    if existing is not None and existing != dims:
        raise RuntimeError(
            f"Neo4j 向量索引维度({existing})与当前嵌入模型维度({dims})不一致，"
            "请清空图数据后重建索引（更换 embedding 模型需全量重抽）"
        )
    await driver.execute_query(
        "CREATE VECTOR INDEX chunk_embedding_index IF NOT EXISTS FOR (c:Chunk) ON c.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
        {"dims": dims},
    )
    logger.info(f"✅ Neo4j 图 Schema 就绪（vector dims={dims}）")
