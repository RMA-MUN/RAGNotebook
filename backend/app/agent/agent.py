import asyncio
import inspect
import json
import time
import uuid
from collections.abc import AsyncGenerator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.agent.agent_middleware import DEFAULT_APPROVAL_TOOLS
from app.agent.agent_rag_tool import init_rag_guard, search_rag
from app.agent.agent_tools import (
    create_note_tool,
    get_note_stats_tool,
    get_related_notes_tool,
    get_thinking_callback_from_context,
    get_today_reviews_tool,
    get_user_info_tools,
    mark_reviewed_tool,
    search_notes_tool,
    set_current_user_id,
    set_thinking_callback,
    what_time_is_now,
)
from app.agent.checkpoint.mysql_saver import MySQLCheckpointSaver
from app.core.logger_handler import logger
from app.core.settings import settings
from app.services import session_manager as sm
from app.utils.prompt_loader import load_prompt


class AgentFactory:
    """
    生产 Agent 工厂类
    支持：
    - 每次调用创建全新的 LangChain 1.0+ create_agent 编译图实例
    - 动态注入工具、提示词、模型配置、中间件
    - 支持异步流式调用（astream_events v2）
    """

    def __init__(
            self,
            model: str = "qwen3-max",
            api_key: str | None = None,
            default_tools: list[BaseTool] | None = None,
            default_middleware: list | None = None,
            default_system_prompt: str | None = None,
            approval_tools: dict | None = None,
    ):
        """
        初始化工厂配置（仅配置，不创建实例）
        :param model: 默认模型名称
        :param api_key: 默认 API Key（不传则从env读取）
        :param default_tools: 默认工具列表
        :param default_system_prompt: 默认系统提示词
        :param approval_tools: HITL 审批白名单（{工具名: {"allowed_decisions": [...]}}）
        """
        self.model = model
        self.api_key = api_key or settings.CHAT_API_KEY or None
        self.default_tools = default_tools or self._get_default_tools()
        self.approval_tools = approval_tools or DEFAULT_APPROVAL_TOOLS
        self.default_middleware = default_middleware or self._get_default_middleware()
        self.default_system_prompt = default_system_prompt or self._get_default_system_prompt()

    @staticmethod
    def _get_default_tools() -> list[BaseTool]:
        """获取默认工具列表"""
        return [
            what_time_is_now,
            get_user_info_tools,
            search_notes_tool,
            get_note_stats_tool,
            get_today_reviews_tool,
            mark_reviewed_tool,
            create_note_tool,
            get_related_notes_tool,
            search_rag,
        ]

    def _get_default_middleware(self) -> list:
        """获取默认中间件列表（含 HITL 审批中间件，白名单可配置）"""
        try:
            from app.agent.agent_middleware import get_middleware

            return get_middleware(self.approval_tools)
        except ImportError:
            logger.warning("Agent middleware unavailable; continuing without middleware.", exc_info=True)
            return []

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: str | None = None):
        """内部方法：创建聊天模型实例（统一 OpenAI 兼容协议）"""
        from app.utils.factory import create_chat_openai

        model = custom_model or settings.OPENAI_MODEL_NAME or "gpt-4o-mini"
        logger.info(f"🤖 Agent使用OpenAI兼容模型: {model}")
        return create_chat_openai(
            model=model,
            api_key=settings.OPENAI_API_KEY or None,
            base_url=settings.OPENAI_BASE_URL or None,
            streaming=True,
            top_p=0.7,
        )

    def create_agent(
            self,
            custom_tools: list[BaseTool] | None = None,
            custom_model: str | None = None,
            custom_system_prompt: str | None = None,
            **kwargs
    ):
        """
        核心工厂方法：创建 LangChain 1.0+ create_agent 编译图实例。
        每次调用都会生成新的实例，彻底避免全局状态污染。

        :param custom_tools: 自定义工具列表（覆盖默认）
        :param custom_model: 自定义模型（覆盖默认）
        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :param kwargs: 其他 create_agent 参数（debug/name 等）
        :return: 全新的 CompiledStateGraph 实例
        """
        # 1. 创建组件（每次都重新创建，避免全局状态污染）
        chat_model = self._create_chat_model(custom_model)
        tools = custom_tools or self.default_tools
        system_prompt = custom_system_prompt or self.default_system_prompt

        # 2. 创建 Agent（LangGraph 编译图）
        # 注：create_agent 不提供 AgentExecutor 时代的 max_iterations/handle_parsing_errors，
        # 错误兜底由编排层 try/except 负责（见 get_agent_response / get_agent_stream_response）。
        return create_agent(
            chat_model,
            tools,
            system_prompt=system_prompt,
            middleware=self.default_middleware,
            checkpointer=get_checkpointer(),
            **kwargs
        )


