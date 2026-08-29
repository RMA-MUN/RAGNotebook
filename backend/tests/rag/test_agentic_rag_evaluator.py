import json

import pytest

from app.rag.agentic_rag import evaluator as evaluator_module
from app.rag.agentic_rag.evaluator import AnswerabilityEvaluator
from app.rag.agentic_rag.schemas import Evidence


class FakeChatModel:
    """ainvoke 返回预设文本（或抛预设异常），记录收到的 prompt。"""

    def __init__(self, content):
        self.content = content
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.content, Exception):
            raise self.content
        return type("Response", (), {"content": self.content})()


def _evidence(content="李白的代表作有《蜀道难》《将进酒》。", score=0.8):
    return Evidence(id="ev-1", source="knowledge_base", title="Doc", content=content, score=score)


def _llm_json(**overrides) -> str:
    payload = {"answerable": True, "confidence": 0.8, "reason": "evidence relevant", "web_queries": []}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_empty_evidence_short_circuits_without_llm():
    model = FakeChatModel(content="{}")
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("What is agentic RAG?", [])

    assert result.answerable is False
    assert result.confidence == 0.0
    assert result.web_queries == ["What is agentic RAG?"]
    assert model.prompts == []  # 快路径不应消耗 LLM 调用


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["OpenAI current price news", "今年 LangChain 版本 变化"])
async def test_freshness_terms_short_circuit_without_llm(query):
    model = FakeChatModel(content="{}")
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate(query, [_evidence()])

    assert result.answerable is False
    assert result.confidence < 0.5
    assert result.web_queries == [query]
    assert model.prompts == []


@pytest.mark.asyncio
async def test_llm_judges_unrelated_evidence_not_answerable():
    """白居易场景：笔记里没有该人物，top-k 噪声证据不构成"可答"。"""
    model = FakeChatModel(content=_llm_json(
        answerable=False, confidence=0.15,
        reason="Evidence is about quantum computing, not Bai Juyi.",
        web_queries=["白居易 唐代诗人"],
    ))
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("给我讲讲白居易", [_evidence(content="量子计算使用量子比特…")])

    assert result.answerable is False
    assert result.confidence == 0.15
    assert result.web_queries == ["白居易 唐代诗人"]
    assert len(model.prompts) == 1
    assert "给我讲讲白居易" in model.prompts[0]
    assert "量子计算" in model.prompts[0]  # 证据片段进入 prompt


@pytest.mark.asyncio
async def test_llm_judges_relevant_evidence_answerable():
    model = FakeChatModel(content=_llm_json(answerable=True, confidence=0.9, web_queries=[]))
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("李白的代表作有哪些", [_evidence()])

    assert result.answerable is True
    assert result.confidence == 0.9
    assert result.web_queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["false", "False", "0", "no"])
async def test_llm_string_answerable_is_coerced(raw):
    model = FakeChatModel(content=_llm_json(answerable=raw))
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("Tell me about Bai Juyi", [_evidence()])

    assert result.answerable is False
    assert result.web_queries == ["Tell me about Bai Juyi"]  # 不可答但缺 web_queries 时兜底原问题


@pytest.mark.asyncio
async def test_llm_malformed_json_falls_back_to_rules():
    model = FakeChatModel(content="这不是 JSON")
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("Summarize local architecture notes", [_evidence()])

    assert result.answerable is True
    assert result.confidence >= 0.5
    assert result.web_queries == []


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_rules():
    model = FakeChatModel(content=RuntimeError("llm down"))
    evaluator = AnswerabilityEvaluator(chat_model=model)

    result = await evaluator.evaluate("Summarize local architecture notes", [_evidence()])

    assert result.answerable is True
    assert result.web_queries == []


@pytest.mark.asyncio
async def test_no_available_model_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(evaluator_module, "_resolve_shared_chat_model", lambda: None)
    evaluator = AnswerabilityEvaluator()

    result = await evaluator.evaluate("Summarize local architecture notes", [_evidence()])

    assert result.answerable is True
    assert result.web_queries == []
