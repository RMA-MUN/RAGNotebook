# 知识图谱个人知识库 — 设计文档

- 日期：2026-08-17（v2 修订：2026-08-26）
- 状态：已评审 · 待实施
- 关联项目：RAG NoteBook（FastAPI + LangChain 智能笔记助手）
- 所属规划：`plan/` 目录（本仓库设计文档专区）

## 修订记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-08-17 | 初稿（待评审） |
| v2 | 2026-08-26 | 评审修订：LLM 统一 OpenAI 兼容协议（移除 LLM_TYPE 假设）、异步机制改为 `asyncio.create_task` 火后不管（参照 `_auto_tag_and_review`，非 task_queue）、新增 `GET /api/graph/events` 长连接 SSE 订阅、抽取改为内容哈希增量触发、新增 `graph_extract_logs` 表、双链前端改为完整自定义渲染（marked/turndown/Tiptap Node）、补笔记生命周期联动清理、明确 MySQL 起步并保留迁移 Neo4j 的抽象接口与混合边界 |

## 1. 背景与目标

RAG NoteBook 目前是一个"笔记管理 + RAG 知识库 + AI 写作辅助"的智能笔记助手，核心痛点是"笔记写了从不回看、知识散落成孤岛"。

本次改造的目标：将项目升级为**知识图谱驱动的个人知识库**，分两个阶段推进：

- **阶段一（本次范围）**：知识图谱可视化 + 笔记关联。从笔记中抽取实体和关系，生成可交互的知识图谱；引入 Obsidian 式双链，让笔记通过引用关系自动互联。
- **阶段二（后续，本次只预留接口）**：GraphRAG 增强检索。用图结构增强现有 RAG 问答——识别问题中的实体、沿图扩展一跳/二跳关联实体、将关联笔记作为上下文注入检索管线。

设计决策（经讨论确认）：

| 维度 | 决定 |
|------|------|
| 节点语义 | 混合图：实体/概念图谱为主 + 笔记节点经双链互连 + 实体可点击跳回笔记 |
| 抽取数据源 | 仅用户笔记（上传的 RAG 文档不入图谱） |
| 人工控制 | LLM 自动抽取 + 人工可编辑（合并/删除/添加/拆分/类型管理） |
| 实体去重 | 规范化名 + 别名表 + 人工合并（不做向量相似度自动合并） |
| 抽取触发 | **内容哈希增量触发**：仅正文 hash 变化才重新抽取，幂等覆盖旧关联 |
| 异步机制 | **`asyncio.create_task` 火后不管**（参照 `note_service._auto_tag_and_review`），不依赖 task_queue |
| 实时通知 | **`GET /api/graph/events` 长连接 SSE 订阅**（per-user asyncio.Queue 发布/订阅） |
| 图谱 UI | 独立图谱页面（/graph）+ 实体详情面板 |
| 双链 | Tiptap 编辑器内 `[[` 自动补全（可链笔记或实体）+ 双向引用边 + **完整自定义渲染**（marked 扩展 + turndown 规则 + Tiptap Node） |
| 实体类型 | 系统预置核心类型 + 用户自定义类型 |
| 存储 | **MySQL 自建图层（6 张表）** + GraphStore 抽象接口（**MySQL 起步，保留未来迁移 Neo4j 的接口与混合边界**，见 2.2） |
| 认证 | 维持现状（多用户 JWT 隔离），不引入单用户模式 |
| 可视化库 | AntV G6 5.x（React 生态，按需 import） |

## 2. 总体架构

### 2.1 系统分层

