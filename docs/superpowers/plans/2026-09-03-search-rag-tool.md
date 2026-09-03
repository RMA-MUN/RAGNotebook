# search_rag 工具化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留前置 AgenticRAG 保底注入的前提下，给 Agent 绑定一个可自主调用的 `search_rag(query)` 工具（粗粒度复用 `AgenticRagService`），并配齐同 query 短路、请求级限次、思考事件回传与提示词模板化。

**Architecture:** 前置管线（`chat.py` 先跑 `AgenticRagService` 注入 context）不变；新增 `backend/app/agent/agent_rag_tool.py` 承载请求级护栏（ContextVar）与 `search_rag` 工具；`agent.py` 把工具加入默认工具列表、在 `run_agent()` 初始化护栏；`agent.py` 的 RAG 分支 system prompt 从硬编码 f-string 改为 `load_prompt('rag_context_prompt')` 模板，`main_prompt` 一并 md 化。

**Tech Stack:** Python 3.12、LangChain 1.0+ `create_agent`、LangGraph、pydantic v2、pytest-asyncio、ruff、FastAPI SSE。

**Spec:** `docs/superpowers/plans/2026-09-03-agent-rag-tool-design.md`

## Global Constraints

- 运行/测试：在 `backend/` 目录下用 `.\.venv\Scripts\python.exe -m pytest <path> -v`（仓库已有 `.venv`）。
- Lint：`.\.venv\Scripts\python.exe -m ruff check <file>`；规则 E/F/I/W，line-length=180，双引号。
- 不改前置管线 `AgenticRagService` 本体、`AgenticRagResult` schema、`RetrievalPlan/RetrievalStep` schema。
- 保留 prompt.yaml 逻辑名→路径映射；Agent 相关模板用 `.md`。
- 工具沿用现有风格：内部 try/except 返回错误字符串，不把异常抛给 agent 图；用户身份从 `get_current_user_id_from_context()` 取。
- 不改前端（工具思考帧复用现有 SSE thinking 通道，无需前端改动）。
- 现有测试 `tests/agent/test_agent.py` 中 `test_get_agent_stream_response_with_rag_context` 断言 system prompt 含「参考资料」「区分本地证据」「外部搜索证据」「证据不足」——模板必须保留这些短语，否则测试会红。

---

### Task 1: 提示词模板化（main_prompt.md / rag_context_prompt.md）

**Files:**
- Modify: `backend/app/config/prompt.yaml`
- Rename: `backend/app/prompt/main_prompt.txt` → `backend/app/prompt/main_prompt.md`
- Create: `backend/app/prompt/rag_context_prompt.md`
- Modify: `backend/app/agent/agent.py:264-278`（RAG 分支硬编码 f-string → 模板加载）
- Test: `backend/tests/agent/test_agent.py:422`（保持绿，额外加断言）

**Interfaces:**
- Consumes: `load_prompt(prompt_type)`（`app/utils/prompt_loader.py`，按 prompt.yaml 读文件）。
- Produces: prompt.yaml 新增 `rag_context_prompt: app/prompt/rag_context_prompt.md`；模板含 `{context}` 占位符与 search_rag 纪律；main_prompt.md 含 search_rag 纪律与双 context 说明。Task 3 的 `run_agent` 仍走 `agent_factory.default_system_prompt`（main_prompt）与本次模板。

- [ ] **Step 1: 更新 prompt.yaml**

把 `main_prompt` 值改为 `.md`，并新增 `rag_context_prompt` 条目：

```yaml
main_prompt: app/prompt/main_prompt.md
rag_context_prompt: app/prompt/rag_context_prompt.md
report_prompt: app/prompt/report_prompt.txt
reorder_prompt: app/prompt/reorder_prompt.txt
auto_tag_prompt: app/prompt/auto_tag_prompt.txt
review_question_prompt: app/prompt/review_question_prompt.txt
autocomplete_prompt: app/prompt/autocomplete_prompt.txt
write_assistant_prompt: app/prompt/write_assistant_prompt.txt
agentic_rag_planner_prompt: app/prompt/agentic_rag_planner.txt
agentic_rag_answerability_prompt: app/prompt/agentic_rag_answerability.txt
entity_extraction_prompt: app/prompt/entity_extraction_prompt.txt
query_entity_extraction_prompt: app/prompt/query_entity_extraction.txt
```

