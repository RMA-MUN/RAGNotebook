"""MySQL Checkpointer：LangGraph BaseCheckpointSaver 的 SQLAlchemy/MySQL 实现。

以官方 langgraph checkpoint sqlite aio 版为蓝本移植，适配：
- 通过注入的 session_factory 执行（生产 AsyncSessionLocal=MySQL，测试=SQLite）；
- upsert 方言分支：mysql 用 ON DUPLICATE KEY UPDATE / INSERT IGNORE，
  sqlite 用 INSERT OR REPLACE / INSERT OR IGNORE（首次使用时探测并缓存）。

序列化统一走 JsonPlusSerializer（可编码 BaseMessage）。
注意：SQL 一律用 SQLAlchemy text() 的 :name 命名绑定（由引擎按方言编译为 %s / ?），
切勿手写 %s / ? 占位符。
"""
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import text

_CHECKPOINT_COLS = (
    "thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
    "type, checkpoint, metadata"
)


class MySQLCheckpointSaver(BaseCheckpointSaver):
    """把 LangGraph checkpoint 存到 SQLAlchemy 会话背后的库（MySQL/测试 SQLite）。"""

    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory
        self._dialect: str | None = None
        self._dialect_lock = asyncio.Lock()

    # ---- 方言探测（每个进程一次） ----
    async def _dialect_name(self) -> str:
        if self._dialect is not None:
            return self._dialect
        async with self._dialect_lock:
            if self._dialect is not None:
                return self._dialect
            async with self.session_factory() as session:
                self._dialect = session.get_bind().dialect.name
        return self._dialect

    # ---- SQL 模板（按方言分支） ----
    async def _sql(self, *, upsert: bool) -> tuple[str, str]:
        """返回 (checkpoints_sql, writes_sql) 两条 upsert 模板（参数为 :name 命名绑定）。"""
        dialect = await self._dialect_name()
        if dialect == "mysql":
            cp = (
                "INSERT INTO agent_checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                "type, checkpoint, metadata) VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, "
                ":parent_checkpoint_id, :type, :checkpoint, :metadata) "
                "ON DUPLICATE KEY UPDATE parent_checkpoint_id=VALUES(parent_checkpoint_id), "
                "type=VALUES(type), checkpoint=VALUES(checkpoint), metadata=VALUES(metadata)"
            )
            wr = (
                "INSERT INTO agent_checkpoint_writes "
                "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
                "VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, :task_id, :idx, "
                ":channel, :type, :value) "
                "ON DUPLICATE KEY UPDATE channel=VALUES(channel), type=VALUES(type), value=VALUES(value)"
                if upsert else
                "INSERT IGNORE INTO agent_checkpoint_writes "
                "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
                "VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, :task_id, :idx, "
                ":channel, :type, :value)"
            )
        else:
            cp = (
                "INSERT OR REPLACE INTO agent_checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                "type, checkpoint, metadata) VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, "
                ":parent_checkpoint_id, :type, :checkpoint, :metadata)"
            )
            wr = (
                "INSERT OR REPLACE INTO agent_checkpoint_writes "
                "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
                "VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, :task_id, :idx, "
                ":channel, :type, :value)"
                if upsert else
                "INSERT OR IGNORE INTO agent_checkpoint_writes "
                "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
                "VALUES (:thread_id, :checkpoint_ns, :checkpoint_id, :task_id, :idx, "
                ":channel, :type, :value)"
            )
        return cp, wr

    # ---- langgraph 接口 ----
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)

        async with self.session_factory() as session:
            if checkpoint_id:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {_CHECKPOINT_COLS} FROM agent_checkpoints "
                            "WHERE thread_id=:tid AND checkpoint_ns=:ns AND checkpoint_id=:cid"
                        ),
                        {"tid": thread_id, "ns": checkpoint_ns, "cid": checkpoint_id},
                    )
                ).fetchall()
            else:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {_CHECKPOINT_COLS} FROM agent_checkpoints "
                            "WHERE thread_id=:tid AND checkpoint_ns=:ns "
                            "ORDER BY checkpoint_id DESC LIMIT 1"
                        ),
                        {"tid": thread_id, "ns": checkpoint_ns},
                    )
                ).fetchall()
            if not rows:
                return None
            row = rows[0]
            tid, cid, parent_cid, type_, checkpoint, metadata = (
                row[0], row[2], row[3], row[4], row[5], row[6],
            )
            if checkpoint_id is None:
                config = {
                    "configurable": {
                        "thread_id": tid,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": cid,
                    }
                }
            writes = (
                await session.execute(
                    text(
                        "SELECT task_id, channel, type, value FROM agent_checkpoint_writes "
                        "WHERE thread_id=:tid AND checkpoint_ns=:ns AND checkpoint_id=:cid "
                        "ORDER BY task_id, idx"
                    ),
                    {"tid": tid, "ns": checkpoint_ns, "cid": str(config["configurable"]["checkpoint_id"])},
                )
            ).fetchall()

        parent_config = None
        if parent_cid:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_cid,
                }
            }
        return CheckpointTuple(
            config,
            self.serde.loads_typed((type_, checkpoint)),
            cast(CheckpointMetadata, json.loads(metadata.decode("utf-8")) if metadata else {}),
            parent_config,
            [
                (task_id, channel, self.serde.loads_typed((wtype, value)))
                for task_id, channel, wtype, value in writes
            ],
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            return
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        where = "WHERE thread_id=:tid AND checkpoint_ns=:ns"
        params: dict[str, Any] = {"tid": thread_id, "ns": checkpoint_ns}
        if before and (bid := get_checkpoint_id(before)):
            where += " AND checkpoint_id < :bid"
            params["bid"] = bid
        sql = (
            f"SELECT {_CHECKPOINT_COLS} FROM agent_checkpoints {where} "
            "ORDER BY checkpoint_id DESC"
        )
        if limit is not None:
            sql += " LIMIT :lim"
            params["lim"] = limit
        async with self.session_factory() as session:
            rows = (await session.execute(text(sql), params)).fetchall()
            for row in rows:
                tid, cid, parent_cid, type_, checkpoint, metadata = (
                    row[0], row[2], row[3], row[4], row[5], row[6],
                )
                yield CheckpointTuple(
                    {
                        "configurable": {
                            "thread_id": tid,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": cid,
                        }
                    },
                    self.serde.loads_typed((type_, checkpoint)),
                    cast(CheckpointMetadata, json.loads(metadata.decode("utf-8")) if metadata else {}),
                    (
                        {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": checkpoint_ns,
                                "checkpoint_id": parent_cid,
                            }
                        }
                        if parent_cid else None
                    ),
                    [],
                )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        sql_cp, _ = await self._sql(upsert=True)
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        type_, serialized = self.serde.dumps_typed(checkpoint)
        serialized_metadata = json.dumps(
            get_checkpoint_metadata(config, metadata), ensure_ascii=False
        ).encode("utf-8", "ignore")
        async with self.session_factory() as session:
            await session.execute(
                text(sql_cp),
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint["id"],
                    "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                    "type": type_,
                    "checkpoint": serialized,
                    "metadata": serialized_metadata,
                },
            )
            await session.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: list[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        upsert = all(w[0] in WRITES_IDX_MAP for w in writes)
        _, sql_wr = await self._sql(upsert=upsert)
        rows = [
            (
                str(config["configurable"]["thread_id"]),
                str(config["configurable"].get("checkpoint_ns", "")),
                str(config["configurable"]["checkpoint_id"]),
                task_id,
                WRITES_IDX_MAP.get(channel, idx),
                channel,
                *self.serde.dumps_typed(value),
            )
            for idx, (channel, value) in enumerate(writes)
        ]
        async with self.session_factory() as session:
            for params in rows:
                await session.execute(
                    text(sql_wr),
                    {
                        "thread_id": params[0],
                        "checkpoint_ns": params[1],
                        "checkpoint_id": params[2],
                        "task_id": params[3],
                        "idx": params[4],
                        "channel": params[5],
                        "type": params[6],
                        "value": params[7],
                    },
                )
            await session.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM agent_checkpoints WHERE thread_id=:tid"
                ),
                {"tid": str(thread_id)},
            )
            await session.execute(
                text(
                    "DELETE FROM agent_checkpoint_writes WHERE thread_id=:tid"
                ),
                {"tid": str(thread_id)},
            )
            await session.commit()
