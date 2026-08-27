"""Agent 中间件钩子测试。

`app.agent.agent_middleware` 用 langchain 的 middleware 装饰器（before/after/wrap）
注册 6 个自定义钩子，并追加 3 个官方中间件（模型重试/工具重试/工具调用限流）：
- before/after 钩子只返回 None（记录摘要日志）；
- wrap 钩子必须调用 handler 并原样返回其结果；
- model_call_hook 额外提取 usage_metadata、计时并推送 model_metrics 思考事件；
- 慢调用（超过 SLOW_MODEL_CALL_MS）记录 WARNING。
"""
import asyncio
from types import SimpleNamespace

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from app.agent import agent_middleware as mw_module
from app.agent.agent_middleware import get_middleware
from app.agent.agent_tools import set_thinking_callback


class FakeLogger:
    """捕获 logger 调用的替身（loguru 与 caplog 不兼容）。"""

    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, msg, *args, **kwargs):
        self.infos.append(msg)

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg)


def test_get_middleware_returns_all_hooks_in_order():
    mw = get_middleware()
    assert len(mw) == 9
    assert [m.name for m in mw] == [
        "log_before_agent",
        "log_after_agent",
        "log_before_model",
        "log_after_model",
        "model_call_hook",
        "tool_call_hook",
        "ModelRetryMiddleware",
        "ToolRetryMiddleware",
        "ToolCallLimitMiddleware",
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
    """wrap_model_call 必须调用 handler 并透传其结果（异步上下文）。"""
    mw = get_middleware()
    calls = []

    async def handler(request):
        calls.append(request)
        return "model-output"

    request = SimpleNamespace(tool_call=None)
    result = asyncio.run(mw[4].awrap_model_call(request, handler))
    assert result == "model-output"
    assert calls == [request]


def test_tool_call_hook_wraps_handler():
    """wrap_tool_call 读取 request.tool_call 名称/参数后调用 handler（异步上下文）。"""
    mw = get_middleware()
    calls = []

    async def handler(request):
        calls.append(request)
        return "tool-output"

    request = SimpleNamespace(tool_call={"name": "search_notes_tool", "args": {"query": "测试"}})
    result = asyncio.run(mw[5].awrap_tool_call(request, handler))
    assert result == "tool-output"
    assert calls == [request]


def test_model_call_hook_emits_metrics_event():
    """带 usage_metadata 的 ModelResponse → 推送 model_metrics 思考事件并透传结果。"""
    mw = get_middleware()
    seen = []

    async def thinking_callback(data: dict):
        seen.append(data)

    set_thinking_callback(thinking_callback)
    try:
        request = SimpleNamespace(
            model=SimpleNamespace(model_name="mimo-v2.5"),
            messages=[HumanMessage(content="hi")],
        )

        async def handler(request):
            await asyncio.sleep(0.005)
            return ModelResponse(result=[AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 1081, "output_tokens": 92, "total_tokens": 1173},
            )])

        result = asyncio.run(mw[4].awrap_model_call(request, handler))
    finally:
        set_thinking_callback(None)

    assert result.result[0].content == "ok"
    assert len(seen) == 1
    event = seen[0]
    assert event["type"] == "thinking"
    assert event["stage"] == "model_metrics"
    assert event["details"]["model"] == "mimo-v2.5"
    assert event["details"]["input_tokens"] == 1081
    assert event["details"]["output_tokens"] == 92
    assert event["details"]["total_tokens"] == 1173
    assert event["details"]["duration_ms"] > 0


def test_model_call_hook_skips_metrics_for_plain_response(monkeypatch):
    """非 ModelResponse 返回值：不透传 usage，不推送事件，原样返回。"""
    mw = get_middleware()
    fake_logger = FakeLogger()
    monkeypatch.setattr(mw_module, "logger", fake_logger)
    seen = []

    async def thinking_callback(data: dict):
        seen.append(data)

    set_thinking_callback(thinking_callback)
    try:
        request = SimpleNamespace(model=SimpleNamespace(model_name="mimo-v2.5"))

        async def handler(request):
            return "plain-output"

        result = asyncio.run(mw[4].awrap_model_call(request, handler))
    finally:
        set_thinking_callback(None)

    assert result == "plain-output"
    assert seen == []


def test_model_call_hook_warns_on_slow_call(monkeypatch):
    """耗时超过 SLOW_MODEL_CALL_MS 的模型调用记录 WARNING。"""
    mw = get_middleware()
    fake_logger = FakeLogger()
    monkeypatch.setattr(mw_module, "logger", fake_logger)
    monkeypatch.setattr(mw_module, "SLOW_MODEL_CALL_MS", 1)

    request = SimpleNamespace(model=SimpleNamespace(model_name="mimo-v2.5"))

    async def handler(request):
        await asyncio.sleep(0.01)
        return "ok"

    result = asyncio.run(mw[4].awrap_model_call(request, handler))
    assert result == "ok"
    assert any("mimo-v2.5" in msg for msg in fake_logger.warnings)


def test_tool_call_hook_truncates_long_args(monkeypatch):
    """工具参数过长时日志中截断，且调用原样透传。"""
    mw = get_middleware()
    fake_logger = FakeLogger()
    monkeypatch.setattr(mw_module, "logger", fake_logger)

    async def handler(request):
        return "tool-output"

    request = SimpleNamespace(tool_call={
        "name": "search_notes_tool",
        "args": {"query": "x" * 500},
    })
    result = asyncio.run(mw[5].awrap_tool_call(request, handler))
    assert result == "tool-output"
    assert any("search_notes_tool" in msg for msg in fake_logger.infos)