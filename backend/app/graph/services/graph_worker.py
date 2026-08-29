"""图谱构建 worker：应用内 asyncio 循环，轮询消费 graph_build_tasks 任务表。

- 认领：乐观 UPDATE（pending→running），单实例部署无竞争；
- 执行：graph_service.process_task（哈希判重 → 抽取管线 → 状态回写）；
- 停止：stop_event 置位后退出；任务持久化在 MySQL，应用重启自动恢复消费。
"""
import asyncio

from sqlalchemy import select, update

from app.core.logger_handler import logger
from app.models.graph import GraphBuildTask

POLL_INTERVAL = 2.0


async def _claim_next_task() -> str | None:
    """认领一条 pending 任务（置 running），返回任务 id；无任务返回 None。

    会话工厂经 graph_service 模块属性访问，测试统一 monkeypatch 该入口。
    """
    from app.graph.services import graph_service

    async with graph_service.AsyncSessionLocal() as db:
        row = (await db.execute(
            select(GraphBuildTask).where(GraphBuildTask.status == "pending")
            .order_by(GraphBuildTask.created_at)
            .limit(1))).scalar_one_or_none()
        if row is None:
            return None
        result = await db.execute(
            update(GraphBuildTask)
            .where(GraphBuildTask.id == row.id, GraphBuildTask.status == "pending")
            .values(status="running"))
        await db.commit()
        return row.id if result.rowcount else None


async def _tick() -> bool:
    """处理一条任务；队列空返回 False（调用方据此休眠）。"""
    task_id = await _claim_next_task()
    if task_id is None:
        return False
    from app.graph.services import graph_service

    try:
        await graph_service.process_task(task_id)
    except Exception as e:
        # process_task 内部状态机之外抛出的异常：兜底回 pending，避免任务卡死在 running
        logger.error(f"图谱构建任务执行异常 task_id={task_id}: {e}", exc_info=True)
        try:
            async with graph_service.AsyncSessionLocal() as db:
                await db.execute(
                    update(GraphBuildTask)
                    .where(GraphBuildTask.id == task_id, GraphBuildTask.status == "running")
                    .values(status="pending"))
                await db.commit()
        except Exception as e2:
            logger.error(f"任务状态回滚失败 task_id={task_id}: {e2}")
    return True


async def run_worker_loop(stop_event: asyncio.Event) -> None:
    """轮询消费任务表；stop_event 置位后优雅退出。"""
    logger.info("图谱构建 worker 已启动")
    while not stop_event.is_set():
        try:
            processed = await _tick()
        except Exception as e:
            logger.error(f"图谱构建 worker 轮询异常: {e}", exc_info=True)
            processed = False
        if not processed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
    logger.info("图谱构建 worker 已停止")