```
┌─────────────────────────────────────────────────────────┐
│  Front (React 19)                                       │
│  图谱页面(/graph) · 实体详情面板 · Tiptap [[补全扩展      │
└──────────────────────────┬──────────────────────────────┘
                           │ REST + SSE(长连接订阅)
┌──────────────────────────▼──────────────────────────────┐
│  Backend (FastAPI)                                       │
│  ┌────────────────────────────────────────────────────┐  │
│  │ app/graph（新模块，三层分离）                        │  │
│  │  ├─ routers/    graph_router.py  图谱 API 路由      │  │
│  │  ├─ services/   graph_service.py 业务编排           │  │
│  │  ├─ extraction/ entity_extractor.py LLM 结构化抽取  │  │
│  │  │              link_parser.py     [[双链]] 语法解析 │  │
│  │  ├─ storage/    graph_store.py    抽象接口          │  │
│  │  │              mysql_graph_store.py MySQL 实现     │  │
│  │  └─ schemas/    graph.py Pydantic 模型              │  │
│  └────────────────────────────────────────────────────┘  │
│  asyncio.create_task  —— 笔记保存后异步触发抽取          │
│  SSE 长连接订阅         —— 抽取进度/结果实时推送          │
└──────────────────────────────────────────────────────────┘
```

- 新模块 `app/graph` 与现有 `app/rag`、`app/services` 平级，边界清晰。
- 与现有架构的衔接点：
  - **异步任务**：笔记保存后以 **`asyncio.create_task` 火后不管**方式触发抽取（`note_service._auto_tag_and_review` 同款模式，见 `backend/app/services/note_service.py:124/392`），任务内使用独立 `AsyncSessionLocal` 会话、异常捕获记日志。**不复用 `app/rag/task_queue.py`**（该队列仅用于切片线程协调，无 worker/重试/持久化，且都在单次请求生命周期内）。
  - **事件推送**：新增 `GET /api/graph/events` 长连接订阅端点，服务端 per-user `asyncio.Queue` 发布/订阅；抽取完成/失败实时推送给打开图谱页的前端。
  - **LLM 调用**：复用 `init_manager.chat_model`（**OpenAI 兼容协议**，`OPENAI_BASE_URL/API_KEY/MODEL_NAME`，无 LLM_TYPE 方言）。结构化输出采用**双路径**：优先尝试 `response_format={"type": "json_object"}`（qwen3/百炼 compatible-mode 支持），失败则回落"提示词 + 手写正则提取 JSON"（复用 `note_service._extract_json` 模式）。
  - **用户隔离**：复用现有 JWT 体系（`get_current_user_id`），图谱全部表带 `user_id`。
  - **嵌入模型**：不做向量自动合并，但实体描述/提及片段的语义检索可复用现有嵌入服务（作为可选增强，不在一期范围）。

### 2.2 GraphStore 存储层抽象（MySQL 起步，保留 Neo4j 迁移缝）

GraphStore 是存储层抽象接口（14 个方法，见下），第一实现为 `MySQLGraphStore`（基于 SQLAlchemy 异步 ORM，与现有模型一致）。

**存储决策**：本期数据全部落在 MySQL（6 张表，见 §3）。保留抽象接口的目的，是让未来**可选迁移 Neo4j** 时 service 层零改动——只需新增一个 `Neo4jGraphStore` 实现类 + 一次性数据迁移脚本。

**存储实现的"切接口"缝**：service 层通过工厂函数 `get_graph_store()`（参照现有 `get_note_service()` 模式，见 `backend/app/services/note_service.py:679`）获取 `GraphStore` 实例，**不在任何业务代码里直接实例化 `MySQLGraphStore`**。将来迁移只需改工厂返回 `Neo4jGraphStore`，service 层零感知。

**未来迁移的混合边界**（接口设计已为此留缝，本期不实现）：
- 实体/关系（`graph_entities`、`graph_relations`）语义与图数据库天然匹配，未来可整体迁入 Neo4j（节点 + 关系边，`get_neighbors(depth)` 变长路径查询正是 Cypher 主场）。
- 笔记侧（`graph_entity_notes`、`graph_note_edges`）因笔记主数据仍在 MySQL，未来要么保留在 MySQL（跨库两步查询：先查 Neo4j 邻居 → 再拿 note_id 去 MySQL join），要么将笔记节点一并入图（需处理与 MySQL 的双写同步，成本较高）——**迁移时再决策**。
- 本期只保证：接口签名稳定、`get_neighbors`（实体图，未来进 Neo4j）与 `get_entity_notes`/`get_note_graph`（涉及笔记，未来跨库）边界清晰，service 层不感知存储实现差异。

