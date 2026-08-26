import pytest

from app.graph.extraction.entity_extractor import _extract_json, extract_entities
from tests.fakes import make_fake_chat_model


def test_extract_json_strips_codeblock():
    raw = '```json\n{"entities": []}\n```'
    assert '"entities"' in _extract_json(raw)


def test_extract_json_handles_plain_prefix():
    raw = '以下是结果：\n{"entities": [{"name": "A"}]}\n完毕'
    assert '"A"' in _extract_json(raw)


@pytest.mark.asyncio
async def test_extract_entities_returns_structured_result():
    model = make_fake_chat_model(
        ['{"entities": [{"name": "Python", "type": "tech", "mentions": ["用 Python"]}], '
         '"relations": []}']
    )
    result = await extract_entities("我的笔记", "用 Python 写爬虫", model)
    assert result.entities[0].name == "Python"
    assert result.entities[0].type == "tech"


@pytest.mark.asyncio
async def test_extract_entities_tolerates_garbage_and_returns_empty():
    model = make_fake_chat_model(["抱歉，无法理解"])
    result = await extract_entities("标题", "正文", model)
    assert result.entities == []
