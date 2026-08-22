# 知识图谱个人知识库 — 设计文档

- 日期：2026-08-17
- 状态：待评审
- 关联项目：RAG NoteBook（FastAPI + LangChain 智能笔记助手）
- 所属规划：`plan/` 目录（本仓库设计文档专区）

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
| 图谱 UI | 独立图谱页面（/graph）+ 实体详情面板 |
| 双链 | Tiptap 编辑器内 `[[` 自动补全（可链笔记或实体）+ 双向引用边 |
| 实体类型 | 系统预置核心类型 + 用户自定义类型 |
| 存储 | MySQL 自建图层 + GraphStore 抽象接口（未来可平滑迁移 Neo4j） |
| 认证 | 维持现状（多用户 JWT 隔离），不引入单用户模式 |
| 可视化库 | AntV G6 5.x（React 生态） |

## 2. 总体架构

### 2.1 系统分层

```
┌─────────────────────────────────────────────────────────┐
│  Front (React 19)                                       │
│  图谱页面(/graph) · 实体详情面板 · Tiptap [[补全扩展      │
└──────────────────────────┬──────────────────────────────┘
                           │ REST + SSE
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
│  task_queue(复用)  —— 笔记保存后异步触发抽取              │
│  SSE(复用)         —— 推送抽取进度/结果                   │
└──────────────────────────────────────────────────────────┘
```

- 新模块 `app/graph` 与现有 `app/rag`、`app/services` 平级，边界清晰。
- 与现有架构的衔接点：
  - **异步任务**：复用 `app/rag/task_queue.py` 的队列机制，笔记保存后入队抽取任务。
  - **事件推送**：复用现有 SSE 通道，抽取完成/失败实时推送给前端。
  - **LLM 调用**：复用现有 `LLM_TYPE` 配置（ALIYUN Qwen3-Max / OLLAMA），以 JSON mode 结构化输出约束抽取格式。
  - **用户隔离**：复用现有 JWT 体系，图谱全部表带 `user_id`。
  - **嵌入模型**：不做向量自动合并，但实体描述/提及片段的语义检索可复用现有嵌入服务（作为可选增强，不在一期范围）。

### 2.2 GraphStore 存储层抽象

GraphStore 是存储层抽象接口（约 10 个方法），第一实现为 `MySQLGraphStore`（基于 SQLAlchemy 异步 ORM，与现有模型一致）。未来若迁移 Neo4j，只需新增一个实现类，service 层零改动。

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

## 3. 数据模型（MySQL 图层）

五张新表，全部带 `user_id` 延续现有隔离方案：

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

### 3.5 `graph_note_edges` — 笔记间双链引用边

| 列 | 类型 | 说明 |
|----|------|------|
| id | String(36) PK | UUID |
| user_id | String(36) 索引 | 隔离键 |
| source_note_id | String(36) 索引 | 引用笔记 |
| target_note_id | String(36) 索引 | 被引用笔记 |
| kind | String(20) | `wiki`（`[[` 手写双链）/ `auto`（暂留扩展） |
| created_at | DateTime | 时间戳 |

## 4. 实体抽取管线

### 4.1 异步流程

```
保存笔记 → MySQL 落库(现有,不阻塞) → 入队抽取任务(异步)
                                          │
                    ┌─────────────────────▼─────────────────────┐
                    │ ① 双链解析  [[...]] 语法扫描                │
                    │ ② LLM 结构化抽取（JSON mode）               │
                    │    entities: [{name, type, aliases,        │
                    │                description, mentions[]}]    │
                    │    relations: [{source, target, rel_type}] │
                    │ ③ 规范化名查重 → upsert 实体                │
                    │ ④ upsert 关系 + 实体↔笔记关联               │
                    │ ⑤ SSE 推送 {笔记id, 状态, 新增/更新数量}     │
                    └────────────────────────────────────────────┘
```

- **模型打通**：复用 `LLM_TYPE`（ALIYUN / OLLAMA），JSON mode 结构化输出。
- **幂等**：任务键 = 笔记 id + `updated_at` 版本；重复触发只覆盖更新，不重复建节点。
- **失败处理**：抽取失败不阻塞笔记保存；记日志 + SSE 推失败状态 + 图谱页提供"重新抽取"按钮（`POST /api/graph/notes/{id}/re-extract`）。
- **抽取窗口**：取标题 + 正文前约 6000 字；超出部分分批抽取后经去重合并（一期可简化为单次截断抽取，分批合并作为二期增强）。
- **重试**：LLM 返回非法 JSON → 重试 1 次 → 仍失败则记日志落 SSE 失败事件；超时/限流由 task_queue 机制重试，上限 3 次。

