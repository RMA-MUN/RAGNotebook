"""图谱构建 worker：应用内 asyncio 循环，轮询消费 graph_build_tasks 任务表。

- 启动恢复：把上次进程遗留的 running 任务拨回 pending（单实例部署无并发认领者，
  进程认领后崩溃不至于永久卡死）；
- 认领：乐观 UPDATE（pending→running）并写入认领令牌 run_token；任务执行期间被
  重新入队（编辑/force 触发）会替换令牌，旧执行方据此自愿中止，防旧结果覆盖新任务；
- 执行：graph_service.process_task（哈希判重 → 抽取管线 → 状态回写）；
- 停止：stop_event 置位后退出；任务持久化在 MySQL，pending 任务应用重启自动恢复消费。
"""
import asyncio
import uuid

from sqlalchemy import select, update

from app.core.logger_handler import logger
from app.models.graph import GraphBuildTask

POLL_INTERVAL = 2.0


async def recover_stale_tasks() -> int:
    """把遗留的 running 任务拨回 pending 并清空旧令牌；返回恢复条数（worker 启动时调用）。

    单实例部署下 running 只可能来自已死进程；多实例部署需先引入 lease/owner 机制再放开。
    """
    from app.graph.services import graph_service

    async with graph_service.AsyncSessionLocal() as db:
        result = await db.execute(
            update(GraphBuildTask)
            .where(GraphBuildTask.status == "running")
            .values(status="pending", run_token=None))
        await db.commit()
        return result.rowcount


async def _claim_next_task() -> tuple[str, str] | None:
    """认领一条 pending 任务（置 running + 写入认领令牌），返回 (任务 id, 令牌)；无任务返回 None。

    会话工厂经 graph_service 模块属性访问，测试统一 monkeypatch 该入口。
    """
    from app.graph.services import graph_service

    token = str(uuid.uuid4())
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
            .values(status="running", run_token=token))
        await db.commit()
        return (row.id, token) if result.rowcount else None


async def _tick() -> bool:
    """处理一条任务；队列空返回 False（调用方据此休眠）。

    任务执行期间被重新入队时 process_task 会校验令牌自愿中止，本层不感知。
    """
    claimed = await _claim_next_task()
    if claimed is None:
        return False
    task_id, run_token = claimed
    from app.graph.services import graph_service

    try:
        await graph_service.process_task(task_id, run_token=run_token)
    except Exception as e:
        # process_task 内部状态机之外抛出的异常：兜底回 pending 避免卡死 running；
        # 令牌条件保证不误伤重新入队后的新任务
        logger.error(f"图谱构建任务执行异常 task_id={task_id}: {e}", exc_info=True)
        try:
            async with graph_service.AsyncSessionLocal() as db:
                await db.execute(
                    update(GraphBuildTask)
                    .where(GraphBuildTask.id == task_id, GraphBuildTask.status == "running",
                           GraphBuildTask.run_token == run_token)
                    .values(status="pending"))
                await db.commit()
        except Exception as e2:
            logger.error(f"任务状态回滚失败 task_id={task_id}: {e2}")
    return True


async def run_worker_loop(stop_event: asyncio.Event) -> None:
    """轮询消费任务表；启动时先恢复遗留 running 任务，stop_event 置位后优雅退出。"""
    recovered = await recover_stale_tasks()
    if recovered:
        logger.info(f"已恢复 {recovered} 条上次进程遗留的 running 图谱构建任务")
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
