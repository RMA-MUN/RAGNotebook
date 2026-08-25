# 移除 LLM_TYPE 与各供应商方言配置：全量统一 OpenAI 兼容协议（第一期后续重构）

> **前置**：`plan/2026-08-17-agentic-rag-openai-compat.md` 的第一期改造已在 `refact/openai-compatible-unified` 分支完成（聊天/视觉/嵌入已统一走 `ChatOpenAI` / `OpenAIEmbeddings` / LangChain 多模态路径）。
> **本文档定义下一次重构**：删除 `LLM_TYPE` 以及 `ALIYUN_*` / `OLLAMA_*` / `VISION_MODEL_TYPE` / `EMBED_MODEL_TYPE` 等"方言配置"，让对话 / 视觉 / 嵌入三能力**只认 OpenAI 兼容协议**——每个能力只需 `base_url + api_key + model`。

## 1. 背景与动机

### 1.1 当前遗留的方言层

第一期改造后，代码仍保留 "LLM_TYPE 方言分发"，已经是死重：

- `factory.py::resolve_chat_config()` 按 `LLM_TYPE` 分三支（ALIYUN / OPENAI_COMPAT / OLLAMA），其中 ALIYUN 与 OPENAI_COMPAT 生成**完全相同的 ChatOpenAI**，仅环境变量来源不同；
- `VisionModelFactory` 仍有 `VISION_MODEL_TYPE`（OPENAI_COMPAT / OLLAMA）与 legacy ALIYUN 回落；
- `EmbedModelFactory` 仍有 `EMBED_MODEL_TYPE`（OLLAMA / OPENAI_COMPAT / ALIYUN）分支，其中 OLLAMA 走 `OllamaEmbeddings`；
- `agent.py` 的 `_create_chat_model` 仍为 OLLAMA 保留 `ChatOllama` 分支；
- 依赖里还挂着 `langchain-ollama`。

### 1.2 关键事实（支撑"全部统一 OpenAI 格式"）

