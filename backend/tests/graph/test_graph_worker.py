"""图谱构建 worker 测试：入队幂等、认领互斥、失败重试、强制重抽。"""
import json

import pytest
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.graph.services import graph_service
from app.graph.services.graph_service import content_hash
from app.graph.services.graph_worker import _claim_next_task, _tick, recover_stale_tasks
from app.models.graph import GraphBuildTask, GraphDoc, GraphExtractLog
from app.models.note import Note
from tests.fakes import make_fake_chat_model


@pytest.fixture
def patched(session_factory, monkeypatch):
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    return session_factory


@pytest.mark.asyncio
async def test_enqueue_upsert_idempotent(db_session, patched):
    assert await graph_service._enqueue_build_task(
        "u1", "note", "n1", "标题", content_hash("v1"), {"text": "v1"}) is True
    assert await graph_service._enqueue_build_task(
        "u1", "note", "n1", "标题2", content_hash("v2"), {"text": "v2"}) is True

    rows = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.user_id == "u1"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "标题2"
    assert rows[0].content_hash == content_hash("v2")
    assert json.loads(rows[0].payload) == {"text": "v2"}
    assert rows[0].status == "pending"


@pytest.mark.asyncio
async def test_enqueue_resets_attempts_on_content_change(db_session, patched):
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h1", {"text": "a"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        row.attempts = 2
        row.status = "failed"
        await db.commit()
    # 相同内容重入队：不重置计数、保留 failed → 也不复活
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h1", {"text": "a"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.attempts == 2
    # 内容变化：重置计数并回到 pending
    await graph_service._enqueue_build_task("u1", "doc", "md5", "f.pdf", "h2", {"text": "b"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.attempts == 0 and row.status == "pending"


@pytest.mark.asyncio
async def test_claim_marks_running_and_returns_none_when_empty(db_session, patched):
    assert await _claim_next_task() is None
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h", {"text": "x"})
    task_id = await _claim_next_task()
    assert task_id is not None
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "running"
    # 已无 pending → 认领不到
    assert await _claim_next_task() is None


@pytest.mark.asyncio
async def test_process_task_retry_then_failed(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(graph_service, "MAX_TASK_ATTEMPTS", 2)
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr(graph_service, "extract_entities", _boom)

    await graph_service._enqueue_build_task("u1", "doc", "md51", "f.pdf",
                                            content_hash("body"), {"text": "用 Python 写"})
    await db_session.execute(
        GraphDoc.__table__.insert().values(id="md51", user_id="u1", filename="f.pdf"))
    await db_session.commit()

    # 第一次消费：失败 → 重回 pending
    task_id, run_token = await _claim_next_task()
    assert await graph_service.process_task(task_id, run_token=run_token) is False
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "pending" and row.attempts == 1

    # 第二次消费：达到上限 → failed 终态
    task_id, run_token = await _claim_next_task()
    assert await graph_service.process_task(task_id, run_token=run_token) is True
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "failed"
    assert "LLM 不可用" in row.error_message


@pytest.mark.asyncio
async def test_process_task_force_bypasses_hash_check(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    db_session.add(GraphExtractLog(id="logf", user_id="u1", note_id="n1",
                                   content_hash="h", status="success"))
    db_session.add(Note(id="n1", user_id="u1", title="标题", content="用 Python 写"))
    await db_session.commit()

    await graph_service._enqueue_build_task("u1", "note", "n1", "标题", "h", {}, force=True)
    task_id, run_token = await _claim_next_task()
    assert await graph_service.process_task(task_id, run_token=run_token) is True

    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "completed"
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_tick_drains_queue_in_order(db_session, patched, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    db_session.add(Note(id="n1", user_id="u1", title="t1", content="a"))
    db_session.add(Note(id="n2", user_id="u1", title="t2", content="b"))
    await db_session.commit()
    await graph_service._enqueue_build_task("u1", "note", "n1", "t1", content_hash("a"), {"text": "a"})
    await graph_service._enqueue_build_task("u1", "note", "n2", "t2", content_hash("b"), {"text": "b"})

    assert await _tick() is True
    assert await _tick() is True
    assert await _tick() is False  # 队列空

    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    rows = (await db_session.execute(select(GraphBuildTask))).scalars().all()
    assert all(r.status == "completed" for r in rows)
    logs = (await db_session.execute(select(GraphExtractLog))).scalars().all()
    assert {log.note_id for log in logs} == {"n1", "n2"}


@pytest.mark.asyncio
async def test_claim_writes_and_rotates_run_token(db_session, patched):
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h", {"text": "x"})
    task_id, run_token = await _claim_next_task()
    assert run_token
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "running" and row.run_token == run_token

    # 执行期间重新入队后再认领：同一任务行，令牌替换
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h2", {"text": "y"})
    task_id2, run_token2 = await _claim_next_task()
    assert task_id2 == task_id and run_token2 != run_token


@pytest.mark.asyncio
async def test_recover_stale_tasks_requeues_running(db_session, patched):
    """P1 回归：进程崩溃遗留的 running 任务在 worker 启动时恢复为 pending。"""
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h", {"text": "x"})
    task_id, run_token = await _claim_next_task()
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "running"

    assert await recover_stale_tasks() == 1
    await db_session.rollback()
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "pending" and row.run_token is None
    assert await recover_stale_tasks() == 0  # 幂等


@pytest.mark.asyncio
async def test_process_task_superseded_discards_result(db_session, patched, monkeypatch):
    """P1 回归：执行期间任务被重新入队（令牌被替换），旧执行中止且不写终态/日志。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))

    seen = {}

    async def _fake_extraction(source_id, user_id, title, body_hash, body=None,
                               source_type="note", chunks=None, claim_check=None):
        # 模拟抽取期间用户编辑：任务重新入队并被新令牌认领
        async with patched() as db:
            await db.execute(
                sa_update(GraphBuildTask)
                .where(GraphBuildTask.id == seen["task_id"])
                .values(status="running", run_token="token-new",
                        content_hash=content_hash("v2"), payload='{"text": "v2"}'))
            await db.commit()
        seen["claim_valid"] = await claim_check()
        return True

    monkeypatch.setattr(graph_service, "_run_extraction", _fake_extraction)

    await graph_service._enqueue_build_task(
        "u1", "note", "n1", "t", content_hash("v1"), {"text": "v1"})
    task_id, run_token = await _claim_next_task()
    seen["task_id"] = task_id

    assert await graph_service.process_task(task_id, run_token=run_token) is True
    assert seen["claim_valid"] is False  # 写图谱前的中途校验拦截
    await db_session.rollback()
    row = (await db_session.execute(select(GraphBuildTask))).scalar_one()
    assert row.status == "running" and row.run_token == "token-new"  # 未写 completed
    logs = (await db_session.execute(select(GraphExtractLog))).scalars().all()
    assert len(logs) == 1 and logs[0].status == "pending"  # 日志未被推到 success


@pytest.mark.asyncio
async def test_force_not_sticky_on_normal_save(db_session, patched):
    """手动强抽的 force 不粘滞：后续普通保存按本次触发重置。"""
    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h1", {"text": "a"}, force=True)
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.force is True

    await graph_service._enqueue_build_task("u1", "note", "n1", "t", "h2", {"text": "b"})
    async with patched() as db:
        row = (await db.execute(select(GraphBuildTask))).scalar_one()
        assert row.force is False and row.status == "pending"


@pytest.mark.asyncio
async def test_manual_re_extract_filters_source_type(db_session, patched):
    """手动重抽只重置笔记日志，不牵连同 id 的文档日志。"""
    db_session.add(GraphExtractLog(id="l1", user_id="u1", note_id="n1",
                                   source_type="note", content_hash="h", status="success"))
    db_session.add(GraphExtractLog(id="l2", user_id="u1", note_id="n1",
                                   source_type="doc", content_hash="h", status="success"))
    await db_session.commit()

    await graph_service.manual_re_extract("n1", "u1")

    await db_session.rollback()
    note_log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.id == "l1"))).scalar_one()
    doc_log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.id == "l2"))).scalar_one()
    assert note_log.status == "pending"
    assert doc_log.status == "success"
