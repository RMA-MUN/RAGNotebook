# 模型接口统一 OpenAI 兼容协议改造方案（第一期：聊天 / 视觉 / 嵌入）

> **范围声明（2026-08-17 修订）**：本期**只做模型接口统一**——聊天、视觉、嵌入三类模型全部统一为 OpenAI 兼容协议，并解耦出可选的视觉模块。
> **Agentic RAG 工具链改造延至第二期**，见文末 §8；本期不新增任何 Agent 工具、不改 `main_prompt.txt`、不加 `Agentic_RAG` 开关。

**Goal:** 让聊天 / 视觉 / 嵌入三类模型全部通过 OpenAI 兼容协议创建，由环境变量自由选择供应商（OpenAI / 百炼兼容模式 / 智谱 / Moonshot / vLLM / Ollama /v1 等），删除全部 DashScope 原生 SDK 与协议绑定 hack，且旧 `LLM_TYPE=ALIYUN` 配置零改动可用。

**Architecture:** 用一个 LLM 类型解析函数（`resolve_*_config()`）+ 一个 OpenAI 兼容工厂函数（`create_chat_openai` / `OpenAIEmbeddings`）替代原来按供应商硬编码的三套工厂分支；`vision_service.py` 的多模态调用统一走 LangChain `HumanMessage(image_url)` 路径；视觉用 `VISION_ENABLED` 开关做成可选模块。

**Tech Stack:** `langchain-openai`（ChatOpenAI / OpenAIEmbeddings）、`langchain-community`（其余组件保留）、`langchain-ollama`（OLLAMA 本地分支保留）、FastAPI asyncio。

**Spec:** 本文档即规格与实施计划；对照代码 `backend/app/agent/agent.py`、`backend/app/utils/factory.py`、`backend/app/utils/vision_service.py`、`backend/app/utils/pdf_multimodal_loader.py`、`backend/app/core/background_init.py` 实施。

---

## 1. 背景与现状

### 1.1 现状：三种模型、三种协议，三处供应商耦合

**① 聊天模型（两处实现，行为需保持一致）**
- `backend/app/agent/agent.py::_create_chat_model`：
  - `LLM_TYPE=ALIYUN` → `_NormalizedTongyi`（继承 `langchain_community.ChatTongyi`，走 **DashScope 原生 SDK**）；
  - `LLM_TYPE=OLLAMA` → `ChatOllama`（langchain-ollama）。
- `backend/app/utils/factory.py::ChatModelFactory.generator()`：同样的 ALIYUN / OLLAMA 两分支。
- `_NormalizedTongyi` 是**协议绑定 hack**：为修复 Qwen3 流式 `tool_calls.arguments` 增量减法产生非法 JSON 而重写 `subtract_client_response`，仅适用于 DashScope SDK。

**② 视觉模型（双轨制：ChatOllama 走 LangChain，其余走 DashScope 原生）**
- `factory.py::VisionModelFactory`：ALIYUN → `ChatTongyi`；OLLAMA → `ChatOllama`。
- `vision_service.py`：`_is_ollama()` 判断模型类名，**非 Ollama 一律走 `_dashscope_describe*`**（`dashscope.MultiModalConversation.call`，消息格式是 DashScope 私有的 `{"image": ...}`，不是 OpenAI 的 `image_url`）。这是第二个协议耦合点。
- `pdf_multimodal_loader.py`（异步 + 同步两版）无条件渲染页面 PNG 并调用视觉服务。

**③ 嵌入模型**
- `factory.py::EmbedModelFactory`：`EMBED_MODEL_TYPE=ALIYUN` → `DashScopeEmbeddingsWrapper`（原生 `dashscope.TextEmbedding.call`）；`OLLAMA` → `OllamaEmbeddings`。

**后果**：换供应商必须改代码；`_NormalizedTongyi` 是脆弱补丁；视觉/嵌入与阿里云隐式绑定（`VISION_*` 复用 `ALIYUN_*` 变量）。

### 1.2 已核实的关键事实（决定本期方案可行性）

