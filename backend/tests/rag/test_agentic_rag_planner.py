import asyncio

from app.core.settings import settings
from app.rag.agentic_rag.planner import AgenticRagPlanner
from app.rag.agentic_rag.schemas import RetrievalPlan


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChatModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.prompts = []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


async def test_planner_uses_valid_llm_json_response():
    model = FakeChatModel(
        FakeMessage(
            '{"need_retrieval": true, "steps": [{"tool": "search_notes", '
            '"query": "project memo", "top_k": 3}], '
            '"allow_web_fallback": false, "reason": "search private notes"}'
        )
    )
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("project memo")

    assert plan.need_retrieval is True
    assert plan.steps[0].tool == "search_notes"
    assert plan.steps[0].query == "project memo"
    assert plan.steps[0].top_k == 3
    assert plan.allow_web_fallback is False
    assert plan.reason == "search private notes"
    assert plan.metadata["source"] == "llm"


async def test_planner_extracts_json_from_markdown_fence():
    model = FakeChatModel(
        "```json\n"
        '{"need_retrieval": false, "steps": [], '
        '"allow_web_fallback": false, "reason": "casual greeting"}'
        "\n```"
    )
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("hi")

    assert plan.need_retrieval is False
    assert plan.steps == []
    assert plan.reason == "casual greeting"


async def test_planner_falls_back_to_no_retrieval_for_casual_greeting_on_invalid_llm():
    planner = AgenticRagPlanner(chat_model=FakeChatModel("not json"))

    plan = await planner.plan("你好")

    assert plan.need_retrieval is False
    assert plan.steps == []
    assert plan.allow_web_fallback is False


async def test_planner_falls_back_to_hybrid_search_with_freshness_web_flag_on_model_error():
    planner = AgenticRagPlanner(chat_model=FakeChatModel(error=RuntimeError("model down")))

    query = "LangChain 最新版本"

    plan = await planner.plan(query)

    assert plan.need_retrieval is True
    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "hybrid_search"
    assert plan.steps[0].query == query
    assert plan.steps[0].top_k == 5
    assert plan.allow_web_fallback is True
    assert plan.metadata["source"] == "fallback"


async def test_planner_falls_back_to_hybrid_search_without_web_for_non_fresh_query():
    planner = AgenticRagPlanner(chat_model=FakeChatModel(None))
    query = "Explain my saved notes about vector databases"

    plan = await planner.plan(query)

    assert plan.need_retrieval is True
    assert plan.steps[0].tool == "hybrid_search"
    assert plan.steps[0].query == query
    assert plan.allow_web_fallback is False


async def test_planner_falls_back_to_graph_for_entity_query_on_model_error():
    planner = AgenticRagPlanner(chat_model=FakeChatModel(error=RuntimeError("model down")))

    plan = await planner.plan("DeepSeek 是什么")

    assert plan.need_retrieval is True
    assert plan.steps[0].tool == "search_graph"
    assert plan.steps[0].query == "DeepSeek 是什么"
    assert plan.steps[1].tool == "hybrid_search"
    assert plan.metadata["source"] == "fallback"


async def test_planner_preserves_llm_search_graph_step():
    model = FakeChatModel(
        FakeMessage(
            '{"need_retrieval": true, "steps": [{"tool": "search_graph", '
            '"query": "量子计算和 FastAPI 的关系", "top_k": 4}], '
            '"allow_web_fallback": false, "reason": "answer with knowledge graph"}'
        )
    )
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("量子计算和 FastAPI 的关系")

    assert plan.steps[0].tool == "search_graph"
    assert plan.steps[0].query == "量子计算和 FastAPI 的关系"
    assert plan.steps[0].top_k == 4


async def test_planner_caps_steps_and_top_k_from_small_model():
    """小模型发散兜底：步数截到 2、top_k 限幅 5。"""
    model = FakeChatModel(
        FakeMessage(
            '{"need_retrieval": true, "steps": ['
            '{"tool": "hybrid_search", "query": "q", "top_k": 99},'
            '{"tool": "search_notes", "query": "q", "top_k": 3},'
            '{"tool": "search_knowledge_base", "query": "q", "top_k": 3}],'
            '"allow_web_fallback": false, "reason": "rambling"}'
        )
    )
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("q")

    assert plan.metadata["source"] == "llm"
    assert len(plan.steps) == 2
    assert plan.steps[0].top_k == 5


async def test_planner_timeout_falls_back_to_deterministic_plan(monkeypatch):
    """规划 LLM 超过 PLANNER_TIMEOUT_S 直接回 fallback，延迟有上界。"""

    class SlowModel:
        async def ainvoke(self, prompt):
            await asyncio.sleep(5)
            raise AssertionError("should have timed out")

    monkeypatch.setattr(settings, "PLANNER_TIMEOUT_S", 0.05)
    planner = AgenticRagPlanner(chat_model=SlowModel())

    plan = await planner.plan("普通问题")

    assert plan.metadata["source"] == "fallback"
    assert plan.need_retrieval is True
    assert plan.steps[0].tool == "hybrid_search"


class StructuredFakeModel:
    """带 with_structured_output 的假模型（模拟支持 function-calling 的通道）。"""

    def __init__(self, result=None, error=None, text=None):
        self.result = result
        self.error = error
        self.text = text
        self.schemas = []
        self.methods = []

    def with_structured_output(self, schema, method=None):
        self.schemas.append(schema)
        self.methods.append(method)

        async def _ainvoke(prompt):
            if self.error:
                raise self.error
            return self.result

        return type("StructuredRunner", (), {"ainvoke": staticmethod(_ainvoke)})()

    async def ainvoke(self, prompt):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


async def test_planner_structured_output_as_backup():
    """文本解析不出时走 pydantic 结构化兜底：野键名问题在模型侧解决。"""
    expected = RetrievalPlan(
        need_retrieval=True,
        steps=[],
        allow_web_fallback=False,
        reason="structured",
    )
    model = StructuredFakeModel(result=expected, text="this text must be ignored")
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("创建一篇关于AI的笔记")

    assert plan.metadata["source"] == "llm"
    assert plan.reason == "structured"
    assert model.schemas == [RetrievalPlan]
    assert model.methods == ["json_mode"]


async def test_planner_text_path_preferred_when_valid():
    """文本 JSON 有效时直接用，不碰结构化（主路径更快）。"""
    model = StructuredFakeModel(
        error=RuntimeError("tool calling not supported"),
        text=FakeMessage(
            '{"need_retrieval": true, "steps": [{"tool": "hybrid_search", '
            '"query": "q", "top_k": 5}], "allow_web_fallback": false, "reason": "text"}'
        ),
    )
    planner = AgenticRagPlanner(chat_model=model)

    plan = await planner.plan("q")

    assert plan.metadata["source"] == "llm"
    assert plan.reason == "text"


async def test_planner_logs_raw_content_on_parse_failure(caplog):
    """文本解析失败打出原始输出，下次不用再猜小模型回了什么。"""
    import logging

    model = FakeChatModel(FakeMessage('{"retrieval": "oops", truncated'))
    planner = AgenticRagPlanner(chat_model=model)

    with caplog.at_level(logging.WARNING, logger="agent"):
        plan = await planner.plan("普通问题")

    assert plan.metadata["source"] == "fallback"
    assert "规划文本解析失败" in caplog.text
    assert "retrieval" in caplog.text
