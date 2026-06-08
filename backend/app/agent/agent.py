import asyncio
import json
import os
from collections.abc import AsyncGenerator

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool
from langchain_ollama import ChatOllama
from langsmith import traceable

from app.agent.agent_middleware import get_middleware
from app.agent.agent_tools import (
    create_note_tool,
    get_note_stats_tool,
    get_related_notes_tool,
    get_today_reviews_tool,
    get_user_info_tools,
    mark_reviewed_tool,
    rag_summary_tools,
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
    - 每次调用创建全新的 AgentExecutor 实例
    - 动态注入工具、提示词、模型配置
    - 支持异步流式调用
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
        self.api_key = api_key or os.getenv("CHAT_API_KEY")
        self.default_tools = default_tools or self._get_default_tools()
        self.default_middleware = default_middleware or self._get_default_middleware()
        self.default_system_prompt = default_system_prompt or self._get_default_system_prompt()

    @staticmethod
    def _get_default_tools() -> list[BaseTool]:
        """获取默认工具列表"""
        return [
            rag_summary_tools,
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
        return get_middleware()

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: str | None = None):
        """内部方法：根据LLM_TYPE创建聊天模型实例"""
        llm_type = os.getenv("LLM_TYPE", "ALIYUN").upper()

        if llm_type == "OLLAMA":
            model_name = custom_model or os.getenv("OLLAMA_MODEL_NAME", self.model)
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

            logger.info(f"🤖 Agent使用Ollama模型: {model_name}")

            return ChatOllama(
                model=model_name,
                base_url=base_url,
                streaming=True,
                top_p=0.7,
            )

        elif llm_type == "ALIYUN":
            api_key = os.getenv("ALIYUN_ACCESS_KEY_SECRET")
            base_url = os.getenv("ALIYUN_BASE_URL")
            model_name = custom_model or os.getenv("ALIYUN_MODEL_NAME", self.model)

            logger.info(f"🤖 Agent使用阿里云百炼模型: {model_name}")

            return ChatTongyi(
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                streaming=True,
                top_p=0.7,
            )

        else:
            raise ValueError(f"不支持的LLM_TYPE: {llm_type}，可选值: ALIYUN, OLLAMA")

    def _create_prompt(self, custom_system_prompt: str | None = None) -> ChatPromptTemplate:
        """内部方法：创建提示词模板"""
        return ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad")
        ])

    def create_agent_executor(
            self,
            custom_tools: list[BaseTool] | None = None,
            custom_model: str | None = None,
            custom_system_prompt: str | None = None,
            verbose: bool = True,
            return_intermediate_steps: bool = True,
            **kwargs
    ) -> AgentExecutor:
        """
        核心工厂方法：创建全新的 AgentExecutor 实例
        每次调用都会生成新的实例，彻底避免全局状态污染

        :param custom_tools: 自定义工具列表（覆盖默认）
        :param custom_model: 自定义模型（覆盖默认）
        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :param verbose: 是否打印详细日志
        :param return_intermediate_steps: 是否返回中间步骤
        :param kwargs: 其他 AgentExecutor 参数
        :return: 全新的 AgentExecutor 实例
        """
        # 1. 创建组件（每次都重新创建，避免全局状态污染）
        chat_model = self._create_chat_model(custom_model)
        prompt = self._create_prompt()
        tools = custom_tools or self.default_tools

        # 2. 创建 Agent
        agent = create_tool_calling_agent(chat_model, tools, prompt)

        # 3. 创建 Executor
        return AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=verbose,
            return_intermediate_steps=return_intermediate_steps,
            handle_parsing_errors=True,
            max_iterations=5,
            **kwargs
        )


# 初始化全局工厂配置
agent_factory = AgentFactory()


def get_agent_executor():
    """
    获取AgentExecutor实例，用于LangGraph
    :return: AgentExecutor实例
    """
    return agent_factory.create_agent_executor()


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
        # 1. 从工厂获取全新的 Executor 实例
        agent_executor = agent_factory.create_agent_executor(custom_tools=custom_tools, **kwargs)

        # 2. 构建聊天历史
        chat_history: list[BaseMessage] = []
        if history:
            from langchain_core.messages import AIMessage, HumanMessage
            for user_msg, assistant_msg in history:
                chat_history.append(HumanMessage(content=user_msg))
                chat_history.append(AIMessage(content=assistant_msg))

        # 3. 流式执行
        full_response = []
        steps = []
        async for chunk in agent_executor.astream({
            "input": query,
            "chat_history": chat_history,
            "system_prompt": agent_factory.default_system_prompt
        }):
            if "output" in chunk:
                full_response.append(chunk["output"])
            elif "intermediate_steps" in chunk:
                for action, observation in chunk["intermediate_steps"]:
                    # 记录日志
                    logger.info(f"\n\n🧠 [Agent 思考] {action.log}")
                    logger.info(f"🛠️ [调用工具] {action.tool}")
                    logger.info(f"📥 [工具输入] {action.tool_input}")
                    logger.info(f"📤 [工具结果] {observation}\n")
                    # 收集步骤
                    steps.append({
                        "thought": action.log,
                        "tool": action.tool,
                        "tool_input": action.tool_input,
                        "tool_output": observation
                    })

        return {
            "response": "".join(full_response) if full_response else "抱歉，我无法理解您的请求。",
            "steps": steps
        }

    except Exception as e:
        logger.error(f"Agent 执行错误: {str(e)}", exc_info=True)
        return {
            "response": f"抱歉，处理您的请求时出现了错误: {str(e)}",
            "steps": []
        }

@traceable
async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """
    获取 Agent 流式响应（包含思考过程，实时推送）
    :param query: 用户查询
    :param session_id: 会话 ID
    :param user_id: 用户 ID
    :param custom_tools: 自定义工具（可选）
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

            chat_history: list[BaseMessage] = []
            if history:
                from langchain_core.messages import AIMessage, HumanMessage
                for user_msg, assistant_msg in history:
                    chat_history.append(HumanMessage(content=user_msg))
                    chat_history.append(AIMessage(content=assistant_msg))

            agent_executor = agent_factory.create_agent_executor(custom_tools=custom_tools, **kwargs)

            full_response = []

            async for chunk in agent_executor.astream({
                "input": query,
                "chat_history": chat_history,
                "system_prompt": agent_factory.default_system_prompt
            }):
                if "output" in chunk:
                    full_response.append(chunk["output"])
                elif "intermediate_steps" in chunk:
                    for action, observation in chunk["intermediate_steps"]:
                        logger.info(f"\n\n🧠 [Agent 思考] {action.log}")
                        logger.info(f"🛠️ [调用工具] {action.tool}")
                        logger.info(f"📥 [工具输入] {action.tool_input}")
                        logger.info(f"📤 [工具结果] {observation}\n")

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

        # 发送回答内容（按chunk发送，减少SSE事件数）
        chunk_size = 15
        for i in range(0, len(response), chunk_size):
            chunk = response[i:i + chunk_size]
            yield f"data: {json.dumps({'type': 'response', 'content': chunk}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)

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