# 全局共享的 checkpointer：懒加载，测试可 monkeypatch get_checkpointer
_checkpointer = None
_checkpointer_factory = None


def get_checkpointer():
    """返回全局 MySQL checkpointer（懒创建，捕获调用时的 AsyncSessionLocal）。"""
    global _checkpointer
    if _checkpointer is None:
        from app.db.db_config import AsyncSessionLocal

        _checkpointer = MySQLCheckpointSaver(session_factory=AsyncSessionLocal)
    return _checkpointer


def reset_checkpointer():
    """测试用：清空全局 checkpointer 单例。"""
    global _checkpointer
    _checkpointer = None


# 同一会话 run/resume 互斥锁（进程内；键为 user_id:session_id）。
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    """同一会话的 run/resume 互斥锁（进程内；键为 user_id:session_id）。"""
    key = f"{user_id}:{session_id}"
    lock = _session_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[key] = lock
    return lock


# 初始化全局工厂配置
agent_factory = AgentFactory()


def get_agent():
    """
    获取 create_agent 编译图实例（LangGraph）
    :return: CompiledStateGraph 实例
    """
    return agent_factory.create_agent()


def _build_chat_history(history: list[tuple] | None) -> list[BaseMessage]:
    """将 [(user_msg, assistant_msg), ...] 历史转换为 Human/AI 消息对。"""
    chat_history: list[BaseMessage] = []
    if history:
        for user_msg, assistant_msg in history:
            chat_history.append(HumanMessage(content=user_msg))
            chat_history.append(AIMessage(content=assistant_msg))
    return chat_history


def _collect_steps(messages: list[BaseMessage]) -> list[dict]:
    """从消息状态中提取工具调用步骤（AIMessage.tool_calls ↔ ToolMessage）。"""
    steps: list[dict] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tool_call in getattr(msg, "tool_calls", []) or []:
            tool_output = None
            for other in messages:
                if isinstance(other, ToolMessage) and other.tool_call_id == tool_call.get("id"):
                    tool_output = other.content
                    break
            steps.append({
                "thought": None,
                "tool": tool_call.get("name"),
                "tool_input": tool_call.get("args"),
                "tool_output": tool_output,
            })
    return steps


