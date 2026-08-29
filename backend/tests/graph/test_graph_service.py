import asyncio

import pytest
from sqlalchemy import select

from app.graph.services.graph_service import (
    _run_extraction,
    cleanup_doc_graph,
    cleanup_note_graph,
    content_hash,
    maybe_schedule_doc_extraction,
    maybe_schedule_extraction,
)
from app.models.graph import (
    GraphBuildTask,
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
    GraphNoteEdge,
    GraphRelation,
)
from app.models.note import Note
from tests.fakes import make_fake_chat_model


def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")


@pytest.mark.asyncio
async def test_maybe_schedule_extraction_triggers_once(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    from app.graph.services.graph_worker import _tick
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    db_session.add(GraphExtractLog(id="log1", user_id="u1", note_id="n1",
                                   content_hash=content_hash("v1"), status="success"))
    db_session.add(Note(id="n1", user_id="u1", title="标题", content="v1"))
    await db_session.commit()

    # 入队幂等：同来源重复触发覆盖载荷
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v1") is True
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v1") is True

    # worker 消费：hash 与已有成功日志一致 → 跳过抽取，任务直接完成
    assert await _tick() is True
    await db_session.rollback()  # 结束本会话快照，读取其他会话的提交
    task = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.source_id == "n1"))).scalar_one()
    assert task.status == "completed"
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("v1")

    # 内容变化 → 重新入队，worker 消费后重新抽取
    assert await maybe_schedule_extraction("n1", "u1", "标题", "v2") is True
    assert await _tick() is True
    await db_session.rollback()
    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.content_hash == content_hash("v2")
    assert log.status == "success"


@pytest.mark.asyncio
async def test_run_extraction_writes_entities_relations_and_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], '
                                               '"relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log2", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    # 抽取来源笔记本身 + 双链目标笔记（[[FastAPI]] 需要命中已存在的笔记才会生成 wiki 边）
    db_session.add(Note(id="n1", user_id="u1", title="我的笔记", content="用 [[FastAPI]] 与 Python"))
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="FastAPI 文档"))
    await db_session.commit()

    await _run_extraction("n1", "u1", "我的笔记", content_hash("body"), body="用 [[FastAPI]] 与 Python")

    # 双链边
    edges = (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all()
    assert any(e.kind == "wiki" for e in edges)
    # 实体与实体-笔记关联
    entities = (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all()
    assert any(e.name == "Python" for e in entities)


@pytest.mark.asyncio
async def test_duplicate_title_notes_uses_first_match(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log3", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    # 抽取来源笔记
    db_session.add(Note(id="n1", user_id="u1", title="我的笔记", content="[[FastAPI]]"))
    # 重名笔记：Note.title 无唯一约束，首条匹配生效，不得 MultipleResultsFound
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="a"))
    db_session.add(Note(id="n3", user_id="u1", title="FastAPI", content="b"))
    await db_session.commit()

    await _run_extraction("n1", "u1", "我的笔记", content_hash("body"), body="[[FastAPI]]")

    edges = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1", GraphNoteEdge.source_note_id == "n1"))).scalars().all()
    assert len(edges) == 1
    assert edges[0].target_note_id == "n2"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "n1"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_re_extract_removes_stale_reverse_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="log4", user_id="u1", note_id="n1", content_hash="h",
                                   status="pending"))
    db_session.add(Note(id="n1", user_id="u1", title="我的笔记", content="[[FastAPI]]"))
    db_session.add(Note(id="n2", user_id="u1", title="FastAPI", content="FastAPI 文档"))
    await db_session.commit()

    # 首抽：[[FastAPI]] → n1→n2 出边 + n2→n1 反向边
    await _run_extraction("n1", "u1", "我的笔记", content_hash("v1"), body="[[FastAPI]]")
    reverse = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1", GraphNoteEdge.source_note_id == "n2",
        GraphNoteEdge.target_note_id == "n1"))).scalar_one_or_none()
    assert reverse is not None

    # 重抽：内容不再含 [[FastAPI]] → 出边与过期反向边都应被清理
    await _run_extraction("n1", "u1", "我的笔记", content_hash("v2"), body="没有链接了")
    edges = (await db_session.execute(select(GraphNoteEdge).where(
        GraphNoteEdge.user_id == "u1"))).scalars().all()
    assert edges == []