接口方法（全部带 `user_id` 参数）：

```python
class GraphStore(ABC):
    # 实体
    async def upsert_entity(self, user_id, entity: EntityIn) -> Entity
    async def get_entity(self, user_id, entity_id) -> Entity | None
    async def search_entities(self, user_id, query: str, limit: int) -> list[Entity]
    async def delete_entity(self, user_id, entity_id) -> None
    async def merge_entities(self, user_id, target_id, source_id) -> Entity  # 事务化
    # 关系
    async def create_relation(self, user_id, rel: RelationIn) -> Relation
    async def delete_relation(self, user_id, relation_id) -> None
    # 查询
    async def get_neighbors(self, user_id, entity_id, depth: int) -> NeighborGraph
    async def get_note_graph(self, user_id, note_id) -> NoteSubGraph
    async def get_entity_notes(self, user_id, entity_id) -> list[EntityNoteLink]
    async def get_overview(self, user_id, type_ids: list, limit: int) -> GraphView
    # 类型
    async def list_types(self, user_id) -> list[EntityType]
    async def upsert_type(self, user_id, type_in: TypeIn) -> EntityType
    async def delete_type(self, user_id, type_id) -> None
```

## 3. 数据模型（MySQL 图层，6 张表）

六张新表，全部带 `user_id` 延续现有隔离方案：

### 3.1 `graph_entity_types` — 实体类型

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 可空 | NULL = 系统预置类型（对所有用户可见可用）；非 NULL = 用户自定义 |
| name | String(50) | 类型标识（如 `person`、`tech`） |
| display_name | String(50) | 显示名（如 "人物"、"技术/工具"） |
| color | String(20) | 图谱着色（十六进制） |
| icon | String(100) 可空 | 图标标识 |
| is_system | Boolean | 是否系统预置 |
| created_at / updated_at | DateTime | 时间戳 |

系统预置核心类型：人物、技术/工具、概念、组织、地点、项目、事件。用户可在图谱页/设置页 CRUD 自定义类型（名称、颜色、图标）。删除类型时实体 `type_id` 置空降级为"未分类"，不级联删除实体，避免误操作丢失数据。

### 3.2 `graph_entities` — 实体

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| name | String(200) | **规范化名**（唯一索引含 user_id），去重主键 |
| display_name | String(200) | 展示名（可含大小写/格式差异） |
| type_id | String(36) 可空 | 关联 `graph_entity_types.id`，空 = 未分类 |
| description | Text 可空 | LLM 生成或人工编辑的描述 |
| aliases | JSON | 别名表，如 `["LLM", "大语言模型"]` |
| confidence | Float | LLM 抽取置信度（0~1） |
| source_note_ids | JSON | 首次来源笔记 id 列表 |
| created_at / updated_at | DateTime | 时间戳 |

### 3.3 `graph_relations` — 实体间关系

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| source_id | String(36) 索引 | 源实体 |
| target_id | String(36) 索引 | 目标实体 |
| relation_type | String(50) | 关系类型（如 "基于"、"任职于"、"属于"） |
| properties | JSON | 关系附加属性 |
| confidence | Float | 抽取置信度 |
| created_at / updated_at | DateTime | 时间戳 |

关系类型不做预定义枚举表（一期保持开放文本，由 LLM 输出 + 人工编辑；如后续需要，可加类型归一化）。

### 3.4 `graph_entity_notes` — 实体 ↔ 笔记关联

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| entity_id | String(36) 索引 | 实体 |
| note_id | String(36) 索引 | 笔记 |
| mention_count | Integer | 提及次数 |
| context | JSON | 提及证据片段列表（支持详情面板溯源跳转） |
| created_at | DateTime | 时间戳 |

