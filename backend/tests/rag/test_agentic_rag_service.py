import pytest

from app.rag.agentic_rag.schemas import (
    AnswerabilityResult,
    Evidence,
    RetrievalPlan,
    RetrievalStep,
)
from app.rag.agentic_rag.service import AgenticRagService


class FakePlanner:
    def __init__(self, plan):
        self.plan_result = plan
        self.queries = []

    async def plan(self, query):
        self.queries.append(query)
        return self.plan_result


class FakeLocalRetriever:
    def __init__(self, evidences=None):
        self.evidences = evidences or []
        self.calls = []

    async def search(self, user_id, steps):
        self.calls.append({"user_id": user_id, "steps": steps})
        return self.evidences


class FakeEvaluator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def evaluate(self, query, evidences):
        self.calls.append({"query": query, "evidences": list(evidences)})
        return self.result


class FakeWebSearchClient:
    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    async def search(self, query, max_results=5):
        self.calls.append({"query": query, "max_results": max_results})
        return self.results


def _plan(*, need_retrieval=True, allow_web_fallback=False):
    return RetrievalPlan(
        need_retrieval=need_retrieval,
        steps=[RetrievalStep(tool="hybrid_search", query="agentic rag")] if need_retrieval else [],
        allow_web_fallback=allow_web_fallback,
        reason="test plan",
    )


def _answerability(*, answerable=True, web_queries=None):
    return AnswerabilityResult(
        answerable=answerable,
        confidence=0.8 if answerable else 0.2,
        reason="test answerability",
        web_queries=web_queries or [],
    )


def _evidence(evidence_id, source, content, title=None):
    return Evidence(
        id=evidence_id,
        source=source,
        title=title or evidence_id,
        content=content,
        url="https://example.com/result" if source == "web" else None,
    )


@pytest.mark.asyncio
async def test_run_returns_empty_context_when_planner_says_no_retrieval():
    planner = FakePlanner(_plan(need_retrieval=False))
    local_retriever = FakeLocalRetriever([_evidence("n1", "note", "private fact")])
    evaluator = FakeEvaluator(_answerability(answerable=True))
    web_search = FakeWebSearchClient([_evidence("w1", "web", "web fact")])
    service = AgenticRagService(
        planner=planner,
        local_retriever=local_retriever,
        evaluator=evaluator,
        web_search_client=web_search,
    )

    result = await service.run("hello", user_id="user-1")

    assert result.context == ""
    assert result.evidences == []
    assert result.plan.need_retrieval is False
    assert result.answerability is None
    assert result.used_web is False
    assert planner.queries == ["hello"]
    assert local_retriever.calls == []
    assert evaluator.calls == []
    assert web_search.calls == []


@pytest.mark.asyncio
async def test_run_uses_local_evidence_without_web_when_answerable():
    local_evidence = _evidence("kb1", "knowledge_base", "Agentic RAG plans retrieval.", "KB Doc")
    planner = FakePlanner(_plan(need_retrieval=True, allow_web_fallback=True))
    local_retriever = FakeLocalRetriever([local_evidence])
    evaluator = FakeEvaluator(_answerability(answerable=True))
    web_search = FakeWebSearchClient([_evidence("w1", "web", "web fact")])
    service = AgenticRagService(
        planner=planner,
        local_retriever=local_retriever,
        evaluator=evaluator,
        web_search_client=web_search,
    )

    result = await service.run("agentic rag", user_id="user-1")

    assert result.evidences == [local_evidence]
    assert "来源：知识库《KB Doc》" in result.context
    assert "Agentic RAG plans retrieval." in result.context
    assert result.answerability.answerable is True
    assert result.used_web is False
    assert local_retriever.calls[0]["user_id"] == "user-1"
    assert web_search.calls == []


@pytest.mark.asyncio
async def test_run_falls_back_to_web_and_keeps_local_evidence_first():
    local_evidence = _evidence("n1", "note", "Local background fact", "Note")
    web_evidence = _evidence("w1", "web", "Fresh web fact", "Web Result")
    planner = FakePlanner(_plan(need_retrieval=True, allow_web_fallback=True))
    local_retriever = FakeLocalRetriever([local_evidence])
    evaluator = FakeEvaluator(_answerability(answerable=False, web_queries=["fresh agentic rag news"]))
    web_search = FakeWebSearchClient([web_evidence])
    service = AgenticRagService(
        planner=planner,
        local_retriever=local_retriever,
        evaluator=evaluator,
        web_search_client=web_search,
    )

    result = await service.run("agentic rag latest", user_id="user-1")

    assert result.evidences == [local_evidence, web_evidence]
    assert result.context.index("来源：笔记《Note》") < result.context.index("来源：外部搜索《Web Result》")
    assert result.used_web is True
    assert web_search.calls == [{"query": "fresh agentic rag news", "max_results": 5}]


