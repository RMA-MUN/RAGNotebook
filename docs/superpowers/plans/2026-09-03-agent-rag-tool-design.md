# 设计：search_rag 工具化（Agent 自主二次检索）

日期：2026-09-03
关联：GitHub issue #15（yzw0422：《关于 Agentic RAG 和 Agent 可恢复执行的两个建议》建议 1）

## 背景与目标

Issue #15 建议把 RAG 封装为 Agent 工具，让 Agent 自主判断是否检索、能否按结果改写问题再次检索。仓库当前 master 已是 LangChain 1.0+ `create_agent` + `AgenticRagService`（planner → 图/文本检索 → answerability → web 兜底 → 证据融合 → context），但仍作为**前置管线**在 `chat.py` 每轮先跑、结果以 `rag_context` 注入 system prompt，Agent 侧没有可调用的 RAG 工具。

经与维护者讨论确认的核心取舍：

- **保留前置管线**：每条消息仍先跑一次 `AgenticRagService` 并把 context 注入 system prompt（确定性保底不变）。
- **新增粗粒度单工具 `search_rag(query)`**：始终绑定、由 Agent 自主决定调用时机与 query；复用现有 `AgenticRagService`，不做细粒度多工具拆分。
- **工具的真正价值**：Agent 在前置资料不足时，派生**新维度的聚焦 query** 二次检索；以及当前置 `rag_context` 为空（寒暄/planner 误判/管线失败置空）时的检索兜底。若只拿原 query 重跑，与前置结果重复、无价值——这是"同 query 短路"要防的主要浪费。
- **护栏分工**：Prompt 软约束为主力；代码闸（同 query 短路 + 请求级限次）为硬性兜底，不依赖模型自觉。

## 架构

前置管线流程不变，search_rag 工具在其后作为 Agent 可用的补充检索通道：

```
用户 query
  └→ 前置 AgenticRagService.run(query) ── context 注入 system prompt（不变）
        └→ create_agent(..., 绑定 search_rag) ── Agent 自主决定是否深挖
              └→ search_rag(query')
                    ├─ 闸① 同 query 短路（不重跑）
                    ├─ 闸② 请求级限次 2 次
                    └─ AgenticRagService.run(query') ── 思考事件实时回传前端
```

## 组件与数据流

### 1. 新工具 `search_rag(query: str) -> str`

- 位置：`backend/app/agent/agent_tools.py`（与现有工具同文件，或拆独立 `rag_tool.py`，实现时按文件体量决定）。
- 粗粒度：内部跑 `AgenticRagService().run(query, user_id, thinking_callback)`，复用 planner/检索/evaluator/web 全部逻辑。
- 返回值：`result.context`（已格式化证据上下文）+ 一句概要（证据条数 / 来源数 / 是否 used_web），供 Agent 判断可信度。
- 归属：加入 `AgentFactory._get_default_tools()`（`agent.py:61`）默认工具列表，对所有聊天请求可用。

### 2. 思考事件回传

- `AgenticRagService.run` 的阶段事件（agentic_plan / local_retrieval / answerability / web_search / evidence_fusion / context_ready）经 `thinking_callback` 播发。
- 工具内部从 `thinking_callback_var`（`agent_tools.py:28` `get_thinking_callback_from_context()`）取回当前请求回调并传给 `AgenticRagService`；取不到则静默执行（不崩溃）。
- 通路已验证：`run_agent()`（`agent.py:256`）`set_thinking_callback` 设的即 `get_agent_stream_response` 内部 queue，工具在 agent 图内执行可读到同一 contextvar，事件流入同一转发循环 yield 到前端。

### 3. 护栏（代码闸，工具入口先查后跑）

请求级 ContextVar `rag_guard_var: ContextVar[dict]`，结构 `{"count": int, "searched": set[str]}`。

- **闸① 同 query 短路**：`normalize_query(query)`（strip → 小写 → 去标点空白）命中 `searched` 集合 → 不重跑，返回："该检索角度已在本轮资料中覆盖。请直接基于已提供的参考资料回答；如需深入，请换一个更聚焦的新角度检索。"
- **闸② 请求级限次**：`count >= 2` → 返回："已检索过 2 次，请基于现有资料回答，不要继续检索。"
- 跑完成功后才 `searched.add(normalize_query(query))`（防同请求内对同一 query 重复跑）。