> 生命周期：笔记被删除时，该笔记在此表的全部关联一并清理（见 4.5）。

### 3.5 `graph_note_edges` — 笔记间双链引用边

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| source_note_id | String(36) 索引 | 引用笔记 |
| target_note_id | String(36) 索引 | 被引用笔记 |
| kind | String(20) | `wiki`（`[[` 手写双链）/ `auto`（暂留扩展） |
| created_at | DateTime | 时间戳 |

> 生命周期：笔记被删除时，该笔记所有入/出边一并清理（见 4.5）。

### 3.6 `graph_extract_logs` — 抽取日志（内容哈希触发 + 状态展示）

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| note_id | String(36) | 笔记（`(user_id, note_id)` 唯一索引） |
| content_hash | String(64) | 上次抽取时正文哈希（MD5 即可，用于增量触发） |
| status | String(20) | `pending` / `success` / `failed` |
| new_count | Integer | 上次抽取新增实体数 |
| update_count | Integer | 上次抽取更新实体数 |
| error_message | Text 可空 | 失败原因 |
| triggered_at / finished_at | DateTime | 时间戳 |

作用：
- **内容哈希增量触发的持久化支撑**：保存笔记时计算正文 hash，与 `content_hash` 相同则跳过抽取；不同则触发。
- **图谱页状态展示**：显示"抽取中 / 抽取成功（新增 N 更新 M）/ 失败原因"，供手动重抽判断（与 §8 错误处理、`re-extract` 端点配套）。

## 4. 实体抽取管线

### 4.1 异步流程

```
保存笔记 → MySQL 落库(现有,不阻塞) → 计算正文 content_hash
                                        │ 查 graph_extract_logs 对比上次 hash
                          ┌─────────────┴─────────────┐
                          │ hash 相同 → 跳过（不重复抽取）│
                          │ hash 不同 → create_task(抽取)│
                          └─────────────┬─────────────┘
                                        ▼
                    ① 双链解析  [[...]] 语法扫描（同步、快）
                    ② LLM 结构化抽取（JSON mode 尝试 + 正则兜底）
                    ③ 规范化名查重 → upsert 实体
                    ④ upsert 关系 + 实体↔笔记关联（按 note_id 先清后插，幂等覆盖）
                    ⑤ 写回 graph_extract_logs（status/new_count/update_count）
                    ⑥ 推送 /api/graph/events 事件 {笔记id, 状态, 新增/更新数量}
```

- **模型打通**：复用 `init_manager.chat_model`（OpenAI 兼容协议）；结构化输出**双路径**——优先 `response_format={"type": "json_object"}`，失败回落"提示词 + `_extract_json` 手写正则"（与 `note_service` 同款，见 `note_service.py:412-416`）。
- **幂等**：抽取键 = `note_id + content_hash`；`graph_extract_logs` 行存在且 hash 相同 → 不触发。重复触发只覆盖更新，不重复建节点。
- **失败处理**：抽取失败不阻塞笔记保存；写回 `extract_logs(status=failed, error_message)` + 推送失败事件；图谱页显示失败原因 + "重新抽取"按钮（`POST /api/graph/notes/{id}/re-extract`，重置 status 复用同一条管线）。
- **抽取窗口**：取标题 + 正文前约 6000 字；超出部分分批抽取后经去重合并（一期可简化为单次截断抽取，分批合并作为二期增强）。
- **重试**：LLM 返回非法 JSON → 重试 1 次 → 仍失败则写 `failed` 并推失败事件；超时/限流记录 `failed`（图谱页可重抽），不做自动重排。

### 4.2 去重与合并

- **规范化名**：LLM 提示词强制输出规范化形式（"python" → "Python"），同一实体多次抽取得到相同文本。
- **入库查重**：`(user_id, name)` 精确命中 → 更新现有节点（累加来源笔记、合并别名）；别名表命中（"LLM" 命中 "大语言模型" 的 aliases）→ 并入现有节点。
- **人工合并**（详情面板）：选中两个实体合并 → 目标实体继承全部关系、备注、别名；源实体标记合并重定向（保留跳转兼容）。合并操作事务化，失败回滚。
- **人工编辑**：实体属性、关系、类型、删除/拆分全部开放，与现有笔记编辑一致。

