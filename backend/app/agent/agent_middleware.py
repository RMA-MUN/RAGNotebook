from typing import Any

try:
    from langchain.agents import AgentState
except ImportError:
    AgentState = dict[str, Any]

try:
    from langchain.agents.middleware import (
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

try:
    from langgraph.runtime import Runtime
except ImportError:
    Runtime = Any

from app.core.logger_handler import logger


@before_agent
def log_before_agent(status: AgentState, runtime: Runtime):
    """agent 运行前执行此函数"""
    logger.info(f"[before_agent] agent启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_agent
def log_after_agent(status: AgentState, runtime: Runtime):
    """agent 运行后执行此函数"""
    logger.info(f"[after_agent] agent运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@before_model
def log_before_model(status: AgentState, runtime: Runtime):
    """model 运行前执行此函数"""
    logger.info(f"[before_model] model启动， 输入：{status['messages']}， 共{len(status['messages'])}条消息")


@after_model
def log_after_model(status: AgentState, runtime: Runtime):
    """model 运行后执行此函数"""
    logger.info(f"[after_model] model运行结束， 输出：{status['messages']}， 共{len(status['messages'])}条消息")

@wrap_model_call
def model_call_hook(request, handler):
    """model 调用前执行此函数"""
    logger.info("模型调用了")
    return handler(request)

@wrap_tool_call
def tool_call_hook(request, handler):
    """tool 调用前执行此函数"""
    logger.info(f"工具{request.tool_call['name']}调用了, 传入参数{request.tool_call['args']}")
    return handler(request)


def get_middleware():
    """返回本模块的所有中间件"""
    return [
        log_before_agent,
        log_after_agent,
        log_before_model,
        log_after_model,
        model_call_hook,
        tool_call_hook,
    ]