async def get_agent_response(
        query: str,
        history: list[tuple] | None = None,
        user_id: str | None = None,
        custom_tools: list[BaseTool] | None = None,
        **kwargs
):
    """
    获取 Agent 响应（使用工厂创建实例）
    :param query: 用户查询
    :param history: 会话历史 [(user_msg, assistant_msg), ...]
    :param user_id: 用户ID
    :param custom_tools: 自定义工具（可选，用于动态切换工具）
    :param kwargs: 其他工厂参数
    :return: 响应结果

    注意：非流式调用者（如 evals）应使用只读工具；命中审批白名单工具时
    本函数不走审批流程，而是返回 {"interrupted": True, "run_id": ...} 结构化信号，
    调用方须经流式 resume 路径（get_agent_resume_stream_response）携带 run_id 继续。
    """
    if user_id:
        set_current_user_id(user_id)

    # 非流式路径使用一次性 thread：checkpointer 要求 configurable 含 thread_id，
    # run 结束后删除该 thread，保持“无状态一次性调用”语义（流式持久化是 Task 5 的事）。
    thread_id = str(uuid.uuid4())
    saver = None
    saw_interrupt = False

    try:
        saver = get_checkpointer()
        # 1. 从工厂获取全新的 Agent 编译图实例
        agent = agent_factory.create_agent(custom_tools=custom_tools, **kwargs)

        # 2. 构建消息状态（历史 + 当前问题）
        chat_history = _build_chat_history(history)
        config = {"configurable": {"thread_id": thread_id}}
        state = await agent.ainvoke(
            {"messages": [*chat_history, HumanMessage(content=query)]},
            config,
        )

        # 3. 最终回答 = 消息状态中最后一条 AIMessage
        messages = state.get("messages", [])
        final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        response = (final.content or "") if final is not None else ""
        steps = _collect_steps(messages)

        # 4. HITL 中断检测：白名单工具请求会停在 __interrupt__，此时不得删除
        # thread（保持可恢复/可检查），返回结构化信号而非误导性文本答复。
        snapshot = await agent.aget_state(config)
        if _interrupt_payload(snapshot) is not None:
            saw_interrupt = True
            return {
                "response": "该操作需要人工审批（命中审批白名单工具），非流式调用无法完成审批；"
                            "已保留执行现场，请使用流式 resume 接口（get_agent_resume_stream_response）"
                            "携带 run_id 继续。",
                "steps": steps,
                "interrupted": True,
                "run_id": thread_id,
            }

        return {
            "response": response if response else "抱歉，我无法理解您的请求。",
            "steps": steps,
        }

    except Exception as e:
        logger.error(f"Agent 执行错误: {str(e)}", exc_info=True)
        return {
            "response": f"抱歉，处理您的请求时出现了错误: {str(e)}",
            "steps": []
        }
    finally:
        # 仅无中断时清理一次性 thread；中断时保留现场供 resume/检查。
        if saver is not None and not saw_interrupt:
            try:
                await saver.adelete_thread(thread_id)
            except Exception as cleanup_error:
                logger.warning(f"清理非流式 run thread 失败（可忽略）: {cleanup_error}")

def _thread_config(run_id: str) -> dict:
    """单次 run 的 thread config。"""
    return {"configurable": {"thread_id": run_id}}


async def _last_human_query(messages: list[BaseMessage]) -> str:
    """取 state 中最后一个 HumanMessage 作为原始 query。"""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def _interrupt_payload(snapshot) -> dict | None:
    """从 aget_state 快照提取 HITL 中断负载。"""
    for interrupt in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(interrupt, "value", None)
        if isinstance(value, dict) and "action_requests" in value:
            return value
    return None


def _extract_interrupt_payload(value) -> dict | None:
    """从 pending_write 值中提取 HITL 负载（兼容裸 Interrupt/(id, Interrupt)/list 形态）。"""
    candidates = list(value) if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, tuple) and len(candidate) == 2:
            candidate = candidate[1]
        inner = getattr(candidate, "value", candidate)
        if isinstance(inner, dict) and "action_requests" in inner:
            return inner
    return None


async def read_pending_interrupt(run_id: str) -> dict | None:
    """只读恢复：从 checkpoint 读待审批中断负载与原始 query（不编译图）。

    返回 {"run_id", "payload", "query"}；无 checkpoint/无中断返回 None。
    """
    saver = get_checkpointer()
    try:
        tup = await saver.aget_tuple(_thread_config(run_id))
    except Exception as e:
        logger.error(f"读取 pending interrupt 失败: {e}", exc_info=True)
        return None
    if tup is None:
        return None
    payload = None
    for entry in (tup.pending_writes or []):
        # pending_writes 形态随版本而异：3 元组 (task_id, channel, value)
        # 或 4 元组 (task_id, channel, type, value)；value 取最后一项。
        if len(entry) < 3:
            continue
        channel, value = entry[1], entry[-1]
        if channel != "__interrupt__":
            continue
        payload = _extract_interrupt_payload(value)
        if payload is not None:
            break
    if payload is None:
        return None
    messages = (tup.checkpoint.get("channel_values") or {}).get("messages", []) or []
    return {
        "run_id": run_id,
        "payload": payload,
        "query": await _last_human_query(messages),
    }


