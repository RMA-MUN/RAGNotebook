import pytest

from app.graph.storage.graph_store import GraphStore


def test_graph_store_is_abstract():
    with pytest.raises(TypeError):
        GraphStore()  # ABC 不能实例化