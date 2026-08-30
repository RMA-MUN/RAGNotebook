import pytest
from pydantic import ValidationError

from app.rag.agentic_rag.schemas import (
    AgenticRagResult,
    AnswerabilityResult,
    Evidence,
    RetrievalPlan,
    RetrievalStep,
)


def test_evidence_accepts_allowed_sources_and_defaults():
    evidence = Evidence(
        id="note-1",
        source="note",
        title="Reading note",
        content="Important local context",
    )

    assert evidence.source == "note"
    assert evidence.score is None
    assert evidence.url is None
    assert evidence.metadata == {}


@pytest.mark.parametrize("source", ["note", "knowledge_base", "web"])
def test_evidence_source_allows_only_supported_values(source):
    evidence = Evidence(id="ev-1", source=source, title="Title", content="Content")

    assert evidence.source == source


def test_evidence_source_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Evidence(id="ev-1", source="database", title="Title", content="Content")


def test_evidence_metadata_uses_independent_default_dicts():
    first = Evidence(id="ev-1", source="web", title="Title", content="Content")
    second = Evidence(id="ev-2", source="web", title="Title", content="Content")

    first.metadata["provider"] = "serper"


    assert second.metadata == {}


def test_retrieval_step_accepts_supported_tools_and_default_top_k():
    step = RetrievalStep(tool="hybrid_search", query="agentic rag")

    assert step.tool == "hybrid_search"
    assert step.query == "agentic rag"
    assert step.top_k == 5


@pytest.mark.parametrize(
    "tool",
    ["search_notes", "search_knowledge_base", "hybrid_search", "web_search"],
)
def test_retrieval_step_tool_allows_only_supported_values(tool):
    step = RetrievalStep(tool=tool, query="q")

    assert step.tool == tool


def test_retrieval_step_tool_rejects_unknown_value():
    with pytest.raises(ValidationError):
        RetrievalStep(tool="sql_search", query="q")


def test_retrieval_plan_defaults():
    plan = RetrievalPlan(need_retrieval=True, steps=[])

    assert plan.need_retrieval is True
    assert plan.steps == []
    assert plan.allow_web_fallback is False
    assert plan.reason == ""


def test_answerability_result_defaults():
    result = AnswerabilityResult(
        answerable=False,
        confidence=0.25,
        reason="Insufficient evidence",
    )

    assert result.answerable is False
    assert result.confidence == 0.25
    assert result.reason == "Insufficient evidence"
    assert result.web_queries == []


def test_answerability_web_queries_use_independent_default_lists():
    first = AnswerabilityResult(answerable=False, confidence=0.1, reason="Need web")
    second = AnswerabilityResult(answerable=False, confidence=0.1, reason="Need web")

    first.web_queries.append("latest agentic rag")

    assert second.web_queries == []


def test_agentic_rag_result_composes_contracts_and_defaults():
    evidence = Evidence(
        id="kb-1",
        source="knowledge_base",
        title="Manual",
        content="Relevant answer",
        score=0.91,
        metadata={"filename": "manual.pdf"},
    )
    plan = RetrievalPlan(
        need_retrieval=True,
        steps=[RetrievalStep(tool="search_knowledge_base", query="manual")],
        allow_web_fallback=True,
        reason="Requires local lookup",
    )
    result = AgenticRagResult(context="[1] Relevant answer", evidences=[evidence], plan=plan)

    assert result.context == "[1] Relevant answer"
    assert result.evidences == [evidence]
    assert result.plan == plan
    assert result.answerability is None
    assert result.used_web is False