- [ ] **Step 2: 重命名 main_prompt.txt → main_prompt.md**

```bash
cd backend && git mv app/prompt/main_prompt.txt app/prompt/main_prompt.md
```

- [ ] **Step 3: 用 markdown 结构重写 main_prompt.md**

保留原有全部语义（工具名、JSON 规则），新增 search_rag 使用纪律与双 context 说明。文件完整内容：

```markdown
你是一个智能笔记助手，核心能力是帮助用户管理笔记并回答问题。你具备笔记管理能力（搜索、统计、创建、回顾），也具备知识问答能力。

你说话简单直接，不说废话。

## 核心任务

1. **基于资料回答**：如果系统提供了参考资料，请基于资料内容回答，并引用来源（如「根据你的笔记…」「根据知识库…」）。资料中没有的信息，如实告知用户。
2. **笔记管理**：搜索笔记用 `search_notes_tool`，查看统计用 `get_note_stats_tool`。
3. **每日回顾**：用户问「今天要回顾什么」时，使用 `get_today_reviews_tool`；回顾完成后用 `mark_reviewed_tool` 标记。
4. **创建笔记**：用户想让 AI 帮忙写笔记时，使用 `create_note_tool`。
5. **关联推荐**：用户想找相似笔记或相关资料时，使用 `get_related_notes_tool`。
6. **补充检索**：当已有参考资料不足以回答用户问题、或用户追问了一个尚未覆盖的新维度、或需要验证某个具体事实时，使用 `search_rag` 工具做补充检索。
7. **直接回答**：当没有提供参考资料时，基于你的知识直接回答用户问题。

## 参考资料与来源区分

- 本轮参考资料包含两部分：**系统注入的前置检索资料**（system prompt 中提供的上下文）与 **search_rag 工具返回的补充证据**。
- 每条证据都会标注来源（笔记 / 知识库 / 知识图谱 / 外部搜索）。回答时必须区分本地证据与外部搜索证据，禁止把外部搜索内容说成本地资料。证据不足时必须如实说明还缺少哪些信息。

## search_rag 使用规则

1. 仅在参考资料不足、需新维度深挖、或需验证具体事实时调用。
2. `query` 参数必须是一个**新的、更聚焦的检索角度**；禁止原样复用本轮已经检索过的问题，也不要重复问一遍用户原话。
3. 若工具返回「该检索角度已覆盖」或「检索已达上限」的提示，立即停止检索，基于现有资料回答。

## 工具使用规则

1. 每次调用工具前，必须输出真实的自然语言思考过程。
2. 思考过程完成后，直接触发工具调用，工具入参必须是合法的 JSON 格式，字符串值必须用双引号包裹，不能使用单引号。
3. 参数中不要包含多余的换行符或非转义字符。
4. 获取工具结果后，生成最终的自然语言回答，给出具体、实用的建议。
5. 生成的结果要简单明了，少说废话。
```

- [ ] **Step 4: 创建 rag_context_prompt.md**

带 `{context}` 占位符，保留 `tests/agent/test_agent.py:422` 断言的短语，并含 search_rag 纪律与双 context 说明。文件完整内容：

```markdown
你是用户的智能助手。

以下是与用户问题相关的**前置检索参考资料**：
{context}

## 参考资料与来源区分

- 本轮参考资料包含两部分：**前置检索资料**（上方上下文）与 **search_rag 工具返回的补充证据**。
- 回答时必须区分本地证据（笔记、知识库）与外部搜索证据，避免把外部搜索内容说成用户本地资料。
- 如果资料中没有足够信息支撑结论，必须明确说明证据不足，并说明还缺少哪些信息。

## search_rag 使用规则

1. 仅在上述前置资料不足、需新维度深挖、或需验证具体事实时调用。
2. `query` 参数必须是一个**新的、更聚焦的检索角度**；禁止原样复用本轮已经检索过的问题，也不要重复问一遍用户原话。
3. 若工具返回「该检索角度已覆盖」或「检索已达上限」的提示，立即停止检索，基于现有资料回答。
```

