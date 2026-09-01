# 可视化配置 API Key / Base URL（按用户）

> **目标**：在设置页为每个用户提供「对话 / 嵌入 / 视觉 / 云端重排序 / 联网搜索」五组模型的可视化配置（API key / base url / model），配置按用户独立，加密落库，调用时解析为该用户对应的模型，未配置时回落应用级 `.env`。
> **前置事实**：模型配置当前全部来自 `.env`（`app/core/settings.py`），由 `app/utils/factory.py` 在运行时解析，启动时预热成全局单例（`app/core/background_init.py`）供所有用户复用。文档已强调 Ollama 统一走 OpenAI 兼容端点（见 `plan/2026-08-18-remove-llm-type-openai-only-config.md`）。

## 1. 背景与动机

- 目前只需要在服务器的 `.env` 里配 `OPENAI_BASE_URL/API_KEY/MODEL_NAME`、`EMBED_*`、`VISION_*`、`RERANKER_*`、`WEB_SEARCH_*`，无法在应用内可视化修改。
- 期望：登录用户能在设置页填自己的 key / base url，立即生效（无需改环境变量、无需重启）。
- 关键约束：RAG 管线是**后端驱动且部分异步**的（对话/Agent 检索、上传时嵌入、后台图谱抽取 worker），后端必须在「无交互请求在飞」时也能拿到某用户的 key，因此配置必须存到服务端（浏览器-only 不可行）。

## 2. 决策记录

| 决策点 | 选定方案 | 理由 |
|---|---|---|
| 配置维度 | **按用户各自一套** | 多部署/多用户各自接入自己的供应商 |
| 后端方案 | **请求级用户模型解析** | 保留启动预热全局模型作回退，改动集中、风险低 |
| key 存储 | **`SECRET_KEY` 字段级加密落库**，调用时内存解密，GET 只回打码 | 满足「配置须在服务端」；加密降低泄露面 |
| 界面位置 | **挂在 `Settings` 页** | 改动最小，与主题/语言同页 |
| 本地 Ollama | api_key 可选，空则解析时**回填占位符 `"ollama"`** | Ollama `/v1` 层 "required but ignored"（官方文档原文）；避免 `ChatOpenAI`/`OpenAIEmbeddings` 构造期缺 key 报错 |
| 本地重排序 | **不支持**（本地已退役），只做云端 `RERANKER_*` | `RerankerModelFactory.generator()` 已 `return None`；commit `2b241cf` 退役本地重排序 |
| 「已配置」判定 | 该能力**填了 `base_url`** 即视为启用 | base_url 决定供应商 = 启用开关 |

### 2.1 关键事实：Ollama 兼容层对 api_key 的处理

Ollama 官方 OpenAI 兼容文档示例：

```python
client = OpenAI(
    base_url='http://localhost:11434/v1/',
    api_key='ollama',  # required but ignored
)
```

- `api_key` **required but ignored**——`/v1` 层接受但完全不校验；curl 示例甚至不带 `Authorization` 头。
- 因此本地 Ollama / vLLM / LiteLLM 等自托管端点，key 可为占位符。
- 由于 `langchain-openai` 的 `ChatOpenAI`/`OpenAIEmbeddings` 在 `api_key=None` 且全局也未配时会在**构造期**抛 "Did not find openai_api_key"，故**解析时务必回填一个非空占位符**，避免构造失败。
- 代码库既有先例：`.env.example:29` 与 `tests/test_factory.py:190` 均以 `ollama` 作为嵌入 key 占位。

## 3. 设计

### 3.1 数据模型

新增 `app/models/user_ai_config.py` 的 `UserAIConfig`，表 `user_ai_config`，每用户一行，PK=`user_id`（关联 `user_service.uuid`），全部可空：

| 能力 | 列 |
|---|---|
| 对话 | `chat_base_url` / `chat_api_key` / `chat_model` |
| 嵌入 | `embed_base_url` / `embed_api_key` / `embed_model` |
| 视觉 | `vision_base_url` / `vision_api_key` / `vision_model` |
| 重排序(云端) | `rerank_base_url` / `rerank_api_key` / `rerank_model` |
| 联网搜索 | `web_search_enabled` / `web_search_api_key` / `web_search_provider` |
| 其他 | `updated_at` |

登记进 `app/models/__init__.py`，由现有 `init_db()`（`app/db/db_config.py`）自动建表。

- 所有列可空：空 = 该能力未配置，回退应用级 `.env`。
- `*_api_key` 存**加密后**文本（`encrypt_secret` 产物），读回时解密。

### 3.2 加密工具

新增 `app/utils/encryption.py`：

- `encrypt_secret(plain: str) -> str` / `decrypt_secret(token: str) -> str`。
- 用 `settings.SECRET_KEY` 经 `hashlib.sha256` 派生 32 字节 → `base64.urlsafe_b64encode` 作 Fernet 密钥。
- 若 `SECRET_KEY` 为空：`PUT /config/ai` 明确报错并提示「请先在 `.env` 配置 SECRET_KEY」（禁止明文落库）。

### 3.3 后端端点

新增 `app/router/config.py`（`config_router`，挂进 `main.py`），复用现有鉴权依赖（返回当前用户），前缀 `/config`：