@pytest.mark.asyncio
async def test_run_emits_local_searching_before_local_evidence():
    events = []

    class TrackingLocalRetriever(FakeLocalRetriever):
        async def search(self, user_id, steps):
            events.append("local_work")
            return await super().search(user_id, steps)

    async def thinking_callback(event):
        events.append(event)

    service = AgenticRagService(
        planner=FakePlanner(_plan()),
        local_retriever=TrackingLocalRetriever([_evidence("n1", "note", "Local fact")]),
        evaluator=FakeEvaluator(_answerability(answerable=True)),
        web_search_client=FakeWebSearchClient(),
    )

    await service.run("local query", user_id="user-1", thinking_callback=thinking_callback)

    local_events = [event for event in events if isinstance(event, dict) and event["stage"] == "local_retrieval"]
    assert local_events[0]["details"] == {"status": "searching"}
    assert local_events[1]["details"]["status"] == "evidence"
    assert events.index(local_events[0]) < events.index("local_work") < events.index(local_events[1])
    assert "content" not in local_events[0]["details"]


@pytest.mark.asyncio
async def test_run_emits_web_searching_before_web_evidence():
    events = []

    class TrackingWebSearchClient(FakeWebSearchClient):
        async def search(self, query, max_results=5):
            events.append("web_work")
            return await super().search(query, max_results)

    async def thinking_callback(event):
        events.append(event)

    service = AgenticRagService(
        planner=FakePlanner(_plan()),
        local_retriever=FakeLocalRetriever([_evidence("n1", "note", "Local fact")]),
        evaluator=FakeEvaluator(_answerability(answerable=False, web_queries=["web query"])),
        web_search_client=TrackingWebSearchClient([_evidence("w1", "web", "Web fact")]),
    )

    await service.run("web query", user_id="user-1", thinking_callback=thinking_callback)

    web_events = [event for event in events if isinstance(event, dict) and event["stage"] == "web_search"]
    assert web_events[0]["details"] == {"status": "searching"}
    assert web_events[1]["details"]["status"] == "evidence"
    assert events.index(web_events[0]) < events.index("web_work") < events.index(web_events[1])
    assert "content" not in web_events[0]["details"]


@pytest.mark.asyncio
async def test_run_triggers_web_when_not_answerable_even_if_plan_vetoes():
    """白居易场景：planner 没开 web 回落，但本地证据不可答时仍应兜底搜网。"""
    local_evidence = _evidence("n1", "note", "Unrelated quantum note", "量子计算入门")
    web_evidence = _evidence("w1", "web", "Bai Juyi was a Tang dynasty poet.", "白居易")
    planner = FakePlanner(_plan(need_retrieval=True, allow_web_fallback=False))
    local_retriever = FakeLocalRetriever([local_evidence])
    evaluator = FakeEvaluator(_answerability(answerable=False, web_queries=["白居易 唐代诗人"]))
    web_search = FakeWebSearchClient([web_evidence])
    service = AgenticRagService(
        planner=planner,
        local_retriever=local_retriever,
        evaluator=evaluator,
        web_search_client=web_search,
    )

    result = await service.run("给我讲讲白居易", user_id="user-1")

    assert result.used_web is True
    assert web_search.calls == [{"query": "白居易 唐代诗人", "max_results": 5}]
    assert result.evidences == [local_evidence, web_evidence]
    assert "来源：外部搜索《白居易》" in result.context


@pytest.mark.asyncio
async def test_run_emits_thinking_events_for_orchestration_stages():
    events = []

    async def thinking_callback(event):
        events.append(event)

    planner = FakePlanner(_plan(need_retrieval=True, allow_web_fallback=True))
    local_retriever = FakeLocalRetriever([_evidence("n1", "note", "Local fact", "Note")])
    evaluator = FakeEvaluator(_answerability(answerable=False, web_queries=[]))
    web_search = FakeWebSearchClient([_evidence("w1", "web", "Web fact", "Web")])
    service = AgenticRagService(
        planner=planner,
        local_retriever=local_retriever,
        evaluator=evaluator,
        web_search_client=web_search,
    )

    await service.run("agentic rag latest", user_id="user-1", thinking_callback=thinking_callback)

    assert [event["stage"] for event in events] == [
        "agentic_plan",
        "local_retrieval",
        "local_retrieval",
        "answerability",
        "web_search",
        "web_search",
        "evidence_fusion",
        "context_ready",
    ]
    assert all(event["type"] == "thinking" for event in events)
    assert all("content" in event and "details" in event for event in events)
    plan_event = next(event for event in events if event["stage"] == "agentic_plan")
    assert plan_event["details"]["query"] == "agentic rag latest"
    assert plan_event["details"]["steps"][0]["query"] == "agentic rag"
    retrieval_event = [event for event in events if event["stage"] == "local_retrieval"][1]
    assert retrieval_event["details"]["status"] == "evidence"
    assert retrieval_event["details"]["results"][0]["preview"] == "Local fact"
    web_event = [event for event in events if event["stage"] == "web_search"][1]
    assert web_event["details"]["status"] == "evidence"
    assert web_event["details"]["results"][0]["preview"] == "Web fact"