def _writes_have_interrupt(pending_writes) -> bool:
    """pending_writes 里是否含 __interrupt__ 通道（3/4 元组形态兼容）。"""
    for entry in (pending_writes or []):
        if len(entry) < 3:
            continue
        if entry[1] == "__interrupt__":
            return True
    return False


async def _pending_is_active(pending_run_id: str) -> bool:
    """pending 指向的 run 是否仍在等审批。

    审批完成后 thread 会被保留（供查看审批前快照），此时最新 checkpoint
    已无中断。读不到/异常一律按 active 处理：宁可挡住新问题，也不丢审批现场。
    saver 无 aget_tuple 方法时（如单测替身）同样按 active 处理，保持旧行为。
    """
    saver = get_checkpointer()
    if getattr(saver, "aget_tuple", None) is None:
        return True
    try:
        tup = await saver.aget_tuple(_thread_config(pending_run_id))
    except Exception:
        return True
    if tup is None:
        return True
    return _writes_have_interrupt(tup.pending_writes)


async def read_approval_snapshot(run_id: str) -> dict | None:
    """读取该 run 的审批前快照（含已审批完成）：扫描 thread 历史找最新 __interrupt__。

    返回 {"run_id", "payload", "query", "checkpoint_id", "active"}；
    active=True 表示仍在等审批，False 表示已审批完成（thread 被保留）；
    该 run 从无审批返回 None。不编译图，saver 无历史方法时返回 None。
    """
    saver = get_checkpointer()
    if getattr(saver, "aget_tuple", None) is None or getattr(saver, "alist", None) is None:
        return None
    try:
        latest = await saver.aget_tuple(_thread_config(run_id))
    except Exception as e:
        logger.error(f"读取审批快照失败: {e}", exc_info=True)
        return None
    if latest is None:
        return None
    if _writes_have_interrupt(latest.pending_writes):
        messages = (latest.checkpoint.get("channel_values") or {}).get("messages", []) or []
        for entry in latest.pending_writes:
            if len(entry) < 3 or entry[1] != "__interrupt__":
                continue
            payload = _extract_interrupt_payload(entry[-1])
            if payload is not None:
                return {
                    "run_id": run_id,
                    "payload": payload,
                    "query": await _last_human_query(messages),
                    "checkpoint_id": latest.config["configurable"].get("checkpoint_id"),
                    "active": True,
                }
        return None
    try:
        async for hist in saver.alist(_thread_config(run_id)):
            cid = (hist.config.get("configurable") or {}).get("checkpoint_id")
            if not cid:
                continue
            tup = await saver.aget_tuple(
                {"configurable": {"thread_id": run_id, "checkpoint_id": cid}})
            if tup is None or not _writes_have_interrupt(tup.pending_writes):
                continue
            messages = (tup.checkpoint.get("channel_values") or {}).get("messages", []) or []
            for entry in tup.pending_writes:
                if len(entry) < 3 or entry[1] != "__interrupt__":
                    continue
                payload = _extract_interrupt_payload(entry[-1])
                if payload is not None:
                    return {
                        "run_id": run_id,
                        "payload": payload,
                        "query": await _last_human_query(messages),
                        "checkpoint_id": cid,
                        "active": False,
                    }
    except Exception as e:
        logger.error(f"扫描审批历史失败: {e}", exc_info=True)
        return None
    return None


def _message_text(output) -> str:
    """从模型消息/终态输出提取文本（str/list content 形态）。"""
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


