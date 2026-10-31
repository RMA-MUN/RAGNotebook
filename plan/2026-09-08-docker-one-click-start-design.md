# Docker 一键启动设计（2026-09-08）

## 背景与目标

RAG NoteBook 当前部署门槛高：需要用户自装 Python 3.12 + uv、Node 16+、MySQL、Redis、Neo4j，再手工启动两个服务。目标：提供 **Docker 一键启动**，让使用者 `start.bat`（或 `docker compose up`）即可获得完整可用的系统（前端 + 后端 + MySQL + Redis + Neo4j）。

用户已确认的决策：

1. **全部容器化**（含 MySQL / Redis / Neo4j），数据卷持久化。
2. LLM 密钥仍通过 `backend/.env` 提供（不进镜像），`docker compose` 用 `env_file` 加载；**服务发现类配置（HOST/URI）由 compose `environment` 强制覆盖为容器服务名**，解决 `.env` 默认 `localhost` 与容器不兼容的问题。
3. 前端采用 **Nginx 托管静态文件 + 反向代理后端** 的形态；对外暴露 **3000**（前端）与 **8000**（后端调试 /docs）。
4. Neo4j 使用 `5.26-community`（满足 `ensure_graph_schema` 对 `CREATE VECTOR INDEX` 的最低版本要求 5.11+）；不使用 APOC。
5. 根目录附带 `start.bat` 一键脚本。

## 架构

```
用户浏览器
   │  http://localhost:3000
   ▼
┌─────────────────────┐        ┌──────────────────────────────┐
│ frontend (nginx)    │───────▶│ backend (uvicorn :8000)      │
│  静态 dist + 15 条   │ proxy  │  main.py 启动链路：           │
│  反代规则照抄 vite   │        │  init_db → seed → redis →    │
└─────────────────────┘        │  graph schema                │
                               └──────┬──────────┬────────────┘
                                      │          │
                     ┌────────────────▼──┐  ┌────▼─────┐  ┌──────────┐
                     │ mysql:8.0 (3306)  │  │ redis:7  │  │ neo4j:5  │
                     └───────────────────┘  └──────────┘  └──────────┘
```

服务间仅通过 compose 内部网络通信；`depends_on` + healthcheck 保证启动顺序。

## 组件设计

### 1. 根目录 `docker-compose.yml`

| 服务 | 镜像/构建 | 端口 | 依赖 | 卷 |
|---|---|---|---|---|
| `mysql` | `mysql:8.0` | 不发布 | — | `mysql_data` |
| `redis` | `redis:7-alpine` | 不发布 | — | `redis_data` |
| `neo4j` | `neo4j:5.26-community` | 不发布 | — | `neo4j_data`, `neo4j_logs` |
| `backend` | `build ./backend` | `8000:8000` | mysql/redis/neo4j `service_healthy` | `./backend/media`、`./backend/logs`、`./backend/data` 绑定挂载 |
| `frontend` | `build ./front` | `3000:80` | backend healthy | — |

**凭据策略（关键点）：** 基础设施内部凭据由 compose 统一生成并注入后端，与 `backend/.env` 解耦，杜绝"用户改了 .env 的 MySQL/Neo4j 密码但容器不同步"的问题：

- compose 顶层用 `${VAR:-默认值}` 内插（用户可在项目根目录建 `.env` 自定义，非必须）。
- mysql 服务 `MYSQL_ROOT_PASSWORD` / `MYSQL_DATABASE=chat_history`。
- neo4j 服务 `NEO4J_USERNAME: neo4j` + `NEO4J_PASSWORD`（官方镜像 5.x 支持拆分变量，等价 NEO4J_AUTH）。
- backend 服务通过 YAML 锚点把上述同一组值注入 `environment`（MYSQL_*、REDIS_HOST/PORT、NEO4J_URI/USER/PASSWORD），覆盖 `env_file` 中同名项。
- backend 其余配置（OPENAI_*/VISION_*/EMBED_*/RERANKER_*/WEB_SEARCH_*/SECRET_KEY 等）来自 `env_file: ./backend/.env`。

**后端启动顺序保障：** mysql / redis / neo4j 各带 `healthcheck`；backend `depends_on: {condition: service_healthy}` + `restart: unless-stopped`（启动时数据库短暂未就绪可自愈）。`main.py::startup_event` 中 `init_db()`（幂等建表补列）、`seed_test_user()`（admin/admin1234）、`connect_redis()`、`ensure_graph_schema()`（幂等建索引）天然安全。

**降级语义：** Neo4j 不可用时仅图谱功能 503，主流程不受影响（现状即有）；但 compose 层面仍以 healthcheck + restart 尽量保证就绪。

**调试端口：** 仅发布 8000（/docs）与 3000。MySQL/Redis/Neo4j 端口不发布，需要调试时用户自行临时映射（文档说明）。

### 2. `backend/Dockerfile`（镜像约 3-5 GB，首次构建 10 分钟+）

- 基础镜像 `python:3.12-slim`（对齐 `requires-python >=3.12`）。
- apt 安装运行非结构化解析所需系统库：`libmagic1`（python-magic）、`poppler-utils`（pdf2image）、`libgl1 libglib2.0-0`（opencv/unstructured-inference 导入需要 libGL）。
- 依赖安装用已编译锁文件 `requirements.txt` + `pip install --no-cache-dir`（文件未变则命中 Docker 层缓存）；`PIP_INDEX_URL` 默认 `https://pypi.tuna.tsinghua.edu.cn/simple`（与项目 uv 镜像一致），可用 build-arg 覆盖。
- `COPY . .`（配合 `.dockerignore`），`WORKDIR /app`。
- `HEALTHCHECK`：`curl -f http://127.0.0.1:8000/health`。
- `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]`。

