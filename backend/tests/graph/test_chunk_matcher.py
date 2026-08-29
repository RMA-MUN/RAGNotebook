"""chunk_matcher 单测：纯文本切片 + 实体规则匹配（零 LLM）。"""
from app.graph.extraction.chunk_matcher import build_text_chunks, match_entities_in_chunks
from app.graph.schemas.graph import ExtractedEntity


def test_build_text_chunks_returns_list():
    text = "第一段内容。\n\n第二段内容。" * 100
    chunks = build_text_chunks(text)
    assert len(chunks) >= 2
    assert all(isinstance(c, str) and c for c in chunks)


def test_build_text_chunks_empty():
    assert build_text_chunks("") == []
    assert build_text_chunks("   ") == []


def test_match_basic_name():
    entities = [ExtractedEntity(name="Python")]
    matched = match_entities_in_chunks(entities, ["用 Python 写爬虫", "前端用 React"])
    assert matched == {"Python": [0]}


def test_match_case_insensitive_and_alias():
    entities = [ExtractedEntity(name="Neo4j", aliases=["neo4j", "图数据库"])]
    matched = match_entities_in_chunks(entities, ["Neo4j 是图数据库", "存储选型", "NEO4J 很快"])
    assert matched["Neo4j"] == [0, 2]


def test_match_single_char_term_ignored():
    entities = [ExtractedEntity(name="图", aliases=[])]
    assert match_entities_in_chunks(entities, ["图数据库", "图谱"]) == {}


def test_match_multiple_chunks_dedup():
    entities = [ExtractedEntity(name="知识图谱")]
    matched = match_entities_in_chunks(entities, ["知识图谱入门", "构建知识图谱", "其他"])
    assert matched == {"知识图谱": [0, 1]}


def test_match_no_hit_returns_empty():
    entities = [ExtractedEntity(name="Rust")]
    assert match_entities_in_chunks(entities, ["Python 与 Go"]) == {}