### 4.2 去重与合并

- **规范化名**：LLM 提示词强制输出规范化形式（"python" → "Python"），同一实体多次抽取得到相同文本。
- **入库查重**：`(user_id, name)` 精确命中 → 更新现有节点（累加来源笔记、合并别名）；别名表命中（"LLM" 命中 "大语言模型" 的 aliases）→ 并入现有节点。
- **人工合并**（详情面板）：选中两个实体合并 → 目标实体继承全部关系、备注、别名；源实体标记合并重定向（保留跳转兼容）。合并操作事务化，失败回滚。
- **人工编辑**：实体属性、关系、类型、删除/拆分全部开放，与现有笔记编辑一致。

### 4.3 双链解析（2.3 节详见前端交互，此处为解析规则）

- **解析时机**：保存笔记时同步扫描语法（快），异步落边。
- `[[笔记标题]]` → 查到笔记 → 写 `graph_note_edges`；双向连通（被引用侧自动挂"被引用"边）。
- `[[实体名]]` → 查到实体 → 写 `graph_entity_notes`；查不到 → 图谱页提示"创建实体"，用户可选择一键创建占位实体（不自动创建，避免脏数据）。
- 编辑器输入 `[[` → 弹出候选（Top 10 笔记标题 + Top 10 实体名两个分组），Tab/回车采纳。

### 4.4 提及证据

LLM 抽取时返回 `mentions[]`（实体的原文片段），存入 `graph_entity_notes.context`。详情面板展示"为什么这个实体会出现在这篇笔记里"——证据是抽取的真实片段，不是猜测。

## 5. 图谱 API（`graph_router.py`）

| 端点 | 说明 |
|------|------|
| `GET /api/graph/overview?types=&limit=` | 图谱总览（节点+边，支持类型过滤，不一次全量下发） |
| `GET /api/graph/entity/{id}` | 实体详情（属性、别名、类型、进出关系） |
| `GET /api/graph/entity/{id}/neighbors?depth=1\|2` | 邻居展开（按需拉取） |
| `GET /api/graph/entity/{id}/notes` | 关联笔记（含提及证据） |
| `GET /api/graph/notes/{note_id}/related` | 笔记子图（当前笔记 + 双链邻居 + 关联实体） |
| `GET /api/graph/search?q=` | 实体搜索（名称+别名模糊，中文支持） |
| `POST /api/graph/entities` / `PUT /api/graph/entities/{id}` / `DELETE /api/graph/entities/{id}` | 实体 CRUD |
| `POST /api/graph/entities/merge` | 合并实体 `{target_id, source_id}` |
| `GET/POST/PUT/DELETE /api/graph/types` | 类型管理（含用户自定义） |
| `GET/POST/PUT/DELETE /api/graph/relations` | 关系管理 |
| `POST /api/graph/notes/{id}/re-extract` | 手动重新抽取 |

**数据下发策略**：图谱数据不一次性全量下发。采用"总览层（Top 节点/按类型过滤）+ 按需邻居展开"模式（与 Obsidian Graph View 一致），前端性能与后端负载都可控。

## 6. 前端设计

### 6.1 图谱页面（新路由 `/graph`）

技术选型：**AntV G6 5.x**（力导向布局、缩放/拖拽/节点聚合即开即用，React 兼容性好）。备选 Cytoscape.js。

页面组成：

- **顶部工具栏**：搜索框（实体/笔记）、类型筛选开关（按类型显示/隐藏）、布局切换（力导向/径向）、刷新。
- **主画布**：节点着色 = 实体类型（颜色取自类型表）；笔记节点用方形固定样式区分，实体节点用圆形。
- **点击节点** → 右侧滑出详情面板。

### 6.2 详情面板

- **实体节点**：名称、类型、描述、别名、进出关系列表、关联笔记列表（**点击跳转编辑器对应笔记**）、提及证据片段、编辑/合并/删除按钮。
- **笔记节点**：标题、摘要、双链邻居、点击跳转编辑器。
- **边缘交互**：hover 边显示关系类型标签。

### 6.3 Tiptap `[[` 补全扩展