> 注意：`app/core/settings.py`、`main.py` 启动即读环境变量构建连接串（`db_config.py:9`），因此 compose 必须在启动时注入全部 infra 环境变量——设计已保证。容器内无 `.env` 文件，CWD=/app 也没有，环境变量直接来自 compose。

### 3. `backend/.dockerignore`

排除 `.env`（**防密钥进镜像**）、`.venv`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.coverage`、`logs/`、`media/`、`data/`、`results/`、`tests/`、`scripts/`、`openapi.json`、`.idea/`。

### 4. `front/Dockerfile`（多阶段）

- `node:20-slim AS build`：`npm ci`（用 package-lock 保证可复现）→ `npm run build`（tsc + vite，产物在 `dist/`）。
- `nginx:alpine`：拷贝 `dist/` 与 `front/nginx.conf`。

### 5. `front/nginx.conf`

- `client_max_body_size 210m`（后端上传上限：单文件 20MB / 总计 200MB，nginx 默认 1MB 会截断）。
- 托管 `dist/` + SPA fallback：`location / { try_files $uri $uri/ /index.html; }`。
- API 反代照抄 `front/vite.config.ts` 的 15 个前缀：`/chat/agent/`（SSE，需 `proxy_buffering off`）、`/chat/rag/`、`/chat/session/`、`/chat/sessions`、`/chat/reorder`、`/knowledge/`、`/note/`、`/note-template/`、`/review/`、`/health`、`/user`、`/file`、`/media`、`/config`、`/api/graph`（图谱 SSE，`proxy_buffering off`）——目标 `http://backend:8000`（不带 URI，保留原路径）。
- 流式超时：`proxy_read_timeout 300s`，`proxy_http_version 1.1`。
- 透传 `Host / X-Real-IP / X-Forwarded-For`。

### 6. `front/.dockerignore`

`node_modules/`、`dist/`。

### 7. 根目录 `start.bat`

流程：

1. `docker version`/`docker info` 预检，未装 Docker Desktop 或引擎未启动给出中文提示并退出。
2. 不存在 `backend\.env` 时从 `backend\.env.example` 复制。
3. `docker compose up -d --build`（失败输出日志提示）。
4. 提示：若未配置 LLM Key，编辑 `backend/.env` 后执行 `docker compose restart backend`。
5. 打开浏览器 `http://localhost:3000`。
6. 打印默认账号 `admin / admin1234`。

> 项目现有约定仅 Windows 环境（作者本人 Win + `net start mysql` 文档），本次只做 `.bat`；Linux/Mac 用户直接跑 `docker compose` 命令，README 说明即可。

### 8. 文档更新

- README「快速开始」前插「⭐ Docker 一键启动」小节：前置要求（Docker Desktop、内存 ≥ 4GB、首次构建约 10 分钟）、步骤、默认账号、数据持久化说明、常见问题指针。
- `docs/troubleshooting.md` 追加「Docker 部署」小节：Key 未配置、改 Key 后重启、重建镜像、重置数据卷。

## 数据持久化

| 内容 | 位置 | 类型 |
|---|---|---|
| MySQL 数据 | `mysql_data` named volume | Docker volume |
| Redis 数据 | `redis_data` named volume | Docker volume |
| Neo4j 数据 | `neo4j_data` volume（含 plugins 子卷，为向量/全文索引留余地） | Docker volume |
| 上传媒体/头像 | `backend/media` | bind mount |
| 运行日志 | `backend/logs` | bind mount |
| PDF 抽取图片等 | `backend/data` | bind mount |

bind mount 目标：`/app/media`、`/app/logs`、`/app/data`（由 `logger_handler.py:9` 与 `path_tool.py:28` 的代码相对路径推算，容器内代码位于 `/app`）。

## 错误处理与验证清单

**错误处理：**

- 启动顺序：healthcheck 全链保障；backend 挂掉自动 restart。
- 改 Key 只需 `docker compose restart backend`（.env 通过 env_file 每次启动读取，不重建镜像）。
- LLM Key 为空：系统可启动、可浏览，问答/写作等 AI 功能报错降级（现状语义，README 说明）。

**验收清单（实施后由作者本地执行 `start.bat` 验证）：**

1. `curl http://localhost:3000` 返回前端 HTML；`curl http://localhost:8000/health` 返回 healthy。
2. `docker compose logs backend` 依次出现"数据库表结构初始化完成 / 数据库会话管理器初始化完成 / Redis连接初始化完成"。
3. 浏览器打开 3000，admin/admin1234 登录成功。
4. 新建笔记并保存 → MySQL `notes` 表有记录；上传 md/pdf 文档 → Neo4j 出现 Doc/Chunk 节点。
5. 重启宿主机后 `docker compose up -d`（或 start.bat）数据仍在。

## 非目标（YAGNI）

- 不做 Linux/macOS 一键脚本（有 compose 命令即可）。
- 不做 CI 镜像构建、不做镜像推送发布。
- 不内置本地 Ollama 容器（用户如需本地模型自选挂 extra compose）。
- 不把 `backend/.env` 以外的密钥传入镜像；不提供 HTTPS/域名生产级部署编排。
- 不引入 watchdog/热重载（保持简单，开发仍走本地方案）。