async def _emit_agent_events(agent, inputs, config, thinking_queue, full_response):
    """执行 astream_events 并转发 thinking/response 事件。

    返回 (interrupted_payload_or_None, error_str_or_None)。
    - 正常结束：检测 aget_state 的 interrupts 负载，返回负载或 None。
    - 异常：返回 (None, 错误文本)。
    """
    async def emit_thinking(stage, content, details):
        callback = get_thinking_callback_from_context()
        if callback is None:
            return
        result = callback({"type": "thinking", "stage": stage,
                           "content": content, "details": details})
        if inspect.isawaitable(result):
            await result

    async def emit_response(content: str):
        if not content:
            return
        full_response.append(content)
        await thinking_queue.put({"type": "response", "content": content})

    try:
        t_first = time.perf_counter()
        first_token_ms: float | None = None
        async for event in agent.astream_events(inputs, config=config, version="v2"):
            event_type = event.get("event")
            event_data = event.get("data") or {}
            if event_type == "on_chat_model_stream":
                chunk = event_data.get("chunk")
                if getattr(chunk, "tool_call_chunks", None):
                    continue
                content = getattr(chunk, "content", None)
                if content is None and isinstance(chunk, dict):
                    content = chunk.get("content")
                if isinstance(content, str):
                    if first_token_ms is None and content:
                        first_token_ms = (time.perf_counter() - t_first) * 1000
                        logger.info(f"【Agent耗时】首token first_token={first_token_ms:.0f}ms")
                    await emit_response(content)
                elif isinstance(content, list):
                    text = "".join(item.get("text", "")
                                   for item in content if isinstance(item, dict))
                    if first_token_ms is None and text:
                        first_token_ms = (time.perf_counter() - t_first) * 1000
                        logger.info(f"【Agent耗时】首token first_token={first_token_ms:.0f}ms")
                    await emit_response(text)
            elif event_type == "on_tool_start":
                tool = event.get("name", "unknown_tool")
                await emit_thinking("tool_start", f"正在调用 {tool}",
                                    {"tool": tool, "tool_input": event_data.get("input")})
            elif event_type == "on_tool_end":
                tool = event.get("name", "unknown_tool")
                tool_output = event_data.get("output")
                if isinstance(tool_output, BaseMessage):
                    tool_output = tool_output.content
                await emit_thinking("tool_end", f"{tool} 执行完成",
                                    {"tool": tool, "tool_output": tool_output})
            elif event_type == "on_chat_model_end":
                # 非流式/假模型不产生 on_chat_model_stream，这里兜底收割最终文本；
                # 真实流式模型已逐块累积（full_response 非空）则跳过，避免重复。
                if not full_response:
                    await emit_response(_message_text(event_data.get("output")))
        # 结束后检测中断点：以 interrupts 负载为准（挂起节点名随版本而异，
        # 如 HumanInTheLoopMiddleware.after_model，不可硬编码 '__interrupt__'）。
        snapshot = await agent.aget_state(config)
        return _interrupt_payload(snapshot), None
    except Exception as e:
        logger.error(f"Agent 执行错误: {e}", exc_info=True)
        return None, str(e)


