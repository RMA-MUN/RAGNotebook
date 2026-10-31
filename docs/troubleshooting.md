# 故障排除

## 常见问题

### 1. API Key 错误

**问题**：`Invalid API Key` 或 `API Key expired`

**解决方法**：
- 检查 `.env` 文件中的 `OPENAI_API_KEY` 是否正确
- 确保 API Key 没有过期或权限不足
- 到对应 LLM 服务商控制台检查 API Key 状态

### 2. 数据库连接失败

**问题**：`Connection refused` 或 `Access denied`

**解决方法**：
- 检查数据库配置是否正确（主机、端口、用户名、密码）
- 确保数据库服务正在运行
- 验证数据库用户权限
- 检查网络连接和防火墙设置

### 3. Neo4j 图谱服务不可用

**问题**：图谱页面提示「图谱服务不可用」（HTTP 503），或日志出现 `Neo.ClientError`

**解决方法**：
- 检查 Neo4j 服务是否在运行：`net start neo4j`（Windows 服务）
- 确认 `.env` 中 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` 正确
- 重启后端，观察启动日志中的「Neo4j 图 Schema 就绪」是否出现
- Neo4j 不可用时笔记/聊天/知识库主流程不受影响，仅图谱功能降级

### 4. 前端访问后端 API 失败

**问题**：`CORS error` 或 `Network error`

**解决方法**：
- 检查 CORS 配置是否正确
- 确保后端服务正在运行
- 验证网络连接和防火墙设置
- 检查 API 端点是否正确

### 5. 云端重排序失败

**问题**：重排序结果一直按原顺序返回（similarity 全为 0）

**解决方法**：
- 检查 `.env` 中 `RERANKER_API_BASE_URL` / `RERANKER_API_KEY` / `RERANKER_MODEL` 是否配置正确
- 确认 rerank 服务账户额度未用尽
- 重排序失败时自动按原顺序降级，不阻塞对话主流程

### 6. 依赖环境问题

**问题**：导入报错或包版本不一致

**解决方法**：
- 后端使用 uv 管理依赖：`uv sync --extra dev` 按锁文件还原环境
- 前端：`npm install`
- 不要在服务运行中执行 `uv sync`（Windows 下可能留下半卸载的包）

### 8. 端口被占用

**问题**：`Address already in use`

**解决方法**：
- 查找占用端口的进程：`netstat -ano | findstr :8000`（Windows）或 `lsof -i :8000`（Linux）
- 终止占用端口的进程
- 使用不同的端口启动服务

### 9. 文件上传失败

**问题**：`File too large` 或 `Unsupported file type`

**解决方法**：
- 检查文件大小是否超过限制（单个文件20MB，多个文件总计200MB）
- 确保文件类型为 TXT / PDF / MD / PPTX / DOCX
- 检查文件权限

### 10. 会话历史丢失

**问题**：无法获取会话历史或会话被意外删除

**解决方法**：

- 检查数据库连接是否正常
- 验证用户权限是否正确
- 检查会话 ID 是否正确

### 11. Docker 部署常见问题

#### 启动失败 / 容器反复重启

**问题**：`docker compose up` 后 `backend` 一直重启，或 `start.bat` 提示后端长时间未就绪

**解决方法**：
- 查看日志定位原因：`docker compose logs -f backend`
- MySQL / Redis / Neo4j 初始化较慢时后端会重试，通常 1-2 分钟内自愈；确认三者 healthy：`docker compose ps`
- 端口冲突：3000 / 8000 被占用时修改 `docker-compose.yml` 的 `ports` 映射后重新 `docker compose up -d`

#### 模型 Key 配置/修改

- 编辑 `backend/.env`，填写 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME`（`.env` 不会进入镜像，改完不必重新构建）
- 生效：`docker compose restart backend`
- Key 无效时前端表现为问答报错/401，页面本身可正常使用

#### 容器内数据库地址错误（Connection refused 指向 localhost）

Docker 模式下 MySQL / Redis / Neo4j 地址与密码由 `docker-compose.yml` 自动覆盖（`mysql` / `redis` / `neo4j` 容器名），**不要**修改 `backend/.env` 中 MYSQL_HOST / REDIS_HOST / NEO4J_URI 为 localhost。

#### 修改数据库密码 / Neo4j 密码

- 在项目根目录创建 `.env`，写入 `MYSQL_ROOT_PASSWORD=xxx`、`NEO4J_PASSWORD=xxx` 后重新执行 `docker compose up -d`
- 注意 Neo4j 密码只在**首次启动（数据卷为空）**时生效；已初始化的数据卷密码固定，如需更换需先 `docker compose down` 并删除 `neo4j_data` 数据卷（`docker volume rm ragnotebook_neo4j_data`），会清空图谱数据

#### 重置所有数据

```bash
docker compose down -v   # 删除数据卷（MySQL/Redis/Neo4j 数据全部清空）
```

重新执行 `start.bat` 或 `docker compose up -d --build` 即可全新初始化。

#### 首次构建很慢 / 下载失败

- 后端镜像含 torch / unstructured 等重型依赖，首次构建约 10 分钟属正常
- 后端依赖用 uv 按 `uv.lock` 安装，镜像源与本地开发一致（backend/pyproject.toml 的 `[tool.uv]` 默认清华 PyPI）；如需换源，修改 pyproject.toml 后重新 `docker compose build backend`（仅换"uv 安装"源用 `--build-arg PIP_INDEX_URL=...`）
- 国内网络拉取 Docker Hub 镜像慢时，为 Docker 配置镜像加速器后重试

#### 无 Key 时哪些功能可用

未配置 LLM Key 时系统可启动、可注册登录、可浏览页面；笔记保存、知识库上传解析正常，但实体抽取/智能标签/问答/写作等 AI 能力会失败或降级。

## 日志检查

### 应用日志
- 后端日志位于 `backend/logs/` 目录
- 前端日志可在浏览器控制台查看

### 常见错误模式

#### 重排序降级
```
【重排序服务】重排序失败: ...
```
→ 检查 RERANKER_* 配置；失败自动按原顺序降级，不阻塞对话

#### 图谱服务不可用
```
HTTP 503: 图谱服务不可用
```
→ 检查 Neo4j 服务状态与 NEO4J_URI 配置

#### 数据库错误
```
OperationalError: (2003, "Can't connect to MySQL server")
```
→ 检查数据库连接配置

#### API 错误
```
HTTPException: 401 Unauthorized
```
→ 检查认证令牌是否有效

## 性能问题排查

### 响应缓慢
- 检查数据库查询性能
- 确认 Neo4j 向量/全文索引已建立（后端启动时 ensure_graph_schema 幂等创建）
- 监控 CPU/内存使用率

### 内存占用过高
- 检查 LLM 上下文长度配置
- 优化文档切片批次大小

## 调试技巧

### 启用详细日志
在 `backend/app/core/logger_handler.py` 中设置日志级别为 `DEBUG`

### 测试 API 端点
使用 FastAPI 自动生成的交互式文档：`http://localhost:8000/docs`

### 检查环境变量
```bash
# Windows
echo %OPENAI_API_KEY%

# Linux/Mac
echo $OPENAI_API_KEY
```

## 联系支持

如果问题仍然存在，请提供以下信息：
1. 完整的错误日志
2. 环境配置信息
3. 操作系统和 Python 版本
4. 复现步骤

可以通过项目 GitHub Issues 或联系作者获取帮助。

---

[← 返回首页](../README.md)