@pytest.mark.asyncio
async def test_cleanup_note_graph_removes_all_relations(db_session):
    from sqlalchemy import insert
    await db_session.execute(insert(GraphNoteEdge).values(id="w1", user_id="u1",
                                                          source_note_id="n1", target_note_id="n2",
                                                          kind="wiki"))
    await db_session.execute(insert(GraphEntityNote).values(id="en1", user_id="u1",
                                                            entity_id="e1", note_id="n1"))
    await db_session.execute(insert(GraphExtractLog).values(id="g1", user_id="u1", note_id="n1",
                                                            content_hash="h", status="success"))
    await db_session.commit()

    await cleanup_note_graph(db_session, "u1", "n1")
    assert (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphEntityNote).where(GraphEntityNote.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == "u1"))).scalars().all() == []


@pytest.mark.asyncio
async def test_maybe_schedule_doc_extraction_creates_doc_and_triggers(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    from app.graph.services.graph_worker import _tick
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)

    assert await maybe_schedule_doc_extraction("u1", "md51", "报告.pdf", "用 Python 写的报告") is True

    assert await _tick() is True
    doc = (await db_session.execute(select(GraphDoc).where(GraphDoc.id == "md51"))).scalar_one()
    assert doc.filename == "报告.pdf"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51", GraphExtractLog.source_type == "doc"))).scalar_one()
    assert log.status == "success"
    en = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.note_id == "md51", GraphEntityNote.source_type == "doc"))).scalars().all()
    assert any(r.entity_id for r in en)


@pytest.mark.asyncio
async def test_maybe_schedule_doc_extraction_skips_same_hash(db_session, session_factory, monkeypatch):
    from app.graph.services.graph_worker import _tick
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphExtractLog(id="logd", user_id="u1", note_id="md51",
                                   content_hash=content_hash("v1"), status="success", source_type="doc"))
    await db_session.commit()

    # 入队总是成功（哈希判重移到 worker 侧）
    assert await maybe_schedule_doc_extraction("u1", "md51", "报告.pdf", "v1") is True
    # 文档节点元数据在入队时就落库（否则总览图看不到文档节点）
    doc = (await db_session.execute(select(GraphDoc).where(GraphDoc.id == "md51"))).scalar_one_or_none()
    assert doc is not None and doc.filename == "报告.pdf"

    # worker 消费：hash 一致 → 跳过抽取，任务完成且日志保持原样
    assert await _tick() is True
    task = (await db_session.execute(
        select(GraphBuildTask).where(GraphBuildTask.source_id == "md51"))).scalar_one()
    assert task.status == "completed"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("v1")


@pytest.mark.asyncio
async def test_run_extraction_doc_skips_note_edges(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd2", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    await db_session.commit()

    # 文档正文里即便有 [[双链]] 语法也不生成笔记双链边
    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 [[FastAPI]] 与 Python", source_type="doc")

    assert (await db_session.execute(
        select(GraphNoteEdge).where(GraphNoteEdge.user_id == "u1"))).scalars().all() == []
    entities = (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all()
    assert any(e.name == "Python" for e in entities)
    en = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.user_id == "u1", GraphEntityNote.note_id == "md51"))).scalars().all()
    assert len(en) == 1 and en[0].source_type == "doc"
    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "success"