- **Ollama 原生提供 OpenAI 兼容端点**：`http://localhost:11434/v1` 支持 `/v1/chat/completions`（含工具调用）、`/v1/embeddings`、`/v1/models`（[Ollama OpenAI Compatibility Layer](https://deepwiki.com/ollama/ollama/3.4-openai-compatibility)）。因此本地模型无需 `ChatOllama`/`OllamaEmbeddings` 特殊分支，用 `ChatOpenAI` / `OpenAIEmbeddings` 指向 `/v1` 即可。
- **阿里云百炼的 compatible-mode 本身就是 OpenAI 格式**（`https://dashscope.aliyuncs.com/compatible-mode/v1`，支持 chat、vision `image_url`、`/v1/embeddings`）。百炼用户直接用 `OPENAI_BASE_URL/OPENAI_API_KEY/OPENAI_MODEL_NAME` 指过去即可，与接 DeepSeek / 智谱 / vLLM / Ollama 无差别。
- **政策决定**：不保留 `ALIYUN_*` 过渡别名（百炼用户量少，且其格式就是 OpenAI；保留别名只会让代码和文档永远两套）。现有 `.env` 用户需一次性迁移（见 §4 迁移说明）。

### 1.3 目标配置模型（三组变量，一个协议）

```dotenv
# —— 对话（必填）——
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1   # 任意 OpenAI 兼容服务；默认 api.openai.com/v1
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL_NAME=qwen3-max

# —— 视觉（可选能力；未配 VISION_* 时回落 OPENAI_*）——
VISION_ENABLED=false          # false=关闭(PDF纯文本) | true=启用
VISION_BASE_URL=              # 留空=用 OPENAI_BASE_URL
VISION_API_KEY=               # 留空=用 OPENAI_API_KEY
VISION_MODEL_NAME=qwen-vl-max

# —— 嵌入（可选能力；未配 EMBED_* 时回落 OPENAI_*）——
EMBED_BASE_URL=               # 留空=用 OPENAI_BASE_URL
EMBED_API_KEY=                # 留空=用 OPENAI_API_KEY
EMBED_MODEL_NAME=text-embedding-v3
```

规则：
1. 对话为唯一"必填"能力；`OPENAI_BASE_URL` 缺省回落 OpenAI SDK 默认（`https://api.openai.com/v1`），`OPENAI_API_KEY` 必填（缺失由 SDK 构造期报 "Missing credentials"），`OPENAI_MODEL_NAME` 缺省 `gpt-4o-mini`。
2. 视觉仍由 `VISION_ENABLED` 三态开关控制（**保留第一期语义**：`false` 关闭零配置 / `true` 启用并校验配置 / 未设置默认跟随旧行为=启用）。
3. 视觉/嵌入的 `*_BASE_URL`/`*_API_KEY` 为空时回落对话的 `OPENAI_*`；`*_MODEL_NAME` 为空时用各自默认值。
4. 删除变量：`LLM_TYPE`、`ALIYUN_*`、`OLLAMA_*`、`VISION_MODEL_TYPE`、`EMBED_MODEL_TYPE`、`TEXT_EMBEDDING_MODEL_NAME`、`ALIYUN_EMBED_MODEL_NAME`、`VISION_CHAT_MODEL_NAME`。

## 2. 改造目标

1. 代码中不再出现 `LLM_TYPE` 分支与任何供应商专属创建路径；三能力统一由 `ChatOpenAI` / `OpenAIEmbeddings` / LangChain 多模态消息创建，参数只来自 `OPENAI_*` / `VISION_*` / `EMBED_*`。
2. 删除 `ChatOllama` / `OllamaEmbeddings` 全部使用，移除 `langchain-ollama` 依赖。
3. `VISION_ENABLED` 可选模块语义完整保留（关闭时零配置、PDF 纯文本）。
4. 保持前端接口协议与 SSE 事件格式不变；默认工具仍 8 个；Agentic RAG 仍属第二期不做。
5. 提供清晰的 `.env` 迁移指引（§4），旧配置用户一次性改三行即可。

## 3. 设计

### 3.1 `factory.py`：新增统一解析函数，删除方言分支

新增通用解析（对话为主，视觉/嵌入回落共用）：

```python
def resolve_openai_config(
    model_env: str,
    base_url_env: str = "OPENAI_BASE_URL",
    api_key_env: str = "OPENAI_API_KEY",
    fallback_to_openai: bool = True,
    default_model: str | None = None,
) -> dict:
    """解析某个能力的 (model, api_key, base_url)，全部走 OpenAI 兼容协议。

    - base_url/api_key 优先取能力专属变量（如 VISION_BASE_URL / VISION_API_KEY），
      为空时回落 OPENAI_BASE_URL / OPENAI_API_KEY（若 fallback_to_openai=True）。
    - model 优先取能力专属变量（如 VISION_MODEL_NAME），为空时用 default_model。
    - 返回 {"model": str, "api_key": str | None, "base_url": str | None}
    """
    base_url = os.getenv(base_url_env) or (os.getenv("OPENAI_BASE_URL") if fallback_to_openai else None)
    api_key = os.getenv(api_key_env) or (os.getenv("OPENAI_API_KEY") if fallback_to_openai else None)
    model = os.getenv(model_env) or default_model
    return {"model": model, "api_key": api_key, "base_url": base_url}
```

三工厂统一掉方言分支：

- `ChatModelFactory.generator()`：删除 `resolve_chat_config` / LLM_TYPE 分支，改为
  ```python
  cfg = resolve_openai_config("OPENAI_MODEL_NAME", default_model="gpt-4o-mini")
  logger.info(f"📦 ChatModel 使用OpenAI兼容模型: {cfg['model']}")
  return create_chat_openai(model=cfg["model"], api_key=cfg["api_key"],
                            base_url=cfg["base_url"], streaming=True, top_p=0.7)
  ```
  `resolve_chat_config` 函数删除（agent.py 同步改调用）。
- `VisionModelFactory.generator()`：保留 `VISION_ENABLED` 三态；启用分支不再看 `VISION_MODEL_TYPE`/OLLAMA：
  ```python
  vision_enabled = os.getenv("VISION_ENABLED")
  if vision_enabled is not None and vision_enabled.lower() == "false":
      logger.info("🎨 视觉模型未启用（VISION_ENABLED=false），PDF 走纯文本")
      return None
  # true 或未设置：统一 OpenAI 兼容（回落 OPENAI_*）
  cfg = resolve_openai_config("VISION_MODEL_NAME", "VISION_BASE_URL", "VISION_API_KEY",
                              default_model="qwen-vl-max")
  if not (cfg["base_url"] and cfg["api_key"]) and vision_enabled is not None and vision_enabled.lower() == "true":
      logger.warning("🎨 VISION_ENABLED=true 但缺少 VISION_BASE_URL/VISION_API_KEY（且无 OPENAI_* 回落），视觉已关闭")
      return None
  logger.info(f"🎨 VisionModel 使用OpenAI兼容多模态模型: {cfg['model']}")
  return create_chat_openai(model=cfg["model"], api_key=cfg["api_key"],
                            base_url=cfg["base_url"], streaming=False, top_p=0.7)
  ```
  注意：`VISION_MODEL_TYPE` / `VISION_OLLAMA_MODEL_NAME` / `ChatOllama` 全部删除。
- `EmbedModelFactory.generator()`：删除 `EMBED_MODEL_TYPE` / `OllamaEmbeddings` 分支：
  ```python
  cfg = resolve_openai_config("EMBED_MODEL_NAME", "EMBED_BASE_URL", "EMBED_API_KEY",
                              default_model="text-embedding-v3")
  logger.info(f"📦 EmbedModel 使用OpenAI兼容嵌入模型: {cfg['model']}")
  return OpenAIEmbeddings(model=cfg["model"], api_key=cfg["api_key"], base_url=cfg["base_url"])
  ```

### 3.2 `agent.py`：删除 ChatOllama 分支

`_create_chat_model` 简化为：

```python
def _create_chat_model(self, custom_model: str | None = None):
    """内部方法：创建聊天模型实例（统一 OpenAI 兼容协议）"""
    from app.utils.factory import create_chat_openai
    model = custom_model or os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
    logger.info(f"🤖 Agent使用OpenAI兼容模型: {model}")
    return create_chat_openai(
        model=model,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        streaming=True, top_p=0.7,
    )
```

删除 `from langchain_ollama import ChatOllama` 导入；`stream_runnable=False`、8 个默认工具、`AgentExecutor` 其余参数不变。

### 3.3 依赖（`pyproject.toml` / `uv.lock` / `requirements.txt`）

- 移除 `langchain-ollama==1.0.1`（本改造后全仓库无 `langchain_ollama` / `ChatOllama` / `OllamaEmbeddings` 引用）。
- 其余（`langchain-openai==1.1.14`、`langchain-core==1.2.31` 等）不变。
- 执行：`uv remove langchain-ollama` → `uv lock` → `uv pip compile pyproject.toml -o requirements.txt`（控制器执行，注意 index 为清华源，勿再触发 torch 重下载）。

### 3.4 `.env.example` 重写（§1.3 的配置模型；删除全部已废弃变量）

```dotenv
# ==============================================
# 对话模型（OpenAI 兼容协议，必填）
# 任意兼容服务：OpenAI / DeepSeek / 百炼 compatible-mode / 智谱 / Moonshot / vLLM / Ollama /v1
# ==============================================
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=your_api_key
OPENAI_MODEL_NAME=qwen3-max

# ==============================================
# 视觉模型（可选能力；默认关闭。VISION_* 留空则回落 OPENAI_*）
# ==============================================
VISION_ENABLED=false
# VISION_BASE_URL=
# VISION_API_KEY=
# VISION_MODEL_NAME=qwen-vl-max

# ==============================================
# 嵌入模型（可选能力；EMBED_* 留空则回落 OPENAI_*）
# ==============================================
# EMBED_BASE_URL=
# EMBED_API_KEY=
# EMBED_MODEL_NAME=text-embedding-v3
```

（视觉批处理/重排序/数据库/Redis/限流/JWT 等段落原样保留，不在本重构范围。）

## 4. 现有 `.env` 迁移指引（一次性三行变更）

| 旧变量 | 迁移到 |
| --- | --- |
| `LLM_TYPE=ALIYUN` | 删除 |
| `ALIYUN_ACCESS_KEY_SECRET` | `OPENAI_API_KEY` |
| `ALIYUN_BASE_URL` | `OPENAI_BASE_URL`（值不变：`https://dashscope.aliyuncs.com/compatible-mode/v1`） |
| `CHAT_MODEL_NAME` | `OPENAI_MODEL_NAME` |
| `LLM_TYPE=OLLAMA` + `OLLAMA_BASE_URL` + `OLLAMA_MODEL_NAME` | `OPENAI_BASE_URL=http://localhost:11434/v1`、`OPENAI_API_KEY=ollama`（任意占位）、`OPENAI_MODEL_NAME=<模型名>` |
| `EMBED_MODEL_TYPE=OLLAMA` + `TEXT_EMBEDDING_MODEL_NAME` | `EMBED_MODEL_NAME=<模型名>`（+ 若需独立地址配 `EMBED_BASE_URL`） |
| `VISION_MODEL_TYPE` / `VISION_CHAT_MODEL_NAME` / `VISION_OLLAMA_MODEL_NAME` | `VISION_MODEL_NAME`（+ 必要时 `VISION_BASE_URL/VISION_API_KEY`） |

## 5. 涉及文件清单

| 文件 | 变更类型 | 说明 |
| --- | --- | --- |
| `backend/app/utils/factory.py` | 修改 | 新增 `resolve_openai_config`；删除 `resolve_chat_config` / `LLM_TYPE` / `ChatOllama` / `VISION_MODEL_TYPE` / `EMBED_MODEL_TYPE` / `OllamaEmbeddings` 分支；三工厂统一 OpenAI 格式 |
| `backend/app/agent/agent.py` | 修改 | `_create_chat_model` 删除 ChatOllama 分支；删除 `langchain_ollama` 导入 |
| `backend/pyproject.toml` | 修改 | 移除 `langchain-ollama` |
| `backend/uv.lock` / `backend/requirements.txt` | 修改 | 重新生成 |
| `backend/.env.example` | 修改 | 重写为 §3.4 配置模型 |

**不动**：`vision_service.py`、`pdf_multimodal_loader.py`（第一期已完成，纯 OpenAI 路径，无方言概念）；`chat.py` / `rag_*` / `main_prompt.txt`（第二期范围）。

## 6. 实施步骤

> 命令在 `backend/` 目录执行；验证用 `backend\.venv\Scripts\python.exe`（勿用 `uv run`，避免触发 torch 重同步）。

1. **改 `factory.py`**：新增 `resolve_openai_config`（§3.1 代码）；`ChatModelFactory` / `VisionModelFactory` / `EmbedModelFactory` 按 §3.1 重写；删除 `resolve_chat_config` 与 `from langchain_ollama import ChatOllama, OllamaEmbeddings` 导入。
2. **改 `agent.py`**：`_create_chat_model` 按 §3.2 简化；删除 `ChatOllama` 导入。
3. **验证（V1-V5）**：
   - V1 导入冒烟：`from app.agent.agent import agent_factory` + `from app.utils.factory import ChatModelFactory, EmbedModelFactory, VisionModelFactory` 无 ImportError；
   - V2 默认工具仍 8 个；
   - V3 对话：无环境变量时 `ChatModelFactory().generator()` 报 "Missing credentials"（SDK 构造期校验，属预期）；设 `OPENAI_API_KEY=sk-test` + `OPENAI_BASE_URL=https://x/v1` + `OPENAI_MODEL_NAME=gpt-4o-mini` 后返回 `ChatOpenAI`；
   - V4 视觉：`VISION_ENABLED=false` → `None`；`VISION_ENABLED=true` 且无 key（无 OPENAI_* 回落）→ `None`（告警）；`VISION_ENABLED` 未设置 + 有 `OPENAI_*` → `ChatOpenAI`（回落生效）；
   - V5 嵌入：`OPENAI_*` 就绪时 `EmbedModelFactory().generator()` 返回 `OpenAIEmbeddings`；`EMBED_MODEL_NAME` 未设置时默认 `text-embedding-v3`。
4. **grep 清扫**：`backend/app`、`pyproject.toml`、`.env.example` 中不得出现 `LLM_TYPE`、`ChatOllama`、`OllamaEmbeddings`、`ALIYUN_`、`OLLAMA_`、`VISION_MODEL_TYPE`、`EMBED_MODEL_TYPE`（URL 字符串 `dashscope.aliyuncs.com` 除外）。
5. **依赖收尾**（控制器执行）：`uv remove langchain-ollama` → `uv lock` → `uv pip compile pyproject.toml -o requirements.txt`；grep 锁文件无 `langchain-ollama`。
6. **更新 `.env.example`**（§3.4）并在文档注释中给出 §4 迁移表。
7. **启动冒烟**：`uvicorn main:app` 短时拉起（真实 `.env` 需按 §4 迁移），确认 `ChatModel 使用OpenAI兼容模型` 日志与 `Application startup complete`。
8. **更新计划文档**：本文件即本次重构的规格；同时修订 `plan/2026-08-17-agentic-rag-openai-compat.md` 中与之冲突的段落（§3.1 环境变量表、§9 验收标准中 "LLM_TYPE=ALIYUN 旧 .env 零改动可用" 改为迁移说明指向本文件）。

## 7. 风险与回退

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 现有配置失效 | 移除 `ALIYUN_*` 后老 `.env` 不再可用 | §4 迁移表一次性改三行；冒烟验证迁移后的 `.env` |
| Ollama 走 /v1 的行为差异 | 原生 API 的一些元数据/quirk 与兼容层有差异 | 基础能力（对话/工具/视觉/嵌入）兼容层已覆盖；如遇 quirk 单独排查，不再回退方言分支（除非实测硬伤） |
| OpenAI SDK 构造期校验 Key | 缺 Key 时模型初始化即失败（沿用第一期已记录的行为变化） | 保持 fail-soft：后台初始化捕获异常，应用可启动；`.env.example` 醒目标注必填 |
| 嵌入式模型名默认值 | `text-embedding-v3` 并非所有兼容服务都有 | `EMBED_MODEL_NAME` 可配置；文档注明以供应商列表为准 |
| `uv remove` 触发 torch 重同步风险 | 重解析可能再次牵动 torch（venv 2.12.0 vs pin 2.11.0） | 控制器检查解析日志；若触发大下载则暂停询问是否顺延 torch pin |

## 8. 验收标准

- [ ] `backend/app` 与 `pyproject.toml` 中无 `LLM_TYPE` / `ChatOllama` / `OllamaEmbeddings` / `ALIYUN_` / `OLLAMA_` / `VISION_MODEL_TYPE` / `EMBED_MODEL_TYPE` 残留（URL 字符串除外）
- [ ] 对话：仅配 `OPENAI_*` 即可创建 `ChatOpenAI`；无 Key 时报 "Missing credentials"（预期）而非自定义报错
- [ ] 视觉：`VISION_ENABLED=false` 零配置返回 `None`；未设置时回落 `OPENAI_*` 创建 `ChatOpenAI`；`true` 缺配置 fail-soft 告警并禁用
- [ ] 嵌入：`OpenAIEmbeddings` 可从 `EMBED_*` 或回落 `OPENAI_*` 创建，默认模型 `text-embedding-v3`
- [ ] `langchain-ollama` 已从 pyproject / uv.lock / requirements.txt 移除
- [ ] 默认工具仍 8 个；`uvicorn main:app` 启动冒烟通过；SSE 事件格式不变
- [ ] `.env.example` 已按 §3.4 重写并附 §4 迁移表
- [ ] 所有 commit message 中文