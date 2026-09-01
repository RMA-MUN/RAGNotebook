import asyncio
import inspect
import json
from collections.abc import AsyncGenerator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.core.settings import settings

from app.agent.agent_tools import (
    create_note_tool,
    get_note_stats_tool,
    get_related_notes_tool,
    get_today_reviews_tool,
    get_user_info_tools,
    get_thinking_callback_from_context,
    mark_reviewed_tool,
    search_notes_tool,
    set_current_user_id,
    set_thinking_callback,
    what_time_is_now,
)
from app.core.logger_handler import logger
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
    ):
        """
        初始化工厂配置（仅配置，不创建实例）
        :param model: 默认模型名称
        :param api_key: 默认 API Key（不传则从env读取）
        :param default_tools: 默认工具列表
        :param default_system_prompt: 默认系统提示词
        """
        self.model = model
        self.api_key = api_key or settings.CHAT_API_KEY or None
        self.default_tools = default_tools or self._get_default_tools()
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
        ]

    def _get_default_middleware(self) -> list:
        """获取默认中间件列表"""
        try:
            from app.agent.agent_middleware import get_middleware

            return get_middleware()
        except ImportError:
            logger.warning("Agent middleware unavailable; continuing without middleware.", exc_info=True)
            return []

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: str | None = None, api_key: str | None = None, base_url: str | None = None):
        """内部方法：创建聊天模型实例（统一 OpenAI 兼容协议）"""
        from app.utils.factory import create_chat_openai

        model = custom_model or settings.OPENAI_MODEL_NAME or "gpt-4o-mini"
        logger.info(f"🤖 Agent使用OpenAI兼容模型: {model}")
        return create_chat_openai(
            model=model,
            api_key=api_key or (settings.OPENAI_API_KEY or None),
            base_url=base_url or (settings.OPENAI_BASE_URL or None),
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
        chat_model = custom_model if getattr(custom_model, "ainvoke", None) else self._create_chat_model(custom_model)
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
            **kwargs
        )


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
    """
    if user_id:
        set_current_user_id(user_id)

    try:
        # 1. 从工厂获取全新的 Agent 编译图实例
        agent = agent_factory.create_agent(custom_tools=custom_tools, **kwargs)

        # 2. 构建消息状态（历史 + 当前问题）
        chat_history = _build_chat_history(history)
        state = await agent.ainvoke({"messages": [*chat_history, HumanMessage(content=query)]})

        # 3. 最终回答 = 消息状态中最后一条 AIMessage
        messages = state.get("messages", [])
        final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        response = (final.content or "") if final is not None else ""
        steps = _collect_steps(messages)

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

async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        rag_context: str = "",
        chat_model: object | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """
    获取 Agent 流式响应（包含思考过程，实时推送）
    :param query: 用户查询
    :param session_id: 会话 ID
    :param user_id: 用户 ID
    :param custom_tools: 自定义工具（可选）
    :param rag_context: 预检索的 RAG 上下文（由路由层注入，为空则跳过）
    :param chat_model: 已构建的每用户聊天模型实例（可选；为 None 时走工厂默认模型）
    :param kwargs: 其他参数
    :return: 流式响应生成器
    """

    thinking_queue = asyncio.Queue()
    agent_result_holder = {"response": None, "error": None}
    agent_done = asyncio.Event()

    async def thinking_callback(data: dict):
        """思考过程回调函数，将事件放入队列"""
        logger.info(f"【思考过程】{data.get('stage', 'unknown')}: {data.get('content', '')}")
        await thinking_queue.put(data)

    async def run_agent():
        """在独立任务中执行 Agent"""
        try:
            set_current_user_id(user_id)
            set_thinking_callback(thinking_callback)

            history = await sm.session_manager.get_history(session_id, user_id)
            logger.info(f"【Agent流式响应】获取会话历史成功，历史记录数: {len(history)}")

            chat_history = _build_chat_history(history)

            # 根据是否有 RAG 上下文决定 system prompt 内容
            if rag_context:
                system_prompt = f"""你是用户的智能助手。

以下是与用户问题相关的参考资料：
{rag_context}