- [ ] **Step 5: 修改 agent.py RAG 分支为模板加载**

`backend/app/agent/agent.py` 第 264-278 行，把硬编码 f-string 分支替换为模板加载（`load_prompt` 已在 `agent.py:27` 顶部导入）：

```python
            # 根据是否有 RAG 上下文决定 system prompt 内容
            if rag_context:
                system_prompt = (
                    load_prompt("rag_context_prompt").replace("{context}", rag_context)
                )
            else:
                system_prompt = agent_factory.default_system_prompt
```

- [ ] **Step 6: 运行受影响测试确认绿**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_prompt_loader.py tests/agent/test_agent.py -v`
Expected: 全绿（prompt_loader 全键非空、文件比对一致；`test_get_agent_stream_response_with_rag_context` 因模板保留短语而通过）。

- [ ] **Step 7: 给 rag_context 测试补 search_rag 纪律断言**

在 `tests/agent/test_agent.py::test_get_agent_stream_response_with_rag_context` 的断言区（`system_prompt` 断言后）追加：

```python
    assert "search_rag" in system_prompt
    assert "不要重复问一遍用户原话" in system_prompt
```

- [ ] **Step 8: 运行测试**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/agent/test_agent.py::test_get_agent_stream_response_with_rag_context -v`
Expected: PASS

- [ ] **Step 9: Lint + Commit**

```bash
cd backend && .\.venv\Scripts\python.exe -m ruff check app/agent/agent.py
git add app/config/prompt.yaml app/prompt/main_prompt.md app/prompt/rag_context_prompt.md app/prompt/main_prompt.txt tests/agent/test_agent.py app/agent/agent.py
git commit -m "refactor：Agent system prompt 抽模板（main_prompt.md/rag_context_prompt.md）并注入 search_rag 纪律"
```

---

### Task 2: agent_rag_tool 模块（护栏 + search_rag 工具）

**Files:**
- Create: `backend/app/agent/agent_rag_tool.py`
- Test: `backend/tests/rag/test_agentic_rag_tool.py`（新建）

**Interfaces:**
- Consumes: `app/agent/agent_tools` 的 `get_current_user_id_from_context` / `get_thinking_callback_from_context`；`app/rag/agentic_rag/service.AgenticRagService`（函数内惰性导入，便于测试 monkeypatch）；`app/rag/agentic_rag/evidence._SOURCE_LABELS`。
- Produces:
  - `normalize_query(query: str) -> str`
  - `rag_guard_var: ContextVar[dict | None]`（结构 `{"count": int, "searched": set[str]}`）
  - `init_rag_guard(rag_searched_queries: list[str] | None) -> None`
  - `build_pre_searched_queries(original_query: str, rag_result) -> list[str]`
  - `async search_rag(query: str) -> str`
  - 常量 `MAX_RAG_CALLS = 2`
  - Task 3 依赖：`agent.py` 引用 `search_rag`（默认工具）与 `init_rag_guard`（run_agent）；`chat.py` 引用 `build_pre_searched_queries`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/rag/test_agentic_rag_tool.py`：

```python
"""search_rag 工具单元测试：同 query 短路、请求级限次、thinking 回传、防抖输出。

测试统一 monkeypatch `app.rag.agentic_rag.service.AgenticRagService`，
断言 search_rag 是否真正触发了 RAG 管线。
"""
import pytest

from app.agent import agent_rag_tool as mod
from app.agent.agent_tools import set_current_user_id, set_thinking_callback


class FakeResult:
    def __init__(self, context="证据文本", evidences=None, used_web=False):
        self.context = context
        self.evidences = evidences or [
            type("E", (), {"source": "note"})()
        ]
        self.used_web = used_web