### 4. 守卫的注入与回填

- 注入点：`run_agent()`（`agent.py:256`）入口，与 `set_current_user_id` 旁初始化守卫 dict。
- 初始 `searched` 回填：chat.py 拿到前置 `AgenticRagResult` 后，将 `{用户原 query} ∪ {plan.steps[].query} ∪ {answerability.web_queries}`（`schemas.py` 现成字段）塞入守卫——前置实际搜过的 query 不重复搜。
- 无守卫上下文时放行（兼容非流式 `get_agent_response` 与 scripts/ 评测脚本不牵连）。

## 5. system prompt 维护改造（软约束主力 + 顺带修复现存维护裂缝）

现状问题：Agent 的 system prompt 有两套且维护方式分裂——
- `main_prompt.txt`：`AgentFactory._get_default_system_prompt()` 经 `load_prompt('main_prompt')` 加载（`agent.py:87`），文件维护；
- RAG 分支：`agent.py:265-272` 是硬编码 f-string，一旦有 `rag_context` 就**整体替换** main_prompt，main_prompt 里的工具纪律在该主路径上根本不生效。

本次一并改造（保持 prompt.yaml 逻辑名→路径映射机制不变，仅 `.txt`→`.md` 与新增条目）：

1. **模板 md 化（仅 Agent 相关）**：`main_prompt.txt` → `main_prompt.md`（内容 markdown 结构书写）；新增 `rag_context_prompt.md`（带 `{context}` 占位符）。`prompt.yaml` 同步改 `.md` 值并新增 `rag_context_prompt` 条目。其它非 Agent 模板不动。
2. **`agent.py:265-272` 硬编码 f-string 抽为模板**：RAG 分支改用 `load_prompt('rag_context_prompt')` 加载后 `.replace("{context}", rag_context)` 注入；无 context 分支维持 `load_prompt('main_prompt')`。
3. **search_rag 使用纪律写入两处模板**（双分支一致）：
   - 何时用：前置资料不足、需新维度、需验证具体事实时才调；
   - 怎么用：query 必须换一个新的聚焦角度，禁止原样复用本轮已检索问题；
   - 何时停：收到"已覆盖 / 已达上限"提示即停止检索，基于现有资料回答；
   - 兜底自由度：即使本轮无前置资料（`rag_context` 为空分支），Agent 仍可用 search_rag 兜底。
4. **双 context 区分说明写入模板**："本轮参考资料包含系统注入的前置检索资料与 search_rag 返回的补充证据，一律按条目标注来源作答，禁止把外部搜索内容说成本地资料。"

### 前置 vs 补充 context 的区分保证

- 物理隔离：前置 context 进 system prompt（每轮请求开头注入一次）；search_rag 返回的 context 以 `ToolMessage` 形态只存在于当前轮的 messages 中，DB 落库仅存 `(user, assistant)` 文本对，不跨轮残留。
- search_rag 返回值**加框定头**，不裸抛 `result.context`：如「以下是针对检索角度『{query}』的**补充检索结果**，证据均已标注来源：\n{result.context}」。
- 证据统一 `format_evidence_context`（`evidence.py:43`）格式、每条自带 `来源：{笔记/知识库/知识图谱/外部搜索}《…》` 标签，模型按条目溯源引用。

## 不改动

- 前置管线 `AgenticRagService` 本体及 `AgenticRagResult` schema。
- chat.py 编排结构（仅取前置 plan 回填守卫初始集，属可选项）。
- prompt.yaml 映射机制本身（仅值改 `.md`、新增条目）。

## 错误处理

- `AgenticRagService.run` 内部各阶段已有降级（单步检索失败跳过、web 兜底失败返回空），工具不新增全局 try/except。
- 思考回调取不到或不可用：静默，不阻塞检索与回答。
- 工具整体异常：沿用现有工具风格返回错误字符串，不抛给 agent 图。

## 测试

- `backend/tests/rag/test_agentic_rag_tool.py`
  - 命中已搜集合 → 不调用 RAG service（mock 断言未调用）；
  - 新 query → 真跑并返回 context 概要；
  - 超过 2 次 → 拒绝并提示；
  - `normalize_query` 纯函数（去标点/空白/大小写）单测。
- `backend/tests/agent/test_agent_rag_integration.py`
  - Agent 收到"已覆盖"提示后不再重复调用工具（模拟 messages 序列断言）。
