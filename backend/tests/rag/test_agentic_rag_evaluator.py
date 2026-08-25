from app.rag.agentic_rag.evaluator import AnswerabilityEvaluator
from app.rag.agentic_rag.schemas import Evidence


def _evidence(content="Local fact"):
    return Evidence(id="ev-1", source="knowledge_base", title="Doc", content=content)


def test_evaluator_returns_not_answerable_and_web_query_when_evidence_empty():
    evaluator = AnswerabilityEvaluator()

    result = evaluator.evaluate("What is agentic RAG?", [])

    assert result.answerable is False
    assert result.confidence == 0.0
    assert result.web_queries == ["What is agentic RAG?"]


def test_evaluator_requests_web_search_for_freshness_terms_even_with_evidence():
    evaluator = AnswerabilityEvaluator()
    query = "OpenAI current price news"

    result = evaluator.evaluate(query, [_evidence()])

    assert result.answerable is False
    assert result.confidence < 0.5
    assert result.web_queries == [query]


def test_evaluator_treats_existing_evidence_as_answerable_for_non_fresh_query():
    evaluator = AnswerabilityEvaluator()

    result = evaluator.evaluate("Summarize local architecture notes", [_evidence()])

    assert result.answerable is True
    assert result.confidence >= 0.5
    assert result.web_queries == []


def test_evaluator_detects_chinese_freshness_terms():
    evaluator = AnswerabilityEvaluator()
    query = "今年 LangChain 版本 变化"

    result = evaluator.evaluate(query, [_evidence()])

    assert result.answerable is False
    assert result.web_queries == [query]