### 4.3 双链解析

- **解析时机**：保存笔记时同步扫描语法（快），异步落边。
- `[[笔记标题]]` → 查到笔记 → 写 `graph_note_edges`；双向连通（被引用侧自动挂"被引用"边）。
- `[[实体名]]` → 查到实体 → 写 `graph_entity_notes`；查不到 → 图谱页提示"创建实体"，用户可选择一键创建占位实体（不自动创建，避免脏数据）。
- 编辑器输入 `[[` → 弹出候选（Top 10 笔记标题 + Top 10 实体名两个分组），Tab/回车采纳（前端交互详见 6.3）。

### 4.4 提及证据

LLM 抽取时返回 `mentions[]`（实体的原文片段），存入 `graph_entity_notes.context`。详情面板展示"为什么这个实体会出现在这篇笔记里"——证据是抽取的真实片段，不是猜测。

### 4.5 笔记生命周期联动

- **笔记删除**：删除该笔记的入/出双链边（`graph_note_edges`）、实体关联（`graph_entity_notes`）、抽取日志（`graph_extract_logs`）；在笔记删除接口内事务化，避免孤儿边/悬空引用。
- **笔记标题变更**：引用旧标题的双链边在下次抽取时按新标题重解析（旧标题匹配的边重建为目标），或随抽取一并更新。
- **重抽幂等**：按 `note_id` 先清后插（删除旧 `graph_entity_notes` 关联 → 重新写入），保证 mentions 不残留旧文本。

## 5. 图谱 API（`graph_router.py`）

| 端点 | 说明 |
|------|------|
| `GET /api/graph/events` | **SSE 长连接订阅**（抽取进度/结果推送，JWT 鉴权，per-user asyncio.Queue） |
| `GET /api/graph/overview?types=&limit=` | 图谱总览（节点+边，支持类型过滤，不一次全量下发） |
| `GET /api/graph/entity/{id}` | 实体详情（属性、别名、类型、进出关系） |
| `GET /api/graph/entity/{id}/neighbors?depth=1\|2` | 邻居展开（按需拉取） |
| `GET /api/graph/entity/{id}/notes` | 关联笔记（含提及证据） |
| `GET /api/graph/notes/{note_id}/related` | 笔记子图（当前笔记 + 双链邻居 + 关联实体） |
| `GET /api/graph/search?q=` | 搜索：**实体（名称+别名）+ 笔记标题两组结果**（中文支持，供 `[[` 补全） |
| `GET /api/graph/extract-logs?note_id=` | 抽取状态查询（供图谱页展示"抽取中/失败原因"） |
| `POST /api/graph/entities` / `PUT /api/graph/entities/{id}` / `DELETE /api/graph/entities/{id}` | 实体 CRUD |
| `POST /api/graph/entities/merge` | 合并实体 `{target_id, source_id}` |
| `GET/POST/PUT/DELETE /api/graph/types` | 类型管理（含用户自定义） |
| `GET/POST/PUT/DELETE /api/graph/relations` | 关系管理 |
| `POST /api/graph/notes/{id}/re-extract` | 手动重新抽取（重置 extract_logs 状态复用管线） |

**数据下发策略**：图谱数据不一次性全量下发。采用"总览层（Top 节点/按类型过滤）+ 按需邻居展开"模式（与 Obsidian Graph View 一致），前端性能与后端负载都可控。

## 6. 前端设计

### 6.1 图谱页面（新路由 `/graph`）

技术选型：**AntV G6 5.x**（力导向布局、缩放/拖拽/节点聚合即开即用，React 兼容性好；按需 import 控制体积）。备选 Cytoscape.js。

页面组成：

