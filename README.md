# RAG NoteBook— 智能笔记助手

<div align="center">
<a href="https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service/stargazers">
  <img src="https://img.shields.io/github/stars/RMA-MUN/LangChain-RAG-FastAPI-Service?style=flat-square&label=Stars&color=orange" alt="Stars">
</a>
<a href="https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service/network/members">
  <img src="https://img.shields.io/github/forks/RMA-MUN/LangChain-RAG-FastAPI-Service?style=flat-square&label=Forks&color=green" alt="Forks">
</a>
  <img src="https://img.shields.io/badge/python-v3.12.4-blue.svg" alt="Python">
</div>


AI 驱动的个人知识管理工具，融合 **笔记管理 + RAG 知识库 + AI 写作辅助**，解决"笔记写了从不回看、知识散落成孤岛"的问题。

---

## 项目变迁

本项目最初是一个**基础 RAG 对话系统**，我们做了一次重要转型，从基础的 RAG，转型为解决实际问题的 RAG NoteBook：

| | 阶段一（base-rag 分支） | 阶段二（master 分支） |
|--|-----------------------|:--------------------|
| **定位** | 纯 RAG 对话服务，开箱即用 | 智能笔记助手，以 RAG 为核心的 NoteBook 工具 |
| **能力** | 文档上传 → 向量检索 → AI 问答 | 笔记管理 + RAG + 间隔重复 + AI 写作 |
| **适合谁** | 想快速集成 RAG 能力的开发者或希望学习RAG技术的个人 | 需要AI管理笔记和知识库的个人以及简历需要RAG项目的求职者 |

**RAG 始终是整个系统的核心引擎。** 基础 RAG 代码已永久保留在 `base-rag` 分支供学习使用，如果只需要纯 RAG 服务，切换到`base-rag`即可开箱使用。

> 📄 [查看完整项目变迁 →](./docs/project_develop.md)

## 📋 目录

