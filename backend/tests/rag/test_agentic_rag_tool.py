"""search_rag 工具单元测试：同 query 短路、请求级限次、thinking 回传、防抖输出。

测试统一 monkeypatch `app.rag.agentic_rag.service.AgenticRagService`，
断言 search_rag 是否真正触发了 RAG 管线。
"""
import pytest

from app.agent import agent_rag_tool as mod
from app.agent.agent_tools import set_current_user_id, set_thinking_callback
from app.rag.agentic_rag.schemas import Evidence


class FakeResult:
    def __init__(self, context="证据文本", evidences=None, used_web=False):
        self.context = context
        self.evidences = (
            evidences
            if evidences is not None
            else [
                Evidence(
                    id="note-default",
                    source="note",
                    title="默认笔记",
                    content="证据文本",
                )
            ]
        )
        self.used_web = used_web


class FakeResultWithEvidence(FakeResult):
    def __init__(self, context="证据文本", used_web=False):
        evidence = Evidence(
            id="note-1",
            source="note",
            title="参考笔记",
            content="x" * 600,
            score=0.0,
            url=None,
        )
        super().__init__(context=context, evidences=[evidence], used_web=used_web)


@pytest.fixture(autouse=True)
def _clean_guard():
    mod.reset_rag_guard()
    set_current_user_id(None)
    set_thinking_callback(None)
    yield
    mod.reset_rag_guard()
    set_current_user_id(None)
    set_thinking_callback(None)


@pytest.fixture
def fake_service(monkeypatch):
    calls = []

    class _FakeAgenticRagService:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, query, user_id, thinking_callback=None):
            calls.append({"query": query, "user_id": user_id, "cb": thinking_callback})
            return FakeResult(context=f"context:{query}")

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService", _FakeAgenticRagService
    )
    return calls


@pytest.fixture
def fake_service_with_evidence(monkeypatch):
    class _FakeAgenticRagService:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, query, user_id, thinking_callback=None):
            return FakeResultWithEvidence(context=f"context:{query}")

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService", _FakeAgenticRagService
    )


def test_normalize_query_strips_whitespace_and_punctuation():
    assert mod.normalize_query(" 重排序？方案。 ") == "重排序方案"
    assert mod.normalize_query("RAG vs Graph") == "ragvsgraph"


async def test_search_rag_short_circuits_on_pre_searched_query(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard(["重排序方案"])
    out = await mod.search_rag.ainvoke({"query": "重排序方案。"})
    assert "已在本轮资料中覆盖" in out
    assert fake_service == []


async def test_search_rag_runs_new_query_and_returns_framed_context(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    out = await mod.search_rag.ainvoke({"query": "全新的图谱维度"})
    assert out.startswith("[补充检索结果开始]")
    assert "检索角度：全新的图谱维度" in out
    assert "证据来源概况：笔记 1 条" in out
    assert "是否包含外部搜索：否" in out
    assert "[检索证据]" in out
    assert "context:全新的图谱维度" in out
    assert out.endswith("[补充检索结果结束]")
    assert fake_service == [{"query": "全新的图谱维度", "user_id": "u1", "cb": None}]


async def test_search_rag_emits_structured_supplemental_event(fake_service_with_evidence):
    events = []

    async def callback(event):
        events.append(event)

    set_current_user_id("u1")
    mod.set_tool_thinking_callback_for_test(callback)
    mod.init_rag_guard([])
    out = await mod.search_rag.ainvoke({"query": "补充角度"})

    assert events == [{
        "type": "thinking",
        "stage": "supplemental_retrieval",
        "content": "补充检索完成",
        "details": {
            "query": "补充角度",
            "status": "evidence",
            "evidence_count": 1,
            "results": [{
                "id": "note-1",
                "source": "note",
                "title": "参考笔记",
                "score": 0.0,
                "url": None,
                "preview": "x" * 500,
            }],
        },
    }]
    assert out.startswith("[补充检索结果开始]")
    assert "[检索证据]" in out
    assert out.endswith("[补充检索结果结束]")


async def test_search_rag_marks_empty_retrieval_as_non_evidence(fake_service, monkeypatch):
    class _EmptyAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            return FakeResult(context="", evidences=[])

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService", _EmptyAgenticRagService
    )
    set_current_user_id("u1")

    out = await mod.search_rag.ainvoke({"query": "没有资料的角度"})

    assert out.startswith("[补充检索结果开始]")
    assert "检索状态：未找到证据（非事实证据）" in out
    assert "[检索证据]\n无" in out
    assert out.endswith("[补充检索结果结束]")


async def test_search_rag_does_not_treat_context_without_evidence_as_factual(
    fake_service, monkeypatch
):
    class _ContextOnlyAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            return FakeResult(context="context without evidence", evidences=[])

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService",
        _ContextOnlyAgenticRagService,
    )
    events = []

    async def callback(event):
        events.append(event)

    set_current_user_id("u1")
    mod.set_tool_thinking_callback_for_test(callback)

    out = await mod.search_rag.ainvoke({"query": "仅有上下文"})

    assert "检索状态：未找到证据（非事实证据）" in out
    assert "[检索证据]\n无" in out
    assert events == []