- 新增 `WikiLink` 扩展：输入 `[[` 触发悬浮候选框（Top 10 笔记 + Top 10 实体两个分组），Tab/回车采纳。
- 插入形式：`[[笔记标题]]` / `[[实体名]]`，渲染为可点击链接（点击跳转对应页/实体面板）。
- 保存时由 4.3 解析器处理，形成图谱边——**写笔记时顺手建立连接，图谱自动生长**。

### 6.4 数据流

保存笔记 → 双链语法同步解析 + 实体抽取异步执行 → 图谱 MySQL 更新 → SSE 推送 → 前端图谱页增量刷新（不整页重拉）。

## 7. 分期实施计划

### 子项目 1：图谱基础（本次范围）

1. 后端：五张表迁移 + GraphStore 接口 + `MySQLGraphStore` + `app/graph` 模块三层骨架
2. 实体抽取管线（task_queue 异步 + SSE + JSON 结构化输出 + 幂等 + 重试）
3. 图谱 API（第 5 节清单）
4. 前端：图谱页 + 详情面板 + Tiptap `[[` 补全
5. 类型管理（预置 + 自定义 CRUD）

### 子项目 2：GraphRAG（后续阶段，本期只预留接口）

- 在图谱查询层之上预留"实体膨胀检索"：用户提问 → 识别实体 → 沿图扩展一跳/二跳关联实体 → 关联笔记/切片作为上下文注入现有 RAG 前置管线（现有 `app/rag/rag_service.py` 的混合检索已提供路由层前置扩展点）。
- 本期不实现，但已保证数据结构可达：`graph_entity_notes` 提供 实体→笔记 反向查询，笔记切片继续走现有向量检索（ChromaDB），两条链路可在 service 层汇合。

## 8. 错误处理汇总

| 场景 | 处理 |
|------|------|
| LLM 抽取返回非法 JSON | 重试 1 次 → 失败则记日志 + SSE 推"抽取失败"，不影响笔记保存 |
| 抽取超时/限流 | 任务队列重试（复用现有机制），上限 3 次 |
| 双链目标不存在 | 图谱页提示创建占位实体（用户可选），不自动创建避免脏数据 |
| 并发保存同一笔记 | 幂等键 = 笔记 id + updated_at，后写覆盖先写 |
| 合并实体时冲突/循环 | 合并事务化，关系全部重定向到目标实体，冲突回滚 |
| 类型删除 | 实体 type_id 置空降级"未分类"，不级联删实体 |
| 图谱存储异常 | 不影响笔记主流程（异步解耦）；图谱页显示"待更新"状态 + 手动重抽 |

## 9. 测试策略

- **后端单元测试**（pytest，复用现有测试结构）：
  - `entity_extractor`：mock LLM 返回，测 JSON 解析、非法格式容错、字段缺省。
  - `link_parser`：`[[...]]` 边界（嵌套、未闭合、中文、空格）。
  - `GraphStore`：内存 SQLite 或测试 MySQL，测 CRUD / 合并 / 邻接查询 / 类型管理。
- **后端集成测试**：API 冒烟（图谱 CRUD 端点、SSE 事件形状）。
- **前端测试**：vitest + RTL，图谱组件渲染、类型过滤、详情面板交互。
- **抽取质量**：人工验收（抽查实体抽取准确性），LLM 任务不做自动化断言。

## 10. 验收标准（阶段一）

1. 新建/编辑笔记保存后，异步抽取自动执行，图谱页能看到新实体与关系（≤ 数十秒）。
2. 编辑器输入 `[[` 能补全笔记/实体，保存后笔记图出现引用边，图谱页可见。
3. 图谱页支持类型过滤、搜索、布局切换、节点展开邻居。
4. 点击实体节点能跳转到关联笔记；点击笔记节点能打开编辑器。
5. 实体/关系/类型可人工编辑、合并、删除，改动即时反映。
6. 用户数据隔离：A 用户的图谱对 B 用户不可见。
7. 抽取失败不影响笔记保存与编辑，且有重新抽取入口。

## 11. 范围外（YAGNI）

- 不做向量相似度自动实体合并（人工合并为准）。
- 不做知识库文档（PDF/PPT/Word）入图谱（仅笔记）。
- 不做单用户模式/匿名登录（维持现状）。
- 不做知识图谱自动生成关系类型枚举表（一期开放文本）。
- 不引入 Redis Pub/Sub 事件总线（现有 task_queue + SSE 够用）。
- 不做多设备/多端同步（维持现状）。