- [项目简介](#项目简介)
- [项目变迁](#项目变迁)
- [核心特性](#核心特性)
- [项目架构](#项目架构)
- [项目演示](#项目演示)
- [快速开始](#快速开始)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [API 文档](#api文档)
- [配置说明](#配置说明)
- [部署指南](#部署指南)
- [开发指南](#开发指南)
- [故障排除](#故障排除)
- [联系方式](#联系方式)

## 项目简介

基于 **FastAPI + LangChain** 构建的智能笔记助手，核心能力包括：

- **笔记管理**：Markdown 编辑器、智能标签（LLM 自动分类）、语义搜索、Markdown 导出
- **RAG 知识库**：多格式文档上传（txt/pdf/md/pptx/docx），基于向量检索的精准问答
- **间隔重复回顾**：艾宾浩斯遗忘曲线算法，对抗遗忘
- **AI 写作辅助**：联机补全、续写/扩写/摘要、关联笔记推荐

系统支持会话持久化（MySQL）、向量检索（ChromaDB）、JWT 用户隔离，前端采用React+Tailwind CSS构建现代化界面。

## 核心特性

- **📝 笔记管理**：Markdown 编辑器，支持新建、编辑、删除、分类筛选、分页列表
- **🏷️ 智能标签**：保存笔记后 LLM 异步生成标签和分类（工作/学习/生活/项目），无需手动归类
- **🔍 语义搜索**：基于向量嵌入的笔记全文搜索，告别关键词匹配
- **🔄 间隔重复回顾**：艾宾浩斯遗忘曲线（1/2/4/7/15/30 天）
- **✍️ AI 联机补全**：打字停顿后模型实时补全，Tab 键快速采纳
- **🤖 AI 写作助手**：续写、扩写、摘要生成，SSE 流式输出
- **🔗 跨源关联推荐**：编辑笔记时，从笔记库和知识库双向检索 Top k 相关文档
- **💬 智能问答**：基于 RAG 技术的 Agent 对话，支持文档引用来源展示
- **💾 会话持久化**：MySQL 存储对话历史，随时回溯
- **📄 文档管理**：支持 TXT / PDF / MD / PPTX / DOCX 上传，可视化切片详情
- **🌐 多语言支持**：前端 i18n，中英文界面切换
- **⛑️ 安全隔离**：用户级知识库隔离，RAG 检索只能访问本人数据

## 项目演示

| 功能模块 | 界面展示 |
|---------|:--------|
| 笔记编辑 | ![笔记编辑](./images/editor_note.png) |
| 笔记列表 | ![笔记列表](./images/note.png) |
| AI 聊天 | ![AI 聊天](./images/aichat.png) |
| 知识库 | ![知识库](./images/knowledge_manager.png) |

## 快速开始

### 环境要求

| 环境 | 版本推荐 |
|------|----------|
| Python | 3.12+ |
| uv | 0.11.9 |
| Node.js | 16+ |

### 克隆项目

```bash
git clone https://github.com/RMA-MUN/LangChain-RAG-FastAPI-Service.git
cd LangChain-RAG-FastAPI-Service
```

### 安装依赖

#### 后端依赖
```bash
cd backend
uv sync
```

#### 前端依赖
```bash
cd front
npm install
```

### 环境配置

#### 创建后端环境变量文件

在 `backend` 目录下创建 `.env` 文件，参考 `.env.example` 文件填写配置：

```env
# ==================== LLM 大模型配置 ====================
# LLM类型：ALIYUN | OLLAMA
LLM_TYPE=ALIYUN

# ==================== Ollama 配置 (LLM_TYPE=OLLAMA) ====================
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_NAME=qwen3.5:0.8b

# ==================== 阿里云百炼配置 (LLM_TYPE=ALIYUN) ====================
ALIYUN_ACCESS_KEY=your_api_key
ALIYUN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_MODEL_NAME=qwen3-max

# ==================== 向量嵌入模型配置 ====================
EMBED_MODEL_TYPE=OLLAMA
TEXT_EMBEDDING_MODEL_NAME=qwen3-embedding:0.6b
ALIYUN_EMBED_MODEL_NAME=qwen3-embedding

# ==================== 数据库配置 ====================
MYSQL_USER=root
MYSQL_PASSWORD=root
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=chat_history

REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# ==================== 重排序模型配置 ====================
RERANKER_MODEL_PATH=D:\Hugging_Face\models\bge-reranker-v2-m3

# ==================== JWT 身份验证配置 ====================
SECRET_KEY=MY_JWT_SECRET_KEY
ALGORITHM=HS256
```



### 向量数据库配置

修改 `backend/app/config/chroma.yaml` 文件：

```yaml
collection_name: rag_collection
persist_directory: data/chromadb
k: 3

data_path: data
md5_hex_store: data/md5_hex_store/md5_hex_store.txt
allow_knowledge_file_types: ["txt", "pdf", "md", "pptx", "docx"]

chunk_size: 200
chunk_overlap: 20
separators: ["\n\n", "\n", "。", "！", "？", "!", "?", " ", ""]
```

### 启动服务

| 服务 | 命令 | 端口 |
|------|------|------|
| 后端服务 | `cd backend && uvicorn main:app --reload` | 8000 |
| 前端服务 | `cd front && npm run dev` | 3000 |

| MySQL | `net start mysql` | 3306 |
| Redis | `redis-server` 或 `net start redis` | 6379 |
| Ollama | `ollama serve` | 11434 |

## 技术栈

### 后端技术

| 技术 | 说明 |
|------|------|
| FastAPI | 高性能异步 Web 框架 |
| LangChain | 大语言模型应用开发框架（AgentExecutor + Tools） |
| ChromaDB | 轻量级向量数据库（rag_collection + notes_collection） |
| SQLAlchemy | 异步 ORM，管理 MySQL |

| MySQL | 关系型数据库（chat_history / notes / reviews） |
| Redis | 缓存 |
| DashScope API | 大语言模型服务（Qwen3-Max） |
| Ollama | 本地模型部署（qwen3.5:0.8b 联机补全） |
| Hugging Face / ModelScope | 重排序模型（BAAI/bge-reranker-v2-m3） |
| Sentence-Transformers | 句子嵌入模型 |

### 前端技术

| 技术 | 说明 |
|------|------|
| React 19 | 现代化前端框架 |
| TypeScript | 类型安全 |
| Vite | 极速构建工具 |
| Tailwind CSS | 原子化 CSS 框架 |
| Radix UI | 无头 UI 组件库 |
| Tiptap | 富文本 Markdown 编辑器 |
| React Router DOM | 路由管理（路由守卫 + JWT 校验） |
| Zustand | 轻量状态管理 |
| i18next | 国际化（中/英） |
| Axios | HTTP 客户端 |
| react-markdown + rehype-highlight | Markdown 渲染与代码高亮 |
| dompurify | HTML 安全过滤 |

## 项目结构

```
├── backend/                     # FastAPI 后端服务
│   ├── app/
│   │   ├── agent/               # Agent 智能代理模块
│   │   │   └── agent.py         # AgentFactory + Tool 定义
│   │   ├── config/              # 配置文件（chroma.yaml 等）
│   │   ├── core/                # 核心工具（限流、响应封装、日志）
│   │   ├── db/                  # 数据库配置（MySQL + Redis）
│   │   ├── model/               # SQLAlchemy ORM 模型
│   │   │   ├── note.py          # 笔记模型
│   │   │   ├── review_record.py # 回顾记录模型
│   │   │   └── chat_history.py  # 对话历史模型
│   │   ├── prompt/              # 提示词模板（8个）
│   │   ├── rag/                 # RAG 核心功能
│   │   │   ├── rag_service.py   # RAG 服务（HyDE + 混合检索）
│   │   │   ├── reorder_service.py
│   │   │   ├── vector_store.py  # ChromaDB 封装
│   │   │   ├── text_spliter.py  # 文档切片
│   │   │   ├── document_handler/# 文档解析（txt/pdf/md/pptx/docx）
│   │   │   ├── retrievers/      # 自定义检索器
│   │   │   └── task_queue.py    # 后台处理队列
│   │   ├── router/              # API 路由
│   │   │   ├── chat.py          # 聊天 & Agent 路由
│   │   │   ├── note_router.py   # 笔记 CRUD & AI 路由
│   │   │   ├── review_router.py # 间隔重复回顾路由
│   │   │   ├── knowledge_router.py
│   │   │   ├── user.py
│   │   │   └── health.py
│   │   ├── schemas/             # Pydantic 数据模型
│   │   ├── services/            # 业务服务层
│   │   │   ├── note_service.py  # 笔记服务（CRUD + 向量化 + AI 写作）
│   │   │   └── review_service.py# 回顾服务（艾宾浩斯算法）
│   │   └── utils/               # 工具函数
│   ├── data/                    # 数据存储目录
│   ├── main.py                  # 应用入口
│   └── pyproject.toml
├── front/                       # React 前端项目
│   ├── src/
│   │   ├── api/                 # API 请求层
│   │   │   ├── auth.ts          # 认证接口
│   │   │   ├── chat.ts          # 聊天接口
│   │   │   ├── notes.ts         # 笔记接口
│   │   │   ├── knowledge.ts     # 知识库接口
│   │   │   ├── review.ts        # 回顾接口
│   │   │   └── sessions.ts      # 会话接口
│   │   ├── components/          # 组件
│   │   │   ├── common/          # 通用组件（TagBadge, ConfirmDialog, EmptyState 等）
│   │   │   ├── knowledge/       # 知识库组件
│   │   │   ├── layout/          # 布局组件（Sidebar）
│   │   │   ├── note/            # 笔记组件（OutlinePanel, RelatedFragments）
│   │   │   └── TiptapEditor.tsx # 富文本编辑器
│   │   ├── hooks/               # 自定义 Hooks
│   │   │   └── useSSE.ts        # SSE 流式处理
│   │   ├── i18n/                # 国际化（中/英）
│   │   ├── layouts/             # 页面布局（AuthLayout, MainLayout）
│   │   ├── pages/               # 页面
│   │   │   ├── NoteEditor.tsx   # 笔记编辑器
│   │   │   ├── NoteList.tsx     # 笔记列表
│   │   │   ├── DailyReview.tsx  # 每日回顾
│   │   │   ├── AIChat.tsx       # AI 聊天
│   │   │   ├── Sessions.tsx     # 会话管理
│   │   │   ├── KnowledgeBase.tsx# 知识库管理
│   │   │   ├── Login.tsx / Register.tsx
│   │   │   ├── Profile.tsx / Settings.tsx
│   │   │   └── AboutUs.tsx
│   │   ├── router/index.tsx     # 路由配置
│   │   ├── stores/              # Zustand 状态管理
│   │   │   ├── useUserStore.ts
│   │   │   ├── useSessionStore.ts
│   │   │   ├── useThemeStore.ts
│   │   │   └── useLanguageStore.ts
│   │   ├── types/api.ts         # TypeScript 类型定义
│   │   ├── App.tsx              # 应用入口组件
│   │   └── main.tsx             # 应用入口
│   └── package.json
├── docs/                        # 项目文档
│   ├── modelscope_model.md     # 模型下载和配置
│   └── troubleshooting.md      # 故障排除
├── images/                      # 截图资源
└── plan.md                     # 项目规划
```

## API 文档

### FastAPI 后端 API

完整的 OpenAPI 规范文件：[backend/openapi.json](./backend/openapi.json)
		启动服务后访问交互式文档：[http://localhost:8000/docs](http://localhost:8000/docs)

## 配置说明

### LLM 模型切换

系统支持 **阿里云百炼（DashScope）** 和 **Ollama（本地部署）**两种模式：

- **LLM_TYPE=ALIYUN**：使用 Qwen3-Max 大模型 + text-embedding-v4 嵌入
- **LLM_TYPE=OLLAMA**：使用本地 Ollama 模型

### 重排序模型

下载 BAAI/bge-reranker-v2-m3 模型并配置 `RERANKER_MODEL_PATH` 路径，参考 [模型配置指南](./docs/modelscope_model.md)。

## 故障排除

详细的故障排除指南请参考：[故障排除](./docs/troubleshooting.md)

常见问题：

- **API Key 错误**：检查 ALIYUN_ACCESS_KEY 是否正确配置
- **数据库连接失败**：确认 MySQL / Redis 服务已启动
- **ChromaDB 异常**：检查 `chroma.yaml` 中的路径配置
- **重排序模型加载失败**：确认 `RERANKER_MODEL_PATH` 指向正确的模型路径
- **Ollama 连接失败**：确认 `ollama serve` 已运行且模型已拉取

## 联系方式

如有任何问题或建议，欢迎提交 GitHub Issues 或联系作者：

- Email: n3032747608@163.com
- QQ: 3032747608

## Star History


 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=RMA-MUN/LangChain-RAG-FastAPI-Service&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=RMA-MUN/LangChain-RAG-FastAPI-Service&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=RMA-MUN/LangChain-RAG-FastAPI-Service&type=date&legend=top-left" />
 </picture>


## License

本项目基于MIT开源协议， [点击跳转LICENSE](LICENSE) 