- **顶部工具栏**：搜索框（实体/笔记）、类型筛选开关（按类型显示/隐藏）、布局切换（力导向/径向）、刷新。
- **主画布**：节点着色 = 实体类型（颜色取自类型表）；笔记节点用方形固定样式区分，实体节点用圆形。
- **点击节点** → 右侧滑出详情面板。

### 6.2 详情面板

- **实体节点**：名称、类型、描述、别名、进出关系列表、关联笔记列表（**点击跳转编辑器对应笔记**）、提及证据片段、编辑/合并/删除按钮。
- **笔记节点**：标题、摘要、双链邻居、点击跳转编辑器。
- **边缘交互**：hover 边显示关系类型标签。

### 6.3 Tiptap `[[` 补全扩展（完整自定义渲染）

编辑器以 Markdown 为数据源（`marked.parse` 渲染入、turndown 转回，见 `TiptapEditor.tsx`），双链需要三方配合：

- **Tiptap `WikiLink` Node**：输入 `[[` 触发悬浮候选框（Top 10 笔记 + Top 10 实体两个分组，数据源 `GET /api/graph/search`），Tab/回车采纳；插入形式 `[[笔记标题]]` / `[[实体名]]`，编辑器内渲染为可点击/高亮的链接样式。
- **marked 扩展**：渲染 Markdown 源码中的 `[[...]]` 为可点击链接（点击跳转对应笔记页/实体面板）。
- **turndown 规则**：转回 Markdown 时**保留 `[[...]]` 原样**（需验证往返不回退成普通链接语法），保证保存解析器（4.3）可识别。
- 保存时由 4.3 解析器处理，形成图谱边——**写笔记时顺手建立连接，图谱自动生长**。

### 6.4 数据流

保存笔记 → 双链语法同步解析 + 抽取异步执行（`create_task`）→ 图谱 MySQL 更新 → 写回 `graph_extract_logs` → `GET /api/graph/events` 推送 → 前端图谱页增量刷新（不整页重拉）。

- **新增 `useGraphEvents` hook**：基于 fetch ReadableStream 的 **GET 长连接**（现有 `useSSE.ts` 是 POST 单次流封装，需扩展或新写），自动携带 JWT，接收抽取完成/失败事件做增量刷新；断线自动重连。

### 6.5 国际化

图谱页、详情面板、补全候选框等新增文案接入现有 **i18n**（i18next，`front/src/i18n/`，zh-CN / en-US 双语言）。

## 7. 分期实施计划

### 子项目 1：图谱基础（本次范围）

1. 后端：**六张表** + GraphStore 接口 + `MySQLGraphStore` + **`get_graph_store()` 工厂** + `app/graph` 模块三层骨架
2. 实体抽取管线（`asyncio.create_task` 异步 + `graph_extract_logs` 哈希触发与状态 + SSE 订阅推送 + JSON 双路径 + 幂等 + 生命周期清理）
3. 图谱 API（第 5 节清单，含 `/events` 与 `/extract-logs`）
4. 前端：图谱页 + 详情面板 + Tiptap `[[` 补全（完整渲染）
5. 类型管理（预置 + 自定义 CRUD）

### 子项目 2：GraphRAG（后续阶段，本期只预留接口）

- 在图谱查询层之上预留"实体膨胀检索"：用户提问 → 识别实体 → 沿图扩展一跳/二跳关联实体 → 关联笔记/切片作为上下文注入现有 RAG 前置管线（现有 `app/rag/rag_service.py` 混合检索之上挂接）。
- 本期不实现，但已保证数据结构可达：`graph_entity_notes` 提供 实体→笔记 反向查询，笔记切片继续走现有向量检索（ChromaDB），两条链路可在 service 层汇合。
- 若届时图查询成为瓶颈，可迁实体/关系到 Neo4j（2.2 混合边界），service 层零改动。

## 8. 错误处理汇总