async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        rag_context: str = "",
        rag_searched_queries: list[str] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """获取 Agent 流式响应（含 HITL 中断）。

    - 若会话存在 pending_run_id：发 PENDING_EXISTS error 帧结束。
    - 正常完成：写 ChatMessage 并删除 thread。
    - 遇 __interrupt__：写 pending_run_id，发 interrupt 帧结束（不落镜像）。
    - 同一会话的 run/resume 由进程内锁串行化（见 _get_session_lock）。
    """
    async with _get_session_lock(user_id, session_id):
        thinking_queue = asyncio.Queue()
        agent_result_holder = {"interrupt": None, "error": None}
        agent_done = asyncio.Event()
        run_id = str(uuid.uuid4())

        async def thinking_callback(data: dict):
            logger.info(f"【思考过程】{data.get('stage', 'unknown')}: {data.get('content', '')}")
            await thinking_queue.put(data)

        async def run_agent():
            try:
                set_current_user_id(user_id)
                set_thinking_callback(thinking_callback)
                init_rag_guard(rag_searched_queries)

                # 顶层守卫：仍在等审批时拒绝新问题；已审批完成的历史 pending
                # （thread 被保留供查看）放行，其 pending 会在下次中断时被覆盖。
                pending = await sm.session_manager.get_pending_run_id(session_id, user_id)
                if pending and await _pending_is_active(pending):
                    agent_result_holder["error"] = "PENDING_EXISTS"
                    return

                history = await sm.session_manager.get_history(session_id, user_id)
                chat_history = _build_chat_history(history)
                system_prompt = (
                    load_prompt("rag_context_prompt").replace("{context}", rag_context)
                    if rag_context else agent_factory.default_system_prompt
                )
                t_create = time.perf_counter()
                agent = agent_factory.create_agent(
                    custom_tools=custom_tools, custom_system_prompt=system_prompt, **kwargs
                )
                create_ms = (time.perf_counter() - t_create) * 1000
                logger.info(f"【Agent耗时】准备阶段 history条数={len(history)} create_agent={create_ms:.0f}ms rag_context_chars={len(rag_context)}")
                full_response = []
                inputs = {"messages": [*chat_history, HumanMessage(content=query)]}
                config = _thread_config(run_id)
                interrupted, error = await _emit_agent_events(
                    agent, inputs, config, thinking_queue, full_response)

                if error:
                    agent_result_holder["error"] = error
                    return

                if interrupted is not None:
                    # 等待用户审批：记录 run_id，不写镜像，保留 checkpoint。
                    # 先记 holder 再写 pending，保证 set_pending 失败时 finally
                    # 仍能看到中断而不误删已停靠的 thread。
                    agent_result_holder["interrupt"] = {
                        "run_id": run_id, "payload": interrupted}
                    if pending and pending != run_id:
                        # 覆盖上次已审批完成的历史 thread，避免无索引孤儿越积越多。
                        try:
                            await get_checkpointer().adelete_thread(pending)
                        except Exception as cleanup_error:
                            logger.warning(f"清理历史审批 thread 失败（可忽略）: {cleanup_error}")
                    await sm.session_manager.set_pending_run_id(session_id, user_id, run_id)
                    return

                response = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
                agent_result_holder["response"] = response
                # 正常完成：落镜像（thread 清理统一由 finally 的守卫删除负责）
                await sm.session_manager.add_message(session_id, user_id, query, response)
            except Exception as e:
                logger.error(f"【Agent流式响应】Agent执行失败: {e}", exc_info=True)
                # 已记录中断时保留中断信号，不被错误覆盖（thread 必须保留）。
                if agent_result_holder.get("interrupt") is None:
                    agent_result_holder["error"] = str(e)
            finally:
                # 孤儿清理：仅无中断且非 PENDING_EXISTS 守卫时删除本 run 的 thread。
                # 中断已记录 → 永不删除（pending 指向它）；守卫路径未产生 thread 内容 → 跳过。
                if (agent_result_holder.get("interrupt") is None
                        and agent_result_holder.get("error") != "PENDING_EXISTS"):
                    try:
                        await get_checkpointer().adelete_thread(run_id)
                    except Exception as cleanup_error:
                        logger.warning(f"清理 thread 失败（可忽略）: {cleanup_error}")
                agent_done.set()

        agent_task = asyncio.create_task(run_agent())
        try:
            yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            while not agent_done.is_set():
                try:
                    event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except TimeoutError:
                    continue
            while not thinking_queue.empty():
                try:
                    event = thinking_queue.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            await agent_task

            if agent_result_holder.get("interrupt"):
                payload = agent_result_holder["interrupt"]
                yield f"data: {json.dumps({'type': 'interrupt', **payload, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                return
            if agent_result_holder.get("error"):
                content = agent_result_holder["error"]
                yield f"data: {json.dumps({'type': 'error', 'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"【Agent流式响应】处理请求失败: {e}", exc_info=True)
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass
            # 外部取消/断开兜底：无中断且非守卫路径才删（run_agent 的 finally
            # 通常已删，此处为幂等兜底，守卫删除永不抛错）。
            if (agent_result_holder.get("interrupt") is None
                    and agent_result_holder.get("error") != "PENDING_EXISTS"):
                try:
                    await get_checkpointer().adelete_thread(run_id)
                except Exception as cleanup_error:
                    logger.warning(f"清理 thread 失败（可忽略）: {cleanup_error}")
            error_message = f"错误: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"


