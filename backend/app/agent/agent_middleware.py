"""Agent 中间件：观测增强 + 健壮性。

自定义钩子（6 个）：
- before/after_agent、before/after_model：摘要日志（消息数/角色/字符数），不刷全量 messages；
- wrap_model_call（model_call_hook）：计时 + token 用量统计（usage_metadata）+ 慢调用告警
  + 经 thinking_callback contextvar 推送 model_metrics 思考事件；
- wrap_tool_call（tool_call_hook）：计时 + 参数截断日志。

追加官方中间件（3 个，防死循环与失败兜底，对齐旧 AgentExecutor 的 max_iterations）：
- ModelRetryMiddleware：模型调用失败退避重试；
- ToolRetryMiddleware：工具调用失败重试；
- ToolCallLimitMiddleware：单轮工具调用上限。
"""
import inspect
import time
from typing import Any

try:
    from langchain.agents import AgentState
except ImportError:
    AgentState = dict[str, Any]

try:
    from langchain.agents.middleware import (
        ModelRetryMiddleware,
        ToolCallLimitMiddleware,
        ToolRetryMiddleware,
        after_agent,
        after_model,
        before_agent,
        before_model,
        wrap_model_call,
        wrap_tool_call,
    )
except ImportError:
    class _HookMiddleware:
        def __init__(self, name, method_name, func):
            self.name = name
            self._method_name = method_name
            self._func = func

        def __getattr__(self, item):
            if item == self._method_name:
                return self._func
            raise AttributeError(item)

    def _fallback_middleware(method_name):
        def decorator(func):
            return _HookMiddleware(func.__name__, method_name, func)
        return decorator

    before_agent = _fallback_middleware("before_agent")
    after_agent = _fallback_middleware("after_agent")
    before_model = _fallback_middleware("before_model")
    after_model = _fallback_middleware("after_model")
    wrap_model_call = _fallback_middleware("wrap_model_call")
    wrap_tool_call = _fallback_middleware("wrap_tool_call")
    ModelRetryMiddleware = None
    ToolRetryMiddleware = None
    ToolCallLimitMiddleware = None

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = Any

from app.agent.agent_tools import get_thinking_callback_from_context
from app.core.logger_handler import logger

SLOW_MODEL_CALL_MS = 5000
TOOL_ARGS_LOG_LIMIT = 200


def _messages_summary(messages) -> str:
    """把消息列表压缩为摘要：数量 + 角色序列 + 总字符数。"""
    roles = [type(m).__name__ for m in (messages or [])]
    chars = sum(len(str(getattr(m, "content", ""))) for m in (messages or []))
    return f"{len(roles)}条消息 [{', '.join(roles)}] · {chars}字符"


@before_agent
def log_before_agent(status: AgentState, runtime: Runtime):
    """agent 运行前执行此函数"""
    logger.info(f"[before_agent] agent启动，输入：{_messages_summary(status.get('messages', []))}")


@after_agent
def log_after_agent(status: AgentState, runtime: Runtime):
    """agent 运行后执行此函数"""
    logger.info(f"[after_agent] agent运行结束，输出：{_messages_summary(status.get('messages', []))}")

@before_model
def log_before_model(status: AgentState, runtime: Runtime):
    """model 运行前执行此函数"""
    logger.info(f"[before_model] model启动，输入：{_messages_summary(status.get('messages', []))}")


@after_model
def log_after_model(status: AgentState, runtime: Runtime):
    """model 运行后执行此函数"""
    logger.info(f"[after_model] model运行结束，输出：{_messages_summary(status.get('messages', []))}")


def _extract_usage(response) -> dict | None:
    """从 ModelResponse.result 首条消息提取 token 用量；非 ModelResponse 返回 None。"""
    messages = getattr(response, "result", None)
    if not messages:
        return None
    usage = getattr(messages[0], "usage_metadata", None)
    if not usage:
        return None
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


async def _push_thinking(stage: str, content: str, details: dict):
    """经 contextvar 回调推送 thinking 事件（无回调则静默跳过）。"""
    callback = get_thinking_callback_from_context()
    if callback is None:
        return
    result = callback({
        "type": "thinking",
        "stage": stage,
        "content": content,
        "details": details,
    })
    if inspect.isawaitable(result):
        await result


@wrap_model_call
async def model_call_hook(request, handler):
    """model 调用包装：计时 + token 用量统计 + 慢调用告警 + 推送 model_metrics 事件。"""
    start = time.perf_counter()
    response = await handler(request)
    duration_ms = (time.perf_counter() - start) * 1000

    model = getattr(request, "model", None)
    model_name = getattr(model, "model_name", None) or (type(model).__name__ if model else "unknown")
    usage = _extract_usage(response)
    usage_str = (f"in {usage['input_tokens']} / out {usage['output_tokens']} / total {usage['total_tokens']}"
                 if usage else "无 usage 信息")
    logger.info(f"模型调用 {model_name} 耗时 {duration_ms:.0f}ms · {usage_str}")
    if duration_ms > SLOW_MODEL_CALL_MS:
        logger.warning(f"模型调用过慢: {model_name} 耗时 {duration_ms:.0f}ms")

    if usage is not None:
        await _push_thinking("model_metrics", "模型调用完成", {
            "model": model_name,
            "duration_ms": round(duration_ms, 1),
            **usage,
        })
    return response


@wrap_tool_call
async def tool_call_hook(request, handler):
    """tool 调用包装：计时 + 参数截断日志（thinking 事件由 agent.py 的 on_tool_start/end 负责）。"""
    tool_call = getattr(request, "tool_call", {}) or {}
    name = tool_call.get("name", "unknown_tool")
    args_str = str(tool_call.get("args"))
    if len(args_str) > TOOL_ARGS_LOG_LIMIT:
        args_str = args_str[:TOOL_ARGS_LOG_LIMIT] + "..."
    start = time.perf_counter()
    response = await handler(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(f"工具 {name} 调用了, 传入参数 {args_str}, 耗时 {duration_ms:.0f}ms")
    return response


def get_middleware():
    """返回本模块的所有中间件（自定义钩子 + 官方健壮性中间件）。"""
    middleware = [
        log_before_agent,
        log_after_agent,
        log_before_model,
        log_after_model,
        model_call_hook,
        tool_call_hook,
    ]
    if ModelRetryMiddleware is not None:
        middleware += [
            ModelRetryMiddleware(max_retries=2),
            ToolRetryMiddleware(max_retries=1),
            ToolCallLimitMiddleware(run_limit=5),
        ]
    return middleware