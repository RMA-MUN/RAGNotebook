"""chat.py 前置 result → guard 预置 query 集合的推导测试。

避免在 chat.py 里重建整套 fake，直接构造一个伪 AgenticRagResult 结构，
验证 build_pre_searched_queries 的前置管线回填语义（由 chat.py 调用）。
"""
from app.agent import agent_rag_tool as mod


def _step(query, tool="hybrid_search"):
    return type("Step", (), {"tool": tool, "query": query, "top_k": 5})()


def _plan(need_retrieval, steps):
    return type("Plan", (), {"need_retrieval": True if need_retrieval else False,
                             "steps": steps, "allow_web_fallback": False, "reason": ""})()


def test_greeting_no_retrieval_yields_empty_seed():
    result = type("R", (), {"plan": _plan(False, []), "answerability": None})()
    assert mod.build_pre_searched_queries("你好", result) == []


def test_need_retrieval_seeds_original_and_steps():
    result = type("R", (), {
        "plan": _plan(True, [_step("子问题A"), _step("子问题B")]),
        "answerability": type("A", (), {"web_queries": []})(),
    })()
    assert mod.build_pre_searched_queries("原始问题", result) == [
        "原始问题", "子问题A", "子问题B",
    ]


def test_result_none_yields_empty_seed():
    assert mod.build_pre_searched_queries("原始问题", None) == []