@pytest.fixture(autouse=True)
def _clean_guard():
    mod.reset_rag_guard()
    set_current_user_id(None)
    set_thinking_callback(None)
    yield
    mod.reset_rag_guard()
    set_current_user_id(None)
    set_thinking_callback(None)


@pytest.fixture
def fake_service(monkeypatch):
    calls = []

    class _FakeAgenticRagService:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, query, user_id, thinking_callback=None):
            calls.append({"query": query, "user_id": user_id, "cb": thinking_callback})
            return FakeResult(context=f"context:{query}")

    monkeypatch.setattr(
        "app.rag.agentic_rag.service.AgenticRagService", _FakeAgenticRagService
    )
    return calls


def test_normalize_query_strips_whitespace_and_punctuation():
    assert mod.normalize_query(" 重排序？方案。 ") == "重排序方案"
    assert mod.normalize_query("RAG vs Graph") == "ragvsgraph"


async def test_search_rag_short_circuits_on_pre_searched_query(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard(["重排序方案"])
    out = await mod.search_rag("重排序方案。")
    assert "已覆盖" in out
    assert fake_service == []


async def test_search_rag_runs_new_query_and_returns_framed_context(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    out = await mod.search_rag("全新的图谱维度")
    assert "补充检索结果" in out
    assert "context:全新的图谱维度" in out
    assert fake_service == [{"query": "全新的图谱维度", "user_id": "u1", "cb": None}]


async def test_search_rag_limits_to_two_calls_per_request(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    first = await mod.search_rag("角度一")
    second = await mod.search_rag("角度二")
    third = await mod.search_rag("角度三")
    assert "已覆盖" not in first and "上限" not in first
    assert "已覆盖" not in second and "上限" not in second
    assert "已达上限" in third
    assert len(fake_service) == 2


async def test_search_rag_records_successful_query_to_guard(fake_service):
    set_current_user_id("u1")
    mod.init_rag_guard([])
    await mod.search_rag("同一个角度")
    out = await mod.search_rag("同一个角度")
    assert "已覆盖" in out
    assert len(fake_service) == 1


async def test_search_rag_passes_thinking_callback(fake_service, monkeypatch):
    set_current_user_id("u1")
    cb = lambda event: None  # noqa: E731
    mod.set_tool_thinking_callback_for_test(cb)
    mod.init_rag_guard([])
    await mod.search_rag("图谱维度")
    assert fake_service[0]["cb"] is cb


async def test_search_rag_without_guard_passes_through(fake_service):
    set_current_user_id("u1")
    out = await mod.search_rag("无护栏也放行")
    assert "补充检索结果" in out
    assert len(fake_service) == 1


async def test_search_rag_returns_error_without_user():
    out = await mod.search_rag("随便问问")
    assert "无法确定用户身份" in out


def test_build_pre_searched_queries_requires_need_retrieval():
    plan = type("P", (), {"need_retrieval": False, "steps": []})()
    assert mod.build_pre_searched_queries("原始问题", type("R", (), {"plan": plan})()) == []


def test_build_pre_searched_queries_includes_steps_and_web():
    step = type("S", (), {"query": "改写后的子问题"})
    plan = type("P", (), {"need_retrieval": True, "steps": [step]})()
    answerability = type("A", (), {"web_queries": ["web:新鲜事实"]})()
    result = type("R", (), {"plan": plan, "answerability": answerability})()
    assert mod.build_pre_searched_queries("原始问题", result) == [
        "原始问题",
        "改写后的子问题",
        "web:新鲜事实",
    ]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/rag/test_agentic_rag_tool.py -v`
Expected: FAIL（`ModuleNotFoundError: app.agent.agent_rag_tool`）

- [ ] **Step 3: 实现 agent_rag_tool.py**

创建 `backend/app/agent/agent_rag_tool.py`，完整内容：

```python
"""Agent 侧 search_rag 工具：RAG 二次检索 + 请求级护栏。

护栏目标：
- 同 query 短路：query 字符归一化后命中「本轮已检索集合」→ 不重跑，返回提示；
- 请求级限次：单请求最多执行 MAX_RAG_CALLS 次真实 RAG 检索；
- 护栏状态存 ContextVar，随请求创建、请求结束即弃；无守卫上下文时放行
  （兼容 get_agent_response 非流式路径与 scripts/ 评测脚本直调）。
"""
import re
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import tool

from app.agent.agent_tools import (
    get_current_user_id_from_context,
    get_thinking_callback_from_context,
)

MAX_RAG_CALLS = 2

_DEDUP_HINT = "该检索角度已在本轮资料中覆盖。请直接基于已提供的参考资料回答；如需深入，请换一个更聚焦的新角度检索。"
_LIMIT_HINT = f"已检索过 {MAX_RAG_CALLS} 次，请基于现有资料回答，不要继续检索。"

rag_guard_var: ContextVar[dict[str, Any] | None] = ContextVar("rag_guard", default=None)


def reset_rag_guard() -> None:
    """清空护栏（每轮请求开始/测试收尾用）。"""
    rag_guard_var.set(None)


def normalize_query(query: str) -> str:
    """query 字符归一化：去空白/去常见全半角标点 + 小写，用于同 query 短路比对。"""
    return re.sub(r"[\s，。！？!?、；：:;，,．.、'\"“”‘’（）()【】\[\]]+", "", query).lower()


def init_rag_guard(rag_searched_queries: list[str] | None) -> None:
    """初始化请求级护栏：searched 预置前置管线已检索的 query（字符归一化后）。"""
    rag_guard_var.set(
        {"count": 0, "searched": {normalize_query(q) for q in (rag_searched_queries or [])}}
    )


def build_pre_searched_queries(original_query: str, rag_result) -> list[str]:
    """从前置 AgenticRagResult 提取「本轮实际检索用过的 query」。

    仅当 plan.need_retrieval=True 时纳入用户原 query 与各检索步 query；
    answerability.web_queries 只要有就纳入（web 兜底实际发生过检索）。
    rag_result 可为 None（管线失败降级）。
    """
    if rag_result is None:
        return []
    plan = getattr(rag_result, "plan", None)
    if plan is None:
        return []
    queries: list[str] = []
    if getattr(plan, "need_retrieval", False):
        queries.append(original_query)
        for step in getattr(plan, "steps", []) or []:
            q = getattr(step, "query", "")
            if q:
                queries.append(q)
    answerability = getattr(rag_result, "answerability", None)
    if answerability is not None:
        queries.extend(getattr(answerability, "web_queries", None) or [])
    return [q for q in queries if q]


@tool(description=(
    "在你的本地知识库/笔记/知识图谱中做补充检索，返回带来源标注的证据摘要。"
    "仅在已有参考资料不足、需按新维度深挖、或需验证具体事实时调用；"
    "参数 query 必须是本轮尚未检索过的新聚焦角度，不要重复已覆盖的问题。"
))
async def search_rag(query: str) -> str:
    """Agent 自主二次检索工具：复用 AgenticRagService 全链路。"""
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"

    guard = rag_guard_var.get()
    if guard is not None:
        if normalize_query(query) in guard["searched"]:
            return _DEDUP_HINT
        if guard["count"] >= MAX_RAG_CALLS:
            return _LIMIT_HINT

    try:
        from app.rag.agentic_rag.evidence import _SOURCE_LABELS
        from app.rag.agentic_rag.service import AgenticRagService

        result = await AgenticRagService().run(
            query,
            user_id,
            thinking_callback=get_thinking_callback_from_context(),
        )
    except Exception as e:
        return f"检索失败: {str(e)}"

    if guard is not None:
        guard["count"] += 1
        guard["searched"].add(normalize_query(query))

    if not result.context:
        return f"未检索到与「{query}」相关的本地资料，请如实告知资料不足。"

    counts: dict[str, int] = {}
    for evidence in getattr(result, "evidences", []) or []:
        source = getattr(evidence, "source", "unknown")
        counts[source] = counts.get(source, 0) + 1
    parts = [f"{_SOURCE_LABELS.get(src, src)} {cnt} 条" for src, cnt in counts.items()]
    summary = "、".join(parts) or "无证据"
    if getattr(result, "used_web", False):
        summary += "（含外部搜索）"

    return (
        f"以下是对检索角度「{query}」的**补充检索结果**，证据均已标注来源：\n"
        f"{result.context}\n\n证据概况：{summary}。"
    )
```

注意：测试里引用 `mod.set_tool_thinking_callback_for_test(cb)`——为测试注入 thinking 回调，需在模块中提供该辅助。请在 `agent_rag_tool.py` 末尾追加：

```python
def set_tool_thinking_callback_for_test(callback) -> None:
    """仅测试用：临时覆盖当前 thinking_callback 上下文，验证 search_rag 回传。"""
    from app.agent import agent_tools as _at

    _at.set_thinking_callback(callback)
```

- [ ] **Step 4: 运行测试**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/rag/test_agentic_rag_tool.py -v`
Expected: 全绿（10 个用例）

- [ ] **Step 5: Lint + Commit**

```bash
cd backend && .\.venv\Scripts\python.exe -m ruff check app/agent/agent_rag_tool.py tests/rag/test_agentic_rag_tool.py
git add app/agent/agent_rag_tool.py tests/rag/test_agentic_rag_tool.py
git commit -m "feat：search_rag 工具与请求级护栏（同 query 短路 + 单请求限 2 次）"
```

---

### Task 3: 绑定默认工具 + run_agent 护栏初始化 + chat.py 回填

**Files:**
- Modify: `backend/app/agent/agent.py:12-24`（import）、`agent.py:61-72`（默认工具）、`agent.py:225-260`（签名 + run_agent 初始化）
- Modify: `backend/app/router/chat.py:50-89`（回填 pre_searched_queries）
- Test: `backend/tests/agent/test_agent.py:137`（工具数 8→9）、新增 `backend/tests/rag/test_agentic_rag_guard_seed.py`（chat 回填逻辑）

**Interfaces:**
- Consumes: Task 2 的 `search_rag`、`init_rag_guard`、`build_pre_searched_queries`。
- Produces: `get_agent_stream_response(query, session_id, user_id, custom_tools=None, rag_context="", rag_searched_queries=None, **kwargs)`；默认工具含 `search_rag`；流式请求每次执行都在 `run_agent()` 内初始化护栏。

- [ ] **Step 1: 写失败测试（默认工具数）**

在 `tests/agent/test_agent.py:137-148` 更新 `test_factory_returns_8_default_tools`：

```python
def test_factory_returns_9_default_tools():
    names = {t.name for t in AgentFactory._get_default_tools()}
    assert names == {
        "what_time_is_now",
        "get_user_info_tools",
        "search_notes_tool",
        "get_note_stats_tool",
        "get_today_reviews_tool",
        "mark_reviewed_tool",
        "create_note_tool",
        "get_related_notes_tool",
        "search_rag",
    }
```

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/agent/test_agent.py::test_factory_returns_9_default_tools -v`
Expected: FAIL（缺 search_rag）

- [ ] **Step 2: 写失败测试（chat 回填 pre_searched_queries）**

创建 `backend/tests/rag/test_agentic_rag_guard_seed.py`（纯函数级验证 chat 回填来源，无需起服务）：

```python
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
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/rag/test_agentic_rag_guard_seed.py -v`
Expected: FAIL（agent_rag_tool 尚无 `build_pre_searched_queries`——Task 2 先于本任务落地后即转绿；若 Task 2 已完成，此测试已通过，则跳过本步并标注"已在 Task 2 实现"）。

- [ ] **Step 4: agent.py 绑定 search_rag 默认工具并初始化护栏**

`backend/app/agent/agent.py` 顶部 import 区（第 12-24 行，`from app.agent.agent_tools import (...)` 之后）追加：

```python
from app.agent.agent_rag_tool import init_rag_guard, search_rag
```

`_get_default_tools()`（第 61-72 行）返回值末尾追加 `search_rag`：

```python
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
```

`get_agent_stream_response`（第 225-232 行）签名追加 `rag_searched_queries: list[str] | None = None`：

```python
async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        rag_context: str = "",
        rag_searched_queries: list[str] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
```

`run_agent()`（第 253-260 行）内，`set_thinking_callback(thinking_callback)` 之后追加护栏初始化：

```python
            set_current_user_id(user_id)
            set_thinking_callback(thinking_callback)
            init_rag_guard(rag_searched_queries)
```

- [ ] **Step 5: chat.py 回填 pre_searched_queries**

`backend/app/router/chat.py` 第 30-97 行 `stream_with_rag_thinking` 内，在拿到 `result` 后调用 `build_pre_searched_queries` 并传给 `get_agent_stream_response`。修改第 77-85 行区域为：

```python
            result = await rag_task
            if result is not None:
                rag_context = result.context or ""

            from app.agent.agent_rag_tool import build_pre_searched_queries

            searched_queries = build_pre_searched_queries(request.query, result)

            # 转发 Agent 流式响应
            async for chunk in get_agent_stream_response(
                request.query,
                session_id,
                user_id,
                rag_context=rag_context,
                rag_searched_queries=searched_queries,
            ):
                yield chunk
```

建议把 `from app.agent.agent_rag_tool import build_pre_searched_queries` 提升到 `chat.py` 顶部 import 区（第 14 行 `get_agent_stream_response` import 之后），不要在函数内 import。

- [ ] **Step 6: 运行受影响测试**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/agent/test_agent.py tests/test_chat_api.py tests/rag/test_agentic_rag_tool.py tests/rag/test_agentic_rag_guard_seed.py -v`
Expected: 全绿（原 chat/agent 测试的 fake 均带 `**kwargs`，新增具名参数不破坏调用；工具数测试转 9）

- [ ] **Step 7: Lint + Commit**

```bash
cd backend && .\.venv\Scripts\python.exe -m ruff check app/agent/agent.py app/router/chat.py tests/agent/test_agent.py tests/rag/test_agentic_rag_guard_seed.py
git add app/agent/agent.py app/router/chat.py tests/agent/test_agent.py tests/rag/test_agentic_rag_guard_seed.py
git commit -m "feat：search_rag 绑定默认工具，流式请求护栏初始化并回填前置检索 query"
```

---

### Task 4: 端到端集成测试 + 全量回归

**Files:**
- Create: `backend/tests/agent/test_agent_rag_integration.py`
- Test: 全量回归

**Interfaces:**
- Consumes: `get_agent_response` / `get_agent_stream_response` 现签名、真实 `create_agent` + fake 模型（对齐 `test_agent.py` Level C 模式 `test_level_c_end_to_end_real_tool_calling`）、Task 2 的 `search_rag`。

- [ ] **Step 1: 写集成测试（真实 create_agent 触发 search_rag）**

创建 `backend/tests/agent/test_agent_rag_integration.py`，复用 `tests/agent/test_agent.py` 的 Level C 手法（fake 聊天模型发出 `search_rag` tool_call），验证：
1) `search_rag` 被真实绑定并被真实工具执行；
2) 工具输出进入最终回答；
3) 工具收到「已覆盖」提示后不再重复调用（第二轮返回提示即终结）。

