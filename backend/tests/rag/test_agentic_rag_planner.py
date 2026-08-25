from app.rag.agentic_rag.planner import AgenticRagPlanner


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


async def test_planner_falls_back_to_hybrid_search_without_web_for_non_fresh_query():
    planner = AgenticRagPlanner(chat_model=FakeChatModel(None))
    query = "Explain my saved notes about vector databases"

    plan = await planner.plan(query)

    assert plan.need_retrieval is True
    assert plan.steps[0].tool == "hybrid_search"
    assert plan.steps[0].query == query
    assert plan.allow_web_fallback is False
