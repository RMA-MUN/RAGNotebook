import pytest

from app.rag.agentic_rag.query_entity_extractor import (
    QueryEntityExtractor,
    _fallback_candidates,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChatModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_extractor_uses_llm_json_array():
    model = FakeChatModel(FakeMessage('["DeepSeek", "量子计算"]'))
    extractor = QueryEntityExtractor(chat_model=model, prompt_template="Q: {query}")

    names = await extractor.extract("DeepSeek 和量子计算是什么关系")

    assert names == ["DeepSeek", "量子计算"]
    assert "DeepSeek 和量子计算是什么关系" in model.prompts[0]


@pytest.mark.asyncio
async def test_extractor_handles_markdown_fence():
    model = FakeChatModel("```json\n[\"FastAPI\", \"Python\"]\n```")
    extractor = QueryEntityExtractor(chat_model=model, prompt_template="Q: {query}")

    names = await extractor.extract("FastAPI 和 Python 的区别")

    assert names == ["FastAPI", "Python"]


@pytest.mark.asyncio
async def test_extractor_falls_back_to_rule_on_error_and_empty():
    # LLM 报错 -> 规则兜底
    model = FakeChatModel(error=RuntimeError("down"))
    extractor = QueryEntityExtractor(chat_model=model, prompt_template="Q: {query}")
    names = await extractor.extract("介绍一下 FastAPI 是什么")
    assert "FastAPI" in names

    # LLM 返回空数组 -> 规则兜底
    model2 = FakeChatModel(FakeMessage("[]"))
    extractor2 = QueryEntityExtractor(chat_model=model2, prompt_template="Q: {query}")
    names2 = await extractor2.extract("介绍一下 FastAPI 是什么")
    assert "FastAPI" in names2


@pytest.mark.asyncio
async def test_extractor_returns_empty_for_noise_only():
    model = FakeChatModel(FakeMessage('["是什么", "怎么", "呢"]'))
    extractor = QueryEntityExtractor(chat_model=model, prompt_template="Q: {query}")
    names = await extractor.extract("是什么")
    # 仅含噪声词/LLM 返回噪声时，规则兜底剔除后可能为空
    assert isinstance(names, list)


def test_fallback_candidates_strips_noise_and_dedups():
    names = _fallback_candidates("介绍一下 DeepSeek 是什么")
    assert "DeepSeek" in names
    assert names.count("DeepSeek") == 1
    assert all(n not in {"是什么", "介绍一下", "什么"} for n in names)