- `GET /config/ai` → 当前用户配置；api_key 一律**打码**返回 `****` + `is_set`（`sk-...abc` 只回尾 4 位或仅布尔），绝不回明文。
- `PUT /config/ai` → upsert；空字符串视为「清空/回退 `.env`」；写库前对非空 api_key 加密；成功后失效该用户 per-user 缓存。
- 校验：每组「已配置」判定为填了 `base_url`；api_key 可为空（回填占位符逻辑在解析端做，不在落库端强制）。

### 3.4 请求级用户模型解析

新增 `app/utils/user_config.py`：

- `get_user_ai_config(user_id) -> UserAIConfig | None`（短 TTL 缓存，如 30s；`PUT` 时 invalidate）。
- 每组能力解析函数（`_resolve_capability(user_id, capability)`）逻辑：
  1. 取该用户该能力配置；若 `base_url` 为空 → **未配置**，回退全局 `settings`（沿用现有 `_resolve_openai_config` 原子回退语义）。
  2. 若已配置：`api_key = 用户值或占位符 "ollama"`；`model = 用户值或默认`；`base_url = 用户值`。
- 便捷函数：
  - `create_chat_model_for_user(user_id)` → `create_chat_openai(...)`
  - `create_embed_model_for_user(user_id)` → `OpenAIEmbeddings(...)`
  - `create_vision_model_for_user(user_id)` → `create_chat_openai(..., streaming=False)`

> 占位符常量建议 `USER_API_KEY_PLACEHOLDER = "ollama"`，集中定义。

接线点（均已有 `user_id`）：

| 位置 | 改动 |
|---|---|
| `app/router/chat.py`（流式回复） | 用 `create_chat_model_for_user(user_id)` 建流式模型 |
| `app/rag/agentic_rag/service.py::run(query,user_id)` | 解析用户 chat 模型，传入 planner / evaluator / entity extractor |
| `app/rag/agentic_rag/planner.py` / `evaluator.py` / `query_entity_extractor.py` | 改为接收/使用 per-user 模型 |
| `app/rag/agentic_rag/local_retriever.py::_query_embedding(user_id,query)` | 用 `create_embed_model_for_user(user_id)` |
| `LocalRetriever._search_graph` 内 `_entity_candidates` | 用 per-user chat 模型 |
| `app/rag/document_handler/processor.py`（上传嵌入） | 用 `create_embed_model_for_user(user_id)` |

启动预热的全局单例（`init_manager.*`）保留，仅作为「未配置用户」的默认回退；评测脚本（无 user_id）不受影响。

### 3.5 前端

- 新增 `front/src/api/aiConfig.ts`：`getAiConfig()` / `saveAiConfig(payload)`，走现有 `client`。
- `front/src/api/endpoints.ts` 追加 `aiConfig: '/config/ai'`。
- `front/src/pages/Settings.tsx` 主题/语言下新增「AI 模型配置」卡片区，五个子卡片（Chat / Embed / Vision / Rerank / WebSearch），各含：
  - `base_url`（text）
  - `api_key`（password + 显隐切换），标注「云端必填，本地 Ollama 可留空/占位」
  - `model`（text）
  - 联网搜索子卡片另有 `enabled`(开关) + `provider`（tavily/serper）。
- 挂载时 `GET` 回填（api_key 仅显示占位 + 空值），保存 `PUT` + toast 成功/失败。
- i18n 键（zh/en）走现有 `useTranslation`。
- 附提示「更换嵌入模型后需重新上传/重建索引」（向量不可跨模型比较）。

### 3.6 过时注释清理

- `app/router/chat.py:138`、`app/router/chat_service.py:39` 的「使用本地Ollama重排序」docstring 已过时（现为云端 rerank），一并修正。

## 4. 实施顺序（3 个里程碑）

1. **数据模型 + 加密 + 配置端点 + 测试**（backend 核心）。
2. **前端 Settings UI + api + i18n**。
3. **对话/LLM 每用户接线**（chat 流式 + AgenticRagService/planner/evaluator/entity extractor）。
4. **嵌入每用户**（上传 + 检索 + 重索引提示）。
5. **视觉 + 云端重排序 + 联网搜索每用户**（含 docstring 清理）。

## 5. 测试与验证

- 后端 `tests/`：
  - `test_config_api.py`：GET/PUT 鉴权、打码（不回明文）、加密往返、空值清空回退、`base_url`→已配置判定、`SECRET_KEY` 缺失时 PUT 报错。
  - `test_user_config.py`：有配置用用户值；api_key 空回填 `"ollama"` 占位符；无配置回退 `.env`。
- 前端：`tsc`/`build` 通过，设置页加载/保存冒烟。

## 6. 风险与边界

- 每请求重建模型实例（HTTP/httpx 类，可接受；后续可加 LRU）。
- 更换 embed 供应商后既有向量不可交叉比较，需重新上传/重建索引（UI 提示）。
- `SECRET_KEY` 未配置时无法落库加密，属阻断项，需明确报错。
- 后台图谱 worker 在无请求时按任务携带的 `user_id` 解析配置，不受会话过期影响（这正是不选「临时存储/Redis TTL」的原因）。