async def get_agent_resume_stream_response(
        session_id: str,
        user_id: str,
        run_id: str,
        decisions: list[dict],
        custom_tools: list[BaseTool] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """以相同 thread 恢复被中断的 run（Command(resume=...)）。

    - 校验该会话确有 pending_run_id==run_id，否则 error 帧。
    - 完成：镜像落库；thread 与 pending 保留（供查看审批前快照，下次中断覆盖）。
    - 再次 resume 同一已完成 run：ALREADY_COMPLETED error 帧（不重复执行）。
    - 再次中断：更新 payload 再发 interrupt 帧。
    - 出错：不删 thread、不清 pending（预留 pending 供重试）。
    - 同一会话的 run/resume 由进程内锁串行化（见 _get_session_lock）。
    """
    async with _get_session_lock(user_id, session_id):
        thinking_queue = asyncio.Queue()
        agent_result_holder = {"interrupt": None, "error": None}
        agent_done = asyncio.Event()

        async def thinking_callback(data: dict):
            logger.info(f"【思考过程-恢复】{data.get('stage', 'unknown')}: {data.get('content', '')}")
            await thinking_queue.put(data)

        async def run_agent():
            try:
                set_current_user_id(user_id)
                set_thinking_callback(thinking_callback)
                pending = await sm.session_manager.get_pending_run_id(session_id, user_id)
                if pending != run_id:
                    agent_result_holder["error"] = "RESUME_MISMATCH"
                    return
                # 已完成重放保护：审批完成后 thread 被保留供查看，同一 run 再次
                # resume 不得重复执行工具/落镜像。saver 不可读时（如单测替身）放行。
                saver = get_checkpointer()
                if getattr(saver, "aget_tuple", None) is not None:
                    try:
                        cur = await saver.aget_tuple(_thread_config(run_id))
                    except Exception as e:
                        logger.error(f"恢复前检查 thread 失败: {e}", exc_info=True)
                        agent_result_holder["error"] = "RESUME_CHECK_FAILED"
                        return
                    if cur is None:
                        agent_result_holder["error"] = "RESUME_MISMATCH"
                        return
                    if not _writes_have_interrupt(cur.pending_writes):
                        agent_result_holder["error"] = "ALREADY_COMPLETED"
                        return

                agent = agent_factory.create_agent(
                    custom_tools=custom_tools, **kwargs)
                config = _thread_config(run_id)
                command = Command(resume={"decisions": decisions})
                full_response = []
                interrupted, error = await _emit_agent_events(
                    agent, command, config, thinking_queue, full_response)
                if error:
                    # 出错保留 thread 与 pending，供重试（不删不清除）。
                    agent_result_holder["error"] = error
                    return
                if interrupted is not None:
                    agent_result_holder["interrupt"] = {"run_id": run_id, "payload": interrupted}
                    return

                # 完成：原始 query 从 checkpoint 读取（不依赖首跑请求参数）。
                # 审批 thread 保留供查看审批前快照，pending 保留到下次中断覆盖。
                snapshot = await agent.aget_state(config)
                messages = (snapshot.values or {}).get("messages", []) or []
                query = await _last_human_query(messages)
                response = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
                await sm.session_manager.add_message(session_id, user_id, query or "（未知问题）", response)
                agent_result_holder["response"] = response
            except Exception as e:
                logger.error(f"【Agent恢复响应】Agent执行失败: {e}", exc_info=True)
                agent_result_holder["error"] = str(e)
            finally:
                agent_done.set()

        agent_task = asyncio.create_task(run_agent())
        try:
            yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            while not agent_done.is_set():
                try:
                    event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except TimeoutError:
                    continue
            while not thinking_queue.empty():
                try:
                    event = thinking_queue.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            await agent_task

            if agent_result_holder.get("interrupt"):
                payload = agent_result_holder["interrupt"]
                yield f"data: {json.dumps({'type': 'interrupt', **payload, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                return
            if agent_result_holder.get("error"):
                yield f"data: {json.dumps({'type': 'error', 'content': agent_result_holder['error'], 'session_id': session_id}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"【Agent恢复响应】处理请求失败: {e}", exc_info=True)
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass
            yield f"data: {json.dumps({'type': 'error', 'content': f'错误: {str(e)}', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