请基于以上资料回答用户的问题。回答时必须区分本地证据（笔记、知识库）与外部搜索证据，避免把外部搜索内容说成用户本地资料。
如果资料中没有足够信息支撑结论，必须明确说明证据不足，并说明还缺少哪些信息。"""
            else:
                system_prompt = agent_factory.default_system_prompt

            create_agent_kwargs = dict(custom_tools=custom_tools, custom_system_prompt=system_prompt, **kwargs)
            if chat_model is not None:
                create_agent_kwargs["custom_model"] = chat_model
            agent = agent_factory.create_agent(**create_agent_kwargs)

            full_response = []

            async def emit_thinking(stage: str, content: str, details: dict):
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

            async def emit_response(content: str):
                if not content:
                    return
                full_response.append(content)
                await thinking_queue.put({
                    "type": "response",
                    "content": content,
                })

            inputs = {
                "messages": [*chat_history, HumanMessage(content=query)],
            }

            # 规划轮（工具调用前的模型轮）chunk 携带 tool_call_chunks 增量，直接丢弃；
            # 纯文本 chunk 实时转发——最终回答的 token 边生成边推，恢复打字机效果。
            async for event in agent.astream_events(inputs, version="v2"):
                event_type = event.get("event")
                event_data = event.get("data") or {}

                if event_type == "on_chat_model_stream":
                    chunk = event_data.get("chunk")
                    # 规划轮 chunk 带 tool_call_chunks（工具参数增量），不流入 response
                    if getattr(chunk, "tool_call_chunks", None):
                        continue
                    content = getattr(chunk, "content", None)
                    if content is None and isinstance(chunk, dict):
                        content = chunk.get("content")
                    if isinstance(content, str):
                        await emit_response(content)
                    elif isinstance(content, list):
                        text = "".join(
                            item.get("text", "")
                            for item in content
                            if isinstance(item, dict)
                        )
                        await emit_response(text)
                elif event_type == "on_tool_start":
                    tool = event.get("name", "unknown_tool")
                    tool_input = event_data.get("input")
                    await emit_thinking(
                        "tool_start",
                        f"正在调用 {tool}",
                        {"tool": tool, "tool_input": tool_input},
                    )
                elif event_type == "on_tool_end":
                    tool = event.get("name", "unknown_tool")
                    tool_output = event_data.get("output")
                    # LangGraph 的 on_tool_end output 是 ToolMessage 对象，
                    # 需提取 content 才能 JSON 序列化进 thinking 事件
                    if isinstance(tool_output, BaseMessage):
                        tool_output = tool_output.content
                    await emit_thinking(
                        "tool_end",
                        f"{tool} 执行完成",
                        {"tool": tool, "tool_output": tool_output},
                    )

            agent_result_holder["response"] = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
        except Exception as e:
            logger.error(f"【Agent流式响应】Agent执行失败: {e}", exc_info=True)
            agent_result_holder["error"] = str(e)
        finally:
            agent_done.set()

    # 启动 Agent 执行任务
    agent_task = asyncio.create_task(run_agent())

    try:
        logger.info(f"【Agent流式响应】开始处理请求，用户ID: {user_id}, 会话ID: {session_id}, 查询: {query}")

        # 先发送初始响应
        yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"

        # 持续监听队列并实时推送思考事件，同时等待 Agent 完成
        while not agent_done.is_set():
            try:
                # 使用短超时轮询队列，实现实时推送
                event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except TimeoutError:
                # 超时是正常的，继续等待
                continue

        # Agent 已完成，推送队列中剩余的所有思考事件
        while not thinking_queue.empty():
            try:
                event = thinking_queue.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                thinking_queue.task_done()
            except asyncio.QueueEmpty:
                break

        # 等待 agent_task 完全结束
        await agent_task

        if agent_result_holder["error"]:
            error_message = f"错误: {agent_result_holder['error']}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        response = agent_result_holder["response"]

        # 添加到会话历史
        await sm.session_manager.add_message(session_id, user_id, query, response)
        logger.info("【Agent流式响应】添加到会话历史成功")

        # 发送结束标记
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        logger.info(f"【Agent流式响应】处理完成，会话ID: {session_id}")

    except Exception as e:
        logger.error(f"【Agent流式响应】处理请求失败: {e}", exc_info=True)

        # 取消 agent 任务
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

        error_message = f"错误: {str(e)}"
        yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