```python
"""端到端集成测试：真实 create_agent 编译图触发 search_rag 工具。"""
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent import agent as agent_module
from app.agent.agent import get_agent_response
from app.agent.agent_tools import set_current_user_id
from app.rag.agentic_rag.service import AgenticRagService


class ToolCallingRagFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _isolate_user(monkeypatch):
    set_current_user_id("u1")
    yield


@pytest.mark.asyncio
async def test_real_agent_calls_search_rag_once(monkeypatch):
    """真实 create_agent 中，Agent 收到 tool_call 后执行 search_rag，工具结果入回答。"""
    calls = []

    class _FakeRagService:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, query, user_id, thinking_callback=None):
            calls.append(query)
            return type("R", (), {
                "context": "来自图谱的证据片段",
                "evidences": [type("E", (), {"source": "graph"})()],
                "used_web": False,
            })()

    monkeypatch.setattr(AgenticRagService, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(AgenticRagService, "run", _FakeRagService().run)

    model = ToolCallingRagFakeModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "search_rag", "args": {"query": "图谱补充维度"}, "id": "call_1"},
        ]),
        AIMessage(content="基于补充证据回答完成。"),
    ])
    monkeypatch.setattr(agent_module.agent_factory, "_create_chat_model", lambda custom_model=None: model)

    result = await get_agent_response("原始问题", user_id="u1")
    assert result["response"] == "基于补充证据回答完成。"
    assert calls == ["图谱补充维度"]
    assert any(step["tool"] == "search_rag" for step in result["steps"])


@pytest.mark.asyncio
async def test_real_agent_stops_after_covered_hint(monkeypatch):
    """护栏返回「已覆盖」提示后，Agent 不再发起第二次 search_rag。"""
    calls = []

    class _FakeRagService:
        async def run(self, query, user_id, thinking_callback=None):
            calls.append(query)
            raise AssertionError("不应真正执行 RAG：该 query 已被护栏短路")

    monkeypatch.setattr(AgenticRagService, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(AgenticRagService, "run", _FakeRagService().run)

    from app.agent import agent_rag_tool as rag_tool

    rag_tool.reset_rag_guard()
    rag_tool.init_rag_guard(["原问题"])
    try:
        model = ToolCallingRagFakeModel(responses=[
            AIMessage(content="", tool_calls=[
                {"name": "search_rag", "args": {"query": "原问题"}, "id": "call_1"},
            ]),
            AIMessage(content="好的，停止检索。"),
        ])
        monkeypatch.setattr(agent_module.agent_factory, "_create_chat_model", lambda custom_model=None: model)
        result = await get_agent_response("原问题", user_id="u1")
        assert calls == []
        assert any(step["tool"] == "search_rag" for step in result["steps"])
    finally:
        rag_tool.reset_rag_guard()
```

