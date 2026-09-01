import pytest

from app.rag.agentic_rag.schemas import (
    AnswerabilityResult,
    Evidence,
    RetrievalPlan,
    RetrievalStep,
)
from app.rag.agentic_rag.service import AgenticRagService


@pytest.fixture(autouse=True)
def _force_user_config_fallback(monkeypatch):
    """Task5 接线后 run() 会解析每用户模型；本套件主用注入假件测编排，
    故默认让 per-user 解析失败（回落注入假件）保持用例确定性；per-user 专项用例自行覆盖。"""
    import app.rag.agentic_rag.service as svc

    async def _fail_chat(user_id, streaming=True):
        raise RuntimeError("unit test: no user chat config")

    async def _fail_embed(user_id):
        raise ValueError("unit test: no embed config")

    monkeypatch.setattr(svc, "create_chat_model_for_user", _fail_chat)
    monkeypatch.setattr(svc, "create_embed_model_for_user", _fail_embed)


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
        "answerability",
        "web_search",
        "evidence_fusion",
        "context_ready",
    ]
    assert all(event["type"] == "thinking" for event in events)
    assert all("content" in event and "details" in event for event in events)
    plan_event = next(event for event in events if event["stage"] == "agentic_plan")
    assert plan_event["details"]["query"] == "agentic rag latest"
    assert plan_event["details"]["steps"][0]["query"] == "agentic rag"
    retrieval_event = next(event for event in events if event["stage"] == "local_retrieval")
    assert retrieval_event["details"]["results"][0]["preview"] == "Local fact"
    web_event = next(event for event in events if event["stage"] == "web_search")
    assert web_event["details"]["results"][0]["preview"] == "Web fact"


@pytest.mark.asyncio
async def test_run_builds_per_user_components(monkeypatch):
    """每用户配置可解析时，run() 用 per-user chat/embed 构建 planner/evaluator/retriever，
    并注入 per-user 实体抽取器；per-user 不可用（autouse）时才回落注入假件。"""
    import app.rag.agentic_rag.service as svc

    class RecordingPlanner:
        def __init__(self, chat_model=None):
            self.chat_model = chat_model
            self.queries = []

        async def plan(self, query):
            self.queries.append(query)
            return _plan(need_retrieval=True)

    class RecordingEvaluator:
        def __init__(self, chat_model=None):
            self.chat_model = chat_model

        async def evaluate(self, query, evidences):
            return _answerability(answerable=True)

    built = {}

    class RecordingRetriever:
        def __init__(self, note_service=None, session_factory=None,
                     query_entity_extractor=None, embed_model=None):
            self.note_service = note_service
            self.session_factory = session_factory
            self.query_entity_extractor = query_entity_extractor
            self.embed_model = embed_model
            built["retriever"] = self

        async def search(self, user_id, steps):
            built["searched"] = (user_id, list(steps))
            return []

    class FakeLocalRetriever:
        note_service = "note-svc"
        session_factory = "session-factory"

        async def search(self, user_id, steps):
            return []

    user_chat = object()
    user_embed = object()

    async def fake_chat(user_id, streaming=True):
        return user_chat

    async def fake_embed(user_id):
        return user_embed

    monkeypatch.setattr(svc, "create_chat_model_for_user", fake_chat)
    monkeypatch.setattr(svc, "create_embed_model_for_user", fake_embed)
    monkeypatch.setattr(svc, "AgenticRagPlanner", RecordingPlanner)
    monkeypatch.setattr(svc, "AnswerabilityEvaluator", RecordingEvaluator)
    monkeypatch.setattr(svc, "LocalRetriever", RecordingRetriever)

    service = AgenticRagService(
        planner=object(),
        local_retriever=FakeLocalRetriever(),
        evaluator=object(),
        web_search_client=FakeWebSearchClient(),
    )
    result = await service.run("query", user_id="user-1")

    assert built["searched"][0] == "user-1"
    assert [step.tool for step in built["searched"][1]] == ["hybrid_search"]
    assert built["retriever"].embed_model is user_embed
    assert built["retriever"].query_entity_extractor.chat_model is user_chat
    assert result.context == ""
    assert result.evidences == []
    assert result.answerability.answerable is True