- `ALIYUN_BASE_URL` 当前值已是 `https://dashscope.aliyuncs.com/compatible-mode/v1`（**本身就是 OpenAI 兼容地址**），所以 ALIYUN 分支换 `ChatOpenAI` 后旧 `.env` 无需改动。
- 百炼兼容模式**支持多模态视觉**：`ChatOpenAI` + `image_url` 可调 qwen-vl-max / qwen3-vl（[阿里云 OpenAI 兼容-Chat 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[Visual understanding 示例](https://www.alibabacloud.com/help/en/model-studio/visual-understanding-hk)）。
- 百炼兼容模式**提供 `/v1/embeddings`**（[阿里云向量模型文档](https://www.alibabacloud.com/help/zh/functioncompute/vector-model)、[QwenCloud text-embedding OpenAI 接口](https://docs.qwencloud.com/api-reference/text-embedding/openai-embedding.md)），可用 `langchain_openai.OpenAIEmbeddings` 直接调用。
- **OpenAI 兼容 = 三个标准端点**：`/v1/chat/completions`（对话 + 视觉共用，视觉只是 content 里加 `image_url` 块）、`/v1/embeddings`、`/v1/models`。协议统一 ≠ 能力普及：对话几乎所有供应商都有，**视觉、嵌入是逐供应商可选的**——因此视觉做成可选项（§3.3），嵌入保留供应商开关（§3.4）。
- **Anthropic Messages 协议与 OpenAI 不兼容**（请求结构、流式事件、鉴权头均不同），`ChatOpenAI` 无法直连 Anthropic 端点；本期不做，作为扩展点记录（§7）。
- 依赖现状：`.venv` 里**已装有** `langchain_openai-1.1.14` + `openai-2.42.0`，但 `pyproject.toml` / `uv.lock` / `requirements.txt` **均未声明**（锁文件漂移），必须补 `uv add langchain-openai` 并锁版本。
- `dashscope` 原生 SDK 的全部引用点：`agent.py`（ChatTongyi）、`factory.py`（ChatTongyi + DashScopeEmbeddingsWrapper）、`vision_service.py`（原生多模态）。本期三点全部移除后，`dashscope`、`langchain-dashscope` 依赖可整体删除。

---

## 2. 改造目标（本期）

1. **聊天 / 视觉 / 嵌入统一 OpenAI 兼容格式**，全部由环境变量驱动，供应商任选；
2. **视觉做成可选模块**：`VISION_ENABLED=false` 时零配置、零耦合、PDF 走纯文本；`true` 时走独立的通用视觉配置；
3. **删除全部 DashScope 原生耦合**：`_NormalizedTongyi`、`ChatTongyi`、`DashScopeEmbeddingsWrapper`、`_dashscope_describe*`，`dashscope` 依赖移除；
4. 旧 `LLM_TYPE=ALIYUN` 配置**零改动**可用（走 compatible-mode + ChatOpenAI）；
5. 不改变前端接口协议与 SSE 事件格式；默认 Agent 工具仍为 8 个（本期不加工具）。
6. **不在本期范围**：Agentic RAG 工具链、web 搜索、`Agentic_RAG` 开关、`main_prompt.txt` 修改（均延至 §8 第二期）。

---

## 3. 设计

### 3.1 配置模型：三组独立配置 + legacy 别名

每个模型类别独立解析自己的 `base_url / api_key / model`。`ALIYUN_*` 变量保留为 **legacy 别名**（因为其 base_url 本身已是兼容模式），自动映射进 OpenAI 兼容配置。

**聊天模型**

| 环境变量 | 作用 | 取值示例 |
| --- | --- | --- |
| `LLM_TYPE` | 模型类型 | `ALIYUN`（默认，legacy）/ `OPENAI_COMPAT`（`OPENAI` 作别名）/ `OLLAMA` |
| `OPENAI_BASE_URL` | OpenAI 兼容服务地址 | `https://api.openai.com/v1`、`https://api.deepseek.com/v1`、`https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `OPENAI_API_KEY` | API Key | `sk-...` |
| `OPENAI_MODEL_NAME` | 模型名 | `gpt-4o-mini`、`deepseek-chat`、`qwen3-max` |
| `ALIYUN_BASE_URL` | legacy：百炼地址（compatible-mode） | 原值不变 |
| `ALIYUN_ACCESS_KEY_SECRET` | legacy：百炼 Key | 原值不变 |
| `CHAT_MODEL_NAME` / `ALIYUN_MODEL_NAME` | legacy：百炼模型名 | 原值不变 |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL_NAME` | 本地模型 | 原值不变 |

**视觉模型（可选模块，独立配置）**

| 环境变量 | 作用 | 取值示例 |
| --- | --- | --- |
| `VISION_ENABLED` | 三态开关：未设置=跟随旧行为；`true`=强制用 `VISION_MODEL_TYPE`；`false`=彻底关闭（零配置） | `false` |
| `VISION_MODEL_TYPE` | 视觉类型 | `OPENAI_COMPAT` / `OLLAMA` |
| `VISION_BASE_URL` | OpenAI 兼容服务地址（OPENAI_COMPAT 用） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `VISION_API_KEY` | API Key（OPENAI_COMPAT 用） | `sk-...` |
| `VISION_MODEL_NAME` | 视觉模型名（兼容旧 `VISION_CHAT_MODEL_NAME`） | `qwen-vl-max`、`gpt-4o` |
| `VISION_OLLAMA_MODEL_NAME` | 本地视觉模型名（OLLAMA 用） | `qwen-vl:7b` |

**嵌入模型**

| 环境变量 | 作用 | 取值示例 |
| --- | --- | --- |
| `EMBED_MODEL_TYPE` | 嵌入类型 | `OLLAMA`（默认，legacy）/ `OPENAI_COMPAT` / `ALIYUN`(legacy 别名) |
| `EMBED_BASE_URL` | OpenAI 兼容嵌入地址 | `https://api.openai.com/v1`、百炼 compatible-mode |
| `EMBED_API_KEY` | API Key | `sk-...` |
| `EMBED_MODEL_NAME` | 嵌入模型名 | `text-embedding-3-small`、`text-embedding-v3` |
| `TEXT_EMBEDDING_MODEL_NAME` | legacy：Ollama 嵌入模型 | `qwen3-embedding:0.6b` |
| `ALIYUN_EMBED_MODEL_NAME` | legacy：百炼嵌入模型（走兼容模式） | `text-embedding-v3`（见 §3.4 风险说明） |
| `EMBED_MODEL_TYPE` 未设置时的旧值 `EMBED_MODEL_TYPE=OLLAMA` | 保持默认 | — |

### 3.2 聊天模型：统一 `ChatOpenAI`

**抽取公共实现（DRY，两处共用）：**

`backend/app/utils/factory.py` 新增：

```python
def create_chat_openai(model: str, api_key: str | None, base_url: str | None,
                       streaming: bool = True, top_p: float = 0.7) -> ChatOpenAI:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        streaming=streaming,
        top_p=top_p,
    )

def resolve_chat_config(custom_model: str | None = None) -> dict:
    """按 LLM_TYPE 解析出 (model, api_key, base_url)。agent.py 与 factory 共用。"""
    llm_type = os.getenv("LLM_TYPE", "ALIYUN").upper()
    if llm_type in ("OPENAI", "OPENAI_COMPAT"):
        return {
            "model": custom_model or os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
            "api_key": os.getenv("OPENAI_API_KEY"),
            "base_url": os.getenv("OPENAI_BASE_URL"),
        }
    if llm_type == "OLLAMA":
        return {
            "model": custom_model or os.getenv("OLLAMA_MODEL_NAME", "qwen3:7b"),
            "api_key": None,
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        }
    # ALIYUN（默认，legacy）：ALIYUN_* 本身就是 OpenAI 兼容(compatible-mode)配置
    return {
        "model": custom_model
                 or os.getenv("ALIYUN_MODEL_NAME")
                 or os.getenv("CHAT_MODEL_NAME", "qwen3-max"),
        "api_key": os.getenv("ALIYUN_ACCESS_KEY_SECRET"),
        "base_url": os.getenv("ALIYUN_BASE_URL"),
    }
```

改动点：
- `factory.py::ChatModelFactory.generator()`：改为
  ```python
  def generator(self):
      llm_type = os.getenv("LLM_TYPE", "ALIYUN").upper()
      if llm_type == "OLLAMA":
          return ChatOllama(
              model=os.getenv("OLLAMA_MODEL_NAME", "qwen3:7b"),
              base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
              streaming=True, top_p=0.7,
          )
      cfg = resolve_chat_config()          # ALIYUN / OPENAI / OPENAI_COMPAT
      logger.info(f"📦 ChatModel 使用 OpenAI 兼容模型: {cfg['model']}")
      return create_chat_openai(**cfg)
  ```
  即：ALIYUN / OPENAI_COMPAT 分支返回 `ChatOpenAI`；OLLAMA 分支保留 `ChatOllama`（本地原生实现更稳，且 `LLM_TYPE=OPENAI_COMPAT` + `OPENAI_BASE_URL=http://localhost:11434/v1` 也可走统一路径，二选一皆可）。
- `agent.py::_create_chat_model`：**改为调用同一套 `resolve_chat_config` + `create_chat_openai`**（OLLAMA 分支保留 `ChatOllama`）；删除 `_NormalizedTongyi` 类与 `ChatTongyi` 导入。
- 保持 `stream_runnable=False`（规避 planning 阶段流式 tool_call 聚合差异，对 OpenAI 兼容服务同样稳妥）。
- 环境变量读取统一后，**修复现状不一致**：`agent.py` 原先只读 `ALIYUN_MODEL_NAME`（忽略 `CHAT_MODEL_NAME`），统一用 `resolve_chat_config` 后两处一致。

> 注：`ChatOpenAI` 基于 OpenAI SDK，流式 `delta.arguments` 为标准增量拼接，无 DashScope SDK 的减法缺陷，`_NormalizedTongyi` 可安全删除；capitalized 兼容模式对 Qwen3 工具调用支持良好。

### 3.3 视觉模型：可选模块（`VISION_ENABLED`）

**`factory.py::VisionModelFactory.generator()` 三态逻辑：**

```python
VISION_ENABLED = os.getenv("VISION_ENABLED")
if VISION_ENABLED is not None and VISION_ENABLED.lower() == "false":
    logger.info("🎨 视觉模型未启用（VISION_ENABLED=false），PDF 走纯文本")
    return None

if VISION_ENABLED is not None and VISION_ENABLED.lower() == "true":
    vtype = os.getenv("VISION_MODEL_TYPE", "").upper()
    if vtype == "OLLAMA":
        return ChatOllama(model=os.getenv("VISION_OLLAMA_MODEL_NAME", "qwen-vl:7b"),
                          base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                          streaming=False, top_p=0.7)
    # OPENAI_COMPAT（含 legacy ALIYUN_* 回退）
    base_url = os.getenv("VISION_BASE_URL") or os.getenv("ALIYUN_BASE_URL")
    api_key = os.getenv("VISION_API_KEY") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
    model = (os.getenv("VISION_MODEL_NAME") or os.getenv("VISION_CHAT_MODEL_NAME")
             or os.getenv("CHAT_MODEL_NAME") or "qwen-vl-max")
    if not (base_url and api_key):
        logger.warning("🎨 VISION_ENABLED=true 但缺少 VISION_BASE_URL/VISION_API_KEY，视觉已关闭（降级纯文本）")
        return None
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                      streaming=False, top_p=0.7)

# 未设置：保持旧行为（跟随 LLM_TYPE / VISION_MODEL_TYPE）
vtype = os.getenv("VISION_MODEL_TYPE", "").upper() or os.getenv("LLM_TYPE", "ALIYUN").upper()
if vtype in ("OPENAI", "OPENAI_COMPAT", "ALIYUN"):
    base_url = os.getenv("VISION_BASE_URL") or os.getenv("ALIYUN_BASE_URL")
    api_key = os.getenv("VISION_API_KEY") or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
    model = (os.getenv("VISION_MODEL_NAME") or os.getenv("VISION_CHAT_MODEL_NAME")
             or os.getenv("CHAT_MODEL_NAME") or "qwen-vl-max")
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                      streaming=False, top_p=0.7)
# OLLAMA
return ChatOllama(model=os.getenv("VISION_OLLAMA_MODEL_NAME") or os.getenv("OLLAMA_MODEL_NAME") or "qwen-vl:7b",
                  base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                  streaming=False, top_p=0.7)
```

**`vision_service.py` 统一多模态路径（删除协议分支）：**
- 删除 `_is_ollama()`、`_dashscope_describe`、`_dashscope_describe_batch` 及全部 `import dashscope`；
- 单图/批量统一走现有 LangChain 路径（`_build_message_from_b64` / `_build_batch_message_from_b64` 生成的 `HumanMessage` 已含 `{"type": "image_url", "image_url": {"url": "data:..."}}`）：
  ```python
  response = await self._get_model().ainvoke([message])   # 异步版
  response = self._get_model().invoke([message])          # 同步版
  ```
- **失败兜底保留**：调用异常时返回 `existing_text` / `""`（供应商不支持视觉时自动降级为纯文本，不中断入库）。

**`pdf_multimodal_loader.py`（异步 + 同步两版）提前跳过：**
- 文件开头读取 `vision_enabled = os.getenv("VISION_ENABLED")`，并在加载函数入口判断：
  若视觉关闭（`VISION_ENABLED==false`，或 `init_manager.vision_model is None` 且 `VISION_ENABLED==true` 但配置缺失）→ **直接跳过页面渲染与临时 PNG 生成**，所有页面按纯文本构造 `Document` 返回；
- 现有"异常 → 返回已有文本"兜底保留，双保险。

### 3.4 嵌入模型：新增 `OPENAI_COMPAT` 分支

`factory.py::EmbedModelFactory.generator()`：

```python
EMBED_MODEL_TYPE = os.getenv("EMBED_MODEL_TYPE", "OLLAMA").upper()
if EMBED_MODEL_TYPE == "OLLAMA":
    return OllamaEmbeddings(
        model=os.getenv("TEXT_EMBEDDING_MODEL_NAME", "qwen3-embedding:0.6b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
# OPENAI_COMPAT 或 legacy ALIYUN（兼容模式 /v1/embeddings）
if EMBED_MODEL_TYPE in ("OPENAI_COMPAT", "OPENAI", "ALIYUN"):
    from langchain_openai import OpenAIEmbeddings
    base_url = (os.getenv("EMBED_BASE_URL") if EMBED_MODEL_TYPE != "ALIYUN"
                else os.getenv("ALIYUN_BASE_URL"))
    api_key = (os.getenv("EMBED_API_KEY") if EMBED_MODEL_TYPE != "ALIYUN"
               else os.getenv("ALIYUN_ACCESS_KEY_SECRET"))
    model = (os.getenv("EMBED_MODEL_NAME") if EMBED_MODEL_TYPE != "ALIYUN"
             else os.getenv("ALIYUN_EMBED_MODEL_NAME", "text-embedding-v3"))
    return OpenAIEmbeddings(model=model, api_key=api_key, base_url=base_url)
```

- 删除 `DashScopeEmbeddingsWrapper` 类与 `import dashscope`。
- **风险说明**：百炼兼容模式 `/v1/embeddings` 的模型名与 DashScope 原生 `TextEmbedding` 的模型名可能有差异（`qwen3-embedding` 若不在兼容模式支持列表，改用 `text-embedding-v3` 即可，**无需改代码**，只改 `.env`；实施时以实测为准并更新 `.env.example` 注释）。

### 3.5 依赖变更（`backend/pyproject.toml`）

- 新增：`langchain-openai`（锁 `1.1.x`，与已锁定的 `langchain-core==1.2.x` / `langchain-classic==1.0.x` 匹配）。
- 移除（本期改造完成后无任何引用）：`dashscope==1.25.14`、`langchain-dashscope==0.1.8`。
- 保留：`langchain-ollama`（OLLAMA 分支）、`langchain-community`（其余组件仍用）。
- 操作：`uv add langchain-openai` → 实施完成后 `uv remove dashscope langchain-dashscope` → `uv lock` / `uv sync` 并**同步重新导出 `requirements.txt`**（现文件与锁文件存在漂移，一并修正）。
- 本期**不新增** `duckduckgo-search` / `tavily-python`（属第二期 web 搜索工具）。

### 3.6 环境变量样例（`backend/.env.example`）

```dotenv
# ==============================================
# LLM 大模型配置（统一 OpenAI 兼容协议）
# LLM_TYPE：ALIYUN(默认,legacy,走百炼兼容模式) | OPENAI_COMPAT(任意兼容服务) | OLLAMA(本地)
# ==============================================
LLM_TYPE=ALIYUN

# --- OPENAI 兼容服务（LLM_TYPE=OPENAI_COMPAT 时生效）---
# OPENAI_BASE_URL=https://api.deepseek.com/v1
# OPENAI_API_KEY=sk-xxx
# OPENAI_MODEL_NAME=deepseek-chat

# --- 百炼（LLM_TYPE=ALIYUN 时生效，base_url 本身就是 OpenAI 兼容地址）---
ALIYUN_ACCESS_KEY_SECRET=your_api_key
ALIYUN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_MODEL_NAME=qwen3-max

# --- Ollama（LLM_TYPE=OLLAMA 时生效）---
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_NAME=qwen3.5:0.8b

# ==============================================
# 视觉模型（可选模块！默认关闭，不启用则以下整段可不配置）
# VISION_ENABLED：false=彻底关闭(PDF走纯文本) | true=启用(需配 VISION_MODEL_TYPE)
# 未设置=跟随 LLM_TYPE 的旧行为
# ==============================================
VISION_ENABLED=false
VISION_MODEL_TYPE=OPENAI_COMPAT
# VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# VISION_API_KEY=sk-xxx
# VISION_MODEL_NAME=qwen-vl-max
# VISION_OLLAMA_MODEL_NAME=qwen-vl:7b   # VISION_MODEL_TYPE=OLLAMA 时用

# ==============================================
# 向量嵌入模型（EMBED_MODEL_TYPE：OLLAMA(默认) | OPENAI_COMPAT | ALIYUN(legacy)）
# ==============================================
EMBED_MODEL_TYPE=OLLAMA
TEXT_EMBEDDING_MODEL_NAME=qwen3-embedding:0.6b
# EMBED_BASE_URL=https://api.openai.com/v1
# EMBED_API_KEY=sk-xxx
# EMBED_MODEL_NAME=text-embedding-3-small
# ALIYUN_EMBED_MODEL_NAME=text-embedding-v3  # EMBED_MODEL_TYPE=ALIYUN 时
```

（数据库 / Redis / JWT / 重排序模型等其余配置原样保留，不在本期改动。）

---

## 4. 涉及文件清单

| 文件 | 变更类型 | 说明 |
| --- | --- | --- |
| `backend/app/utils/factory.py` | 修改 | 新增 `create_chat_openai` / `resolve_chat_config`；`ChatModelFactory` 三分支统一 ChatOpenAI；`VisionModelFactory` 加 `VISION_ENABLED` 三态；`EmbedModelFactory` 加 OPENAI_COMPAT；删除 `ChatTongyi` / `DashScopeEmbeddingsWrapper` 导入 |
| `backend/app/agent/agent.py` | 修改 | 删除 `_NormalizedTongyi` / `ChatTongyi`；`_create_chat_model` 复用 `resolve_chat_config` + `create_chat_openai`；保留 `stream_runnable=False`；默认工具仍 8 个 |
| `backend/app/utils/vision_service.py` | 修改 | 删除 `_is_ollama` / `_dashscope_describe*` / `import dashscope`；统一 LangChain 多模态 invoke；保留失败兜底 |
| `backend/app/utils/pdf_multimodal_loader.py` | 修改 | 异步 + 同步两版：视觉关闭时跳过渲染与临时文件，直接纯文本入库 |
| `backend/pyproject.toml` | 修改 | `uv add langchain-openai`；`uv remove dashscope langchain-dashscope`；同步 `uv.lock` / `requirements.txt` |
| `backend/.env.example` | 修改 | 新增 OPENAI_COMPAT / VISION_ENABLED / EMBED 配置样例与注释 |

**不动**：`rag_service.py`、`vector_store.py`、`reorder_service.py`、`router/chat.py`（前置 RAG 管线本期保持原样）、`main_prompt.txt`、`agent_tools.py`（不加新工具）、`background_init.py`（`generator()` 返回 None 时天然兼容，仅日志提示）。

---

## 5. 实施步骤

> 所有命令在 `backend/` 目录下执行；步骤 2-6 各自完成后跑一次对应验证。

1. **锁依赖**
   - [ ] `uv add langchain-openai`（确认解析到 1.1.x，与 langchain-core 1.2.x 兼容）
   - 验证：`uv run python -c "import langchain_openai; print(langchain_openai.__version__)"`

2. **改 `factory.py`（聊天 + 嵌入）**
   - [ ] 新增 `create_chat_openai` / `resolve_chat_config`（§3.2 代码）
   - [ ] `ChatModelFactory.generator()` 走统一解析（ALIYUN / OPENAI / OPENAI_COMPAT → ChatOpenAI；OLLAMA → ChatOllama）
   - [ ] `EmbedModelFactory.generator()` 新增 OPENAI_COMPAT 分支，删除 `DashScopeEmbeddingsWrapper`
   - 验证：`uv run python -c "from app.utils.factory import ChatModelFactory, EmbedModelFactory; print(ChatModelFactory().generator())"`（ALIYUN 时应打印 ChatOpenAI 实例且无异常）

3. **改 `agent.py`（删 hack + 统一模型）**
   - [ ] 删除 `_NormalizedTongyi` 类与 `ChatTongyi` 导入
   - [ ] `_create_chat_model` 改为调用 `resolve_chat_config` + `create_chat_openai`（OLLAMA 保留 ChatOllama）
   - [ ] 确认默认工具列表仍为 8 个
   - 验证：`uv run python -c "from app.agent.agent import agent_factory; print(len(agent_factory._get_default_tools()))"` 输出 8；`grep -rn "ChatTongyi\|_NormalizedTongyi" app/` 无结果

4. **改 `vision_service.py` 统一多模态路径**
   - [ ] 删除 `_is_ollama` / `_dashscope_describe` / `_dashscope_describe_batch` / `import dashscope`
   - [ ] `describe_page` / `describe_page_sync` / `describe_pages_batch` / `describe_pages_batch_sync` 全部改为直接 `model.ainvoke/invoke([message])`，保留异常兜底（返回 existing_text / ""）
   - 验证：`uv run python -c "from app.utils.vision_service import VisionService; print('import ok')"`；`grep -rn "dashscope" app/` 仅剩 `factory.py`（待步骤 5 后清零）

5. **改 `factory.py`（视觉三态）**
   - [ ] `VisionModelFactory.generator()` 按 §3.3 实现 `VISION_ENABLED` 三态；删除 `ChatTongyi` 导入
   - 验证：`VISION_ENABLED=false` 时 `VisionModelFactory().generator()` 返回 None，无任何 `VISION_*` 配置也不报错

6. **改 `pdf_multimodal_loader.py`（跳过渲染）**
   - [ ] 异步 `pdf_multimodal_loader` 与同步 `pdf_multimodal_loader_sync` 入口：视觉关闭（`VISION_ENABLED=false`，或启用但模型为 None）时跳过页面渲染/临时文件，纯文本构造 Document
   - 验证：`VISION_ENABLED=false` 下对含图 PDF 跑一遍导入流程，日志显示"全部纯文本"，无 PNG 渲染日志

7. **移除 DashScope 依赖并同步锁文件**
   - [ ] `uv remove dashscope langchain-dashscope`；`uv lock` / `uv sync`；重新导出 `requirements.txt`
   - [ ] 全仓库扫描：`grep -rn "dashscope" app/ pyproject.toml requirements.txt` 无结果（`langchain-community` 内部引用除外，仅确认本仓库无直接引用）
   - 验证：`uv run python -c "import app.main"` 启动期导入通过

8. **更新 `.env.example`**：按 §3.6 写入，保留旧变量注释说明

9. **端到端冒烟（回归）**
   - [ ] `LLM_TYPE=ALIYUN`（旧 `.env` 不动）→ 启动应用 → `POST /chat/agent/query/stream`：流式 + 工具调用正常，SSE `thinking` 事件正常
   - [ ] `LLM_TYPE=OPENAI_COMPAT` + `OPENAI_*` 指向任意兼容服务 → 重复上一步
   - [ ] `POST /chat/rag/query`（RAG 摘要）正常（依赖 `init_manager.chat_model`，验证 ChatOpenAI 兼容）
   - [ ] 知识库上传 PDF：`VISION_ENABLED=false` 纯文本入库；`VISION_ENABLED=true` + 百炼 qwen-vl 时含图页出现"[页面视觉描述]"内容
   - [ ] 笔记自动打标签 / 每日回顾等依赖 `init_manager.chat_model` 的功能抽样回归（note_service / review_service）

---

## 6. 风险与回退

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| ALIYUN ChatTongyi → ChatOpenAI 行为差异 | 流式 tool_calls、thinking 参数等语义差异 | 保留 `stream_runnable=False`；冒烟重点验证 Qwen3 工具调用；必要时按服务加轻量适配（不再用 SDK 子类 hack） |
| 兼容模式嵌入模型名差异 | `qwen3-embedding` 可能不在 `/v1/embeddings` 支持列表 | 属纯配置问题，改 `.env`（如 `text-embedding-v3`）即可，代码不动；步骤 9 实测确认后更新 `.env.example` 注释 |
| 供应商不支持视觉/嵌入 | DeepSeek、Moonshot 等无视觉 API | 视觉已做成可选项（`VISION_ENABLED=false` 零配置）；嵌入保留 `EMBED_MODEL_TYPE=OLLAMA` 或换兼容供应商 |
| DashScope 依赖移除遗漏 | 某处隐式引用导致启动失败 | 步骤 7 全量 grep + `import app.main` 验证后才删除；失败则恢复依赖并排查 |
| 锁文件漂移 | pyproject 与 uv.lock 版本不一致 | `uv lock` 重新解析，requirements.txt 重新导出并验 diff |
| 视觉模型切换后请求格式不兼容 | 某兼容供应商对 `image_url` 实现不标准 | 失败自动降级纯文本（现有兜底）；多模态统一路径代码与供应商无关 |

---

## 7. 扩展点（本期不做，记录备查）

- **Anthropic 协议**：如需直连 Claude，用 `langchain-anthropic.ChatAnthropic`（同为 BaseChatModel，可无感接入 `create_tool_calling_agent`）。但 Anthropic 消息/图片块格式（`{"type":"image","source":{...}}`）与 OpenAI 不同，`vision_service` 需再分支一次；本期以 OpenAI 兼容为主协议，Anthropic 作为下下期候选。
- **Ollama 统一路径**：`ChatOpenAI` + `base_url=http://localhost:11434/v1` 亦可替代 `ChatOllama`（可选，不强制）。

---

## 8. 第二期预告：Agentic RAG 工具链改造（不在本期）

延后内容（原稿相关章节整体顺延）：RAG 拆分为 `rag_rewrite_tool` / `rag_retrieve_tool` / `rag_rerank_tool` / `web_search_tool` 四个工具挂载到 Agent（默认工具 8→12）、`Agentic_RAG` 开关（true 时跳过路由层前置注入）、`main_prompt.txt` 补充编排规则、提升 `max_iterations` 上限、新增 `duckduckgo-search`(注:已改名 `ddgs`) / `tavily-python` 依赖。第二期实施时以本期的"统一 OpenAI 兼容协议"为基础，另立新计划文档。

---

## 9. 验收标准（本期）

- [ ] `LLM_TYPE=OPENAI_COMPAT` + `OPENAI_*` 指向任意 OpenAI 兼容服务即可创建 Agent，**全程不出现 DashScope 相关代码路径**
- [ ] `LLM_TYPE=ALIYUN` 旧 `.env` 零改动可用（compatible-mode + ChatOpenAI）
- [ ] `VISION_ENABLED=false` 时：无需任何视觉配置，PDF 走纯文本且**不渲染页面图片、不写临时 PNG**
- [ ] `VISION_ENABLED=true` + `VISION_MODEL_TYPE=OPENAI_COMPAT` + 百炼 qwen-vl：PDF 含图页输出"[页面视觉描述]"
- [ ] `EMBED_MODEL_TYPE=OPENAI_COMPAT`（或 ALIYUN）走 `/v1/embeddings` 建向量成功，RAG 检索可用
- [ ] `uv run python -c "from app.agent.agent import agent_factory"` 通过；`grep -rn "ChatTongyi\|_NormalizedTongyi\|_dashscope_describe" app/` 无残留
- [ ] `dashscope` / `langchain-dashscope` 已从 pyproject / uv.lock / requirements.txt 移除且应用启动正常
- [ ] 默认工具仍为 8 个；`/chat/agent/query/stream` 与 `/chat/rag/query` 冒烟通过
- [ ] 前端 SSE 事件格式无变化（`thinking` / `response` / `done` 事件类型不变）