注意：`get_agent_response`（非流式）不调用 `run_agent`，护栏默认不初始化——因此第二个用例需手动 `init_rag_guard(["原问题"])` 模拟"前置已检索原问题"。

- [ ] **Step 2: 运行集成测试**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/agent/test_agent_rag_integration.py -v`
Expected: 全绿。若 `create_agent` 因缺少 OPENAI_API_KEY 在构造期报错，参考 `tests/agent/test_agent.py:152` 加 `monkeypatch.setenv("OPENAI_API_KEY", "dummy-key-for-construction-test")`。

- [ ] **Step 3: 全量回归**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: 全绿（无外部依赖；Neo4j/Redis/LLM 均被 conftest 屏蔽）

- [ ] **Step 4: 全量 ruff**

Run: `cd backend && .\.venv\Scripts\python.exe -m ruff check app tests`
Expected: 无新增违规（新增文件含 noqa 注释已内联）

- [ ] **Step 5: Commit**

```bash
git add tests/agent/test_agent_rag_integration.py
git commit -m "test：search_rag 端到端集成测试（真实 create_agent 触发与护栏短路停检）"
```

---

## Self-Review 记录

- **Spec coverage**：设计 §1（工具）→Task 2；§2（思考事件回传）→Task 2 工具传 cb + 现成 `agent.py` astream 通路；§3 护栏 A/B →Task 2 normalize/limit；§4 守卫注入回填 →Task 3；§5 模板化/双 context 区分/前置 vs 补充 →Task 1 模板 + Task 2 框定头；测试节 →Task 2/3/4。无遗漏。
- **占位符扫描**：所有代码步骤均含完整文件内容或逐行改动；无常量悬空引用（`MAX_RAG_CALLS`/`_SOURCE_LABELS` 均已定义/导入）。
- **类型一致性**：`rag_guard_var` 结构 `{"count": int, "searched": set[str]}` 全篇一致；`search_rag`/`init_rag_guard`/`build_pre_searched_queries` 签名在 Task 2 定义、Task 3/4 引用一致；`rag_searched_queries` 参数名全篇一致。
- 依赖项核对：测试 helper `set_tool_thinking_callback_for_test` 已在本任务 Step 3 中一并要求追加到实现文件，避免悬空。
