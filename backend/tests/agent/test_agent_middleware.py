"""Agent 中间件钩子测试。

`app.agent.agent_middleware` 用 langchain 的 middleware 装饰器（before/after/wrap）
注册 6 个钩子：agent/model 生命周期日志 + model/tool 调用包装。
这里直接以桩对象调用中间件实例方法（.before_agent/.wrap_model_call/...）验证其行为：
- before/after 钩子只返回 None（仅记录日志）；
- wrap 钩子必须调用 handler 并原样返回其结果。
"""
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from app.agent.agent_middleware import get_middleware


def test_get_middleware_returns_all_hooks_in_order():
    mw = get_middleware()
    assert len(mw) == 6
    assert [m.name for m in mw] == [
        "log_before_agent",
        "log_after_agent",
        "log_before_model",
        "log_after_model",
        "model_call_hook",
        "tool_call_hook",
    ]


def test_before_after_hooks_accept_state_and_runtime():
    """四个 before/after 钩子以 (status, runtime) 调用，返回 None（纯日志）。"""
    mw = get_middleware()
    status = {"messages": [HumanMessage(content="hi")]}
    runtime = object()

    assert mw[0].before_agent(status, runtime) is None
    assert mw[1].after_agent(status, runtime) is None
    assert mw[2].before_model(status, runtime) is None
    assert mw[3].after_model(status, runtime) is None


def test_model_call_hook_wraps_handler():
    """wrap_model_call 必须调用 handler 并透传其结果。"""
    mw = get_middleware()
    calls = []

    def handler(request):
        calls.append(request)
        return "model-output"

    request = SimpleNamespace(tool_call=None)
    result = mw[4].wrap_model_call(request, handler)
    assert result == "model-output"
    assert calls == [request]


def test_tool_call_hook_wraps_handler():
    """wrap_tool_call 读取 request.tool_call 名称/参数后调用 handler。"""
    mw = get_middleware()
    calls = []

    def handler(request):
        calls.append(request)
        return "tool-output"

    request = SimpleNamespace(tool_call={"name": "search_notes_tool", "args": {"query": "测试"}})
    result = mw[5].wrap_tool_call(request, handler)
    assert result == "tool-output"
    assert calls == [request]