| 场景 | 处理 |
|------|------|
| LLM 抽取返回非法 JSON | 重试 1 次（优先 JSON mode，回落正则兜底）→ 失败写 `extract_logs(failed)` + 推送失败事件，不影响笔记保存 |
| 抽取超时/限流 | 写 `extract_logs(failed)`，图谱页显示失败原因 + 手动重抽 |
| 双链目标不存在 | 图谱页提示创建占位实体（用户可选），不自动创建避免脏数据 |
| 并发保存同一笔记 | 抽取键 = `note_id + content_hash`，`(user_id, note_id)` 唯一索引 + 状态锁，同笔记同时只跑一个抽取，后写覆盖先写 |
| 合并实体时冲突/循环 | 合并事务化，关系全部重定向到目标实体，冲突回滚 |
| 类型删除 | 实体 type_id 置空降级"未分类"，不级联删实体 |
| 笔记删除 | 事务化清理该笔记的边、实体关联、抽取日志（4.5） |
| 图谱存储异常 | 不影响笔记主流程（异步解耦）；图谱页显示"待更新/失败"状态（`extract_logs`）+ 手动重抽 |
| 进程重启丢失抽取任务 | 进程内 `create_task` 重启即失；`extract_logs` 无 `pending` 残留 → 下次保存 hash 变化或手动重抽可恢复 |

## 9. 测试策略

- **后端单元测试**（pytest，复用现有测试结构）：
  - `entity_extractor`：mock LLM 返回，测 JSON **双路径**（response_format 与正则兜底）解析、非法格式容错、字段缺省。
  - `link_parser`：`[[...]]` 边界（嵌套、未闭合、中文、空格）。
  - `GraphStore`：内存 SQLite 或测试 MySQL，测 CRUD / 合并 / 邻接查询 / 类型管理。
  - **抽取触发**：content_hash 相同跳过 / 不同触发、`extract_logs` 状态流转（pending→success/failed）。
  - **生命周期**：笔记删除清理边与实体关联、重抽先清后插不残留 mentions。
- **后端集成测试**：API 冒烟（图谱 CRUD 端点、`/api/graph/events` SSE 事件形状、`/extract-logs`）。
- **前端测试**：vitest + RTL，图谱组件渲染、类型过滤、详情面板交互、WikiLink 渲染与 Markdown 往返（`[[...]]` 保留原样）。
- **抽取质量**：人工验收（抽查实体抽取准确性），LLM 任务不做自动化断言。

## 10. 验收标准（阶段一）

1. 新建/编辑笔记（**内容变更**）保存后，异步抽取自动执行，图谱页能看到新实体与关系（≤ 数十秒）。
2. 编辑器输入 `[[` 能补全笔记/实体，编辑器中 `[[...]]` 渲染为可点击链接；保存后笔记图出现引用边，图谱页可见。
3. 图谱页支持类型过滤、搜索、布局切换、节点展开邻居。
4. 点击实体节点能跳转到关联笔记；点击笔记节点能打开编辑器。
5. 实体/关系/类型可人工编辑、合并、删除，改动即时反映。
6. 用户数据隔离：A 用户的图谱对 B 用户不可见。
7. 抽取失败不影响笔记保存与编辑，图谱页显示失败原因 + 重新抽取入口；删除笔记后图谱无孤儿边。

## 11. 范围外（YAGNI）

- 不做向量相似度自动实体合并（人工合并为准）。
- 不做知识库文档（PDF/PPT/Word）入图谱（仅笔记）。
- 不做单用户模式/匿名登录（维持现状）。
- 不做知识图谱自动生成关系类型枚举表（一期开放文本）。
- 不引入 Redis Pub/Sub 事件总线（per-user asyncio.Queue + SSE 长连接够用）。
- 不做多设备/多端同步（维持现状）。
- **不做持久化任务表/后台 worker**（`create_task` 火后不管，重启丢任务由内容哈希 + 手动重抽兜底）。
- **本期不引入 Neo4j**（保留 GraphStore 抽象与混合边界，作为未来可选迁移）。
