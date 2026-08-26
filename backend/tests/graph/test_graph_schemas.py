from app.graph.schemas.graph import (
    Entity,
    EntityIn,
    ExtractResult,
    ExtractedEntity,
    GraphEdge,
    GraphNode,
    GraphView,
)


def test_entity_in_defaults():
    e = EntityIn(name="Python", aliases=["py"])
    assert e.display_name is None
    assert e.confidence == 0.0
    assert e.source_note_ids == []


def test_extract_result_shape():
    result = ExtractResult(
        entities=[ExtractedEntity(name="Python", mentions=["用 Python 写"])],
        relations=[],
    )
    assert result.entities[0].name == "Python"


def test_graph_view_roundtrip():
    v = GraphView(
        nodes=[GraphNode(id="n1", label="笔记A", node_type="note")],
        edges=[GraphEdge(id="e1", source="n1", target="e2", kind="wiki")],
    )
    d = v.model_dump()
    assert d["nodes"][0]["node_type"] == "note"