async def test_search_rag_marks_failures_as_non_evidence(fake_service, monkeypatch):
    class _FailingAgenticRagService:
        async def run(self, query, user_id, thinking_callback=None):
            raise RuntimeError("检索服务不可用")

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService", _FailingAgenticRagService
    )
    set_current_user_id("u1")

    out = await mod.search_rag.ainvoke({"query": "失败的角度"})

    assert out.startswith("[补充检索结果开始]")
    assert "检索状态：检索失败（非事实证据）" in out
    assert "状态说明：检索失败: 检索服务不可用" in out
    assert "检索服务不可用" in out
    assert "[检索证据]\n无" in out
    assert out.endswith("[补充检索结果结束]")


async def test_search_rag_limits_to_two_calls_per_request(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    first = await mod.search_rag.ainvoke({"query": "角度一"})
    second = await mod.search_rag.ainvoke({"query": "角度二"})
    third = await mod.search_rag.ainvoke({"query": "角度三"})
    assert "已在本轮资料中覆盖" not in first and "已检索过 2 次" not in first
    assert "已在本轮资料中覆盖" not in second and "已检索过 2 次" not in second
    assert "已检索过 2 次" in third
    assert len(fake_service) == 2


async def test_search_rag_records_successful_query_to_guard(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    await mod.search_rag.ainvoke({"query": "同一个角度"})
    out = await mod.search_rag.ainvoke({"query": "同一个角度"})
    assert "已在本轮资料中覆盖" in out
    assert len(fake_service) == 1


async def test_search_rag_passes_thinking_callback(fake_service, monkeypatch):
    set_current_user_id("u1")
    cb = lambda event: None  # noqa: E731
    mod.set_tool_thinking_callback_for_test(cb)
    mod.init_rag_guard([])
    await mod.search_rag.ainvoke({"query": "图谱维度"})
    assert fake_service[0]["cb"] is cb


async def test_search_rag_without_guard_passes_through(fake_service):
    set_current_user_id("u1")
    out = await mod.search_rag.ainvoke({"query": "无护栏也放行"})
    assert "补充检索结果" in out
    assert len(fake_service) == 1


async def test_search_rag_returns_error_without_user():
    out = await mod.search_rag.ainvoke({"query": "随便问问"})
    assert "无法确定用户身份" in out


def test_build_pre_searched_queries_requires_need_retrieval():
    plan = type("P", (), {"need_retrieval": False, "steps": []})()
    assert mod.build_pre_searched_queries("原始问题", type("R", (), {"plan": plan})()) == []


def test_build_pre_searched_queries_includes_steps_and_web():
    step = type("S", (), {"query": "改写后的子问题"})
    plan = type("P", (), {"need_retrieval": True, "steps": [step]})()
    answerability = type("A", (), {"web_queries": ["web:新鲜事实"]})()
    result = type("R", (), {"plan": plan, "answerability": answerability})()
    assert mod.build_pre_searched_queries("原始问题", result) == [
        "原始问题",
        "改写后的子问题",
        "web:新鲜事实",
    ]