@pytest.mark.asyncio
async def test_run_extraction_doc_without_log_row_creates_log(db_session, session_factory, monkeypatch):
    """日志行缺失（抽取期间被清理/从未持久化）时不得崩溃，应自建日志行并落 success。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md52", user_id="u1", filename="报告.pdf"))
    await db_session.commit()

    await _run_extraction("md52", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md52", GraphExtractLog.source_type == "doc"))).scalar_one()
    assert log.status == "success"
    assert log.content_hash == content_hash("body")


@pytest.mark.asyncio
async def test_doc_extraction_failure_marks_log_failed(db_session, session_factory, monkeypatch):
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd3", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    await db_session.commit()

    async def _boom(*args, **kwargs):
        raise RuntimeError("LLM 不可用")
    monkeypatch.setattr("app.graph.services.graph_service.extract_entities", _boom)

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalar_one()
    assert log.status == "failed"


@pytest.mark.asyncio
async def test_run_extraction_doc_aborts_when_source_deleted_mid_flight(db_session, session_factory, monkeypatch):
    """文档在抽取（LLM 调用）期间被删除 → 抽取必须放弃，不得复活实体/关联/日志。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model",
                        make_fake_chat_model(['{"entities": [{"name": "Python", "mentions": ["Python"]}], "relations": []}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    # 只残留 pending 日志（GraphDoc 已被删除——模拟"清理先行、抽取后至"的竞态）
    db_session.add(GraphExtractLog(id="logx", user_id="u1", note_id="md51",
                                   content_hash="h", status="pending", source_type="doc"))
    await db_session.commit()

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    assert (await db_session.execute(select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md51"))).scalars().all() == []


@pytest.mark.asyncio
async def test_run_extraction_replaces_stale_relations_by_provenance(db_session, session_factory, monkeypatch):
    """重抽时按来源溯源删除旧关系，插入新关系并打上来源标记。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model", make_fake_chat_model([
        '{"entities": [{"name": "实体A", "mentions": ["A"]}, {"name": "实体B", "mentions": ["B"]}], '
        '"relations": [{"source": "实体A", "target": "实体B", "relation_type": "新关系"}]}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    db_session.add(GraphEntity(id="ea", user_id="u1", name="实体A", display_name="实体A"))
    db_session.add(GraphEntity(id="eb", user_id="u1", name="实体B", display_name="实体B"))
    db_session.add(GraphRelation(id="r_old", user_id="u1", source_id="ea", target_id="eb",
                                 relation_type="旧关系", source_note_id="md51", source_type="doc"))
    await db_session.commit()

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="A 与 B", source_type="doc")

    rels = (await db_session.execute(select(GraphRelation).where(GraphRelation.user_id == "u1"))).scalars().all()
    assert len(rels) == 1
    assert rels[0].relation_type == "新关系"
    assert rels[0].source_note_id == "md51"
    assert rels[0].source_type == "doc"


@pytest.mark.asyncio
async def test_run_extraction_keeps_manual_relations_without_provenance(db_session, session_factory, monkeypatch):
    """无溯源标记的手动关系（图谱页手工连线）不被重抽自动清理。"""
    from app.core.background_init import init_manager
    monkeypatch.setattr(init_manager, "chat_model", make_fake_chat_model([
        '{"entities": [{"name": "实体A", "mentions": ["A"]}, {"name": "实体B", "mentions": ["B"]}], '
        '"relations": [{"source": "实体A", "target": "实体B", "relation_type": "新关系"}]}']))
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    db_session.add(GraphDoc(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphExtractLog(id="logd", user_id="u1", note_id="md51", content_hash="h",
                                   status="pending", source_type="doc"))
    db_session.add(GraphEntity(id="ea", user_id="u1", name="实体A", display_name="实体A"))
    db_session.add(GraphEntity(id="eb", user_id="u1", name="实体B", display_name="实体B"))
    # 手动关系（NULL 溯源）+ 旧抽取关系（doc 溯源）
    db_session.add(GraphRelation(id="r_man", user_id="u1", source_id="ea", target_id="eb",
                                 relation_type="手动关系", source_note_id=None, source_type=None))
    db_session.add(GraphRelation(id="r_stale", user_id="u1", source_id="ea", target_id="eb",
                                 relation_type="旧关系", source_note_id="md51", source_type="doc"))
    await db_session.commit()

    await _run_extraction("md51", "u1", "报告.pdf", content_hash("body"),
                          body="A 与 B", source_type="doc")

    rels = (await db_session.execute(select(GraphRelation).where(GraphRelation.user_id == "u1"))).scalars().all()
    by_type = {r.relation_type: r for r in rels}
    assert set(by_type) == {"手动关系", "新关系"}
    assert by_type["手动关系"].source_note_id is None
    assert by_type["新关系"].source_note_id == "md51"


@pytest.mark.asyncio
async def test_cleanup_doc_graph_deletes_orphans_keeps_shared_and_strips_sources(db_session, session_factory, monkeypatch):
    """清理文档图谱：删孤儿实体及其关系、保留共享实体、摘除 source_note_ids 引用。"""
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    from sqlalchemy import insert
    await db_session.execute(insert(GraphDoc).values(id="md51", user_id="u1", filename="报告.pdf"))
    db_session.add(GraphEntity(id="e1", user_id="u1", name="孤儿实体", display_name="孤儿实体",
                               source_note_ids=["md51"]))
    db_session.add(GraphEntity(id="e2", user_id="u1", name="共享实体", display_name="共享实体",
                               source_note_ids=["md51", "n1"]))
    await db_session.execute(insert(GraphEntityNote).values(id="en1", user_id="u1", entity_id="e1",
                                                            note_id="md51", source_type="doc"))
    await db_session.execute(insert(GraphEntityNote).values(id="en2", user_id="u1", entity_id="e2",
                                                            note_id="md51", source_type="doc"))
    await db_session.execute(insert(GraphEntityNote).values(id="en3", user_id="u1", entity_id="e2",
                                                            note_id="n1", source_type="note"))
    db_session.add(GraphRelation(id="r1", user_id="u1", source_id="e1", target_id="e2", relation_type="相关"))
    await db_session.execute(insert(GraphExtractLog).values(id="gl1", user_id="u1", note_id="md51",
                                                            content_hash="h", status="success",
                                                            source_type="doc"))
    await db_session.commit()

    await cleanup_doc_graph("u1", "md51")

    remaining = (await db_session.execute(select(GraphEntity).where(
        GraphEntity.user_id == "u1"))).scalars().all()
    assert [e.id for e in remaining] == ["e2"]
    assert remaining[0].source_note_ids == ["n1"]
    assert (await db_session.execute(select(GraphRelation).where(
        GraphRelation.user_id == "u1"))).scalars().all() == []
    # 共享实体 e2 保留其笔记来源关联 en3（doc 来源的 en1/en2 已清）
    remaining_links = (await db_session.execute(select(GraphEntityNote).where(
        GraphEntityNote.user_id == "u1"))).scalars().all()
    assert [r.id for r in remaining_links] == ["en3"]
    assert (await db_session.execute(select(GraphDoc).where(
        GraphDoc.user_id == "u1"))).scalars().all() == []


@pytest.mark.asyncio
async def test_cleanup_note_graph_deletes_orphan_entities(db_session):
    """笔记删除清理：仅被该笔记引用的实体成为孤儿被删除，共享实体保留。"""
    db_session.add(GraphEntity(id="e1", user_id="u1", name="笔记孤儿", display_name="笔记孤儿",
                               source_note_ids=["n1"]))
    db_session.add(GraphEntity(id="e2", user_id="u1", name="共享实体", display_name="共享实体",
                               source_note_ids=["n1", "md51"]))
    db_session.add(GraphEntityNote(id="en1", user_id="u1", entity_id="e1", note_id="n1", source_type="note"))
    db_session.add(GraphEntityNote(id="en2", user_id="u1", entity_id="e2", note_id="n1", source_type="note"))
    db_session.add(GraphEntityNote(id="en3", user_id="u1", entity_id="e2", note_id="md51", source_type="doc"))
    await db_session.commit()

    await cleanup_note_graph(db_session, "u1", "n1")

    remaining = (await db_session.execute(select(GraphEntity).where(
        GraphEntity.user_id == "u1"))).scalars().all()
    assert [e.id for e in remaining] == ["e2"]
    assert remaining[0].source_note_ids == ["md51"]


@pytest.mark.asyncio
async def test_cleanup_doc_graph_removes_doc_rows_and_deletes_orphan_entity(db_session, session_factory, monkeypatch):
    """清理文档图谱：无其他来源的实体成为孤儿被删除（含其关联）。"""
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    from sqlalchemy import insert
    await db_session.execute(insert(GraphEntity).values(id="e1", user_id="u1",
                                                        name="Python", display_name="Python"))
    await db_session.execute(insert(GraphDoc).values(id="md51", user_id="u1", filename="报告.pdf"))
    await db_session.execute(insert(GraphEntityNote).values(id="end1", user_id="u1", entity_id="e1",
                                                            note_id="md51", source_type="doc"))
    await db_session.execute(insert(GraphExtractLog).values(id="gd1", user_id="u1", note_id="md51",
                                                            content_hash="h", status="success",
                                                            source_type="doc"))
    await db_session.commit()

    await cleanup_doc_graph("u1", "md51")
    assert (await db_session.execute(
        select(GraphDoc).where(GraphDoc.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphEntityNote).where(GraphEntityNote.user_id == "u1"))).scalars().all() == []
    assert (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.user_id == "u1"))).scalars().all() == []
    # 无其他来源的孤儿实体被清理（与其他来源共享的实体才保留）
    assert (await db_session.execute(
        select(GraphEntity).where(GraphEntity.user_id == "u1"))).scalars().all() == []
